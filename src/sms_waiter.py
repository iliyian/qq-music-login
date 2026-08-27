"""短信验证码等待。

预留两种数据源抽象（SMSSource 接口）：
1. TelegramBotSource —— 轮询指定 chat 的最新文本消息，正则提取 6 位数字，
   并过滤时间窗口之外的旧消息；
2. WebhookFileSource —— 读本地 drop 文件（SMS Forwarder / webhook 落盘），
   按 mtime + 内容时间戳过滤旧消息。
"""

from __future__ import annotations

import json
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TELEGRAM_API = "https://api.telegram.org"

# 6 位验证码：前后不能紧邻数字，避免匹配到 QQ 号/手机号片段
CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")


def extract_code(text: str) -> Optional[str]:
    """从消息文本中提取 6 位数字验证码。"""
    if not text:
        return None
    m = CODE_RE.search(text)
    return m.group(1) if m else None


@dataclass
class SMSMessage:
    text: str
    ts: float  # unix 秒


class SMSSource(ABC):
    """短信验证码数据源抽象。"""

    @abstractmethod
    def fetch_recent(self) -> list[SMSMessage]:
        """返回最近的候选消息（由调用方再做时间窗过滤）。"""

    def poll(self, since_ts: float) -> Optional[str]:
        """取一条 since_ts 之后收到的、含 6 位验证码的消息。"""
        for msg in self.fetch_recent():
            if msg.ts >= since_ts:
                code = extract_code(msg.text)
                if code:
                    return code
        return None


class TelegramBotSource(SMSSource):
    """通过 Bot getUpdates 轮询指定 chat 的文本消息。"""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        proxy: str | None = None,
        api_base: str = TELEGRAM_API,
    ):
        if not bot_token or not chat_id:
            raise ValueError("TelegramBotSource 需要 bot_token 和 chat_id")
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self.proxy = proxy
        self.api_base = api_base.rstrip("/")
        # 防止重复消费同一 update
        self._last_update_id: int | None = None

    def fetch_recent(self) -> list[SMSMessage]:
        import requests as http_requests

        kwargs: dict = {"timeout": 10}
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        params: dict = {"allowed_updates": ["message"], "timeout": 0}
        if self._last_update_id is not None:
            params["offset"] = self._last_update_id + 1
        try:
            resp = http_requests.get(
                f"{self.api_base}/bot{self.bot_token}/getUpdates",
                params=params,
                **kwargs,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
        except Exception as exc:
            print(f"[SmsWaiter] Telegram 轮询失败: {exc}")
            return []

        messages: list[SMSMessage] = []
        for upd in updates:
            uid = upd.get("update_id")
            if uid is not None and (self._last_update_id is None or uid > self._last_update_id):
                self._last_update_id = uid
            msg = upd.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue
            text = msg.get("text") or ""
            date = msg.get("date") or 0
            if text:
                messages.append(SMSMessage(text=text, ts=float(date)))
        return messages


class WebhookFileSource(SMSSource):
    """读本地 drop 文件：每行一个 JSON {"text": ..., "ts": ...}，或纯文本行。

    纯文本行没有可信时间戳时按文件 mtime 计；调用方窗口过滤兜底。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch_recent(self) -> list[SMSMessage]:
        try:
            mtime = self.path.stat().st_mtime
            raw = self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        out: list[SMSMessage] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                text = str(obj.get("text") or "")
                ts = float(obj.get("ts") or mtime)
            except (json.JSONDecodeError, ValueError, TypeError):
                text, ts = line, mtime
            if text:
                out.append(SMSMessage(text=text, ts=ts))
        return out


def wait_for_sms(
    source: SMSSource,
    timeout_s: float = 300,
    poll_interval: float = 5,
    window_s: float = 600,
    clock=time.time,
) -> Optional[str]:
    """轮询 source 直到拿到时间窗内的 6 位验证码或超时。

    window_s: 忽略 now-window_s 之前的旧消息（避免把历史短信当本次验证码）。
    clock 参数便于单测注入假时钟。
    """
    start = clock()
    while True:
        code = source.poll(start)
        if code:
            return code
        if clock() - start >= timeout_s:
            return None
        time.sleep(min(poll_interval, max(0.0, timeout_s - (clock() - start))))


async def wait_for_sms_async(source: SMSSource, **kwargs) -> Optional[str]:
    """wait_for_sms 的异步包装（放线程池跑，别卡事件循环）。"""
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: wait_for_sms(source, **kwargs))
