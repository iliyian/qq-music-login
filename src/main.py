"""流程编排入口（新版）。

流程：
    1. 优先加载 state/session.json（或 INIT_COOKIE），走 try_silent_refresh()；
       探活成功 => 直接收割最新 cookie。
    2. 失败 => AI 决策登录循环（browser_loop）完成账号密码登录；
       期间验证码分支：滑块自动尝试 N 次 -> 短信等待 -> 都失败 abort 上报。
    3. 成功 => 保存 storage_state 到 state/session.json -> Vercel upsert
       QQ_MUSIC_KEY / QQ_UIN -> 触发重部署 -> Telegram 通知结果。
    4. state/last_run.json 记录每次运行时间/结果/失效原因（供外部 heartbeat 读）。

入口兼容：qq_music_login.py 仍在根目录，python qq_music_login.py [--headless]
行为不变。
"""

from __future__ import annotations

import asyncio
import sys

from playwright.async_api import async_playwright

from .browser_loop import (
    LoopAborted,
    LoopError,
    LoopState,
    dump_debug,
    run_login_loop,
)
from .config import PROJECT_ROOT, QQMUSIC_URL, RunOutcome, Settings, load_settings
from .session_persistence import (
    CookieStore,
    RunRecord,
    extract_target_cookies,
    has_valid_login_payload,
    record_last_run,
    try_silent_refresh,
)
from .sms_waiter import SMSSource, TelegramBotSource, WebhookFileSource, wait_for_sms_async
from .ai_decision import AIVisionDecision
from .telegram_notify import send_telegram
from .vercel_api import update_vercel

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def parse_cookie_string(cookie_str: str) -> list[dict]:
    """将 'key1=val1; key2=val2' 格式的 cookie 字符串解析为 Playwright cookie 列表"""
    cookies = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".y.qq.com",
            "path": "/",
        })
    return cookies


# ─── 浏览器上下文构造 ───────────────────────────────────────


async def build_context(p, settings, storage_state: str | None = None):
    launch_opts: dict = {"headless": settings.headless}
    if settings.qq_music_proxy:
        launch_opts["proxy"] = {"server": settings.qq_music_proxy}
        print(f"[Browser] 使用代理: {settings.qq_music_proxy}")
    launch_opts["args"] = ["--no-sandbox"]  # root 环境必需
    browser = await p.chromium.launch(**launch_opts)
    kwargs: dict = {
        "viewport": {"width": 1280, "height": 800},
        "user_agent": DEFAULT_UA,
    }
    if storage_state:
        kwargs["storage_state"] = storage_state
    context = await browser.new_context(**kwargs)
    return browser, context


async def success_when_logged(context):
    """success_check 工厂：context 出现 qqmusic_key 即成功。"""

    async def check(state: LoopState) -> bool:
        cookies = await context.cookies()
        return has_valid_login_payload(extract_target_cookies(cookies))

    return check


# ─── 浏览器登录（循环 + 验证码 + 短信分支） ────────────────────


