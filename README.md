# qq-music-login

自动刷新 QQ 音乐 `qqmusic_key`，更新到 Vercel 环境变量并触发重新部署。专为 [meting-api](https://meting-api.iliyian.com) 提供长期 QQ 音乐 VIP 歌曲支持。

## 为什么需要

QQ 音乐的 `QQ_MUSIC_KEY` 约 3 天自动失效。本项目通过 Playwright 自动化登录 QQ 音乐获取新 key，配合 systemd timer 每 2 天自动运行，确保 meting-api 始终持有有效的 VIP cookie。

## 工作原理

1. Playwright 打开 QQ 音乐网页端（y.qq.com），自动完成账号密码登录
2. 从浏览器 cookie 中提取 `qqmusic_key` 和 `uin`
3. 通过 Vercel API 更新项目环境变量 `QQ_MUSIC_KEY` 和 `QQ_UIN`
4. 触发 Vercel production 重新部署，新 key 立即生效
5. 通过 Telegram Bot 发送结果通知（可选）

## 本地使用

### 安装

```bash
git clone https://github.com/iliyian/qq-music-login.git
cd qq-music-login
pip install playwright python-dotenv requests
python -m playwright install chromium
```

### 配置

```bash
cp .env.example .env
# 编辑 .env 填写配置
```

### 运行

```bash
# 有头模式（可看到浏览器，首次运行推荐）
python qq_music_login.py

# 无头模式
python qq_music_login.py --headless
```

## 服务器部署

在 Linux 服务器上一键部署，自动每 2 天刷新一次：

```bash
# 1. 克隆项目
git clone https://github.com/iliyian/qq-music-login.git
cd qq-music-login

# 2. 配置环境变量
cp .env.example .env
nano .env

# 3. 一键部署
chmod +x setup.sh
sudo ./setup.sh
```

`setup.sh` 会自动完成：
- 安装 Python 依赖 + Playwright Chromium
- 安装 Xvfb（虚拟显示，以有头模式运行规避反检测）
- 创建 systemd timer，每 2 天凌晨 4 点自动运行（带 30 分钟随机延迟）
- 启用并启动定时器

### 管理命令

```bash
# 查看定时器状态
systemctl status qq-music-login.timer

# 查看下次运行时间
systemctl list-timers qq-music-login.timer

# 手动运行一次
sudo systemctl start qq-music-login.service

# 查看运行日志
journalctl -u qq-music-login.service -e
```

## 配置说明

### 必填

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `QQ_UIN` | QQ 号 | 你的 QQ 账号 |
| `QQ_PASSWORD` | QQ 密码 | 你的 QQ 密码 |
| `VERCEL_TOKEN` | Vercel API Token | [Vercel Tokens](https://vercel.com/account/tokens) 创建 |
| `VERCEL_PROJECT_ID` | Vercel 项目 ID | 项目 Settings → General → Project ID |

### 可选（Telegram 通知）

| 变量 | 说明 |
|------|------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token |
| `TELEGRAM_CHAT_ID` | 接收通知的 Chat ID |

不配置 Telegram 变量时，脚本正常运行，只是不发送通知。

## Telegram 通知配置

1. 在 Telegram 中找到 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建机器人，获取 Bot Token
2. 获取 Chat ID：
   - 给你的 Bot 发送一条消息
   - 访问 `https://api.telegram.org/bot<你的Token>/getUpdates`
   - 在返回的 JSON 中找到 `chat.id`
3. 将 Token 和 Chat ID 填入 `.env`

通知效果：
- 成功时：包含 uin、key 前 20 位、Vercel 部署状态
- 失败时：包含具体错误原因

## 验证码处理

QQ 登录有时会弹出滑块验证码：

- **有头模式**：在弹出的浏览器窗口中手动完成验证，脚本会等待最多 2 分钟
- **无头模式**：无法手动操作，频繁触发时建议切换有头模式
- **服务器部署**：使用 Xvfb 虚拟显示以有头模式运行，但验证码仍需关注。同一 IP 稳定运行后触发频率会降低
- 同一 IP 首次登录或频繁登录更容易触发验证码

## 常见问题

**Q: key 多久失效？**
A: 约 3 天。systemd timer 设置为每 2 天运行一次，确保在失效前刷新。

**Q: 服务器上触发验证码怎么办？**
A: 首次部署建议先手动运行 `sudo systemctl start qq-music-login.service` 并通过日志观察。同一 IP 稳定登录后验证码频率会降低。如果持续触发，考虑先在本地有头模式完成一次登录。

**Q: 怎么确认定时任务正常工作？**
A: 配置 Telegram 通知后，每次运行都会收到成功/失败消息。也可通过 `journalctl -u qq-music-login.service -e` 查看日志。

**Q: Vercel Token 和 Project ID 怎么获取？**
A: Token 在 [Vercel Tokens](https://vercel.com/account/tokens) 页面创建。Project ID 在项目 Settings → General 页面底部。
