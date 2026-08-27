"""AIVisionDecision 单元测试：纯逻辑层，不发真实网络请求。"""

import pytest

from src.ai_decision import AIVisionDecision
from src.browser_loop import ACTION_ABORT, ACTION_CLICK, ACTION_WAIT, LoopState


def make_decider(**kw) -> AIVisionDecision:
    return AIVisionDecision(qq_uin="12345", qq_password="secret", **kw)


# ── _parse_actions ──

def test_parse_plain_array():
    out = AIVisionDecision._parse_actions('[{"type":"click","selector":"#u"}]')
    assert out == [{"type": "click", "selector": "#u"}]


def test_parse_code_fence():
    text = "```json\n[{\"type\":\"wait\",\"text\":\"1000\"}]\n```"
    assert AIVisionDecision._parse_actions(text)[0]["type"] == "wait"


def test_parse_with_prose():
    text = '好的，我的计划如下：\n[{"type":"click","selector":"#p"}]\n以上。'
    assert AIVisionDecision._parse_actions(text)[0]["selector"] == "#p"


def test_parse_missing_array_raises():
    with pytest.raises(ValueError):
        AIVisionDecision._parse_actions("我觉得应该点击登录按钮")


# ── 占位符替换 ──

def test_substitute_replaces_placeholders():
    d = make_decider()
    assert d._substitute("<QQ_NUMBER>/<QQ_PASSWORD>") == "12345/secret"


def test_substitute_none_passthrough():
    assert make_decider()._substitute(None) is None


def test_actions_never_contain_raw_credentials():
    d = make_decider()
    acts = d._to_actions([
        {"type": "type", "selector": "#u", "text": "<QQ_NUMBER>"},
        {"type": "type", "selector": "#p", "text": "<QQ_PASSWORD>"},
    ])
    joined = repr(acts)
    assert "12345" in joined and "secret" in joined
    assert "<QQ_NUMBER>" not in joined and "<QQ_PASSWORD>" not in joined


# ── _to_actions 校验 ──

def test_to_actions_drops_invalid():
    d = make_decider()
    acts = d._to_actions([
        {"type": "scroll"},                                # 未知类型
        {"type": "click"},                                 # click 缺 selector
        {"type": "click", "selector": "#ok", "timeout_ms": "9000"},
    ])
    assert len(acts) == 1
    assert acts[0].selector == "#ok"
    assert acts[0].timeout_ms == 9000


def test_to_actions_caps_at_three():
    d = make_decider()
    items = [{"type": "click", "selector": f"#b{i}"} for i in range(6)]
    assert len(d._to_actions(items)) == 3


# ── 决策闭包 ──

def _state() -> LoopState:
    return LoopState(step=1, url="https://y.qq.com", frame_urls=[])


@pytest.mark.asyncio
async def test_no_api_key_aborts(monkeypatch):
    d = make_decider()
    d.api_key = ""
    decide = d.as_decide(page=None)
    acts = await decide(_state(), b"png")
    assert acts[0].type == ACTION_ABORT


@pytest.mark.asyncio
async def test_consecutive_failures_fuse(monkeypatch):
    d = make_decider()
    d.api_key = "k"
    calls = {"n": 0}

    def boom(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("net down")

    monkeypatch.setattr(d, "_call_ai", boom)
    decide = d.as_decide(page=None)
    s = _state()
    assert (await decide(s, b"png"))[0].type == ACTION_WAIT
    assert (await decide(s, b"png"))[0].type == ACTION_WAIT
    third = (await decide(s, b"png"))[0]
    assert third.type == ACTION_ABORT and calls["n"] == 3


@pytest.mark.asyncio
async def test_slider_path_uses_solver(monkeypatch):
    import src.ai_decision as ai

    monkeypatch.setattr(ai, "detect_captcha",
                        lambda **kw: {"kind": "slider", "url": "tcaptcha"})
    solved = {"n": 0}

    async def fake_solve(page, max_attempts=3):
        solved["n"] += 1
        return True

    monkeypatch.setattr(ai, "solve_slider", fake_solve)
    d = make_decider()
    decide = d.as_decide(page=None)
    s = _state()
    acts = await decide(s, b"png")
    assert solved["n"] == 1
    assert acts[0].type == ACTION_WAIT
    assert s.notes.get("captcha_solved") is True


@pytest.mark.asyncio
async def test_happy_path_records_history(monkeypatch):
    d = make_decider()
    d.api_key = "k"
    monkeypatch.setattr(d, "_call_ai", lambda *a, **kw: [
        {"type": "click", "selector": "#switcher_plogin", "frame": "ptlogin2"},
    ])
    decide = d.as_decide(page=None)
    s = _state()
    acts = await decide(s, b"png")
    assert acts[0].type == ACTION_CLICK
    assert s.notes["ai_history"][0]["actions"][0]["selector"] == "#switcher_plogin"
    assert s.notes["ai_steps"] == 1
