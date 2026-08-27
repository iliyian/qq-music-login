"""QQ音乐 key 自动续期 —— 模块化重构版。

模块：
- config:               环境变量/配置
- session_persistence:  storage_state 存取 + 静默续期探活 + last_run 记录
- browser_loop:         AI 决策登录循环骨架
- captcha_solver:       验证码检测 / 滑块轨迹与求解
- sms_waiter:           短信验证码等待（TG bot / webhook drop file）
- vercel_api:           Vercel 环境变量 upsert + 重部署
- telegram_notify:      TG 结果通知
- main:                 流程编排入口
"""

__version__ = "2.0.0-alpha"
