"""极简 5 段 cron 匹配器（分 时 日 月 周），零第三方依赖。

支持：
- `*`：任意
- 具体数字：如 `0`、`15`
- 列表：如 `0,30`
- 范围：如 `1-5`
- 步进：如 `*/10`、`0-30/15`
- 周：0-6 或 7（7 视为周日 0）；中文习惯周日=0

不支持的写法（如 `?`、`L`、`W`、月份名）会当作不匹配，并在日志中提示。
"""

from __future__ import annotations

import logging

logger = logging.getLogger("gfc_cron")


def _parse_field(expr: str, lo: int, hi: int) -> set[int] | None:
    """解析单段 cron 字段；解析失败返回 None。"""
    expr = expr.strip()
    if expr == "*":
        return set(range(lo, hi + 1))
    result: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            base, _, step_s = part.partition("/")
            try:
                step = int(step_s)
            except ValueError:
                return None
            if step <= 0:
                return None
            part = base
        if part in ("*", ""):
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            try:
                start, end = int(a), int(b)
            except ValueError:
                return None
        else:
            try:
                v = int(part)
            except ValueError:
                return None
            start = end = v
        if start < lo or end > hi or start > end:
            return None
        for v in range(start, end + 1, step):
            result.add(v)
    return result


class CronExpr:
    """5 段 cron。week 0=周日，7 也会归一为 0。"""

    def __init__(self, expr: str):
        self.expr = expr
        self.fields: list[set[int]] = []
        self.valid = False
        parts = expr.split()
        if len(parts) != 5:
            logger.warning(f"[cron] 表达式必须为 5 段: {expr}")
            return
        bounds = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
        for field, (lo, hi) in zip(parts, bounds):
            parsed = _parse_field(field, lo, hi)
            if parsed is None:
                logger.warning(f"[cron] 无法解析字段 {field!r}: {expr}")
                return
            self.fields.append(parsed)
        if 7 in self.fields[4]:
            self.fields[4].add(0)
        self.valid = True

    def match(self, minute: int, hour: int, day: int, month: int, weekday: int) -> bool:
        """weekday: 0=周一 ... 6=周日（datetime.weekday() 的约定），此处自动映射到 cron 的 0=周日。"""
        if not self.valid:
            return False
        cron_wd = (weekday + 1) % 7  # 周一(0)->1, 周日(6)->0
        return (
            minute in self.fields[0]
            and hour in self.fields[1]
            and day in self.fields[2]
            and month in self.fields[3]
            and cron_wd in self.fields[4]
        )


def is_valid_cron(expr: str) -> bool:
    return CronExpr(expr).valid
