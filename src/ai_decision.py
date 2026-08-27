"""AI 视觉决策器：每步截图交给多模态模型，由模型决定下一步动作。

设计要点：
- 凭据不进提示词：AI 输出 <QQ_NUMBER>/<QQ_PASSWORD> 占位符，执行前本地替换
- 验证码观察保留代码路径：slider -> solve_slider（专用算法不重造）
- AI 输出严格 JSON 动作数组，解析失败按 wait 处理，连续失败 3 次 abort
"""

from __future__ import annotations

import asyncio
import base64
import json
import os

import requests

from .browser_loop import (
    ACTION_ABORT, ACTION_CLICK, ACTION_KEY, ACTION_TYPE, ACTION_WAIT,
    Action, LoopState,
)
from .captcha_solver import detect_captcha, solve_slider

DEFAULT_BASE_URL = "https://axon.iliyian.com/v1"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"

SYSTEM_PROMPT = """你在协助自动化登录 QQ 音乐网页版（y.qq.com），目标是完成账号登录。
每一轮你会收到浏览器当前截图和状态，请决定下一步要执行的动作。

可用动作（输出 JSON 数组，只输出 JSON，不要任何解释或代码块标记）：
[
  {"type": "click",  "selector": "CSS或playwright选择器", "frame": "可选,iframe URL关键字", "timeout_ms": 8000},
  {"type": "type",   "selector": "...", "text": "要输入的文本", "frame": "..."},
  {"type": "key",    "selector": "...", "text": "Enter", "frame": "..."},
  {"type": "wait",   "text": "2000"},
  {"type": "abort",  "text": "放弃原因"}
]

规则：
1. selector 支持 CSS（#u、#p、#login_button）和 playwright 文本选择器（text=密码登录）。
2. 登录弹窗在 iframe 里，其 URL 含 ptlogin2 —— 操作弹窗内元素时必须带 "frame": "ptlogin2"。
3. 需要输入账号时 text 用 "<QQ_NUMBER>"，输入密码时 text 用 "<QQ_PASSWORD>"（系统会本地替换，禁止输出真实值）。
4. 新版登录页默认展示二维码，需要点「密码登录」切换（#switcher_plogin 或 text=密码登录）。
5. 每步最多 3 个动作；页面看起来正在加载/跳转时用 wait 等待 2000ms。
6. 已登录（页面出现用户头像/昵称）或判断无法继续时才用 abort。
7. 动作保守：只做必要操作，避免点广告或无关元素。
"""


class AIVisionDecision:
    """用多模态模型做每步决策，保留验证码代码路径。"""

    def __init__(self, slider_max_attempts: int = 3, give_up_after: int = 15,
                 qq_uin: str = "", qq_password: str = ""):
        self.slider_max_attempts = slider_max_attempts
        self.give_up_after = give_up_after
        self.qq_uin = qq_uin
        self.qq_password = qq_password
        self.captcha_seen: dict | None = None
        self.slider_failed = False
        self.base_url = os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.model = os.getenv("AI_MODEL", DEFAULT_MODEL)
        self.api_key = os.getenv("AXON_API_KEY", "")
        self._consecutive_ai_fail = 0

    # ── AI 调用 ──
    def _call_ai(self, state: LoopState, screenshot: bytes) -> list[dict]:
        b64 = base64.b64encode(screenshot).decode()
        history = state.notes.get("ai_history", [])[-6:]
        user_text = (
            f"当前第 {state.step} 步。URL: {state.url}\n"
            f"frames: {state.frame_urls}\n"
            f"历史动作记录: {json.dumps(history, ensure_ascii=False)}\n"
            "请根据截图输出下一步动作 JSON 数组。"
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]},
            ],
            "max_tokens": 800,
            "temperature": 0.2,
        }
        r = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload, timeout=90,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return self._parse_actions(content)

    @staticmethod
    def _parse_actions(content: str) -> list[dict]:
        text = content.strip()
        if "```" in text:
            for part in text.split("```"):
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:]
                if part.strip().startswith("["):
                    text = part.strip()
                    break
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"AI 输出里没有 JSON 数组: {content[:200]}")
        arr = json.loads(text[start:end + 1])
        return arr if isinstance(arr, list) else []

    def _substitute(self, text: str | None) -> str | None:
        if text is None:
            return None
        return (text.replace("<QQ_NUMBER>", self.qq_uin)
                    .replace("<QQ_PASSWORD>", self.qq_password))

    def _to_actions(self, items: list[dict]) -> list[Action]:
        acts: list[Action] = []
        for it in items[:3]:
            if not isinstance(it, dict) or it.get("type") not in (
                ACTION_CLICK, ACTION_TYPE, ACTION_KEY, ACTION_WAIT, ACTION_ABORT
            ):
                continue
            t = it["type"]
            if t in (ACTION_CLICK, ACTION_TYPE, ACTION_KEY) and not it.get("selector"):
                continue
            acts.append(Action(
                type=t,
                selector=it.get("selector"),
                text=self._substitute(it.get("text")),
                frame=it.get("frame"),
                timeout_ms=int(it.get("timeout_ms", 8000)),
            ))
        return acts

    def as_decide(self, page, settings_state=None):
        print(f"[AI] 决策模型: {self.model} @ {self.base_url}")

        async def decide(state: LoopState, screenshot: bytes) -> list[Action]:
            state.notes.setdefault("flow", "ai-vision")

            # 1) 验证码观察（代码路径，AI 不参与求解）
            det = detect_captcha(screenshot_bytes=screenshot, frame_urls=state.frame_urls)
            if det and not self.slider_failed:
                self.captcha_seen = det
                state.notes["captcha"] = det
                if det["kind"] == "slider":
                    print("[AI] 检测到滑块，调用专用求解器")
                    ok = await solve_slider(page, max_attempts=self.slider_max_attempts)
                    if ok:
                        state.notes["captcha_solved"] = True
                        return [Action(type=ACTION_WAIT, text="2500")]
                    self.slider_failed = True
                    return [Action(type=ACTION_ABORT, text="滑块自动尝试全部失败，转短信/人工分支")]

            # 2) AI 视觉决策
            if not self.api_key:
                return [Action(type=ACTION_ABORT, text="未配置 AXON_API_KEY，无法使用 AI 决策")]
            try:
                items = await asyncio.to_thread(self._call_ai, state, screenshot)
                self._consecutive_ai_fail = 0
            except Exception as exc:
                self._consecutive_ai_fail += 1
                print(f"[AI] 决策调用失败({self._consecutive_ai_fail}): {exc}")
                if self._consecutive_ai_fail >= 3:
                    return [Action(type=ACTION_ABORT, text=f"AI 决策连续失败: {exc}")]
                return [Action(type=ACTION_WAIT, text="2500")]

            acts = self._to_actions(items)
            if not acts:
                return [Action(type=ACTION_WAIT, text="2000")]

            state.notes["ai_history"] = state.notes.get("ai_history", []) + [
                {"step": state.step, "url": state.url[:120],
                 "actions": [{k: v for k, v in a.__dict__.items() if v is not None} for a in acts]}
            ]

            state.notes["ai_steps"] = state.notes.get("ai_steps", 0) + 1
            if state.notes["ai_steps"] > self.give_up_after:
                return [Action(type=ACTION_ABORT, text="AI 决策步数超预算，登录未完成")]
            return acts

        return decide

    async def __call__(self, state: LoopState, screenshot: bytes) -> list[Action]:
        raise NotImplementedError("使用 as_decide(page) 生成闭包后再传入 run_login_loop")
