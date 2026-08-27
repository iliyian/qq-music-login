"""Telegram 通知。"""

from __future__ import annotations

import requests as http_requests

from .config import TELEGRAM_API


def send_telegram(token: str, chat_id: str, message: str, proxy: str | None = None):
    """发送 Telegram 消息通知"""
    kwargs = {
        "json": {"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
        "timeout": 10,
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    try:
        resp = http_requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            **kwargs,
        )
        resp.raise_for_status()
        print("[Telegram] 通知发送成功")
    except Exception as e:
        print(f"[Telegram] 通知发送失败: {e}")