async def run_browser_login(context, settings, notify) -> tuple[dict | None, str]:
    """执行完整浏览器登录，返回 (cookie_payload, 失效原因)。"""
    page = await context.new_page()
    decider = AIVisionDecision(
        slider_max_attempts=settings.slider_max_attempts,
        qq_uin=settings.qq_uin,
        qq_password=settings.qq_password,
    )
    outcome: dict = {"success": False, "reason": ""}
    try:
        print("[Login] 打开QQ音乐首页...")
        await page.goto(QQMUSIC_URL, wait_until="domcontentloaded")

        summary = await run_login_loop(
            page,
            decider.as_decide(page),
            success_check=await success_when_logged(context),
            max_steps=settings.loop_max_steps,
            step_timeout_s=settings.loop_step_timeout_s,
            decide_timeout_s=120,  # 决策函数里可能做滑块求解，放宽一点
            total_timeout_s=settings.loop_total_timeout_s,
        )
        outcome["success"] = bool(summary.get("success"))
        outcome["reason"] = summary.get("reason", "")
    except LoopAborted as exc:
        outcome["reason"] = f"决策函数终止: {exc}"
    except LoopError as exc:
        outcome["reason"] = f"登录循环失败: {exc}"
    payload: dict = {}
    try:
        cookies = await context.cookies()
        payload = extract_target_cookies(cookies)
    except Exception:
        payload = {}

    if outcome["success"] and has_valid_login_payload(payload):
        return payload, ""

    # ── 验证码兜底分支：滑块失败 -> 短信等待 ──
    if decider.captcha_seen:
        kind = decider.captcha_seen.get("kind", "unknown")
        print(f"[Login] 验证码分支：kind={kind}，滑块已试完，转入短信等待...")
        code = await wait_sms_with_settings(settings)
        if code:
            print(f"[Login] 收到短信验证码: {code}（验证码输入界面自动化为后续 TODO，"
                  f"若此刻页面已完成登录将直接收割 cookie）")
            # 给页面一点时间完成剩余跳转，再收割一次
            await page.wait_for_timeout(5000)
            cookies = await context.cookies()
            payload = extract_target_cookies(cookies)
            if has_valid_login_payload(payload):
                return payload, ""
            outcome["reason"] = f"短信验证码 {code} 已收到，但登录未自动完成（TODO: 输入界面自动化）"
        else:
            outcome["reason"] = f"验证码(kind={kind})滑块与短信等待均失败，放弃本次登录"

    await dump_debug(page, outcome["reason"] or "登录未成功")
    return None, outcome["reason"] or "登录未成功"


def build_sms_source(settings) -> SMSSource | None:
    """按配置构造短信验证码数据源。"""
    if settings.sms_telegram_bot_token and settings.sms_telegram_chat_id:
        try:
            return TelegramBotSource(
                settings.sms_telegram_bot_token,
                settings.sms_telegram_chat_id,
                proxy=settings.telegram_proxy,
            )
        except ValueError as exc:
            print(f"[SmsWaiter] Telegram 源不可用: {exc}")
    if settings.sms_drop_file:
        return WebhookFileSource(settings.sms_drop_file)
    return None


async def wait_sms_with_settings(settings) -> str | None:
    """从配置的数据源等一条时间窗内的 6 位验证码。"""
    source = build_sms_source(settings)
    if source is None:
        print("[SmsWaiter] 未配置任何短信数据源（SMS_TELEGRAM_*/SMS_DROP_FILE），跳过等待")
        return None
    return await wait_for_sms_async(
        timeout_s=300,
        poll_interval=5,
        window_s=settings.sms_window_s,
    )


# ─── 发布（Vercel + 通知 + 落盘） ───────────────────────────


def publish_result(settings, payload: dict, source: str, notify) -> RunOutcome:
    """保存会话 -> Vercel upsert -> 触发重部署 -> TG 通知。"""
    uin = payload.get("uin", "")
    qqmusic_key = payload.get("qqmusic_key", "")

    vercel_ok = True
    vercel_err = ""
    if settings.vercel_token and settings.vercel_project_id:
        try:
            update_vercel(
                settings.vercel_token, settings.vercel_project_id,
                uin, qqmusic_key, proxy=settings.vercel_proxy,
            )
        except Exception as exc:
            vercel_ok = False
            vercel_err = str(exc)
            notify(
                f"❌ <b>QQ音乐 Key 刷新失败</b>\n\n"
                f"登录成功但 Vercel 更新失败\n"
                f"uin: {uin}\n"
                f"错误: {vercel_err}"
            )
    else:
        print("[Vercel] 未配置 VERCEL_TOKEN/VERCEL_PROJECT_ID，跳过更新")

    if vercel_ok:
        notify(
            f"✅ <b>QQ音乐 Key 刷新成功</b>\n\n"
            f"uin: {uin}\n"
            f"来源: {source}\n"
            f"qqmusic_key: <code>{qqmusic_key[:8]}…</code>\n\n"
            f"Vercel 环境变量已更新并触发重新部署"
        )
    return RunOutcome(
        success=vercel_ok,
        reason="" if vercel_ok else f"Vercel 更新失败: {vercel_err}",
        uin=uin,
        qqmusic_key=qqmusic_key,
        detail={"source": source},
    )


