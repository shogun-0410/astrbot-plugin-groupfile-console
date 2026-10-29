"""时间戳归一化（v2.8.0）：兼容秒/毫秒、字符串、未来时间、0 值。

群文件接口在不同协议端上既可能给秒，也可能给毫秒；TTL 判断一旦把毫秒当秒，
文件年龄会被算成几十年，必须统一收口，异常值一律视为「时间未知」。
"""

from __future__ import annotations

import time


def normalize_ts(value, now: float | None = None) -> int:
    """返回秒级时间戳；0 表示未知/不可信（调用方不得据此删除文件）。"""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return 0
    if ts <= 0:
        return 0
    # ≥ 1e12 视作毫秒（1e12 秒 ≈ 公元 33658 年，正常群文件不会出现）
    if ts >= 1_000_000_000_000:
        ts = ts / 1000.0
    # 超过当前时间 1 天 = 不可信（时区/单位错误），按未知处理
    base = float(now if now is not None else time.time())
    if ts > base + 86400:
        return 0
    # 1970 附近的 0.x 秒也视为无效
    if ts < 315532800:  # 1980-01-01
        return 0
    return int(ts)


def age_days(ts: int, now: float | None = None) -> float:
    if ts <= 0:
        return 0.0
    base = float(now if now is not None else time.time())
    return max(0.0, (base - ts) / 86400.0)
