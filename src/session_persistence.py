"""会话持久化与静默续期探活。

职责：
1. 登录成功后把 Playwright storage_state（含 qm_keyst / uin / psrf_* 系列 cookie）
   保存到 state/session.json；
2. 启动时优先加载已存状态；
3. try_silent_refresh()：带会话访问 y.qq.com，通过 musicu.fcg 探活 qm_keyst
   是否有效，有效则直接拿到最新 cookie 走更新流程，省掉一次浏览器登录。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests as http_requests

from .config import QQMUSIC_URL, STATE_DIR, TARGET_COOKIE_KEYS

SESSION_PATH = STATE_DIR / "session.json"

# 探活接口：musicu.fcg 统一网关。module 取「拉取当前登录用户信息」的接口，
# 返回 code==0 且带用户身份字段 => 会话 cookie（qm_keyst）仍有效。
PROBE_ENDPOINT = "https://u.y.qq.com/cgi-bin/musicu.fcg"
PROBE_PAYLOAD = {
    "comm": {"ct": 24, "cv": 0, "uin": 0},
    "req": {
        "module": "music.musichallSsoInfoChecker.UserInfo",
        "method": "GetUserInfo",
        "param": {},
    },
}
PROBE_TIMEOUT_S = 15


# ─── cookie 集合抽取 ───────────────────────────────────────


def extract_target_cookies(cookies: list[dict]) -> dict[str, str]:
    """从 Playwright cookie 列表里挑出目标 key（去重时保留后出现的值）。"""
    return {
        c["name"]: c["value"]
        for c in cookies
        if c.get("name") in TARGET_COOKIE_KEYS and c.get("value") is not None
    }


def has_valid_login_payload(payload: dict[str, str]) -> bool:
    """判定 cookie 集合是否构成一次『登录成功』。

    标准与旧代码一致：qqmusic_key（或等价的 qm_keyst）必须存在。
    """
    return bool(payload.get("qqmusic_key") or payload.get("qm_keyst"))


# ─── 探活判定（纯逻辑，可单测） ──────────────────────────────


def judge_probe_response(data: Any) -> bool:
    """判定 musicu.fcg 用户信息接口返回是否意味着 qm_keyst 有效。

    判定逻辑：
    - 顶层 code 必须为 0（非 0 通常是未登录 / key 失效，如 1001/2001）；
    - req.data 中应能拿到非零 uin（或存在 qqmusic_key / qm_keyst 字段）。
    缺少任何一条都视为无效。
    """
    if not isinstance(data, dict):
        return False
    if data.get("code") != 0:
        return False
    req = data.get("req") or {}
    if req.get("code") != 0:
        return False
    body = req.get("data") or {}
    uin = body.get("uin") or body.get("encryptUin") or ""
    has_key = bool(body.get("qqmusic_key") or body.get("qm_keyst"))
    # uin 可能是数字 0 或字符串 "0"/""，都视为未登录
    uin_ok = str(uin).strip("0") != ""
    return uin_ok or has_key


# ─── 会话存取 ──────────────────────────────────────────────


class CookieStore:
    """负责 state/session.json 的读写。"""

    def __init__(self, path: Path = SESSION_PATH):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, cookies: list[dict], origins: list[dict] | None = None) -> Path:
        """保存 storage_state 结构（Playwright context.storage_state() 的返回值）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "cookies": list(cookies),
            "origins": list(origins or []),
            "saved_at": time.time(),
        }
        self.path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        return self.path

    def load(self) -> dict | None:
        """读取 storage_state；损坏或缺失返回 None。"""
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not data.get("cookies"):
            return None
        return data

    def load_cookies(self) -> list[dict]:
        state = self.load()
        return list(state["cookies"]) if state else []

    def invalidate(self) -> None:
        """探活失败时删除旧会话，避免下次又用坏状态。"""
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ─── 运行记录（外部 heartbeat 读） ───────────────────────────


@dataclass
class RunRecord:
    ts: float = field(default_factory=time.time)
    success: bool = False
    reason: str = ""
    source: str = ""  # silent_refresh | browser_login | abort ...
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(self.ts)),
            "success": self.success,
            "reason": self.reason,
            "source": self.source,
            "detail": self.detail,
        }


def record_last_run(record: RunRecord, path: Path | None = None) -> Path:
    """追加式记录最近一次运行结果到 state/last_run.json。"""
    path = Path(path or (STATE_DIR / "last_run.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(path.read_text())
        if not isinstance(history, list):
            history = []
    except (OSError, json.JSONDecodeError):
        history = []
    history.append(record.to_dict())
    # 只保留最近 20 条，避免无限膨胀
    path.write_text(json.dumps(history[-20:], ensure_ascii=False, indent=2))
    return path


# ─── 静默刷新（需要真实网络/浏览器，正式运行才执行） ────────────


async def try_silent_refresh(
    context,
    proxy: str | None = None,
) -> dict | None:
    """带已有会话访问 y.qq.com，探活 qm_keyst 是否有效。

    流程：
    1. 用 storage_state 新建 context（调用方负责）；
    2. 打开 y.qq.com 首页让 cookie 生效，并触发一次性 musicu.fcg 探活请求；
    3. 判定成功 => 从 context 收割目标 cookie（qm_keyst 等可能已被服务端刷新），
       返回 cookie 字典；失败 => 返回 None，由调用方走浏览器登录循环。

    判定标准（judge_probe_response）：
    - musicu.fcg 返回 code==0 且 req.code==0；
    - data 中 uin 非零，或包含 qqmusic_key/qm_keyst。
    """
    if context is None:
        return None
    page = await context.new_page()
    try:
        await page.goto(QQMUSIC_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # 页面内发起一次带 cookie 的探活请求（fetch 会自动带上 u.y.qq.com 凭据）
        probe = await page.evaluate(
            """async (payload) => {
                const resp = await fetch(
                    '%s',
                    {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload),
                    }
                );
                return await resp.json().catch(() => null);
            }"""
            % PROBE_ENDPOINT,
            PROBE_PAYLOAD,
        )
        if not judge_probe_response(probe):
            # 二次确认：访问个人页看 cookie 是否被刷新出 qqmusic_key
            await page.goto(
                QQMUSIC_URL + "n/ryqq/profile", wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(5000)

        cookies = await context.cookies()
        payload = extract_target_cookies(cookies)
        if has_valid_login_payload(payload):
            return payload
        return None
    except Exception as exc:  # 探活失败不算致命，交给浏览器循环兜底
        print(f"[SilentRefresh] 探活异常: {exc}")
        return None
    finally:
        await page.close()


def probe_key_via_requests(cookies: dict[str, str], proxy: str | None = None) -> bool:
    """无浏览器情形下的纯 requests 探活（复用 judge_probe_response 判定）。

    供脚本化检查 / 单测 mock 使用，不依赖 Playwright。
    """
    jar = http_requests.Session()
    for name, value in cookies.items():
        jar.cookies.set(name, value, domain=".y.qq.com")
    kwargs: dict[str, Any] = {"timeout": PROBE_TIMEOUT_S}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        resp = jar.post(PROBE_ENDPOINT, json=PROBE_PAYLOAD, **kwargs)
        resp.raise_for_status()
        return judge_probe_response(resp.json())
    except Exception:
        return False