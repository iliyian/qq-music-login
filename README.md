# qq-music-login

通过 Playwright 自动化浏览器登录 QQ 音乐，获取 `qqmusic_key`，并自动更新到 Vercel 项目环境变量触发重新部署。

## 原理

1. 使用 Playwright 打开 QQ 音乐网页端（y.qq.com）
2. 自动点击登录 → 切换到账号密码登录 → 填入 QQ 号和密码 → 提交
3. 登录成功后从浏览器 cookie 中提取 `qqmusic_key`（即 `QQ_MUSIC_KEY`）和 `uin`（即 `QQ_UID`）
4. 调用 Vercel API 将 `QQ_UID` 和 `QQ_MUSIC_KEY` 写入指定项目的环境变量
5. 自动触发该项目的 production 重新部署，使新 key 生效

## 前置条件

- Python 3.10+
- 系统能打开 Chromium 浏览器（无头模式不需要桌面环境，但遇到验证码时需要有头模式）

## 安装

```bash
git clone https://github.com/iliyian/qq-music-login.git
cd qq-music-login
pip install playwright python-dotenv requests
python -m playwright install chromium
```

## 配置

复制 `.env.example` 为 `.env`，填写以下四项：

```bash
cp .env.example .env
```

| 变量 | 说明 | 获取方式 |
|------|------|----------|
| `QQ_UIN` | QQ 号 | 你的 QQ 账号 |
| `QQ_PASSWORD` | QQ 密码 | 你的 QQ 密码 |
| `VERCEL_TOKEN` | Vercel API Token | [Vercel Tokens](https://vercel.com/account/tokens) 页面创建 |
| `VERCEL_PROJECT_ID` | Vercel 项目 ID | 项目 Settings → General → Project ID |

### 获取 Vercel Token

1. 打开 https://vercel.com/account/tokens
2. 点击 **Create Token**
3. 名称随意填，Scope 选择你的项目所在的团队或个人账户
4. 复制生成的 token 填入 `.env`

### 获取 Vercel Project ID

1. 打开你的 Vercel 项目
2. 进入 **Settings** → **General**
3. 页面底部可以看到 **Project ID**，复制填入 `.env`

## 使用

### 基本用法（有头模式，可看到浏览器）

```bash
python qq_music_login.py
```

### 无头模式

```bash
python qq_music_login.py --headless
```

> 注意：无头模式下如果触发滑块验证码将无法手动处理，建议首次运行使用有头模式。

### 运行流程

```
[1/6] 打开QQ音乐首页...
[2/6] 点击登录...
[3/6] 等待登录框...
  找到登录框
[4/6] 切换到账号密码登录...
[5/6] 输入账号密码...
  已提交，等待响应...
[6/6] 提取cookie...

登录成功!
  uin         = 123456789
  qqmusic_key = Q_H_L_xxxxxxxxxxxxxxxxxxxxxxxx...
  cookie已保存到: cookies.json

[Vercel] 更新环境变量...
  更新 QQ_UID 成功
  更新 QQ_MUSIC_KEY 成功
[Vercel] 触发重新部署...
  已触发重新部署: your-project-xxxxxxxx.vercel.app

全部完成!
```

## 验证码处理

QQ 登录有时会弹出滑块验证码，脚本会自动检测并提示：

- **有头模式**：在弹出的浏览器窗口中手动完成验证即可，脚本会等待验证完成后继续
- **无头模式**：无法手动操作，如果频繁触发验证码，建议切换到有头模式
- 同一 IP 首次登录或频繁登录更容易触发验证码

## 写入 Vercel 的环境变量

| Vercel 环境变量 | 值 | 来源 |
|---|---|---|
| `QQ_UID` | QQ 号 | 登录后 cookie 中的 `uin` |
| `QQ_MUSIC_KEY` | 音乐 key | 登录后 cookie 中的 `qqmusic_key` |

这两个变量会同时设置到 production、preview、development 三个环境，并在更新后自动触发 production 重新部署。

## 文件说明

```
├── .env.example        # 环境变量模板
├── .gitignore          # Git 忽略规则
├── qq_music_login.py   # 主程序
└── README.md
```

运行后会额外生成：

- `cookies.json` — 完整的浏览器 cookie 备份（已被 gitignore）

## 注意事项

- `qqmusic_key` 有有效期，过期后需要重新运行脚本
- `.env` 包含敏感信息，已被 `.gitignore` 排除，不要手动提交
- `cookies.json` 包含完整登录态，同样不要提交
