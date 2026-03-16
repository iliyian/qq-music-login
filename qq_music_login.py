#!/usr/bin/env python3
"""
QQ音乐登录 - 自动获取 qqmusic_key 并更新到 Vercel 项目环境变量

用法:
    1. 复制 .env.example 为 .env 并填写配置
    2. python qq_music_login.py [--headless]
"""

import asyncio
import os
import random
import sys
from pathlib import Path

import requests as http_requests
from dotenv import load_dotenv
from playwright.async_api import async_playwright

QQMUSIC_URL = "https://y.qq.com/"
VERCEL_API = "https://api.vercel.com"
TELEGRAM_API = "https://api.telegram.org"


# ─── QQ音乐登录 ───────────────────────────────────────────


async def login(qq: str, password: str, headless: bool = False) -> dict | None:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        try:
            result = await _do_login(page, context, qq, password)
        finally:
            await browser.close()
        return result


async def _do_login(page, context, qq: str, password: str) -> dict | None:
    print("[1/6] 打开QQ音乐首页...")
    await page.goto(QQMUSIC_URL, wait_until="domcontentloaded")
    await _human_delay(page, 3000, 5000)

    print("[2/6] 点击登录...")
    await page.locator("a:has-text('登录')").first.click()
    await _human_delay(page, 3000, 5000)

    print("[3/6] 等待登录框...")
    login_frame = await _wait_for_login_frame(page)
    if not login_frame:
        print("错误：未找到登录iframe")
        for f in page.frames:
            print(f"  - {f.url[:100]}")
        return None
    print("  找到登录框")

    print("[4/6] 切换到账号密码登录...")
    # 等待iframe内容加载完成（最多15秒）
    pwd_link = login_frame.locator("a:has-text('密码登录')")
    old_switcher = login_frame.locator("#switcher_plogin")
    found = False
    for _ in range(30):
        if await pwd_link.count() > 0 and await pwd_link.is_visible():
            await pwd_link.click()
            found = True
            break
        if await old_switcher.count() > 0 and await old_switcher.is_visible():
            await old_switcher.click()
            found = True
            break
        await page.wait_for_timeout(500)
    if not found:
        await page.screenshot(path="/tmp/qq_login_debug.png")
        print("  错误：未找到密码登录切换入口，截图已保存到 /tmp/qq_login_debug.png")
        return None
    await _human_delay(page, 1500, 3000)

    print("[5/6] 输入账号密码...")
    # 账号框: 优先 #u，回退到 input[type="text"]
    u_field = login_frame.locator("#u")
    if await u_field.count() == 0:
        u_field = login_frame.locator("input[type='text']").first
    try:
        await u_field.wait_for(state="visible", timeout=10000)
    except Exception:
        await _dump_debug(page, login_frame, "账号框未出现")
        return None
    await u_field.click()
    await _human_delay(page, 300, 600)
    await u_field.type(qq, delay=random.randint(80, 160))
    await _human_delay(page, 800, 1500)

    # 密码框: 优先 #p，回退到 input[type="password"]
    p_field = login_frame.locator("#p")
    if await p_field.count() == 0:
        p_field = login_frame.locator("input[type='password']").first
    try:
        await p_field.wait_for(state="visible", timeout=10000)
    except Exception:
        await _dump_debug(page, login_frame, "密码框未出现")
        return None
    await p_field.click()
    await _human_delay(page, 300, 600)
    await p_field.type(password, delay=random.randint(80, 160))
    await _human_delay(page, 1000, 2000)

    # 登录按钮: 优先 #login_button，回退到文本匹配
    login_btn = login_frame.locator("#login_button")
    if await login_btn.count() == 0:
        login_btn = login_frame.locator("a:has-text('登录'), button:has-text('登录')").first
    await login_btn.click()
    print("  已提交，等待响应...")

    logged_in = await _wait_for_login_result(page, login_frame)
    if not logged_in:
        return None

    print("[6/6] 提取cookie...")
    cookies = await context.cookies()

    target_keys = {
        "qqmusic_key", "qm_keyst", "uin",
        "psrf_qqaccess_token", "psrf_qqopenid",
        "psrf_qqunionid", "psrf_qqrefresh_token",
        "psrf_access_token_expiresAt", "tmeLoginType",
        "euin", "psrf_musickey_createtime",
    }
    result = {c["name"]: c["value"] for c in cookies if c["name"] in target_keys}

    if "qqmusic_key" not in result:
        print("\n未获取到 qqmusic_key，登录可能失败")
        return None

    print("\n登录成功!")
    print(f"  uin = {result.get('uin', '?')}")
    return result


