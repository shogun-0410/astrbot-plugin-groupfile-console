"""运维基础设施（v2.8.0）：审计日志 / 异步任务 / 回收站记录。

设计原则：
- 只写本插件数据目录，不碰群文件；
- 审计与回收站落 JSON/JSONL，重启不丢；
- 任务管理器只保存「已完成任务摘要」，运行中的任务在内存（重启后标记中断）。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from .config import data_root

Progress = Callable[[int, str], None]
Runner = Callable[[Progress], Awaitable[Any]]


def _atomic_write(path: Path, data: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


class AuditLog:
    """追加式审计日志（JSONL），按大小轮转。"""

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


class TaskManager:
    """异步任务：提交 → 进度 → 完成/失败/取消；保留最近摘要。"""

    def __init__(self, path: Path | None = None, max_history: int = 200):
        self.path = path or (data_root() / "tasks.json")
        self.max_history = max_history
        self.tasks: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.history = list(data.get("tasks") or [])[-self.max_history:]
        except Exception:
            self.history = []

    def _persist(self) -> None:
        _atomic_write(self.path, {"version": 1, "tasks": self.history[-self.max_history:]})

    @staticmethod
    def _summary(t: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in t.items() if not k.startswith("_")}

    def start(self, kind: str, group: str, actor: str, runner: Runner) -> str:
        tid = uuid.uuid4().hex[:12]
        task: dict[str, Any] = {
            "id": tid,
            "kind": str(kind)[:20],
            "group": str(group)[:20],
            "actor": str(actor)[:32],
            "status": "running",
            "progress": 0,
            "message": "已开始",
            "created": int(time.time()),
            "started": int(time.time()),
            "finished": 0,
            "result": None,
            "error": "",
        }
        task["_task"] = asyncio.create_task(self._run(task, runner))
        self.tasks[tid] = task
        self._prune_running()
        return tid

    async def _run(self, task: dict[str, Any], runner: Runner) -> None:
        def progress(pct: int, msg: str = "") -> None:
            try:
                task["progress"] = max(0, min(100, int(pct)))
                if msg:
                    task["message"] = str(msg)[:200]
            except Exception:
                pass

        try:
            task["result"] = await runner(progress)
            task["status"] = "done"
            task["progress"] = 100
            task["message"] = task.get("message") or "完成"
        except asyncio.CancelledError:
            task["status"] = "cancelled"
            task["message"] = "已取消"
        except Exception as e:
            task["status"] = "failed"
            task["error"] = str(e)[:300]
            task["message"] = "失败：" + str(e)[:160]
        finally:
            task["finished"] = int(time.time())
            task.pop("_task", None)
            self.history.append(self._summary(task))
            self._persist()

    def _prune_running(self) -> None:
        # 只保留最近 50 个运行中任务，防止异常提交刷爆内存
        running = [k for k, v in self.tasks.items() if v.get("status") == "running"]
        for k in running[:-50]:
            self.tasks.pop(k, None)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        live = [self._summary(v) for v in self.tasks.values()]
        live.sort(key=lambda x: x.get("created", 0), reverse=True)
        hist = list(reversed(self.history[-max(0, int(limit or 50)):]))
        return (live + hist)[: max(1, min(int(limit or 50), 200))]

    def get(self, tid: str) -> dict[str, Any] | None:
        t = self.tasks.get(tid)
        if t:
            return self._summary(t)
        for h in reversed(self.history):
            if h.get("id") == tid:
                return dict(h)
        return None

    def cancel(self, tid: str) -> bool:
        t = self.tasks.get(tid)
        if not t or t.get("status") != "running":
            return False
        task = t.get("_task")
        if task:
            task.cancel()
        t["status"] = "cancelling"
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "running": sum(1 for t in self.tasks.values() if t.get("status") in ("running", "cancelling")),
            "history": len(self.history),
        }


class RecycleStore:
    """回收站索引：TTL recycle 时记录原位置，供面板一键还原。"""

    def __init__(self, path: Path | None = None, max_items: int = 2000):
        self.path = path or (data_root() / "recycle.json")
        self.max_items = max_items
        self.items: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.items = list(data.get("items") or [])[-self.max_items:]
        except Exception:
            self.items = []

    def _persist(self) -> None:
        _atomic_write(self.path, {"version": 1, "items": self.items[-self.max_items:]})

    def add(self, **entry) -> dict[str, Any]:
        item = {
            "id": uuid.uuid4().hex[:12],
            "ts": int(time.time()),
            **{k: v for k, v in entry.items() if k != "id"},
        }
        self.items.append(item)
        self._persist()
        return item

    def list(self, group: str = "", limit: int = 100) -> list[dict[str, Any]]:
        rows = [dict(x) for x in self.items if not group or str(x.get("group")) == str(group)]
        return list(reversed(rows))[: max(1, min(int(limit or 100), 500))]

    def get(self, item_id: str) -> dict[str, Any] | None:
        for x in self.items:
            if x.get("id") == item_id:
                return dict(x)
        return None

    def remove(self, item_id: str) -> bool:
        for i, x in enumerate(self.items):
            if x.get("id") == item_id:
                self.items.pop(i)
                self._persist()
                return True
        return False

    def prune(self, keep_days: int = 30) -> int:
        cutoff = int(time.time()) - max(1, int(keep_days)) * 86400
        before = len(self.items)
        self.items = [x for x in self.items if int(x.get("ts") or 0) >= cutoff]
        if len(self.items) != before:
            self._persist()
        return before - len(self.items)

    def stats(self) -> dict[str, Any]:
        return {"count": len(self.items), "path": str(self.path)}
