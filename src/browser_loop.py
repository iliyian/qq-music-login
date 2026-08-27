"""AI 决策登录循环骨架。

流程（每一步）：
    截图存档（shots/时间戳.png）
    → 调用外部「决策函数」（当前为 DummyDecision，真实 AI 决策后续接 OpenClaw）
    → 逐条执行返回的 Action（单步超时硬限制）
    → 再截图确认本步结果
    → 达到 abort / 登录成功 / 步数上限 / 总时长上限时退出

硬限制：
    - 最大步数（默认 25）
    - 单步超时（默认 30s）
    - 总时长上限（默认 900s）
    - 任意异常时 dump 调试现场（截图 + HTML）后抛出 LoopError
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from .config import SHOTS_DIR

# 动作类型常量
ACTION_CLICK = "click"
ACTION_TYPE = "type"
ACTION_KEY = "key"
ACTION_WAIT = "wait"
ACTION_SCREENSHOT = "screenshot"
ACTION_ABORT = "abort"
ACTION_TYPES = (
    ACTION_CLICK, ACTION_TYPE, ACTION_KEY,
    ACTION_WAIT, ACTION_SCREENSHOT, ACTION_ABORT,
)


@dataclass
class Action:
    """决策函数的最小输出单元。

    type:
        click      -> selector 或 coords 必填
        type       -> selector + text 必填
        key        -> selector + text(键名，如 Enter) 必填
        wait       -> text 为毫秒数
        screenshot -> 仅触发一次截图存档
        abort      -> 立即终止循环（reason 可放 text）
    """

    type: str
    selector: str | None = None
    coords: tuple[int, int] | None = None
    text: str | None = None
    frame: str | None = None   # 可选：iframe 选择器（如 ptlogin2 登录框）
    timeout_ms: int = 5000     # 该动作的执行超时

    def validate(self) -> None:
        if self.type not in ACTION_TYPES:
            raise ValueError(f"未知动作类型: {self.type!r}")
        if self.type in (ACTION_CLICK, ACTION_TYPE, ACTION_KEY) and not self.selector and not self.coords:
            raise ValueError(f"{self.type} 动作需要 selector 或 coords")
        if self.type == ACTION_TYPE and self.text is None:
            raise ValueError("type 动作需要 text")


# 决策函数：输入 (LoopState, 截图bytes)，返回本步要执行的动作列表
DecisionFn = Callable[["LoopState", bytes], Awaitable[list[Action]]]


@dataclass
class LoopState:
    """循环过程中暴露给决策函数的上下文。"""

    step: int = 0
    url: str = ""
    frame_urls: list[str] = field(default_factory=list)
    started_at: float = 0.0
    notes: dict = field(default_factory=dict)  # 决策函数可自由记录状态


class LoopAborted(Exception):
    """决策函数主动 abort。"""


class LoopError(RuntimeError):
    """循环硬限制触发或执行异常。"""


# ─── 截图存档 ──────────────────────────────────────────────


def shot_filename(prefix: str = "step", ext: str = "png") -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return f"{prefix}_{ts}.{ext}"


def save_shot(png: bytes, shots_dir: Path | str = SHOTS_DIR, prefix: str = "step") -> Path:
    """把截图 bytes 存档到 shots/ 目录（带毫秒时间戳），返回文件路径。"""
    path = Path(shots_dir)
    path.mkdir(parents=True, exist_ok=True)
    fp = path / shot_filename(prefix)
    fp.write_bytes(png)
    return fp


# ─── 调试现场 dump（复用旧版 _dump_debug 思路） ───────────────


async def dump_debug(page, reason: str, shots_dir: Path | str = SHOTS_DIR) -> dict:
    """异常/失败时保存截图 + HTML + frame URL 清单，返回现场描述。"""
    scene: dict = {"reason": reason, "url": "", "frame_urls": [], "files": []}
    try:
        scene["url"] = page.url
        scene["frame_urls"] = [f.url for f in page.frames]
        png = await page.screenshot(type="png")
        fp = save_shot(png, shots_dir, prefix="debug")
        scene["files"].append(str(fp))
        html_fp = fp.with_suffix(".html")
        html_fp.write_text(await page.content(), encoding="utf-8")
        scene["files"].append(str(html_fp))
    except Exception as exc:
        scene["dump_error"] = str(exc)
    print(f"[Loop] 调试现场已 dump: {scene['files']}")
    return scene


# ─── 动作执行 ──────────────────────────────────────────────


def _resolve_locator(page, action: Action):
    """优先 iframe，其次 selector / coords。"""
    target = page
    if action.frame:
        for frame in page.frames:
            if action.frame in frame.url:
                target = frame
                break
    if action.selector:
        return target, target.locator(action.selector).first
    return target, None


async def execute_action(page, action: Action, state: LoopState, shots_dir=SHOTS_DIR) -> str:
    """执行单个 Action，返回执行结果说明。超时抛 asyncio.TimeoutError。"""
    action.validate()

    async def _run() -> str:
        if action.type == ACTION_CLICK:
            if action.coords:
                await page.mouse.click(*action.coords)
            else:
                _, loc = _resolve_locator(page, action)
                await loc.click(timeout=action.timeout_ms)
            return f"clicked {action.selector or action.coords}"

        if action.type == ACTION_TYPE:
            _, loc = _resolve_locator(page, action)
            await loc.click(timeout=action.timeout_ms)
            await loc.fill("")  # 清空再输入，模拟重新填写
            await loc.type(action.text or "", delay=80)
            return f"typed into {action.selector}"

        if action.type == ACTION_KEY:
            _, loc = _resolve_locator(page, action)
            if loc is not None:
                await loc.press(action.text or "", timeout=action.timeout_ms)
            else:
                await page.keyboard.press(action.text or "")
            return f"pressed {action.text}"

        if action.type == ACTION_WAIT:
            ms = int(action.text or 1000)
            await page.wait_for_timeout(min(ms, 30000))
            return f"waited {ms}ms"

        if action.type == ACTION_SCREENSHOT:
            png = await page.screenshot(type="png")
            save_shot(png, shots_dir, prefix="manual")
            return "screenshot saved"

        if action.type == ACTION_ABORT:
            raise LoopAborted(action.text or "决策函数要求终止")

        raise ValueError(f"未知动作类型: {action.type!r}")

    return await asyncio.wait_for(_run(), timeout=max(action.timeout_ms, 1000) / 1000 + 5)


# ─── 决策实现 ──────────────────────────────────────────────


class DummyDecision:
    """占位决策器：记录每步截图信息并等待，达到步数后 abort。

    真实 AI 决策（视觉理解 + 动作规划）后续由 OpenClaw 提供，
    只需实现与 DecisionFn 兼容的 callable 即可替换本类。
    """

    def __init__(self, give_up_after: int = 3):
        self.give_up_after = give_up_after
        self.log: list[str] = []

    async def __call__(self, state: LoopState, screenshot: bytes) -> list[Action]:
        msg = f"step={state.step} url={state.url!r} shot={len(screenshot)}B"
        self.log.append(msg)
        print(f"[DummyDecision] {msg}")
        if state.step >= self.give_up_after:
            return [Action(type=ACTION_ABORT, text="dummy 决策器放弃（骨架验证用）")]
        return [Action(type=ACTION_WAIT, text="1000")]


# ─── 主循环 ────────────────────────────────────────────────


async def _frame_urls(page) -> list[str]:
    return [f.url for f in page.frames]


async def run_login_loop(
    page,
    decide: DecisionFn,
    *,
    success_check: Callable[[LoopState], Awaitable[bool]] | None = None,
    max_steps: int = 25,
    step_timeout_s: float = 30,
    decide_timeout_s: float | None = None,   # 决策函数允许的最长耗时（验证码求解较慢，可单独放宽）
    total_timeout_s: float = 900,
    shots_dir: Path | str = SHOTS_DIR,
) -> dict:
    """执行 AI 决策登录循环。

    success_check: 每步开始前调用，返回 True 则视为登录成功并结束循环。
    返回 dict: {success, reason, steps, shots: [...], abort_reason?}
    抛出 LoopError 表示硬限制触发；LoopAborted 表示决策函数主动 abort。
    """
    state = LoopState(started_at=time.time())
    deadline = time.monotonic() + total_timeout_s
    decide_timeout = decide_timeout_s if decide_timeout_s is not None else step_timeout_s
    shots: list[str] = []
    last_scene: dict = {}

    try:
        for step in range(1, max_steps + 1):
            if time.monotonic() > deadline:
                raise LoopError(f"总时长超过 {total_timeout_s}s 上限")

            state.step = step
            state.url = page.url
            state.frame_urls = await _frame_urls(page)

            if success_check and await success_check(state):
                return {"success": True, "reason": "success_check 通过", "steps": step, "shots": shots}

            # 1) 截图存档
            png = await asyncio.wait_for(
                page.screenshot(type="png"), timeout=step_timeout_s
            )
            shots.append(str(save_shot(png, shots_dir, prefix="step")))

            # 2) 决策（决策超时独立于动作超时，便于后续接入耗时较高的 AI/验证码分支）
            actions = await asyncio.wait_for(
                decide(state, png), timeout=decide_timeout
            )
            if not actions:
                actions = [Action(type=ACTION_WAIT, text="500")]

            # 3) 执行动作
            results = []
            for act in actions:
                try:
                    outcome = await asyncio.wait_for(
                        execute_action(page, act, state, shots_dir),
                        timeout=step_timeout_s,
                    )
                    results.append(outcome)
                except asyncio.TimeoutError:
                    await dump_debug(page, f"step {step} 动作超时: {act}", shots_dir)
                    raise LoopError(f"step {step} 动作超时: {act.type}")
            state.notes[f"step_{step}_results"] = results

            # 4) 再截图确认
            png2 = await asyncio.wait_for(
                page.screenshot(type="png"), timeout=step_timeout_s
            )
            shots.append(str(save_shot(png2, shots_dir, prefix="confirm")))

        raise LoopError(f"达到最大步数 {max_steps}，登录未完成")
    except LoopAborted as exc:
        last_scene = await dump_debug(page, f"决策函数主动终止: {exc}", shots_dir)
        raise
    except LoopError:
        raise
    except Exception as exc:
        await dump_debug(page, f"循环异常: {exc!r}", shots_dir)
        raise LoopError(f"循环异常: {exc!r}") from exc
    finally:
        state.notes["total_shots"] = len(shots)
        state.notes["last_scene"] = last_scene