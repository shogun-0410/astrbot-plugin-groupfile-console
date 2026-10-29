"""发送临时目录解析与清理策略（纯函数，便于单测）。

v2.7.0：群文件下载后要交给协议端读取（file:// ），必须落在 AstrBot 与协议端
共享的 temp 目录；清理延迟/残留阈值可配，且发送中的文件不能被清扫。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

PLUGIN_DIR_NAME = "astrbot_plugin_groupfile_console"
SEND_SUBDIR = "send_tmp"

DEFAULT_KEEP_SECONDS = 600
DEFAULT_ORPHAN_HOURS = 1
MIN_KEEP_SECONDS = 30
MAX_KEEP_SECONDS = 86400
MIN_ORPHAN_HOURS = 1
MAX_ORPHAN_HOURS = 168


def clamp_int(value, default: int, lo: int, hi: int) -> int:
    """把任意输入收成 [lo, hi] 的整数；非法值回默认值。"""
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def is_abs_path(path: str) -> bool:
    """绝对路径判断：同时接受 Linux（/…）与 Windows（盘符）写法，便于本地单测与跨平台部署。"""
    s = str(path or "").strip()
    return bool(s) and (os.path.isabs(s) or s.startswith("/"))


def default_send_dir(temp_root: str | Path | None, plugin_dir_name: str = PLUGIN_DIR_NAME) -> Path:
    """共享 temp 下的默认发送目录：<temp>/<插件名>/send_tmp。"""
    root = Path(temp_root) if temp_root else Path(os.path.realpath(os.path.join(".", "temp")))
    return root / plugin_dir_name / SEND_SUBDIR


def resolve_send_dir(*candidates) -> Path:
    """按优先级取第一个非空绝对路径；都没有则回退 data 目录下的 send_tmp。

    candidates 顺序：面板自定义目录 -> 插件配置 deliver_dir -> 默认共享目录 -> 旧 send_tmp。
    相对路径一律忽略（发送给协议端必须是绝对路径）。
    """
    for raw in candidates:
        if raw is None:
            continue
        p = str(raw).strip()
        if is_abs_path(p):
            return Path(p)
    return Path(os.path.realpath(os.path.join(".", SEND_SUBDIR)))


def is_under(path: str | Path, root: str | Path) -> bool:
    """path 是否在 root 之下（按 realpath 比较）。"""
    try:
        p = os.path.realpath(str(path))
        r = os.path.realpath(str(root))
        return os.path.commonpath([p, r]) == r
    except (OSError, ValueError):
        return False


def plan_sweep(
    entries: Iterable[tuple[str, bool, float]],
    now: float,
    orphan_seconds: int,
    inflight: Iterable[str] = (),
) -> list[str]:
    """算出可安全删除的残留文件。

    entries: [(绝对路径, 是否普通文件, mtime)]
    规则：只删普通文件；发送中的文件跳过；年龄（now-mtime）未超过阈值的不删。
    返回待删路径列表（去重、稳定顺序）。
    """
    flying = {os.path.realpath(str(p)) for p in inflight}
    out: list[str] = []
    seen: set[str] = set()
    for path, is_file, mtime in entries:
        if not is_file:
            continue
        real = os.path.realpath(str(path))
        if real in flying or real in seen:
            continue
        try:
            age = now - float(mtime)
        except (TypeError, ValueError):
            continue
        if age < 0:
            age = 0
        if age < max(0, int(orphan_seconds)):
            continue
        seen.add(real)
        out.append(str(path))
    return out
