"""
Hermes-style jitter 重试工具
Decorrelated seed 防止多个 session 同时重试造成雪崩
"""
from __future__ import annotations

import random
import time


def jittered_backoff(
    attempt: int,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    jitter_ratio: float = 0.5,
) -> float:
    """
    指数退避 + 随机抖动，seed 与时间戳异或，避免并发 session 同步重试。
    attempt=0 → ~5s, attempt=1 → ~10s, attempt=2 → ~20s, 上限 120s
    """
    tick = int(time.time() * 1000)
    seed = (time.time_ns() ^ (tick * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    delay = min(base_delay * (2 ** attempt), max_delay)
    low = delay * (1 - jitter_ratio)
    high = delay * (1 + jitter_ratio)
    return low + rng.random() * (high - low)
