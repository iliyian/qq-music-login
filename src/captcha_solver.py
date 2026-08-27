"""验证码检测与滑块求解。

- detect_captcha(): 优先用 iframe URL 特征判断 tcaptcha，返回类型猜测。
  纯截图 bytes 判定属于图像识别范畴，留 TODO 给后续 AI 决策模块。
- solve_slider(): 缺口定位 + 轨迹生成 + 拖拽。缺口定位提供基于 PIL/numpy
  的模板匹配实现（沙箱内已验证可用），若装了 cv2 则优先用 cv2.matchTemplate。
- generate_trajectory(): 人类运动模型位移序列（缓动 + 过冲 + 微抖动），
  纯函数，可单测。匀速直线会被风控判死，这里绝不输出匀速序列。
"""

from __future__ import annotations

import base64
import io
import random
from typing import Optional

# ─── 检测 ──────────────────────────────────────────────────

# tcaptcha iframe URL 特征（复用旧版 _wait_for_login_result 的检测思路）
TCAPTCHA_URL_MARKERS = ("tcaptcha", "captcha.qq.com", "ssl.captcha.qq.com")

# URL 关键字 -> 类型猜测
_KIND_HINTS = (
    ("slider", "slider"),
    ("point", "point_select"),   # 点选类
    ("word", "point_select"),
    ("sms", "sms"),
    ("phone", "sms"),
)


def guess_kind_from_url(url: str) -> str:
    """根据 tcaptcha URL 的关键字猜测验证码类型。"""
    low = url.lower()
    for marker, kind in _KIND_HINTS:
        if marker in low:
            return kind
    return "unknown"


def detect_captcha(
    screenshot_bytes: Optional[bytes] = None,
    frame_urls: Optional[list[str]] = None,
) -> Optional[dict]:
    """检测当前是否存在验证码。

    判定顺序：
    1. frame_urls 中任意 URL 含 tcaptcha / captcha.qq.com 特征 => 存在验证码，
       类型由 guess_kind_from_url 从 URL 关键字猜测（slider/point_select/sms/unknown）。
    2. 仅提供截图 bytes 时：TODO —— 依赖视觉模型识别（后续接 OpenClaw AI 决策），
       目前返回 None（不误报）。

    返回 {"kind": ..., "url": ...} 或 None。
    """
    if frame_urls:
        for url in frame_urls:
            if any(m in url.lower() for m in TCAPTCHA_URL_MARKERS):
                return {"kind": guess_kind_from_url(url), "url": url}
    return None


# ─── 缺口定位 ──────────────────────────────────────────────


