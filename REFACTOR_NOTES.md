# REFACTOR_NOTES — AI 全自动登录循环改造（v2.0）

> 分支：`feature/ai-login-loop`　日期：2026-08-27
> 本阶段目标：搭好「AI 全自动登录循环」骨架，把单文件脚本重构为可插拔的模块化结构。
> **未对 y.qq.com 发起任何真实登录请求**（无凭据且会污染风控），只构建代码结构与可单测逻辑。

## 一、架构图（文字版）

```
qq_music_login.py            入口兼容 shim（python qq_music_login.py [--headless]）
        │
        ▼
src/main.py  ───────────────  流程编排
        │
        ├─1─► src/session_persistence.py
        │       state/session.json（storage_state 持久化）
        │       try_silent_refresh()：带会话探活 qm_keyst（musicu.fcg）
        │       state/last_run.json（运行记录，供外部 heartbeat 读）
        │
        ├─2─► 静默失败 ──► src/browser_loop.py（AI 决策循环骨架）
        │       每步：截图存档 shots/时间戳.png → DecisionFn(state, png) → 执行 Action
        │             → 再截图确认
        │       硬限制：max_steps / step_timeout / total_timeout / 异常 dump
        │       │
        │       └─► src/main.py::CredentialFlowDecision（决策函数实现，可替换为真实 AI）
        │               ├─ 固定脚本：点登录 → 切密码 → 填 #u/#p → 点 #login_button
        │               └─ 观察模式：每步检测验证码分支
        │                       │
        │                       ▼
        │               src/captcha_solver.py
        │                 detect_captcha()   → {kind: slider|point_select|sms|unknown}
        │                 solve_slider()     → 缺口定位(PIL/numpy 模板匹配, cv2 优先) + 拖拽
        │                 generate_trajectory() → 缓动+过冲+微抖动轨迹（已实装+单测）
        │                       │
        │                       ▼ slider 失败
        │               src/sms_waiter.py
        │                 TelegramBotSource（Bot getUpdates 轮询指定 chat）
        │                 WebhookFileSource（本地 drop 文件）
        │                 wait_for_sms(timeout_s=300, poll_interval=5)
        │                       │ 都失败
        │                       ▼ abort 上报
        ├─3─► 成功收割 cookie（qqmusic_key/qm_keyst/uin/psrf_*）→ 存 state/session.json
        │
        ├─4─► src/vercel_api.py    _upsert_env_var(QQ_MUSIC_KEY/QQ_UIN) + 触发重部署
        └─5─► src/telegram_notify.py  send_telegram() 结果通知
```

## 二、模块职责

| 模块 | 职责 | 状态 |
|---|---|---|
| `src/config.py` | 集中加载 `.env` 配置（Settings dataclass）、常量、RunOutcome | ✅ 可用 |
| `src/session_persistence.py` | storage_state 保存/加载/失效；`judge_probe_response` 探活判定（纯逻辑）；`try_silent_refresh` 静默续期；`record_last_run` 运行记录 | 存取/判定 ✅；静默刷新 ⚠️ 未经真实页面验证 |
| `src/browser_loop.py` | Action 数据结构、决策循环框架、截图存档、动作执行（单步超时）、异常 dump | 骨架 ✅（结构完整可跑，未真机验证） |
| `src/captcha_solver.py` | `detect_captcha`（frame URL 判 tcaptcha + kind 猜测）；`generate_trajectory` 人类运动轨迹（缓动/过冲/微抖，纯函数已单测）；`locate_gap` 缺口定位（cv2 优先、PIL/numpy 回退）；`solve_slider` 拖拽 | 轨迹/判定 ✅；缺口匹配 ⚠️ 算法已实现但无真实样本验证 |
| `src/sms_waiter.py` | SMSSource 抽象 + TelegramBotSource（getUpdates 轮询、chat 过滤、offset 去重、6 位码正则、时间窗过滤）+ WebhookFileSource；`wait_for_sms` | 抽象/纯逻辑 ✅；TG 真实轮询 ⚠️ 未联调 |
| `src/vercel_api.py` | 原 `_upsert_env_var`/`_trigger_redeploy`/`update_vercel` 原样迁移 | ✅ 可用（沿用旧逻辑） |
| `src/telegram_notify.py` | 原 `send_telegram` 原样迁移 | ✅ 可用 |
| `src/main.py` | 编排：静默刷新 → 浏览器循环 → 验证码/短信分支 → 收割 cookie → 存档 → Vercel → TG 通知 → last_run 记录 | 骨架 ✅（编排逻辑完整，未真机联调） |

