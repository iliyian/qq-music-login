"""sms_waiter 纯逻辑单测（正则提取 / 时间窗过滤 / WebhookFileSource）。不碰网络。"""

import json
import time

import pytest

from src.sms_waiter import (
    SMSMessage,
    WebhookFileSource,
    extract_code,
    wait_for_sms,
)


# ─── extract_code ──────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("【QQ音乐】验证码 482913，10分钟内有效", "482913"),
        ("您的验证码是 123456，请勿泄露", "123456"),
        ("code: 000000.", "000000"),
        ("no code here", None),
        ("1234567 is 7 digits", None),          # 7 位不匹配
        ("QQ 123456789 (9 digits)", None),       # 9 位中截取不出独立 6 位
        ("order 12345678 contains no code", None),
        ("", None),
    ],
)
def test_extract_code(text, expected):
    assert extract_code(text) == expected


# ─── 时间窗过滤 ────────────────────────────────────────────


class FakeSource:
    def __init__(self, messages):
        self.messages = messages

    def fetch_recent(self):
        return self.messages

    def poll(self, since_ts):
        for msg in self.messages:
            if msg.ts >= since_ts:
                code = extract_code(msg.text)
                if code:
                    return code
        return None


def test_poll_filters_old_messages():
    now = time.time()
    src = FakeSource([
        SMSMessage(text="验证码 111111", ts=now - 3600),  # 旧消息，必须被过滤
        SMSMessage(text="验证码 222222", ts=now),          # 新消息
    ])
    assert src.poll(now - 60) == "222222"


def test_poll_window_excludes_everything():
    now = time.time()
    src = FakeSource([SMSMessage(text="验证码 111111", ts=now - 3600)])
    assert src.poll(now - 60) is None


# ─── wait_for_sms（假时钟 + 假源） ───────────────────────────


class StepClock:
    """依次返回给定时间序列；耗尽后停在最后一个值。"""

    def __init__(self, times):
        self.times = list(times)
        self.i = 0

    def __call__(self):
        t = self.times[min(self.i, len(self.times) - 1)]
        self.i += 1
        return t


def test_wait_for_sms_timeout(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    clock = StepClock([0, 10, 20, 30])
    src = FakeSource([])  # 永远没有验证码
    assert wait_for_sms(src, timeout_s=25, poll_interval=5, clock=clock) is None


def test_wait_for_sms_found(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    clock = StepClock([0, 1, 2])
    src = FakeSource([SMSMessage(text="您的验证码 654321", ts=1.5)])
    assert wait_for_sms(src, timeout_s=30, poll_interval=5, clock=clock) == "654321"


# ─── WebhookFileSource ─────────────────────────────────────


def test_webhook_file_source_json_lines(tmp_path):
    fp = tmp_path / "sms.txt"
    now = time.time()
    fp.write_text(
        json.dumps({"text": "验证码 111222", "ts": now - 5}) + "\n"
        + json.dumps({"text": "验证码 333444", "ts": now - 1}) + "\n"
    )
    src = WebhookFileSource(fp)
    msgs = src.fetch_recent()
    assert len(msgs) == 2
    code = src.poll(now - 30)
    assert code == "111222"  # poll 返回窗口内第一条含码消息


def test_webhook_file_source_plain_lines(tmp_path):
    fp = tmp_path / "sms.txt"
    fp.write_text("您的验证码是 987654\n")
    src = WebhookFileSource(fp)
    msgs = src.fetch_recent()
    assert len(msgs) == 1
    assert extract_code(msgs[0].text) == "987654"


def test_webhook_file_source_missing(tmp_path):
    src = WebhookFileSource(tmp_path / "absent.txt")
    assert src.fetch_recent() == []