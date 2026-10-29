"""审计日志（v2.9.0）：只保留关键操作留痕，不参与业务。

- 追加式 JSONL，单文件超过 5MB 轮转为 .1；
- 只记录删除/移动/整理/绑定/配置等关键动作，不逐文件写；
- 面板不再单独开页，命令 `/gf 审计` 查看最近记录。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .config import data_root


class AuditLog:
    def __init__(self, path: Path | None = None, max_bytes: int = 5 * 1024 * 1024):
        self.path = path or (data_root() / "audit.jsonl")
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def append(
        self,
        actor: str,
        group: str,
        action: str,
        target: str = "",
        result: str = "ok",
        detail: str = "",
    ) -> None:
        row = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ts": int(time.time()),
            "actor": str(actor)[:32],
            "group": str(group)[:20],
            "action": str(action)[:40],
            "target": str(target)[:200],
            "result": str(result)[:20],
            "detail": str(detail)[:300],
        }
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                    backup = self.path.with_suffix(".1.jsonl")
                    try:
                        backup.unlink(missing_ok=True)
                    except OSError:
                        pass
                    self.path.replace(backup)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def read(self, limit: int = 100, group: str = "", action: str = "") -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            if not self.path.exists():
                return []
            with self.path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if group and str(row.get("group")) != str(group):
                        continue
                    if action and action not in str(row.get("action")):
                        continue
                    out.append(row)
        except Exception:
            return []
        return out[-max(1, min(int(limit or 100), 1000)):][::-1]

    def stats(self) -> dict[str, Any]:
        try:
            size = self.path.stat().st_size if self.path.exists() else 0
        except OSError:
            size = 0
        return {"path": str(self.path), "size": size}