## 三、入口兼容

- `python qq_music_login.py [--headless]` 行为不变（shim → `src.main.main`）。
- 旧函数 `login` / `login_with_cookie` / `_parse_cookie_string` 等：`_parse_cookie_string`
  保留为 `src.main.parse_cookie_string`；账号密码直连登录函数被
  `CredentialFlowDecision` + `run_login_loop` 取代（更细粒度、可插拔 AI 决策）。

## 四、探活判定标准（try_silent_refresh）

1. 用 `state/session.json`（storage_state）新建 context，打开 y.qq.com 首页；
2. 页面内 `fetch(credentials=include)` POST 一次 `u.y.qq.com/cgi-bin/musicu.fcg`
   （`music.musichallSsoInfoChecker.UserInfo/GetUserInfo`）；
3. 判定（`judge_probe_response`，纯函数可单测）：
   - 顶层 `code == 0` 且 `req.code == 0`；
   - `req.data.uin` 非零，或 data 带 `qqmusic_key`/`qm_keyst`；
4. 接口判定失败 → 二次兜底：访问 `y.qq.com/n/ryqq/profile` 看是否被服务端刷出
   `qqmusic_key` cookie；
5. 失败 → 删除 session.json，走浏览器登录循环。

> 注意：探活 module/method 名基于公开网关惯例推断，未真机验证；如果实际 module 名
> 不同，只需改 `session_persistence.PROBE_PAYLOAD` 与 `judge_probe_response`，其余不动。

## 五、硬限制（browser_loop）

- 最大步数 `LOOP_MAX_STEPS`（默认 25）；达到即 `LoopError`
- 单步动作超时 `LOOP_STEP_TIMEOUT_S`（默认 30s）；决策函数独立超时（代码里默认 120s，
  因为滑块求解可能较慢）
- 总时长上限 `LOOP_TOTAL_TIMEOUT_S`（默认 900s）
- 任何异常 → `dump_debug()`：截图 + HTML + frame URL 清单存到 `shots/debug_*.png/.html`

## 六、验证码分支（主流程顺序）

```
检测到 tcaptcha
  → kind==slider  → solve_slider 自动试 SLIDER_MAX_ATTEMPTS(默认3) 轮
      成功 → 继续观察等登录跳转
      失败 → ↓
  → 短信分支 → wait_for_sms(300s, poll 5s)（TG bot 或 drop 文件）
      收到验证码 → 再收割一次 context cookie（若此时已完成登录）
      未收到/未完成 → abort，写 last_run.json + TG 上报
```

## 七、后续待办清单

### 接入真实 AI 决策（OpenClaw）
- [ ] 实现 OpenClaw 决策器：与 `DecisionFn = Callable[[LoopState, bytes], Awaitable[list[Action]]]`
      兼容的 callable —— 输入截图 bytes（可配 `shots/` 存档路径）+ `LoopState`，
      输出 `Action` 列表；替换 `src/main.py` 中 `CredentialFlowDecision.as_decide(page)`。
- [ ] 截图级验证码识别：`detect_captcha(screenshot_bytes=...)` 目前仅 frame URL 判定，
      bytes 视觉识别留给 AI 模型（函数内已留 TODO，不误报）。
- [ ] 决策函数的 prompt 侧需要 Action 枚举、当前 URL、frame_urls、步数、剩余预算等
      信息 —— 都已在 `LoopState` / `Action` docstring 中。

