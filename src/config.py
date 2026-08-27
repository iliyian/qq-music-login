"""集中配置：从 .env / 环境变量加载所有运行参数。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（src/config.py 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

QQMUSIC_URL = "https://y.qq.com/"
VERCEL_API = "https://api.vercel.com"
TELEGRAM_API = "https://api.telegram.org"

# 登录成功后必须存在的核心 cookie
TARGET_COOKIE_KEYS = (
    "qqmusic_key", "qm_keyst", "uin",
    "psrf_qqaccess_token", "psrf_qqopenid",
    "psrf_qqunionid", "psrf_qqrefresh_token",
    "psrf_access_token_expiresAt", "tmeLoginType",
    "euin", "psrf_musickey_createtime",
)

STATE_DIR = PROJECT_ROOT / "state"
SHOTS_DIR = PROJECT_ROOT / "shots"


@dataclass
class Settings:
    """运行时配置快照，全部来自环境变量。"""

    # QQ 凭据
    qq_uin: str = ""
    qq_password: str = ""
    init_cookie: str = ""

    # Vercel
    vercel_token: str = ""
    vercel_project_id: str = ""

    # Telegram 通知
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 短信验证码来源
    sms_telegram_bot_token: str = ""
    sms_telegram_chat_id: str = ""
    sms_drop_file: str = ""
    sms_window_s: int = 600  # 忽略此时间窗之外的旧短信消息

    # AI 决策循环
    loop_max_steps: int = 25
    loop_step_timeout_s: int = 30
    loop_total_timeout_s: int = 900
    slider_max_attempts: int = 3

    # 代理
    qq_music_proxy: str | None = None
    telegram_proxy: str | None = None
    vercel_proxy: str | None = None

    headless: bool = False


def load_settings(env_file: Path | None = None, argv: list[str] | None = None) -> Settings:
    """读取 .env 并构造 Settings。

    argv 默认取 sys.argv[1:]，用于识别 --headless。
    """
    if env_file is None:
        env_file = PROJECT_ROOT / ".env"
    load_dotenv(env_file)
    argv = sys_argv() if argv is None else argv

    def _get(name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()

    return Settings(
        qq_uin=_get("QQ_UIN"),
        qq_password=_get("QQ_PASSWORD"),
        init_cookie=_get("INIT_COOKIE"),
        vercel_token=_get("VERCEL_TOKEN"),
        vercel_project_id=_get("VERCEL_PROJECT_ID"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
        sms_telegram_bot_token=os.getenv("SMS_TELEGRAM_BOT_TOKEN", "").strip()
        or _get("TELEGRAM_BOT_TOKEN"),
        sms_telegram_chat_id=os.getenv("SMS_TELEGRAM_CHAT_ID", "").strip()
        or _get("TELEGRAM_CHAT_ID"),
        sms_drop_file=_get("SMS_DROP_FILE", str(STATE_DIR / "sms.txt")),
        sms_window_s=int(_get("SMS_WINDOW_S", "600")),
        loop_max_steps=int(_get("LOOP_MAX_STEPS", "25")),
        loop_step_timeout_s=int(_get("LOOP_STEP_TIMEOUT_S", "30")),
        loop_total_timeout_s=int(_get("LOOP_TOTAL_TIMEOUT_S", "900")),
        slider_max_attempts=int(_get("SLIDER_MAX_ATTEMPTS", "3")),
        qq_music_proxy=os.getenv("QQ_MUSIC_PROXY") or None,
        telegram_proxy=os.getenv("TELEGRAM_PROXY") or None,
        vercel_proxy=os.getenv("VERCEL_PROXY") or None,
        headless="--headless" in argv,
    )


def sys_argv() -> list[str]:
    import sys

    return sys.argv[1:]


@dataclass
class RunOutcome:
    """一次完整运行的最终结果，用于通知与 last_run.json。"""

    success: bool
    reason: str = ""
    uin: str = ""
    qqmusic_key: str = ""
    detail: dict = field(default_factory=dict)
