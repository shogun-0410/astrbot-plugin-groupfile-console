"""OneBot 群文件 API 封装（兼容 NapCat / LLOneBot / SnowLuma / go-cqhttp 常见实现）。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("gfc_qqfile")


class QQFileError(Exception):
    pass


def _err_text(err: Exception) -> str:
    """把协议端错误转成可判断的文本（ActionFailed 的 wording 在 str 里）。"""
    try:
        return str(err)
    except Exception:
        return ""


def _is_missing(err: Exception) -> bool:
    """文件/文件夹已不存在：继续换参数重试只会重复失败，直接结束。"""
    s = _err_text(err)
    return ("文件不存在" in s) or ("目标文件夹不存在" in s) or ("not exist" in s.lower())


class QQFileClient:
    """包装 aiocqhttp OneBot 客户端，只暴露群文件相关动作。"""

    def __init__(self, bot: Any) -> None:
        self.bot = bot

    @property
    def available(self) -> bool:
        return bool(self.bot and hasattr(self.bot, "api") and hasattr(self.bot.api, "call_action"))

    async def call(self, action: str, **params) -> Any:
        if not self.available:
            raise QQFileError("OneBot 客户端不可用（尚未绑定）")
        try:
            return await self.bot.api.call_action(action=action, **params)
        except Exception as e:
            logger.error(f"[群文件] 动作 {action} 失败: {e}")
            raise QQFileError(f"动作 {action} 失败: {e}") from e

    async def version_info(self) -> dict[str, Any]:
        info = await self.call("get_version_info")
        return info if isinstance(info, dict) else {}

    async def login_info(self) -> dict[str, Any]:
        info = await self.call("get_login_info")
        return info if isinstance(info, dict) else {}

    # ---- 文件系统 ----
    async def root_files(self, group_id: str | int) -> dict[str, Any]:
        return await self.call("get_group_root_files", group_id=int(group_id))

    async def files_by_folder(self, group_id: str | int, folder_id: str) -> dict[str, Any]:
        return await self.call("get_group_files_by_folder", group_id=int(group_id), folder_id=str(folder_id))

    async def file_download_url(self, group_id: str | int, file_id: str, busid: int = 0) -> str:
        """获取群文件下载直链（get_group_file_url）。失败返回空串。"""
        gid = int(group_id)
        attempts: list[dict] = [
            {"group_id": gid, "file_id": str(file_id), "busid": int(busid)},
            {"group_id": gid, "file_id": str(file_id)},
        ]
        for params in attempts:
            try:
                r = await self.call("get_group_file_url", **params)
                if isinstance(r, dict):
                    url = r.get("url") or r.get("file_url") or ""
                    if url:
                        return str(url)
            except QQFileError:
                continue
        return ""

    async def file_system_info(self, group_id: str | int) -> dict[str, Any]:
        info = await self.call("get_group_file_system_info", group_id=int(group_id))
        return info if isinstance(info, dict) else {}

    # ---- 写操作 ----
    async def delete_file(
        self,
        group_id: str | int,
        folder_id: str | None,
        file_id: str,
        busid: int | None = None,
    ) -> None:
        """删除群文件。SnowLuma/NapCat 参数组合不一，按多种参数组合逐级降级，最后一种也失败才抛错。"""
        gid = int(group_id)
        attempts: list[dict] = []
        if folder_id is not None and str(folder_id) not in ("", "/"):
            attempts.append(dict(group_id=gid, folder_id=str(folder_id), file_id=str(file_id)))
        attempts.append(dict(group_id=gid, file_id=str(file_id)))
        if busid:
            attempts.append(dict(group_id=gid, file_id=str(file_id), busid=int(busid)))
            if folder_id is not None and str(folder_id) not in ("", "/"):
                attempts.append(dict(group_id=gid, folder_id=str(folder_id), file_id=str(file_id), busid=int(busid)))
        last_err: Exception | None = None
        for kw in attempts:
            try:
                await self.call("delete_group_file", **kw)
                return
            except QQFileError as e:  # 换下一种参数组合
                last_err = e
                if _is_missing(e):
                    break
        if last_err:
            raise last_err

    async def move_file(
        self,
        group_id: str | int,
        file_id: str,
        target_directory: str,
        target_id: str | None = None,
        current_id: str | None = None,
    ) -> None:
        """移动文件到目标文件夹。

        SnowLuma 实测确认（API 浏览器 + 测试台）：
        - move_group_file 四参数全必填：group_id / file_id / parent_directory / target_directory
        - parent_directory = 文件当前所在文件夹的 folder_id（/UUID）；根目录文件填 "/"
        - target_directory = 目标文件夹的 folder_id（/UUID），不是文件夹名
        """
        gid = int(group_id)
        cur = current_id or "/"  # 根目录文件用 "/"
        tgt = target_id or target_directory
        attempts: list[dict] = [
            {"parent_directory": cur, "target_directory": tgt},
            {"parent_directory": cur, "target_directory": target_directory},
            {"parent_directory": "/", "target_directory": tgt},
            {"parent_directory": "/", "target_directory": target_directory},
        ]
        last_err: Exception | None = None
        for params in attempts:
            try:
                await self.call("move_group_file", group_id=gid, file_id=str(file_id), **params)
                return
            except QQFileError as e:
                last_err = e
                # 文件已不在原处 / 目标文件夹不存在：再换参数只会重复失败，直接结束
                if _is_missing(e):
                    break
        raise QQFileError(f"移动失败（协议端可能不支持 move_group_file 或参数不同）: {last_err}") from last_err

    async def create_folder(self, group_id: str | int, folder_name: str, parent_folder_id: str | None = None) -> str:
        """创建群文件夹，返回新文件夹 ID。"""
        gid = int(group_id)
        # snowluma/NapCat 参数名以 name 为准，folder_name 为别名：两个都传兼容所有实现
        params: dict[str, Any] = {"group_id": gid, "name": folder_name, "folder_name": folder_name}
        if parent_folder_id:
            params["parent_folder_id"] = str(parent_folder_id)
        resp = await self.call("create_group_file_folder", **params)
        if isinstance(resp, dict):
            # SnowLuma: {"result":{"retCode":0},"groupItem":{"folderInfo":{"folderId":"/..."}}}
            for node in (
                resp,
                resp.get("groupItem") or {},
                (resp.get("groupItem") or {}).get("folderInfo") or {},
                resp.get("data") or {},
            ):
                if not isinstance(node, dict):
                    continue
                for k in ("folder_id", "folderId", "id", "fid"):
                    if node.get(k):
                        return str(node[k])
        return ""

# ---- 数据归一化 ----

def norm_files(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """把 get_group_root_files / get_group_files_by_folder 返回的文件列表归一化。"""
    files = raw.get("files") or []
    out = []
    for f in files:
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "file_id": str(f.get("file_id", "")),
                "name": f.get("file_name", ""),
                "size": int(f.get("file_size", 0) or 0),
                "busid": f.get("busid", 0),
                "uploader_name": f.get("uploader_name", ""),
                "upload_time": int(f.get("upload_time", 0) or 0),
                "modify_time": int(f.get("modify_time", 0) or 0),
            }
        )
    return out


def norm_folders(raw: dict[str, Any]) -> list[dict[str, Any]]:
    folders = raw.get("folders") or []
    out = []
    for d in folders:
        if not isinstance(d, dict):
            continue
        out.append(
            {
                "folder_id": str(d.get("folder_id", "")),
                "name": d.get("folder_name", ""),
                "file_count": int(d.get("total_file_count", 0) or 0),
            }
        )
    return out