async def _dump_debug(page, login_frame, reason: str):
    """截图并打印iframe中的input元素用于调试"""
    await page.screenshot(path="/tmp/qq_login_debug.png")
    print(f"  错误：{reason}，截图已保存到 /tmp/qq_login_debug.png")
    print("  当前iframe中的input元素:")
    inputs = login_frame.locator("input")
    for i in range(await inputs.count()):
        el = inputs.nth(i)
        el_id = await el.get_attribute("id") or ""
        el_type = await el.get_attribute("type") or ""
        el_name = await el.get_attribute("name") or ""
        print(f"    <input id='{el_id}' type='{el_type}' name='{el_name}'>")
    print("  当前iframe中的a元素:")
    links = login_frame.locator("a")
    for i in range(await links.count()):
        el = links.nth(i)
        text = (await el.text_content() or "").strip()
        if text:
            print(f"    <a>{text}</a>")


async def _human_delay(page, min_ms: int = 1000, max_ms: int = 3000):
    """模拟人类操作间隔"""
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def _wait_for_login_frame(page, timeout_s: int = 15):
    for _ in range(timeout_s * 2):
        for frame in page.frames:
            if "ptlogin2" in frame.url:
                return frame
        await page.wait_for_timeout(500)
    return None


async def _wait_for_login_result(page, login_frame, timeout_s: int = 10) -> bool:
    for _ in range(timeout_s * 2):
        if "y.qq.com" in page.url and "wx_redirect" not in page.url:
            await page.wait_for_timeout(2000)
            return True

        err = login_frame.locator("#err_m")
        if await err.count() > 0 and await err.is_visible():
            err_text = (await err.text_content() or "").strip()
            if err_text:
                print(f"  登录失败: {err_text}")
                return False

        has_captcha = any(
            "captcha" in f.url or "tcaptcha" in f.url.lower()
            for f in page.frames
        )
        if has_captcha:
            print("  检测到验证码，请在浏览器中手动完成验证...")
            try:
                await page.wait_for_url("**/y.qq.com/**", timeout=120000)
                await page.wait_for_timeout(3000)
                return True
            except Exception:
                print("  验证超时")
                return False

        await page.wait_for_timeout(500)

    try:
        await page.wait_for_url("**/y.qq.com/**", timeout=5000)
        await page.wait_for_timeout(3000)
        return True
    except Exception:
        print("  登录超时，未检测到成功跳转")
        print("  如需手动操作，请在浏览器中完成后按回车...")
        await asyncio.get_event_loop().run_in_executor(None, input)
        await page.wait_for_timeout(3000)
        return "y.qq.com" in page.url




# ─── Vercel API ────────────────────────────────────────────


def _vercel_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get_env_vars(token: str, project_id: str) -> list[dict]:
    """获取项目当前所有环境变量"""
    resp = http_requests.get(
        f"{VERCEL_API}/v9/projects/{project_id}/env",
        headers=_vercel_headers(token),
    )
    resp.raise_for_status()
    return resp.json().get("envs", [])


def _upsert_env_var(token: str, project_id: str, key: str, value: str):
    """创建或更新单个环境变量（覆盖所有target: production/preview/development）"""
    headers = _vercel_headers(token)
    envs = _get_env_vars(token, project_id)

    existing = [e for e in envs if e["key"] == key]

    if existing:
        env_id = existing[0]["id"]
        resp = http_requests.patch(
            f"{VERCEL_API}/v9/projects/{project_id}/env/{env_id}",
            headers=headers,
            json={
                "value": value,
                "target": ["production", "preview", "development"],
                "type": "encrypted",
            },
        )
    else:
        resp = http_requests.post(
            f"{VERCEL_API}/v10/projects/{project_id}/env",
            headers=headers,
            json={
                "key": key,
                "value": value,
                "target": ["production", "preview", "development"],
                "type": "encrypted",
            },
        )

    resp.raise_for_status()
    action = "更新" if existing else "创建"
    print(f"  {action} {key} 成功")