# ─── 主流程 ────────────────────────────────────────────────


async def run(settings: Settings, notify) -> RunOutcome:
    store = CookieStore()
    outcome: RunOutcome

    async with async_playwright() as p:
        # 1) 优先静默续期：已存 storage_state 或 INIT_COOKIE
        payload = None
        source = ""
        if store.exists():
            print("[SilentRefresh] 发现已存会话 state/session.json，尝试静默续期...")
            _, context = await build_context(p, settings, storage_state=str(store.path))
            payload = await try_silent_refresh(context, proxy=settings.qq_music_proxy)
            await context.close()

        if not payload and settings.init_cookie:
            print("[SilentRefresh] 用 INIT_COOKIE 注入后探活...")
            _, context = await build_context(p, settings)
            await context.add_cookies(parse_cookie_string(settings.init_cookie))
            payload = await try_silent_refresh(context, proxy=settings.qq_music_proxy)
            await context.close()

        # 2) 静默失败 => 浏览器登录循环
        if payload:
            source = "silent_refresh"
        else:
            if store.exists():
                print("[SilentRefresh] 会话已失效，删除 state/session.json")
                store.invalidate()
            if not settings.qq_uin or not settings.qq_password:
                outcome = RunOutcome(
                    success=False,
                    reason="静默续期失败且未配置 QQ_UIN/QQ_PASSWORD，无法浏览器登录",
                    detail={"source": "abort"},
                )
                record_last_run(RunRecord(success=False, reason=outcome.reason, source="abort"))
                return outcome
            _, context = await build_context(p, settings)
            try:
                payload, reason = await run_browser_login(context, settings, notify)
                source = "browser_login"
                if not payload:
                    outcome = RunOutcome(success=False, reason=reason or "浏览器登录失败",
                                         detail={"source": "browser_login"})
                    record_last_run(RunRecord(success=False, reason=outcome.reason,
                                              source="browser_login"))
                    return outcome
            finally:
                await context.close()

        # 3) 成功：保存会话 + 发布
        all_cookies = payload  # try_silent_refresh / run_browser_login 已收割目标 cookie
        print(f"[Done] 登录成功（来源: {source}），uin={payload.get('uin', '?')}")
        # 保存 storage_state 需要完整 cookie 列表；这里用收割到的目标 cookie 简化存档，
        # 完整 storage_state 在 run_browser_login 内部有 context 时已经可以拿到，
        # 为简化编排，此处直接存目标 cookie 子集（Playwright 兼容格式）。
        cookie_objs = [
            {"name": k, "value": v, "domain": ".y.qq.com", "path": "/"}
            for k, v in all_cookies.items()
        ]
        store.save(cookie_objs)
        outcome = publish_result(settings, payload, source, notify)
        record_last_run(RunRecord(
            success=outcome.success, reason=outcome.reason,
            source=source, detail={"uin": outcome.uin},
        ))
        return outcome


async def main(argv: list[str] | None = None):
    argv = list(sys.argv) if argv is None else argv
    settings = load_settings(argv=argv[1:])

    if not settings.vercel_token or not settings.vercel_project_id:
        print("警告：未配置 VERCEL_TOKEN/VERCEL_PROJECT_ID，将只登录并本地存档（不更新线上）")

    def notify(message: str):
        if settings.telegram_bot_token and settings.telegram_chat_id:
            send_telegram(settings.telegram_bot_token, settings.telegram_chat_id, message,
                          proxy=settings.telegram_proxy)

    outcome = await run(settings, notify)
    if not outcome.success:
        notify(f"❌ <b>QQ音乐 Key 刷新失败</b>\n\n{outcome.reason}")
        raise SystemExit(1)
    print("\n全部完成!")
    return outcome


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())