#!/usr/bin/env python3
"""
QQ音乐登录 - 自动获取 qqmusic_key 并更新到 Vercel 项目环境变量

用法:
    1. 复制 .env.example 为 .env 并填写配置
    2. python qq_music_login.py [--headless]

v2.0: 实现已模块化到 src/ 包（session_persistence / browser_loop /
captcha_solver / sms_waiter / vercel_api / telegram_notify / main）。
本文件保留为入口兼容 shim。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import main  # noqa: E402

if __name__ == "__main__":
    asyncio.run(main(sys.argv))