def _trigger_redeploy(token: str, project_id: str):
    """获取最近一次production部署并触发重新部署"""
    headers = _vercel_headers(token)

    # 获取最近的 production deployment
    resp = http_requests.get(
        f"{VERCEL_API}/v6/deployments",
        headers=headers,
        params={"projectId": project_id, "target": "production", "limit": 1},
    )
    resp.raise_for_status()
    deployments = resp.json().get("deployments", [])

    if not deployments:
        print("  警告：未找到production部署，跳过重新部署")
        return

    deploy_id = deployments[0]["uid"]
    name = deployments[0].get("name", "?")

    resp = http_requests.post(
        f"{VERCEL_API}/v13/deployments",
        headers=headers,
        json={
            "name": name,
            "deploymentId": deploy_id,
            "target": "production",
        },
    )
    resp.raise_for_status()
    new_url = resp.json().get("url", "")
    print(f"  已触发重新部署: {new_url}")


def update_vercel(token: str, project_id: str, uin: str, qqmusic_key: str):
    """更新Vercel环境变量并触发重新部署"""
    print("\n[Vercel] 更新环境变量...")
    _upsert_env_var(token, project_id, "QQ_UIN", uin)
    _upsert_env_var(token, project_id, "QQ_MUSIC_KEY", qqmusic_key)

    print("[Vercel] 触发重新部署...")
    _trigger_redeploy(token, project_id)


# ─── Telegram 通知 ────────────────────────────────────────


def send_telegram(token: str, chat_id: str, message: str):
    """发送 Telegram 消息通知"""
    try:
        resp = http_requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        print(f"[Telegram] 通知发送成功")
    except Exception as e:
        print(f"[Telegram] 通知发送失败: {e}")


# ─── 入口 ──────────────────────────────────────────────────


async def main():
    load_dotenv(Path(__file__).parent / ".env")

    qq = os.getenv("QQ_UIN")
    password = os.getenv("QQ_PASSWORD")
    vercel_token = os.getenv("VERCEL_TOKEN")
    vercel_project_id = os.getenv("VERCEL_PROJECT_ID")
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not qq or not password:
        print("错误：请在 .env 中配置 QQ_UIN 和 QQ_PASSWORD")
        sys.exit(1)
    if not vercel_token or not vercel_project_id:
        print("错误：请在 .env 中配置 VERCEL_TOKEN 和 VERCEL_PROJECT_ID")
        sys.exit(1)

    headless = "--headless" in sys.argv

    def notify(message: str):
        if tg_token and tg_chat_id:
            send_telegram(tg_token, tg_chat_id, message)

    # 1. 登录QQ音乐
    result = await login(qq, password, headless=headless)
    if not result:
        notify("❌ <b>QQ音乐 Key 刷新失败</b>\n\n登录未成功，未获取到 qqmusic_key")
        sys.exit(1)

    # 2. 更新Vercel
    uin = result.get("uin", qq)
    qqmusic_key = result["qqmusic_key"]
    try:
        update_vercel(vercel_token, vercel_project_id, uin, qqmusic_key)
    except Exception as e:
        notify(
            f"❌ <b>QQ音乐 Key 刷新失败</b>\n\n"
            f"登录成功但 Vercel 更新失败\n"
            f"uin: {uin}\n"
            f"错误: {e}"
        )
        raise

    notify(
        f"✅ <b>QQ音乐 Key 刷新成功</b>\n\n"
        f"uin: {uin}\n"
        f"Vercel 环境变量已更新并触发重新部署"
    )

    print("\n全部完成!")


if __name__ == "__main__":
    asyncio.run(main())