def _locate_gap_numpy(bg_bytes: bytes, block_bytes: bytes) -> Optional[int]:
    """PIL + numpy 模板匹配：在灰度图上按列做滑窗 SAD（绝对差之和）。

    返回缺口左边缘的 x 坐标（像素）；失败返回 None。
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None

    try:
        bg = Image.open(io.BytesIO(bg_bytes)).convert("L")
        block = Image.open(io.BytesIO(block_bytes)).convert("L")
    except Exception:
        return None

    bg_arr = np.asarray(bg, dtype=np.int32)
    block_arr = np.asarray(block, dtype=np.int32)
    bh, bw = block_arr.shape
    H, W = bg_arr.shape
    if bh > H or bw > W:
        return None

    best_x, best_score = None, None
    for x in range(W - bw + 1):
        window = bg_arr[:bh, x:x + bw]
        score = int(np.abs(window - block_arr).sum())
        if best_score is None or score < best_score:
            best_score, best_x = score, x
    return best_x


def _locate_gap_cv2(bg_bytes: bytes, block_bytes: bytes) -> Optional[int]:
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    block = cv2.imdecode(np.frombuffer(block_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if bg is None or block is None:
        return None
    bh, bw = block.shape
    if bh > bg.shape[0] or bw > bg.shape[1]:
        return None
    res = cv2.matchTemplate(bg, block, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(res)
    return int(max_loc[0])


def locate_gap(bg_bytes: bytes, block_bytes: bytes) -> Optional[int]:
    """滑块缺口定位：优先 cv2 模板匹配，回退 numpy SAD。

    bg_bytes/block_bytes 来自 tcaptcha 页面里 #slideBg 的 img src（base64 去头后）。
    """
    if not bg_bytes or not block_bytes:
        return None
    gap = _locate_gap_cv2(bg_bytes, block_bytes)
    if gap is None:
        gap = _locate_gap_numpy(bg_bytes, block_bytes)
    return gap


def decode_data_uri(src: str) -> Optional[bytes]:
    """把 data:image/png;base64,... 解码为 bytes。"""
    if not src:
        return None
    try:
        if "," in src:
            _, b64 = src.split(",", 1)
        else:
            b64 = src
        return base64.b64decode(b64)
    except Exception:
        return None


# ─── 轨迹生成（人类运动模型，必须实装） ────────────────────────


def generate_trajectory(
    distance: int,
    *,
    rng=None,
    overshoot_ratio: float = 0.06,
    jitter_px: float = 1.5,
) -> list[dict]:
    """生成从 0 滑到 distance 的位移序列。

    特征：
    - 缓动：先加速后减速（ease-out），启动快、收尾慢，符合人类拖拽习惯；
    - 过冲：冲过目标 2%~8%，再回拉修正；
    - 微抖动：每步叠加 ±jitter_px 的随机抖动；
    - 时间间隔：前密后疏 + 随机化（dt 单位 ms）。

    返回 [{"x": 累计位移, "dt": 距上一步的毫秒数}, ...]，
    最后一个点的 x 恰为 distance（整型）。
    """
    import random

    rng = rng or random.Random()
    if distance <= 0:
        return [{"x": 0, "dt": 0}]

    overshoot = max(1, int(distance * overshoot_ratio))
    peak = distance + rng.randint(1, max(2, overshoot))

    points: list[tuple[int, float]] = []
    t_total = rng.uniform(0.55, 0.95)  # 总时长（秒），真实滑块不会拖很久
    steps = max(12, int(distance / 6) + rng.randint(3, 8))
    x = 0.0
    for i in range(1, steps + 1):
        # ease-out cubic：进度 = 1-(1-t)^3，前快后慢
        t = i / steps
        eased = 1.0 - (1.0 - t) ** 3
        target_x = peak * eased
        # 微抖动（收尾阶段抖动幅度收敛，避免结尾乱晃）
        jitter_scale = 1.0 if t < 0.85 else 0.25
        target_x += rng.uniform(-jitter_px, jitter_px) * jitter_scale
        x = target_x
        # dt：前段间隔小、后段间隔大 + 随机
        dt = (t_total * 1000 / steps) * (1.0 + 1.6 * t) * rng.uniform(0.7, 1.3)
        points.append((int(round(x)), dt))

    # 回拉修正：2~3 步从 peak 回到 distance
    pull_steps = rng.randint(2, 3)
    for j in range(pull_steps):
        x = peak + (distance - peak) * (j + 1) / pull_steps
        dt = rng.uniform(35, 90)
        points.append((int(round(x)), dt))
    # 确保终点精确
    points.append((distance, rng.uniform(40, 100)))

    # 相邻点位移不能倒退超过 1px（回拉阶段允许小幅负位移）
    trajectory: list[dict] = []
    prev = 0
    for x_i, dt in points:
        x_i = max(x_i, prev - 2)
        trajectory.append({"x": x_i, "dt": int(round(dt))})
        prev = x_i
    return trajectory


# ─── 滑块求解（需要真实页面，骨架） ────────────────────────────


SLIDE_BG_SELECTOR = "#slideBg"
SLIDE_BTN_SELECTOR = "#tcaptcha_drag_thumb, .tc-drag-thumb, .tcaptcha-drag-thumb"


async def solve_slider(page, max_attempts: int = 3) -> bool:
    """在 tcaptcha 滑块页面上自动拖动。

    流程：等 iframe 出现 → 截取 bg/block 图片 → locate_gap 定位缺口
    → generate_trajectory 生成人类轨迹 → 按轨迹拖动滑块 → 观察是否通过。
    失败重试至 max_attempts。

    注意：缺口定位接口依赖真实 DOM（#slideBg 的 src 是动态 base64），
    这里是可运行实现，但未经真实页面验证 —— 标记为骨架。
    """
    for attempt in range(1, max_attempts + 1):
        try:
            bg_loc = page.locator(SLIDE_BG_SELECTOR).first
            await bg_loc_wait(bg_loc, page)
            src = await bg_loc.get_attribute("src")
            bg_bytes = decode_data_uri(src or "")
            if not bg_bytes:
                print(f"[Captcha] 第 {attempt} 次：取不到背景图")
                continue
            # tcaptcha 的滑块拼图通常叠加在 bg 上，这里先用 bg 自身边缘找缺口
            # TODO: 真实接入时再确认 block 图来源（部分版本无独立 block img）
            gap_x = locate_gap(bg_bytes, bg_bytes)
            if gap_x is None:
                continue
            distance = int(gap_x * GAP_SCALE_HINT)
            trajectory = generate_trajectory(distance)
            btn = page.locator(SLIDE_BTN_SELECTOR).first
            box = await btn.bounding_box()
            if not box:
                continue
            start_x = box["x"] + box["width"] / 2
            start_y = box["y"] + box["height"] / 2
            await page.mouse.move(start_x, start_y)
            await page.mouse.down()
            for point in trajectory:
                await page.mouse.move(start_x + point["x"], start_y, steps=1)
                await page.wait_for_timeout(point["dt"])
            await page.mouse.up()
            await page.wait_for_timeout(1500)
            # 通过判定：滑块 iframe 消失
            if not any("tcaptcha" in f.url.lower() for f in page.frames):
                print(f"[Captcha] 第 {attempt} 次滑块通过")
                return True
        except Exception as exc:
            print(f"[Captcha] 第 {attempt} 次滑块失败: {exc}")
    return False


GAP_SCALE_HINT = 1.0  # bg 图坐标 -> 页面位移的缩放（真实接入时按实际尺寸校准）


async def bg_loc_wait(bg_loc, page, timeout_s: int = 10) -> None:
    import asyncio

    for _ in range(timeout_s * 2):
        if await bg_loc.count() > 0:
            try:
                if await bg_loc.is_visible():
                    return
            except Exception:
                pass
        await page.wait_for_timeout(500)
    raise TimeoutError("滑块背景图未出现")