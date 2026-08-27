"""generate_trajectory 人类运动模型单测。"""

import random

from src.captcha_solver import generate_trajectory


def test_trajectory_reaches_target():
    traj = generate_trajectory(300, rng=random.Random(42))
    assert traj, "轨迹不能为空"
    assert traj[-1]["x"] == 300, "终点必须精确落在目标距离"


def test_trajectory_has_overshoot():
    # 大距离时必然出现过冲（中途 x > distance 再回拉）
    traj = generate_trajectory(200, rng=random.Random(7))
    peak = max(p["x"] for p in traj)
    assert peak > 200, f"期望出现过冲，peak={peak}"


def test_trajectory_monotonic_non_negative_steps():
    # 除回拉阶段外位移单调不减；任何一步的位移都有限（不会瞬移）
    traj = generate_trajectory(300, rng=random.Random(1))
    prev = 0
    for p in traj:
        assert p["x"] >= prev - 2, "倒退不能超过 2px"
        step = abs(p["x"] - prev)
        assert step <= 60, f"单步位移过大: {step}px"
        prev = p["x"]


def test_trajectory_not_constant_speed():
    # 风控会判死匀速直线：dt 必须有明显变化
    traj = generate_trajectory(150, rng=random.Random(3))
    dts = [p["dt"] for p in traj]
    assert len(set(dts)) > 1, "dt 全相同 => 匀速，会被风控"
    # 加速-减速：前半段与后半段的平均间隔不同
    half = len(dts) // 2
    assert abs(sum(dts[:half]) / half - sum(dts[half:]) / (len(dts) - half)) > 1


def test_trajectory_jitter():
    # 微抖动：相邻位移增量不会是完全平滑的确定性序列
    traj = generate_trajectory(100, rng=random.Random(5))
    steps = [traj[i + 1]["x"] - traj[i]["x"] for i in range(len(traj) - 1)]
    assert len(set(steps)) > 3, "步长过于规律，缺少微抖动"


def test_trajectory_zero_and_negative_distance():
    assert generate_trajectory(0) == [{"x": 0, "dt": 0}]
    # 非法输入（负距离）按 0 处理，不产生异常轨迹
    traj = generate_trajectory(-5)
    assert traj[-1]["x"] == 0


def test_trajectory_all_positive_dt():
    traj = generate_trajectory(80, rng=random.Random(9))
    assert all(p["dt"] >= 0 for p in traj)