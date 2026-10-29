"""群文件扫描器（v2.8.0）：统一递归入口，带深度/数量/环保护。

- 所有调用（/gf 列、/gf 查、整理、TTL、查重）共用一个实现，避免四份逻辑漂移；
- folder_id 去重 + 深度上限，防协议端返回自身/异常数据导致无限递归；
- 超过文件/文件夹上限时返回 truncated=True，由调用方提示用户缩小范围。
"""

from __future__ import annotations

from typing import Any

from .qqfile import norm_files, norm_folders
from .timeutil import normalize_ts

MAX_DEPTH = 10
MAX_FOLDERS = 500
MAX_FILES = 5000


async def walk_files(
    client,
    group_id: str | int,
    *,
    max_depth: int = MAX_DEPTH,
    max_folders: int = MAX_FOLDERS,
    max_files: int = MAX_FILES,
) -> dict[str, Any]:
    """递归收集群文件。返回 {files, truncated, errors, folders_seen}。

    files 每项：原 file 字段 + folder（""=根目录，否则 "/A/B"）+ folder_id + ts（秒级，0=未知）
    """
    out: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    state = {"folders": 0, "truncated": False}

    async def walk(folder_id: str, label: str, depth: int) -> None:
        if state["truncated"]:
            return
        if depth > max_depth:
            state["truncated"] = True
            errors.append(f"超过最大深度 {max_depth}，已停止：{label or '根目录'}")
            return
        if state["folders"] >= max_folders:
            state["truncated"] = True
            errors.append(f"超过最大文件夹数 {max_folders}，已停止")
            return
        try:
            raw = (
                await client.files_by_folder(group_id, folder_id)
                if folder_id
                else await client.root_files(group_id)
            )
        except Exception as e:  # 单个文件夹读失败不拖垮整次扫描
            errors.append(f"读取 {label or '根目录'} 失败：{e}")
            return
        if not isinstance(raw, dict):
            return
        for f in norm_files(raw):
            if len(out) >= max_files:
                state["truncated"] = True
                return
            ts = normalize_ts(max(int(f.get("upload_time") or 0), int(f.get("modify_time") or 0)))
            out.append({**f, "folder": label, "folder_id": folder_id or "", "ts": ts})
        for fd in norm_folders(raw):
            fid = str(fd.get("folder_id") or "")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            state["folders"] += 1
            child = f"{label}/{fd['name']}" if label else f"/{fd['name']}"
            await walk(fid, child, depth + 1)

    await walk("", "", 0)
    return {
        "files": out,
        "truncated": state["truncated"],
        "errors": errors,
        "folders_seen": state["folders"],
    }


async def list_folder(client, group_id: str | int, folder_id: str = "") -> dict[str, Any]:
    """只读一层（根目录或指定文件夹），用于 /gf 列 与面板文件页。"""
    raw = (
        await client.files_by_folder(group_id, folder_id)
        if folder_id
        else await client.root_files(group_id)
    )
    if not isinstance(raw, dict):
        raw = {}
    files = []
    for f in norm_files(raw):
        files.append(
            {
                **f,
                "ts": normalize_ts(max(int(f.get("upload_time") or 0), int(f.get("modify_time") or 0))),
            }
        )
    return {"files": files, "folders": norm_folders(raw)}