### 接入真实凭据 / 联调
- [ ] 首次真机跑通静默探活：确认 `PROBE_PAYLOAD` 的 module/method 名、返回结构，
      校准 `judge_probe_response`。
- [ ] 真机校准滑块：确认 tcaptcha 版本、`#slideBg` / 拖钮选择器、bg 坐标 → 页面位移的
      `GAP_SCALE_HINT` 缩放、block 图来源（部分版本无独立 block img，现用 bg 自匹配）。
- [ ] 短信验证码输入界面自动化（当前收到验证码只收割 cookie/上报，未自动填入）。
- [ ] TelegramBotSource 真实 token 联调（getUpdates 需 bot 无 privacy 冲突或加群）。
- [ ] 端到端跑一次完整循环（headless 与 headed 各一次），校准 `_script` 里的选择器
      （新登录框结构可能与旧版不同，脚本只保留最常见选择器）。
- [ ] `state/session.json` 现只存收割的目标 cookie 子集（Playwright 兼容格式）；后续可在
      `run_browser_login` 成功点直接调 `context.storage_state()` 存全量，进一步降低风控熵差。
- [ ] 考虑对 `record_last_run` 挂 cron/heartbeat：读 `state/last_run.json` 失败次数超阈值告警。

### 风控注意事项
- `LOOP_TOTAL_TIMEOUT_S` / `SLIDER_MAX_ATTEMPTS` 别调太高，失败次数过多会触发更严风控。
- 上线初期建议 `headless=False` 人在旁边盯着跑几轮。
- Cookie 模式（session.json / INIT_COOKIE）永远优先，尽量少走账密登录。

## 八、假设说明

1. **探活接口**：`musicu.fcg` 统一网关 + 用户信息 module 能反映 qm_keyst 有效性 ——
   按公开惯例假设，未实测；判定函数已隔离成纯逻辑便于替换。
2. **验证码类型**：tcaptcha URL 中带 `slider`/`point`/`word`/`sms`/`phone` 关键字时猜测
   对应类型，否则 `unknown`。
3. **`detect_captcha` 签名**：任务要求 `detect_captcha(screenshot_bytes)`，但截图 bytes
   无法判断 iframe URL —— 实现为 `detect_captcha(screenshot_bytes=None, frame_urls=None)`，
   frame URL 是主判定，bytes 识别留 TODO（返回 None 不误报）。
4. **轨迹模型**：ease-out cubic（1-(1-t)³）+ 过冲 2%~8% + ±1.5px 微抖 + dt 前密后疏；
   总时长 0.55~0.95s、步数 ≥12。纯经验模型，未过真机风控验证。
5. **短信源时间窗**：TG `message.date` / drop 文件 `ts`（或 mtime）≥ 循环开始时间即视为
   新消息；`SMS_WINDOW_S` 只做下限兜底。
6. **入口兼容**：`python qq_music_login.py --headless` 行为与旧版一致（读 .env → 登录 →
   Vercel → TG）；旧 cookie 直登函数未逐字段保留，被静默续期（storage_state）+ 注入
   INIT_COOKIE 两条路径覆盖。
7. **测试环境**：沙箱内无法访问 y.qq.com（任务明确禁止），所有网络相关函数仅保持
   「结构正确 + 纯逻辑可单测」，未发起任何真实请求。

## 九、测试

```
.venv/bin/python -m pytest tests/ -q   # 45 passed
```

- `test_trajectory.py`：轨迹到达性/过冲/单调性/非匀速/微抖动/边界
- `test_detect_captcha.py`：frame URL 检测、kind 猜测、无误报
- `test_state_store.py`：CookieStore 读写/损坏恢复/失效、目标 cookie 抽取、探活判定
  正反例、last_run.json 追加/损坏恢复
- `test_sms_waiter.py`：6 位码正则、时间窗过滤、假时钟 wait_for_sms、WebhookFileSource