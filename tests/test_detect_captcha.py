"""detect_captcha / guess_kind_from_url 单测（纯逻辑，不碰网络）。"""

from src.captcha_solver import detect_captcha, guess_kind_from_url


def test_detect_tcaptcha_slider():
    det = detect_captcha(
        frame_urls=["https://ssl.captcha.qq.com/TCaptcha.js"],
    )
    # TCaptcha.js 是脚本不算 iframe；用 iframe URL 测
    det = detect_captcha(
        frame_urls=["https://t.captcha.qq.com/cap_union_prehandle?aid=xxx&type=slider"]
    )
    assert det is not None
    assert det["kind"] == "slider"


def test_detect_tcaptcha_generic_url_unknown_kind():
    det = detect_captcha(frame_urls=["https://captcha.qq.com/template/something"])
    assert det is not None
    assert det["kind"] in ("slider", "point_select", "sms", "unknown")


def test_detect_no_captcha():
    assert detect_captcha(frame_urls=["https://y.qq.com/", "https://ptlogin2.qq.com/"]) is None


def test_detect_no_args():
    assert detect_captcha() is None


def test_screenshot_only_returns_none_todo():
    # 截图 bytes 识别是 TODO（后续接 AI 决策），当前不得误报
    assert detect_captcha(screenshot_bytes=b"\x89PNG-fake") is None


def test_kind_hints():
    assert guess_kind_from_url("https://x/?type=point_select") == "point_select"
    assert guess_kind_from_url("https://x/sms/verify") == "sms"
    assert guess_kind_from_url("https://x/slider.html") == "slider"
    assert guess_kind_from_url("https://x/whatever") == "unknown"