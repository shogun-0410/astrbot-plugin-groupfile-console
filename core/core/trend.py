"""容量趋势（v2.8.0）：每天记录一次群文件用量，供面板看增长。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import data_root

MAX_DAYS = 120


class TrendStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_root() / "trend.json")
        self.data: dict[str, list[dict[str, Any]]] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.data = {str(k): list(v)[-MAX_DAYS:] for k, v in (raw.get("groups") or {}).items()}
        except Exception:
            self.data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps({"version": 1, "groups": self.data}, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def sample(self, gid: str | int, used: int, total: int, count: int) -> None:
        gid = str(gid)
        day = time.strftime("%Y-%m-%d")
        rows = self.data.setdefault(gid, [])
        item = {"d": day, "u": int(used or 0), "t": int(total or 0), "n": int(count or 0)}
        if rows and rows[-1].get("d") == day:
            rows[-1] = item
        else:
            rows.append(item)
        if len(rows) > MAX_DAYS:
            self.data[gid] = rows[-MAX_DAYS:]
        self._save()

    def series(self, gid: str | int, days: int = 14) -> list[dict[str, Any]]:
        rows = list(self.data.get(str(gid), []))[-max(1, min(int(days or 14), MAX_DAYS)):]
        return rows

    def stats(self) -> dict[str, Any]:
        return {"groups": len(self.data), "path": str(self.path)}
