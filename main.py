"""群文件管理控制台 —— AstrBot 插件

功能：多群独立配置、规则分类、定时整理流水线（分类 → 时效清理 → 容量播报）、
文件查找/删除/移动/建文件夹、WebUI 可视化控制台。

命令前缀「/群文件」（短前缀 /gf）：v2.0 起命令表精简为单字动词，
设置类操作统一走 WebUI 控制台（AstrBot 面板 → 插件 → 群文件管理）。

v2.1.1：命令回复默认改走事件链（管道）——消息打包插件可聚合 /gf 命令输出
（含长文件列表）；原直发加速降级为配置开关 direct_reply（默认关）。

v2.4.0：文件类型体系扩充——规则支持漫画 cbz/cbr/cb7/cbt、电子书、代码等
130+ 预置类型与任意自定义扩展名；exts 兼容 "cbz,cbr" / ".CBZ" / "*.cbr" 写法。

v2.4.1：控制台折叠优化——规则卡片默认收起（只留摘要，可单条/全部展开）；
文件类型改为"分组标签 + 单面板"，不再把 130+ 个选项平铺成一大片。

v2.5.0：规则快捷模板（漫画/电子书/压缩包/图片/文档一键建规则）；
扫描结果按文件夹折叠；文件页类型筛选支持多选并记住上次选择。

v2.6.0：规则复制到其他群（追加合并/覆盖）；规则未保存提示（切群/关页面拦截）；
批量生成规则二次确认；文件类型筛选改为按群记忆。

v2.7.0：发送临时目录默认落到共享 temp（协议端可读，修掉 file:// EACCES）；
清理延迟/残留阈值可配 + 发送中不误删 + 失败兜底；规则 kind（关键词/正则）真正生效；
时效清理对「没有上传时间」的文件跳过；移除 move_only/report_used 等空头开关。
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .core.config import GroupConfigStore, data_root, normalize_exts
from .core.ops import AuditLog
from .core.qqfile import QQFileClient, QQFileError, norm_files, norm_folders
from .core.scanner import list_folder, walk_files
from .core.scheduler import CleanupScheduler
from .core.sendtmp import (
    DEFAULT_KEEP_SECONDS,
    DEFAULT_ORPHAN_HOURS,
    MAX_KEEP_SECONDS,
    MAX_ORPHAN_HOURS,
    MIN_KEEP_SECONDS,
    MIN_ORPHAN_HOURS,
    clamp_int,
    default_send_dir,
    is_abs_path,
    is_under,
    plan_sweep,
    resolve_send_dir,
)
from .core.validate import clamp_int as vint

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
except Exception:  # 旧版 AstrBot / 开发环境
    get_astrbot_temp_path = None

PLUGIN_NAME = "astrbot_plugin_groupfile_console"
CMD = "群文件"
VERSION = "2.9.2"

GB = 1024 ** 3
LISTING_TTL = 300  # 列表上下文有效期（秒）
PENDING_TTL = 60   # 待确认操作有效期（秒）
SEND_MAX_BYTES = 512 * 1024 * 1024  # 发送文件下载上限（512MB）


class GroupFileConsole(Star):
    _run_task = None  # 类级：全局唯一的定时循环，防止面板重载/热更新后旧循环残留，导致到点重复发送

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config: dict = config or {}
        self.store = GroupConfigStore()
        self.bot = None
        self.platform_id = ""
        self.qq: QQFileClient | None = None
        self.scheduler = CleanupScheduler(self)
        self.umo_cache: dict[str, str] = {}  # group_id -> unified_msg_origin
        self._bind_attempted = False
        self.recent_events: deque = deque(maxlen=50)  # 运行事件（供诊断报告）
        self._enabled_override: bool | None = None
        # 列表上下文：uid -> {gid, folder, folder_id, files, ts}（查/列 之后 5 分钟内可用序号操作）
        self._listing: dict[str, dict] = {}
        # 待确认操作：uid -> {action, gid, payload, ts}（删/解绑 等两段式确认）
        self._pending: dict[str, dict] = {}

        # ---- 发送临时文件（v2.7.0）----
        self._send_inflight: set[str] = set()   # 正在发送/下载中的临时文件（清扫时跳过）
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._last_sweep = 0.0
        self._last_sweep_info: dict[str, Any] = {}

        # ---- 审计 / 锁（v2.9.0）----
        self.audit = AuditLog()
        self._locks: dict[str, asyncio.Lock] = {}
        self._event_clients: dict[int, QQFileClient] = {}

        # ---- Plugin Pages 后端 API ----
        api = context.register_web_api
        api(f"/{PLUGIN_NAME}/status", self.api_status, ["GET"], "插件运行状态")
        api(f"/{PLUGIN_NAME}/groups", self.api_groups_get, ["GET"], "群配置列表")
        api(f"/{PLUGIN_NAME}/groups", self.api_groups_upsert, ["POST"], "新增/更新群配置")
        api(f"/{PLUGIN_NAME}/group_delete", self.api_group_delete, ["POST"], "删除群配置")
        api(f"/{PLUGIN_NAME}/rules", self.api_rules_get, ["GET"], "读取分类规则")
        api(f"/{PLUGIN_NAME}/rules", self.api_rules_set, ["POST"], "保存分类规则")
        api(f"/{PLUGIN_NAME}/rule_test", self.api_rule_test, ["POST"], "试一试：文件名测规则命中")
        api(f"/{PLUGIN_NAME}/cleanup_preview", self.api_cleanup_preview, ["POST"], "预览整理（只报告不移动）")
        api(f"/{PLUGIN_NAME}/ttl_preview", self.api_ttl_preview, ["POST"], "预览时效清理（只报告不动作）")
        api(f"/{PLUGIN_NAME}/check_limit", self.api_check_limit, ["GET"], "手动检查容量（不自动发消息）")
        api(f"/{PLUGIN_NAME}/files", self.api_files, ["GET"], "群文件列表")
        api(f"/{PLUGIN_NAME}/delete", self.api_delete, ["POST"], "删除文件")
        api(f"/{PLUGIN_NAME}/move", self.api_move, ["POST"], "移动文件")
        api(f"/{PLUGIN_NAME}/scan", self.api_scan, ["GET"], "扫描群文件（供自动生成规则）")
        api(f"/{PLUGIN_NAME}/search", self.api_search, ["GET"], "按文件名关键词查群文件")
        api(f"/{PLUGIN_NAME}/send_file", self.api_send_file, ["POST"], "把群文件发送到群")
        api(f"/{PLUGIN_NAME}/send_tmp", self.api_send_tmp_get, ["GET"], "发送临时文件策略")
        api(f"/{PLUGIN_NAME}/send_tmp", self.api_send_tmp_set, ["POST"], "保存发送临时文件策略")
        api(f"/{PLUGIN_NAME}/send_tmp_sweep", self.api_send_tmp_sweep, ["POST"], "立即清理发送临时文件")
        api(f"/{PLUGIN_NAME}/export", self.api_export, ["GET"], "导出群配置 JSON")
        api(f"/{PLUGIN_NAME}/import", self.api_import, ["POST"], "导入群配置 JSON")
        api(f"/{PLUGIN_NAME}/export_all", self.api_export_all, ["GET"], "导出全部群配置（全局备份）")
        api(f"/{PLUGIN_NAME}/import_all", self.api_import_all, ["POST"], "导入全局备份（按群号覆盖合并）")
        api(f"/{PLUGIN_NAME}/dupes", self.api_dupes, ["GET"], "查重（同名+同大小）")
        api(f"/{PLUGIN_NAME}/members", self.api_members, ["GET"], "群成员列表（供选管理员）")
        api(f"/{PLUGIN_NAME}/cleanup", self.api_cleanup, ["POST"], "手动触发分类")
        api(f"/{PLUGIN_NAME}/create_folder", self.api_create_folder, ["POST"], "创建群文件夹")
        api(f"/{PLUGIN_NAME}/admin_users", self.api_admin_users_get, ["GET"], "读取全局管理员")
        api(f"/{PLUGIN_NAME}/admin_users", self.api_admin_users_set, ["POST"], "保存全局管理员")

    # ================= 配置辅助 =================
    @property
    def merged_config(self) -> dict:
        return self.config

    @property
    def plugin_enabled(self) -> bool:
        if self._enabled_override is not None:
            return bool(self._enabled_override)
        return bool(self.config.get("plugin_enabled", True))

    def set_plugin_enabled(self, on: bool) -> None:
        self._enabled_override = bool(on)

    def record_event(self, module: str, level: str, text: str) -> None:
        """记录运行事件（最近 50 条），供 /群文件 诊断 查看。"""
        try:
            self.recent_events.appendleft(
                {
                    "t": time.strftime("%m-%d %H:%M:%S"),
                    "m": module,
                    "l": level,
                    "x": str(text)[:300],
                }
            )
        except Exception:
            pass

    # ================= 权限 / 锁 / 审计（v2.8.0） =================
    def group_lock(self, gid: str) -> asyncio.Lock:
        gid = str(gid)
        lock = self._locks.get(gid)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[gid] = lock
        return lock

    def _is_admin_user(self, event: AstrMessageEvent, gid: str | None = None) -> bool:
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        uid = str(event.get_sender_id() or "")
        if not uid:
            return False
        globals_ = self.store.get_admin_users() or [str(a) for a in (self.config.get("admin_users") or [])]
        if uid in globals_:
            return True
        if not gid:
            return False
        return uid in [str(a) for a in (self.store.get_group(gid).get("admins") or [])]

    def audit_now(
        self,
        actor: str,
        gid: str,
        action: str,
        target: str = "",
        result: str = "ok",
        detail: str = "",
    ) -> None:
        try:
            self.audit.append(actor, gid, action, target, result, detail)
        except Exception:
            pass

    def client_for(self, event: AstrMessageEvent) -> QQFileClient | None:
        """按事件所属平台取客户端，避免多 OneBot 平台时绑错。"""
        bot = getattr(event, "bot", None)
        if bot is not None and hasattr(bot, "api"):
            key = id(bot)
            c = self._event_clients.get(key)
            if c is None:
                c = QQFileClient(bot)
                self._event_clients[key] = c
            return c
        return self.qq

    # ================= 发送临时目录 / 清理（v2.7.0） =================
    @staticmethod
    def _temp_root() -> Path | None:
        if get_astrbot_temp_path is None:
            return None
        try:
            return Path(get_astrbot_temp_path())
        except Exception:
            return None

    def _default_send_dir(self) -> Path:
        temp = self._temp_root()
        if temp is not None:
            return default_send_dir(temp)
        return data_root() / "send_tmp"

    def send_settings(self) -> dict[str, Any]:
        """发送临时文件的生效策略：面板（groups.json）> 插件配置 > 内置默认。"""
        cfg = self.store.get_send_tmp()
        raw_custom = str(cfg.get("dir") or "").strip()
        raw_conf = str(self.config.get("deliver_dir") or "").strip()
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "keep_seconds": clamp_int(
                cfg.get("keep_seconds"), DEFAULT_KEEP_SECONDS, MIN_KEEP_SECONDS, MAX_KEEP_SECONDS
            ),
            "orphan_hours": clamp_int(
                cfg.get("orphan_hours"), DEFAULT_ORPHAN_HOURS, MIN_ORPHAN_HOURS, MAX_ORPHAN_HOURS
            ),
            "custom_dir": raw_custom if is_abs_path(raw_custom) else "",
            "config_dir": raw_conf if is_abs_path(raw_conf) else "",
            "default_dir": str(self._default_send_dir()),
        }

    def send_dir(self) -> Path:
        s = self.send_settings()
        return resolve_send_dir(s["custom_dir"], s["config_dir"], s["default_dir"])

    def send_dir_info(self) -> dict[str, Any]:
        s = self.send_settings()
        d = self.send_dir()
        temp = self._temp_root()
        if s["custom_dir"]:
            source = "面板自定义目录"
        elif s["config_dir"]:
            source = "插件配置 deliver_dir"
        else:
            source = "默认共享 temp"
        return {
            "dir": str(d),
            "source": source,
            "shared": bool(temp and is_under(d, temp)),
            "temp_root": str(temp) if temp else "",
            "enabled": s["enabled"],
            "keep_seconds": s["keep_seconds"],
            "orphan_hours": s["orphan_hours"],
            "inflight": len(self._send_inflight),
            "last_sweep": dict(self._last_sweep_info),
        }

    def ensure_send_dir(self) -> Path:
        """确保发送目录存在且协议端可读（目录 777，父级共享目录保持可进）。"""
        d = self.send_dir()
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(f"[群管理] 创建发送目录失败 {d}: {e}")
        try:
            os.chmod(d, 0o777)
        except OSError:
            pass
        if d.parent and d.parent.name == PLUGIN_NAME:
            try:
                os.chmod(d.parent, 0o755)
            except OSError:
                pass
        return d

    @staticmethod
    def _ensure_readable(path: Path) -> None:
        """发送前补 others 读，避免容器 umask 偏严导致协议端 EACCES。"""
        try:
            os.chmod(path, 0o666)
        except OSError:
            pass

    def _mark_inflight(self, path: Path | str) -> str:
        real = os.path.realpath(str(path))
        self._send_inflight.add(real)
        return real

    def _unmark_inflight(self, path: Path | str) -> None:
        self._send_inflight.discard(os.path.realpath(str(path)))

    def _schedule_cleanup(self, paths: list[Path], delay: int | None = None, attempt: int = 0) -> None:
        """延迟清理临时文件；发送中的文件跳过并在 30 秒后重试（最多 20 次）。"""
        if not paths or attempt > 20:
            return
        s = self.send_settings()
        if not s["enabled"]:
            return
        wait = s["keep_seconds"] if delay is None else max(1, int(delay))
        try:
            task = asyncio.create_task(self._cleanup_after(list(paths), wait, attempt))
        except RuntimeError:  # 事件循环已关闭（关机/卸载中）
            return
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup_after(self, paths: list[Path], wait: int, attempt: int = 0) -> None:
        await asyncio.sleep(wait)
        if not self.send_settings()["enabled"]:
            return  # 等待期间用户关掉了自动清理：不再执行
        left: list[Path] = []
        for p in paths:
            if os.path.realpath(str(p)) in self._send_inflight:
                left.append(p)  # 还在发送/上传，绝不在中途删
                continue
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
        if left:
            self._schedule_cleanup(left, 30, attempt + 1)

    async def cleanup_sweep(self, force: bool = False) -> dict[str, Any]:
        """清扫发送目录里的残留（只动本插件的发送目录，不碰群文件与配置）。"""
        s = self.send_settings()
        if not force and not s["enabled"]:
            return {"skipped": "disabled"}
        d = self.send_dir()
        info: dict[str, Any] = {"dir": str(d), "removed": 0, "kept": 0, "errors": 0}
        try:
            if d.is_dir():
                entries: list[tuple[str, bool, float]] = []
                for p in d.iterdir():
                    try:
                        entries.append((str(p), p.is_file(), p.stat().st_mtime))
                    except OSError:
                        continue
                victims = plan_sweep(
                    entries, time.time(), s["orphan_hours"] * 3600, self._send_inflight
                )
                for v in victims:
                    if os.path.realpath(v) in self._send_inflight:
                        continue
                    try:
                        Path(v).unlink(missing_ok=True)
                        info["removed"] += 1
                    except Exception:
                        info["errors"] += 1
                info["kept"] = max(0, len(entries) - info["removed"])
        except Exception as e:
            logger.warning(f"[群管理] 清扫发送目录失败: {e}")
            info["error"] = str(e)
        info["at"] = time.strftime("%m-%d %H:%M:%S")
        self._last_sweep = time.time()
        self._last_sweep_info = dict(info)
        if info.get("removed"):
            self.record_event(
                "发送清理", "info" if not info.get("errors") else "warn",
                f"清扫发送临时文件 {info['removed']} 个 / 保留 {info.get('kept', 0)} 个",
            )
        return info

    async def cleanup_tick(self) -> None:
        """定时循环里顺带清扫（10 分钟一次，不额外开线程）。"""
        if not self.send_settings()["enabled"]:
            return
        if time.time() - self._last_sweep < 600:
            return
        await self.cleanup_sweep()

    async def _startup_sweep(self) -> None:
        await asyncio.sleep(10)
        try:
            # 自动清理关掉时不做自动清扫（用户点「立即清理」仍可强制清）
            await self.cleanup_sweep(force=False)
        except Exception:
            pass

    # ================= 生命周期 =================
    async def initialize(self) -> None:
        asyncio.create_task(self._startup())

    async def _startup(self) -> None:
        await self._bind_bot()
        if not self.bot:
            logger.warning("[群管理] 首次绑定失败，30 秒后重试")
            self.record_event("启动", "warn", "OneBot 绑定失败，30 秒后重试")
            await asyncio.sleep(30)
            await self._bind_bot()
        # 双保险：若上一个插件实例的定时循环仍在运行（WebUI 重载/热更新后常见），先取消，确保全局只有一个 tick
        if GroupFileConsole._run_task is not None:
            GroupFileConsole._run_task.cancel()
            logger.warning("[群管理] 检测到上一实例的定时循环仍在运行，已取消（防止到点重复发送）")
        GroupFileConsole._run_task = asyncio.create_task(self.scheduler.run())
        self.record_event(
            "启动", "info", f"插件启动（v{VERSION}），OneBot {'已绑定' if self.qq else '未绑定'}"
        )
        asyncio.create_task(self._startup_sweep())
        if self.qq:
            try:
                await self.qq.login_info()
            except Exception:
                pass
        logger.info("[群管理] 插件启动完成")

    async def terminate(self) -> None:
        """插件卸载/重载时停掉定时循环，避免旧实例残留继续到点发消息。"""
        try:
            if GroupFileConsole._run_task is not None:
                GroupFileConsole._run_task.cancel()
                GroupFileConsole._run_task = None
                logger.info("[群管理] 已停止定时循环（terminate）")
        except Exception:
            pass
        for task in list(self._cleanup_tasks):
            try:
                task.cancel()
            except Exception:
                pass
        self._cleanup_tasks.clear()
        self._send_inflight.clear()

    async def _bind_bot(self) -> None:
        """绑定一个可用的 aiocqhttp 客户端。

        v2.8.0：不再只看「第一个平台」，遍历所有平台，优先已连接且能拿到 api 的；
        命令执行时还会用 event.bot 覆盖成事件所属平台的客户端。
        """
        candidates = []
        try:
            candidates = list(self.context.platform_manager.platform_insts)
        except Exception:
            p = self.context.get_platform(filter.PlatformAdapterType.AIOCQHTTP)
            candidates = [p] if p else []
        for platform in candidates:
            try:
                meta = platform.meta()
                if "aiocqhttp" not in str(getattr(meta, "name", "")):
                    continue
                if not hasattr(platform, "get_client"):
                    continue
                client = platform.get_client()
                if client and hasattr(client, "api") and hasattr(client.api, "call_action"):
                    self.bot = client
                    self.qq = QQFileClient(client)
                    self.platform_id = getattr(meta, "id", "")
                    logger.info(f"[群管理] 已绑定 OneBot 客户端（{self.platform_id or 'platform'}）")
                    return
            except Exception as e:
                logger.debug(f"[群管理] 平台绑定跳过: {e}")
        logger.warning("[群管理] 没有可用的 aiocqhttp 客户端")

    # ================= 群通知（定时任务/WebUI 触发）=================
    async def notify_group(self, group_id: str | int, text: str) -> None:
        gid = str(group_id)
        umo = self.umo_cache.get(gid)
        if umo:
            try:
                chain = MessageChain([Comp.Plain(text)])
                await self.context.send_message(umo, chain)
                return
            except Exception as e:
                logger.warning(f"[通知] 走合集发送失败，改直发: {e}")
        if self.qq:
            try:
                await self.qq.call("send_group_msg", group_id=int(gid), message=text)
            except Exception as e:
                logger.error(f"[通知] 群 {gid} 发送失败: {e}")

    async def _direct_send(self, event: AstrMessageEvent, text: str) -> bool:
        """管理命令结果直发（绕过事件链直接发群消息，回复更快更稳）。成功返回 True。"""
        gid = str(event.message_obj.group_id or "") if event.message_obj else ""
        if gid and self.qq and self.qq.available:
            try:
                await asyncio.wait_for(
                    self.qq.call("send_group_msg", group_id=int(gid), message=text),
                    timeout=15,
                )
                return True
            except Exception as e:
                logger.warning(f"[群管理] 直发失败，改用普通回复: {e}")
        return False

    # ================= 命令组 /群文件（短前缀 /gf） =================
    @filter.command_group(CMD, alias={"gf"})
    def groupfile(self) -> None:
        """群文件控制台"""

    # ---- 上下文 / 确认 基础设施 ----
    def _uid(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    def _pending_key(self, event: AstrMessageEvent, gid: str) -> str:
        """待确认操作按「用户+群」隔离，防止 A 群发起、B 群确认。"""
        return f"{self._uid(event)}:{gid}"

    def _save_listing(self, event: AstrMessageEvent, gid: str, folder: str, folder_id: str, files: list[dict]) -> None:
        """记录最近一次文件列表（列/查），5 分钟内可用序号直接 删/移。"""
        uid = self._uid(event)
        if uid:
            self._listing[uid] = {"gid": str(gid), "folder": folder, "folder_id": folder_id, "files": files, "ts": time.time()}

    def _get_listing(self, event: AstrMessageEvent, gid: str | None = None) -> dict | None:
        uid = self._uid(event)
        it = self._listing.get(uid)
        if not it:
            return None
        if time.time() - it.get("ts", 0) > LISTING_TTL:
            self._listing.pop(uid, None)
            return None
        if gid and it.get("gid") != str(gid):
            return None
        return it

    def _invalidate_listing(self, gid: str) -> None:
        """写操作成功后立即失效该群的列表上下文，防止序号错位。"""
        for k in [k for k, v in self._listing.items() if v.get("gid") == str(gid)]:
            self._listing.pop(k, None)

    @staticmethod
    def _parse_picks(spec: str, total: int) -> list[int]:
        """解析序号表达式（1,3-5 / 全部）为去重升序的 1-based 序号列表。"""
        idxs: set[int] = set()
        for seg in str(spec or "").replace("，", ",").split(","):
            seg = seg.strip()
            if not seg:
                continue
            if seg in ("全部", "all", "*"):
                return list(range(1, total + 1))
            if "-" in seg:
                a, _, b = seg.partition("-")
                if a.strip().isdigit() and b.strip().isdigit():
                    idxs.update(range(int(a), min(int(b), total) + 1))
            elif seg.isdigit():
                idxs.add(int(seg))
        return sorted(i for i in idxs if 1 <= i <= total)

    @staticmethod
    def _match_folders(pool: dict[str, str], name: str) -> list[str]:
        """文件夹名自动匹配：精确 → 忽略大小写精确 → 不区分大小写包含。

        返回候选文件夹名列表（空 = 无匹配）。唯一命中即可直接使用。
        """
        name = str(name or "").strip()
        if not name:
            return []
        if name in pool:
            return [name]
        low = name.lower()
        exact_ci = [k for k in pool if k.lower() == low]
        if exact_ci:
            return exact_ci
        return [k for k in pool if low in k.lower()]

    def _fmt_folder_candidates(self, cands: list[str], name: str) -> str:
        """多命中/零命中时的提示文案。"""
        if not cands:
            return ""
        show = "、".join(cands[:8])
        if len(cands) > 8:
            show += f" 等 {len(cands)} 个"
        return f"「{name}」匹配到 {len(cands)} 个文件夹：{show}"

    # ---- 查看类 ----
    @groupfile.command("帮助", alias={"help", "?", "？", "菜单"})
    async def cmd_help(self, event: AstrMessageEvent) -> None:
        """帮助：全部指令速查。"""
        text = (
            f"📁 群文件管理 v{VERSION}（/gf = /群文件，群号可省略）\n"
            "━━━━━━━━━━━━\n"
            "📋 查看：\n"
            "/gf 状态 —— 空间/整理/时效/规则一览\n"
            "/gf 列 [文件夹] —— 文件列表（无参=根目录+文件夹）\n"
            "/gf 查 <关键词> [序号] —— 搜索；带序号=发送该文件\n"
            "/gf 诊 —— 插件自查报告\n"
            "📂 操作（仅管理员）：\n"
            "/gf 删 <序号|范围|全部> —— 删最近列表中的文件（先 列/查）\n"
            "/gf 删 <文件夹> <序号|全部> —— 直接按文件夹删\n"
            "/gf 移 <序号|关键词> <目标文件夹> —— 移动\n"
            "/gf 建 <文件夹名> —— 新建文件夹\n"
            "/gf 整 —— 立即整理（分类→时效→容量）\n"
            "🔧 管理（仅管理员）：\n"
            "/gf 绑 [备注]、解 —— 绑定/解绑群\n"
            "/gf 开、关 —— 本群启用/停用\n"
            "/gf 管 @某人 / 管 删 @某人 —— 群管理员\n"
            "/gf 确认、取消 —— 两段式确认\n"
            "🧵 运维：\n"
            "/gf 预览 —— 试运行：只报告将移动/回收/删除哪些文件\n"
            "/gf 任务 —— 后台任务进度\n"
            "/gf 审计 [n] —— 最近操作审计\n"
            "/gf 回收 —— 回收站；/gf 恢复 <序号> 还原\n"
            "━━━━━━━━━━━━\n"
            "💡 旧命令名（列表/删除/移动/诊断等）都能用；\n"
            "   文件夹名不用打全（如 /gf 列 hv 自动进 HV2）；\n"
            "   只发 /gf 会显示全部指令说明。\n"
            "⚙ 规则/定时/时效/容量设置：AstrBot 面板 → 插件 → 群文件管理"
        )
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("状态", alias={"status", "容量"})
    async def cmd_status(self, event: AstrMessageEvent) -> None:
        """状态：本群空间用量、整理/时效/容量配置与管理员一览。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 状态 <群号>")
            return
        qq = self.client_for(event) or self.qq
        if not qq:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        # v2.7.0：容量接口失败不再整条报错——文件列表/规则/发送目录照常显示
        info: dict[str, Any] = {}
        cap_err = ""
        try:
            info = await qq.file_system_info(gid)
        except QQFileError as e:
            cap_err = str(e)
        try:
            raw = await qq.root_files(gid)
        except QQFileError as e:
            yield event.plain_result(f"读取群文件失败：{e}")
            return
        used = int(info.get("used_space") or 0)
        total = int(info.get("total_space") or 0)
        count = int(info.get("file_count") or 0)
        folders = norm_folders(raw)
        cfg = self.store.get_group(gid)
        cleanup = cfg.get("cleanup") or {}
        ttl = cfg.get("ttl") or {}
        limits = cfg.get("limits") or {}
        admins = cfg.get("admins") or []
        rules = self.store.rules(gid)
        count_limit = int(limits.get("count_limit") or 0)
        space_limit_gb = float(limits.get("space_limit_gb") or 0)
        quota_on = (count_limit > 0 or space_limit_gb > 0) and limits.get("notify", True) is not False
        ttl_on = bool(ttl.get("enabled"))
        ttl_days = int(ttl.get("default_days") or 0)
        ttl_action = str(ttl.get("action") or "report")
        ttl_act_txt = {"report": "只报告", "recycle": "移入回收", "delete": "删除"}.get(ttl_action, ttl_action)
        crones = cleanup.get("crones") or []
        sd = self.send_dir_info()
        lines = [
            f"📊 群 {gid} 文件状态",
            f"· 文件数：{count} 个 / 文件夹 {len(folders)} 个",
            f"· 空间：{used/GB:.2f}GB / {total/GB:.2f}GB" if not cap_err else "· 空间：容量接口暂不可用（不影响文件操作）",
            f"· 本群启用：{'开' if cfg.get('enabled', True) is not False else '关'}；插件总开关：{'开' if self.plugin_enabled else '关'}",
            f"· 定时整理：{'开（' + ' / '.join(crones) + '）' if cleanup.get('enabled') and crones else '关'}",
            f"· 时效清理：{'开（默认 ' + str(ttl_days) + ' 天，' + ttl_act_txt + '）' if ttl_on else '关'}",
            f"· 容量提醒：{'开' if quota_on else '关'}"
            + (f"（超 {count_limit} 个 / {space_limit_gb}GB 提醒）" if quota_on else ""),
            f"· 管理员：{'、'.join(admins) if admins else '仅主人/全局管理员'}",
            f"· 发送临时文件：{'共享目录' if sd['shared'] else '⚠ 非共享目录'} · 保留 {sd['keep_seconds']}s · 残留 {sd['orphan_hours']}h",
            f"  {sd['dir']}（{sd['source']}）",
            f"· 分类规则 {len(rules)} 条：",
        ]
        for r in rules:
            pat = r.get("pattern") or "（全部）"
            kind = str(r.get("kind") or "").strip()
            extra = f" · {kind}" if kind else ""
            lines.append(f"  - {r.get('source', '?')} → 「{r.get('folder', '')}」（{pat}{extra}）")
        events = list(self.recent_events)
        if events:
            lines.append("━━━━━━━━━━━━")
            lines.append("最近动作：")
            for ev in events[:8]:
                lines.append(f"[{ev['t']}] {ev['m']} {'⚠' if ev['l']=='warn' else ''} {ev['x']}")
        async for r in self._reply(event, "\n".join(lines)):
            yield r

    @groupfile.command("列", alias={"列表", "ls"})
    async def cmd_list(self, event: AstrMessageEvent) -> None:
        """列：根目录/文件夹文件（分页，每页 30），序号为全量序号，可直接 删/移。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 列 [群号] [文件夹名] [页码]")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        page = 1
        folder_name = " ".join(args).strip() if args else ""
        if len(args) >= 2 and args[-1].isdigit():
            page = max(1, int(args[-1]))
            folder_name = " ".join(args[:-1]).strip()
        try:
            raw = await qq.root_files(gid)
            pool = {d["name"]: d["folder_id"] for d in norm_folders(raw)}
            if folder_name in ("根目录", "根", "root"):
                folder_name = ""
            if folder_name.isdigit() and folder_name not in pool:
                page = max(1, int(folder_name))
                folder_name = ""
            if folder_name:
                cands = self._match_folders(pool, folder_name)
                if not cands:
                    avail = "、".join(list(pool)[:10]) or "（根目录下没有文件夹）"
                    yield event.plain_result(
                        f"未找到文件夹「{folder_name}」。现有文件夹：{avail}\n"
                        "（输入名称的一部分也能自动匹配，如 /gf 列 hv 会进 HV2；翻页：/gf 列 文件夹 2）"
                    )
                    return
                if len(cands) > 1:
                    yield event.plain_result(
                        self._fmt_folder_candidates(cands, folder_name) + "\n请用完整名称重试：/gf 列 <名称> [页码]"
                    )
                    return
                resolved = cands[0]
                match_hint = "" if resolved == folder_name else f"（按「{folder_name}」自动匹配）"
                raw = await qq.files_by_folder(gid, pool[resolved])
                files = norm_files(raw)
                self._save_listing(event, gid, resolved, pool[resolved], files)
                page_size = 30
                total_pages = max(1, (len(files) + page_size - 1) // page_size)
                page = min(page, total_pages)
                start = (page - 1) * page_size
                lines = [
                    f"📁 {resolved}{match_hint} 共 {len(files)} 个文件"
                    f"（第 {page}/{total_pages} 页；/gf 列 {resolved} {page + 1} 翻页）"
                ]
                for i in range(start, min(start + page_size, len(files))):
                    f = files[i]
                    lines.append(f"{i + 1}. {f['name']}（{self._fmt_size(f['size'])}）")
                if not files:
                    lines.append("（空）")
            else:
                files = norm_files(raw)
                folders = norm_folders(raw)
                self._save_listing(event, gid, "", "", files)
                page_size = 30
                total_pages = max(1, (len(files) + page_size - 1) // page_size)
                page = min(page, total_pages)
                start = (page - 1) * page_size
                lines = [
                    f"📁 群 {gid} 根目录：{len(files)} 个文件 / {len(folders)} 个文件夹"
                    f"（第 {page}/{total_pages} 页；/gf 列 {page + 1} 翻页）"
                ]
                for i in range(start, min(start + page_size, len(files))):
                    f = files[i]
                    lines.append(f"{i + 1}. {f['name']}（{self._fmt_size(f['size'])}）")
                for d in folders[:20]:
                    lines.append(f"📂 {d['name']}（{d['file_count']} 个文件）→ /gf 列 {d['name']}")
                if len(folders) > 20:
                    lines.append(f"... 其余 {len(folders) - 20} 个文件夹略")
                if not files and not folders:
                    lines.append("（空）")
        except QQFileError as e:
            yield event.plain_result(f"读取失败：{e}")
            return
        async for r in self._reply(event, "\n".join(lines)):
            yield r

    @groupfile.command("查", alias={"搜", "搜索", "find"})
    async def cmd_search(self, event: AstrMessageEvent) -> None:
        """查：关键词全目录搜索（支持空格）；末尾数字 = 序号，下载并发送该文件。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 查 [群号] <关键词> [序号]")
            return
        if not args:
            yield event.plain_result("用法：/gf 查 [群号] <关键词> [序号]\n例：/gf 查 温泉 → 列出；/gf 查 温泉 1 → 发送第 1 个")
            return
        pick = int(args[-1]) if args[-1].isdigit() else 0
        kw = " ".join(args[:-1] if pick else args).strip()
        if not kw:
            yield event.plain_result("用法：/gf 查 <关键词> [序号]（关键词不能为空）")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        try:
            scan = await self._collect_files(gid, qq)
            hits = [f for f in scan if kw.lower() in str(f["name"]).lower()]
        except QQFileError as e:
            yield event.plain_result(f"读取群文件失败：{e}")
            return
        if not hits:
            async for r in self._reply(event, f"🔍 未找到文件名含「{kw}」的文件（已扫根目录和所有子文件夹）"):
                yield r
            return
        if pick > 0:
            if pick > len(hits):
                yield event.plain_result(f"序号超范围：只有 {len(hits)} 个命中")
                return
            f = hits[pick - 1]
            dest, err = await self._download_file(gid, f, qq)
            if dest is None:
                self.audit_now(self._uid(event), gid, "发文件", str(f.get("name")), "fail", err)
                async for r in self._reply(event, f"发送失败：{err}"):
                    yield r
                return
            # v2.1.1：文件走事件链（可被聚合插件收进合并转发卡，
            # collector 会做硬链接副本保护）；发送是异步的。
            # v2.7.0：发送期间标记 in-flight，清理任务/清扫都会跳过它；
            # 发送完成后再按配置的保留秒数延迟清理。
            fname = Path(str(f.get("name") or "file")).name or "file"
            self._ensure_readable(dest)
            self._mark_inflight(dest)
            try:
                yield event.chain_result([Comp.File(name=fname, file=str(dest))])
            finally:
                self._unmark_inflight(dest)
            self._schedule_cleanup([dest])
            keep = self.send_settings()["keep_seconds"]
            self.audit_now(self._uid(event), gid, "发文件", str(f.get("name")), "ok", f"{self._fmt_size(f.get('size') or 0)}")
            async for r in self._reply(event, f"📤 已发送「{f['name']}」（临时文件 {keep}s 后清理）"):
                yield r
            return
        self._save_listing(event, gid, f"搜索:{kw}", "", hits)
        lines = [f"🔍 找到 {len(hits)} 个含「{kw}」的文件（/gf 查 {kw} <序号> 发送 · /gf 删 <序号> 删除）"]
        for i, f in enumerate(hits[:20], 1):
            lines.append(f"{i}. {f['name']}（{self._fmt_size(f['size'])}）@ {f['folder'] or '根目录'}")
        if len(hits) > 20:
            lines.append(f"... 其余 {len(hits) - 20} 个略（可加关键词缩小范围）")
        async for r in self._reply(event, "\n".join(lines)):
            yield r

    @groupfile.command("诊", alias={"诊断", "diag"})
    async def cmd_diag(self, event: AstrMessageEvent) -> None:
        """诊：插件运行状态自查报告。"""
        sd = self.send_dir_info()
        lines = [
            f"🔍 群文件管理 v{VERSION} 诊断",
            f"· 插件总开关：{'开' if self.plugin_enabled else '关'}",
            f"· OneBot 绑定：{'是' if (self.qq and self.qq.available) else '否（未就绪）'}",
            f"· 已配置群 {len(self.store.group_ids())} 个：" + ("、".join(self.store.group_ids()) or "无"),
            f"· 缓存会话 {len(self.umo_cache)} 个：" + ("、".join(self.umo_cache.keys()) or "无"),
            f"· 列表上下文 {len(self._listing)} 个 / 待确认操作 {len(self._pending)} 个",
            f"· 发送目录：{sd['dir']}",
            f"  {'共享 temp（协议端可读）' if sd['shared'] else '⚠ 不在共享 temp，协议端可能读不到'}；来源 {sd['source']}",
            f"· 发送清理：{'开' if sd['enabled'] else '关'} · 保留 {sd['keep_seconds']}s · 残留 {sd['orphan_hours']}h · 发送中 {sd['inflight']} 个",
            f"· 审计：{self.audit.stats().get('size', 0)} 字节（/gf 审计 查看）",
        ]
        if sd.get("last_sweep"):
            ls = sd["last_sweep"]
            lines.append(f"· 上次清扫：{ls.get('at', '—')} 删除 {ls.get('removed', 0)} 个")
        events = list(self.recent_events)
        if events:
            lines.append("━━━━━━━━━━━━")
            lines.append("最近动作：")
            for ev in events[:6]:
                lines.append(f"[{ev['t']}] {ev['m']} {'⚠' if ev['l']=='warn' else ''} {ev['x']}")
        async for r in self._reply(event, "\n".join(lines)):
            yield r

    # ---- 文件操作（仅管理员） ----
    @groupfile.command("删", alias={"删除", "del"})
    async def cmd_delete(self, event: AstrMessageEvent) -> None:
        """删：删除群文件。群内用最近列表序号（/gf 删 3 / 3,5-7 / 全部），或 /gf 删 <文件夹名> <序号|全部>。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 删 [群号] <文件夹名> <序号|全部>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可删除群文件")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        label = ""
        if args and len(args) == 1 and args[0] not in ("全部", "all") and not args[0].isdigit():
            # 单参数且不是序号/全部：用户大概想删整个文件夹——必须给序号或「全部」
            yield event.plain_result(
                f"「{args[0]}」要删除需要指定范围：/gf 删 {args[0]} 全部（或 1 / 1,3-5）\n"
                "想删最近列表里的文件：先 /gf 列 或 /gf 查，再 /gf 删 <序号>"
            )
            return
        if args and len(args) >= 2 and args[0] not in ("全部", "all") and not args[0].isdigit():
            # 语法 A：/gf 删 <文件夹名> <序号|全部>（文件夹名可含空格，取最后一个参数为范围）
            spec = args[-1]
            folder_name = " ".join(args[:-1]).strip()
            if spec not in ("全部", "all") and not re.fullmatch(r"[\d,\-，]+", spec):
                yield event.plain_result(f"序号无效：{spec}（支持 3 / 3,5-7 / 全部）")
                return
            try:
                root = await qq.root_files(gid)
                pool = {d["name"]: d["folder_id"] for d in norm_folders(root)}
                cands = self._match_folders(pool, folder_name)
                if not cands:
                    avail = "、".join(list(pool)[:10]) or "（根目录下没有文件夹）"
                    yield event.plain_result(f"未找到文件夹「{folder_name}」。现有：{avail}")
                    return
                if len(cands) > 1:
                    yield event.plain_result(
                        self._fmt_folder_candidates(cands, folder_name) + "\n请用完整名称重试：/gf 删 <文件夹> <序号>"
                    )
                    return
                resolved = cands[0]
                raw = await qq.files_by_folder(gid, pool[resolved])
                files = norm_files(raw)
                self._save_listing(event, gid, resolved, pool[resolved], files)
            except QQFileError as e:
                yield event.plain_result(f"读取失败：{e}")
                return
            label = resolved if resolved == folder_name else f"{resolved}（按「{folder_name}」匹配）"
        else:
            # 语法 B：/gf 删 <序号|范围|全部> —— 使用最近一次 列/查 的上下文
            spec = args[0] if args else ""
            listing = self._get_listing(event, gid)
            if not listing:
                yield event.plain_result(
                    "没有可用的列表上下文（5 分钟内有效）。先 /gf 列 [文件夹] 或 /gf 查 <关键词>，\n或改用 /gf 删 <文件夹名> <序号|全部>"
                )
                return
            files = listing["files"]
            label = listing.get("folder") or "根目录"
        if not files:
            yield event.plain_result("（该范围内没有文件）")
            return
        picks = self._parse_picks(spec, len(files))
        if not picks:
            yield event.plain_result(f"序号无效：{spec}（共 {len(files)} 个文件，支持 3 / 3,5-7 / 全部）")
            return
        targets = [files[i - 1] for i in picks]
        preview = [f"· {t['name']}（{self._fmt_size(t.get('size') or 0)}）" for t in targets[:10]]
        if len(targets) > 10:
            preview.append(f"... 等共 {len(targets)} 个")
        self._pending[self._pending_key(event, gid)] = {"action": "delete", "gid": gid, "targets": targets, "ts": time.time()}
        text = (
            f"⚠ 将从「{label}」删除 {len(targets)} 个文件：\n" + "\n".join(preview)
            + "\n60 秒内回复 /gf 确认 执行；/gf 取消 放弃"
        )
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("移", alias={"移动", "mv"})
    async def cmd_move(self, event: AstrMessageEvent) -> None:
        """移：移动文件到目标文件夹（不存在自动创建）。序号=最近列表；关键词=全目录匹配。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 移 [群号] <序号|关键词> <目标文件夹>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可移动群文件")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        if len(args) < 2:
            yield event.plain_result("用法：/gf 移 <序号|关键词> <目标文件夹>\n例：/gf 移 3 COS（最近列表 3 号）/gf 移 景图 HV2")
            return
        src, target = args[0], " ".join(args[1:]).strip()
        if src.isdigit():
            listing = self._get_listing(event, gid)
            if not listing:
                yield event.plain_result("没有可用的列表上下文（5 分钟内有效）。先 /gf 列 [文件夹] 或 /gf 查 <关键词>")
                return
            files = listing["files"]
            idx = int(src)
            if not (1 <= idx <= len(files)):
                yield event.plain_result(f"序号超范围：列表共 {len(files)} 个")
                return
            targets = [files[idx - 1]]
        else:
            try:
                all_files = await self._collect_files(gid, qq)
            except QQFileError as e:
                yield event.plain_result(f"读取群文件失败：{e}")
                return
            hits = [f for f in all_files if src.lower() in str(f["name"]).lower()]
            if not hits:
                yield event.plain_result(f"未找到文件名含「{src}」的文件")
                return
            if len(hits) > 1:
                self._save_listing(event, gid, f"搜索:{src}", "", hits)
                lines = [f"命中 {len(hits)} 个，请用序号精确移动（/gf 移 <序号> {target}）："]
                for i, f in enumerate(hits[:10], 1):
                    lines.append(f"{i}. {f['name']} @ {f['folder'] or '根目录'}")
                async for r in self._reply(event, "\n".join(lines)):
                    yield r
                return
            targets = [hits[0]]
        # 解析目标文件夹：先模糊匹配现有（唯一命中直接用），无匹配才新建
        created = False
        try:
            root = await qq.root_files(gid)
            pool = {d["name"]: d["folder_id"] for d in norm_folders(root)}
        except QQFileError as e:
            yield event.plain_result(f"读取文件夹失败：{e}")
            return
        cands = self._match_folders(pool, target)
        if len(cands) > 1:
            # 多命中不自动新建，防止手滑建出错误文件夹
            yield event.plain_result(
                self._fmt_folder_candidates(cands, target) + "\n请用完整名称重试，避免移动到错误文件夹"
            )
            return
        if len(cands) == 1:
            if cands[0] != target:
                target = cands[0]  # 唯一模糊命中：自动改用现有文件夹
        else:
            try:
                fid = await qq.create_folder(gid, target)
                if not fid:
                    root2 = await qq.root_files(gid)
                    fid = {d["name"]: d["folder_id"] for d in norm_folders(root2)}.get(target, "")
                pool[target] = fid
                created = True
            except QQFileError as e:
                yield event.plain_result(f"目标文件夹「{target}」创建失败：{e}")
                return
        ok, fail, errs = 0, 0, []
        async with self.group_lock(gid):
            for t in targets:
                try:
                    await qq.move_file(
                        gid,
                        str(t["file_id"]),
                        target,
                        target_id=pool.get(target),
                        current_id=t.get("folder_id") or None,
                    )
                    ok += 1
                    self.audit_now(self._uid(event), gid, "移动", str(t.get("name") or ""), "ok", f"→ {target}")
                except QQFileError as e:
                    fail += 1
                    errs.append(f"{t['name']}: {e}")
                    self.audit_now(self._uid(event), gid, "移动", str(t.get("name") or ""), "fail", str(e))
        self._invalidate_listing(gid)
        text = f"📦 已移动 {ok} 个到「{target}」" + ("（新文件夹，已自动创建）" if created else "")
        text += f"，失败 {fail} 个" if fail else ""
        if errs:
            text += "\n" + "\n".join(errs[:3])
        self.record_event("移动", "info" if not fail else "warn", f"群 {gid} 命令移动 {ok}/{ok + fail} → {target}")
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("建", alias={"建文件夹", "新建", "mkdir"})
    async def cmd_create_folder(self, event: AstrMessageEvent) -> None:
        """建：在根目录创建文件夹。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 建 [群号] <文件夹名>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可创建文件夹")
            return
        name = " ".join(args).strip()
        if not name:
            yield event.plain_result("用法：/gf 建 <文件夹名>")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        # 创建前查重：同名/忽略大小写同名直接拒绝；名称相近给出提示
        similar = ""
        try:
            root = await qq.root_files(gid)
            pool = {d["name"]: d["folder_id"] for d in norm_folders(root)}
            cands = self._match_folders(pool, name)
            exact = [c for c in cands if c.lower() == name.lower()]
            if exact:
                yield event.plain_result(f"文件夹「{exact[0]}」已存在。要移动文件请用：/gf 移 <序号|关键词> {exact[0]}")
                return
            if cands:
                similar = f"\n（提示：已有名称相近的文件夹 {('、'.join(cands[:5]))}，如想用它请直接 /gf 移 <文件> {cands[0]}）"
        except QQFileError:
            pass  # 拉不到文件夹列表时照常尝试创建
        try:
            fid = await qq.create_folder(gid, name)
        except QQFileError as e:
            yield event.plain_result(f"创建失败：{e}")
            return
        if not fid:
            # 有些协议端成功也不回 folderId：回查根目录确认是否已建好
            try:
                root2 = await qq.root_files(gid)
                fid2 = {d["name"]: d["folder_id"] for d in norm_folders(root2)}.get(name, "")
                if fid2:
                    fid = fid2
            except QQFileError:
                pass
        self.record_event("建文件夹", "info", f"群 {gid} 创建「{name}」")
        self.audit_now(self._uid(event), gid, "建文件夹", name, "ok" if fid else "warn")
        text = f"📂 已创建文件夹「{name}」" if fid else f"创建返回异常（可能已存在「{name}」），可用 /gf 列 确认"
        text += similar
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("整", alias={"整理", "清理", "cleanup"})
    async def cmd_cleanup(self, event: AstrMessageEvent) -> None:
        """整：立即执行整理流水线（分类移动 → 时效清理 → 容量检查）并回报结果。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 整 <群号>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可触发整理")
            return
        qq = self.client_for(event) or self.qq
        if not qq or not qq.available:
            yield event.plain_result("OneBot 客户端未就绪，稍后再试")
            return
        report = self.scheduler._format_full_report(await self.scheduler.run_pipeline(gid, manual=True))
        async for r in self._reply(event, report):
            yield r

    # ---- 群管理（仅管理员） ----
    @groupfile.command("预览", alias={"preview", "试运行"})
    async def cmd_preview(self, event: AstrMessageEvent) -> None:
        """预览：整理/时效清理将影响哪些文件（只报告，不动文件）。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 预览 <群号>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可使用试运行预览")
            return
        qq = self.client_for(event) or self.qq
        if not (qq and qq.available):
            yield event.plain_result("OneBot 客户端未就绪")
            return
        try:
            async with self.group_lock(gid):
                plan = await self.scheduler.preview_cleanup(gid)
                ttl = await self.scheduler.preview_ttl(gid)
        except QQFileError as e:
            yield event.plain_result(f"读取失败：{e}")
            return
        lines = [
            f"👁 群 {gid} 试运行（不会动文件）",
            f"· 分类：共 {plan.get('total', 0)} 个文件；将移动 {len(plan.get('planned') or [])} 个；"
            f"已在目标 {plan.get('in_place', 0)}；无规则 {plan.get('no_match', 0)}",
        ]
        for p in (plan.get("planned") or [])[:5]:
            lines.append(f"  → {p.get('name')}：{p.get('from')} → {p.get('to')}")
        if ttl.get("enabled"):
            lines.append(
                f"· 时效：超龄 {ttl.get('over', 0)} 个；无时间 {ttl.get('unknown_time', 0)} 个"
                + ("（扫描已截断）" if ttl.get("truncated") else "")
            )
            for p in (ttl.get("planned") or [])[:5]:
                lines.append(
                    f"  {p.get('action')} {p.get('name')}（{p.get('age_days')} 天 / 阈值 {p.get('days')} 天）"
                )
        else:
            lines.append("· 时效：未启用")
        async for r in self._reply(event, "\n".join(lines)):
            yield r

    @groupfile.command("绑", alias={"绑定", "bind"})
    async def cmd_bind(self, event: AstrMessageEvent) -> None:
        """绑：把当前群（或指定群号）纳入管理，可跟备注名。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("用法：群聊中 /gf 绑 [备注名]；私聊中 /gf 绑 <群号> [备注名]")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可绑定群")
            return
        name = " ".join(args).strip()
        self.store.upsert_group(gid, {"name": name} if name else {})
        self.record_event("绑定", "info", f"群 {gid} 已绑定" + (f"（备注 {name}）" if name else ""))
        text = f"✅ 已绑定群 {gid}" + (f"（备注：{name}）" if name else "")
        text += "\n分类规则/定时整理/时效清理在 WebUI 配置：AstrBot 面板 → 插件 → 群文件管理"
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("解", alias={"解绑", "unbind"})
    async def cmd_unbind(self, event: AstrMessageEvent) -> None:
        """解：解绑群（两段式确认，配置将被删除）。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 解 <群号>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可解绑")
            return
        if str(gid) not in self.store.groups:
            yield event.plain_result("该群未绑定，无需解绑")
            return
        self._pending[self._pending_key(event, str(gid))] = {"action": "unbind", "gid": str(gid), "ts": time.time()}
        text = f"⚠ 解绑将删除群 {gid} 的全部配置（规则/定时/时效/管理员）。\n60 秒内 /gf 确认 执行；/gf 取消 放弃"
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("开", alias={"启用", "on"})
    async def cmd_enable(self, event: AstrMessageEvent) -> None:
        """开：启用本群（恢复定时整理等自动动作）。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 开 <群号>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可操作")
            return
        self.store.upsert_group(gid, {"enabled": True})
        self.record_event("启用", "info", f"群 {gid} 已启用")
        async for r in self._reply(event, f"✅ 群 {gid} 已启用（定时整理/时效清理/容量播报恢复运行）"):
            yield r

    @groupfile.command("关", alias={"停用", "off"})
    async def cmd_disable(self, event: AstrMessageEvent) -> None:
        """关：停用本群（暂停自动动作，手动命令仍可用）。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请指定：/gf 关 <群号>")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可操作")
            return
        self.store.upsert_group(gid, {"enabled": False})
        self.record_event("停用", "info", f"群 {gid} 已停用")
        async for r in self._reply(event, f"⏸ 群 {gid} 已停用（定时/时效/容量播报暂停；手动命令仍可用）"):
            yield r

    @groupfile.command("管", alias={"管理员", "admin"})
    async def cmd_admin(self, event: AstrMessageEvent) -> None:
        """管：查看/添加/移除该群管理员。@某人 = 添加；管 删 @某人 = 移除。"""
        gid, args = self._parse_args(event)
        if not gid:
            yield event.plain_result("群聊中使用：/gf 管 @某人（添加）/gf 管 删 @某人（移除）")
            return
        if not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可管理该群的管理员名单")
            return
        at_ids: list[str] = []
        try:
            for seg in (event.message_obj.message or []):
                if isinstance(seg, Comp.At):
                    qq = str(getattr(seg, "qq", "") or "")
                    if qq:
                        at_ids.append(qq)
        except Exception:
            pass
        remove_mode = bool(args) and args[0] in ("删", "移除", "del", "-")
        text_ids = [a for a in (args[1:] if remove_mode else args) if a.isdigit() and len(a) >= 5]
        targets = list(dict.fromkeys(at_ids + text_ids))
        admins = [str(a) for a in (self.store.get_group(gid).get("admins") or [])]
        if not targets:
            if remove_mode:
                yield event.plain_result("用法：/gf 管 删 @某人（或 /gf 管 删 <QQ 号>）")
                return
            cur = "、".join(admins) if admins else "（空，仅机器人主人/全局管理员）"
            async for r in self._reply(event, f"👑 该群管理员：{cur}\n添加：/gf 管 @某人；移除：/gf 管 删 @某人"):
                yield r
            return
        if remove_mode:
            admins = [a for a in admins if a not in targets]
            self.store.upsert_group(gid, {"admins": admins})
            text = f"👑 已移除群管理员：{'、'.join(targets)}（现为 {'、'.join(admins) if admins else '空'}）"
        else:
            admins = list(dict.fromkeys(admins + targets))
            self.store.upsert_group(gid, {"admins": admins})
            text = f"👑 已添加群管理员：{'、'.join(targets)}"
        self.record_event("管理员", "info", f"群 {gid} 管理员名单更新（{'-' if remove_mode else '+'}{len(targets)}）")
        async for r in self._reply(event, text):
            yield r

    # ---- 两段式确认 ----
    @groupfile.command("确认", alias={"yes", "ok"})
    async def cmd_confirm(self, event: AstrMessageEvent) -> None:
        """确认：执行待确认操作（删/解）；必须在本群确认。"""
        gid, _ = self._parse_args(event)
        if not gid:
            yield event.plain_result("无法确定群号，请在本群回复 /gf 确认（私聊请带群号）")
            return
        key = self._pending_key(event, gid)
        p = self._pending.get(key)
        if not p or time.time() - p.get("ts", 0) > PENDING_TTL:
            self._pending.pop(key, None)
            yield event.plain_result("没有待确认的操作（60 秒内有效，且需在同一群确认）")
            return
        action, pgid = p.get("action"), p.get("gid")
        try:
            if action == "delete":
                if not self._is_admin_user(event, pgid):
                    yield event.plain_result("权限已变化，取消本次删除")
                    return
                qq = self.client_for(event) or self.qq
                if not (qq and qq.available):
                    yield event.plain_result("OneBot 客户端未就绪，删除未执行")
                    return
                ok, fail, errs = 0, 0, []
                async with self.group_lock(pgid):
                    for t in p.get("targets") or []:
                        try:
                            await qq.delete_file(
                                pgid,
                                t.get("folder_id") or None,
                                str(t["file_id"]),
                                busid=t.get("busid") or 0,
                            )
                            ok += 1
                            self.audit_now(self._uid(event), pgid, "删除", str(t.get("name") or t.get("file_id")), "ok")
                        except Exception as e:
                            fail += 1
                            errs.append(f"{t.get('name')}: {e}")
                            self.audit_now(self._uid(event), pgid, "删除", str(t.get("name") or ""), "fail", str(e))
                self._invalidate_listing(pgid)
                text = f"🗑 删除完成：成功 {ok} 个，失败 {fail} 个"
                if errs:
                    text += "\n" + "\n".join(errs[:5])
                self.record_event("删除", "info" if not fail else "warn", f"群 {pgid} 命令删除 {ok}/{ok + fail}")
            elif action == "unbind":
                if not self._is_admin_user(event, pgid):
                    yield event.plain_result("权限已变化，取消本次解绑")
                    return
                self.store.remove_group(pgid)
                self._invalidate_listing(pgid)
                text = f"✅ 已解绑群 {pgid}（配置已删除）"
                self.record_event("解绑", "info", f"群 {pgid} 已解绑")
                self.audit_now(self._uid(event), pgid, "解绑", pgid, "ok")
            else:
                text = "未知操作类型，已取消"
        finally:
            self._pending.pop(key, None)
        async for r in self._reply(event, text):
            yield r

    @groupfile.command("取消", alias={"no", "cancel"})
    async def cmd_cancel(self, event: AstrMessageEvent) -> None:
        """取消：放弃本群的待确认操作。"""
        gid, _ = self._parse_args(event)
        key = self._pending_key(event, gid) if gid else ""
        if key and self._pending.pop(key, None):
            yield event.plain_result("已取消本次操作")
        else:
            yield event.plain_result("没有待确认的操作")

    # ---- 运维查询（v2.9.0：只保留审计命令） ----
    @groupfile.command("审计", alias={"audit", "日志"})
    async def cmd_audit(self, event: AstrMessageEvent) -> None:
        """审计：查看本群最近操作记录（仅管理员）。"""
        gid, args = self._parse_args(event)
        if not gid or not self._is_admin_user(event, gid):
            yield event.plain_result("仅管理员可查看审计日志")
            return
        n = int(args[0]) if args and args[0].isdigit() else 10
        rows = self.audit.read(min(max(n, 1), 50), gid)
        if not rows:
            yield event.plain_result("暂无审计记录（或审计已关闭）。")
            return
        lines = [f"📜 群 {gid} 最近 {len(rows)} 条审计："]
        for r_ in rows:
            mark = "✅" if r_.get("result") == "ok" else "⚠"
            lines.append(f"{mark} [{r_.get('t')}] {r_.get('actor')} {r_.get('action')} {r_.get('target')} {r_.get('detail')}")
        async for x in self._reply(event, "\n".join(lines)):
            yield x

    # ---- 内部工具 ----
    def _parse_args(self, event: AstrMessageEvent) -> tuple[str, list[str]]:
        """解析 /gf 子命令剩余参数，返回 (当前群号, 参数列表)。"""
        text = (event.message_str or "").strip()
        parts = [p for p in text.replace("/", " ", 1).split() if p.strip()]
        args = parts[2:]
        gid = str(event.message_obj.group_id or "") if event.message_obj else ""
        # 仅在私聊（无当前群上下文）时，才把开头的长数字当群号；
        # 群聊里这样做会把纯数字关键词（如 QQ 号）误吞成群号。
        if not gid and args and args[0].isdigit() and len(args[0]) >= 5:
            gid = args.pop(0)
        return gid, args

    async def _reply(self, event: AstrMessageEvent, text: str):
        """统一回复（async 生成器）。
        v2.1.1：默认走事件链（管道）——消息打包类插件（forward_pack）
        能把命令输出聚合成合并转发卡片；原直发加速改为配置开关
        direct_reply=true 时启用（绕过管道、不进聚合卡，回复快几毫秒）。
        直发失败仍回退事件链，绝不让回复丢失。
        用法：async for r in self._reply(event, text): yield r"""
        if bool(self.config.get("direct_reply", False)):
            if await self._direct_send(event, text):
                return
        yield event.chain_result([Comp.Plain(text)])

    async def _download_file(
        self,
        gid: str,
        f: dict,
        client: QQFileClient | None = None,
        progress=None,
    ) -> tuple[Path | None, str]:
        """下载群文件到「发送临时目录」（流式下载 + 文件名消毒 + 大小上限）。

        v2.8.0：可按事件平台传 client；progress(pct,msg) 供任务中心显示进度。
        """
        qq = client or self.qq
        if not (qq and qq.available):
            return None, "OneBot 客户端未就绪"
        dest = None
        try:
            url = await qq.file_download_url(gid, str(f["file_id"]), int(f.get("busid") or 0))
            if not url:
                return None, "获取下载链接失败（get_group_file_url 返回空）"
            # 文件名消毒：只保留基本名，剥掉任何路径成分
            name = Path(str(f.get("name") or "file")).name or "file"
            dest_dir = self.ensure_send_dir()
            dest = dest_dir / f"{int(time.time() * 1000)}_{name}"
            import httpx

            size = 0
            mark = 0
            if progress:
                progress(5, f"下载「{name}」")
            async with httpx.AsyncClient(follow_redirects=True, timeout=120) as hc:
                async with hc.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with dest.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(64 * 1024):
                            size += len(chunk)
                            if size > SEND_MAX_BYTES:
                                raise ValueError(
                                    f"文件超过发送上限（{SEND_MAX_BYTES / 1024 / 1024:.0f}MB）"
                                )
                            fh.write(chunk)
                            if progress and size - mark >= 5 * 1024 * 1024:
                                mark = size
                                progress(min(80, 10 + size // (10 * 1024 * 1024)), f"已下载 {size / 1024 / 1024:.0f}MB")
            self._ensure_readable(dest)
            if progress:
                progress(85, "下载完成，准备发送")
            return dest, ""
        except Exception as e:
            if dest is not None:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
            return None, str(e)

    @staticmethod
    def _fmt_size(size: int) -> str:
        size = int(size or 0)
        if size >= GB:
            return f"{size/GB:.2f}GB"
        if size >= 1024 * 1024:
            return f"{size/1024/1024:.1f}MB"
        return f"{size/1024:.0f}KB"

    # ================= 事件钩子 =================
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        """缓存群会话 umo，供定时任务/WebUI 主动推送使用。"""
        try:
            gid = str(event.message_obj.group_id or "")
            if gid:
                self.umo_cache[gid] = event.unified_msg_origin
        except Exception:
            pass

    # ================= Plugin Pages 后端 API =================
    @staticmethod
    def _q(name: str, default: str = "") -> str:
        from astrbot.api.web import request

        return str(request.query.get(name) or default)

    async def api_status(self):
        from astrbot.api.web import json_response

        return json_response(
            {
                "ok": True,
                "version": VERSION,
                "plugin_enabled": self.plugin_enabled,
                "bot_bound": bool(self.qq and self.qq.available),
                "admin_users": self.store.get_admin_users()
                or (self.config.get("admin_users") or []),
                "groups": len(self.store.group_ids()),
                "umi": list(self.umo_cache.keys()),
                "send_tmp": self.send_dir_info(),
                "platform_id": self.platform_id,
                "audit": self.audit.stats(),
            }
        )

    async def api_admin_users_get(self):
        from astrbot.api.web import json_response

        return json_response(
            {"admins": self.store.get_admin_users() or (self.config.get("admin_users") or [])}
        )

    async def api_admin_users_set(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        raw = payload.get("admins")
        if raw is None:
            return error_response("缺少 admins", status_code=400)
        if isinstance(raw, str):
            raw = [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]
        self.store.set_admin_users([str(a).strip() for a in raw if str(a).strip()])
        return json_response({"saved": True, "admins": self.store.get_admin_users()})

    async def api_groups_get(self):
        from astrbot.api.web import json_response

        # available：bot 实际所在的群（从 OneBot 拉取），供「管理范围」勾选列表与下拉使用
        available: list[dict] = []
        if self.qq and self.qq.available:
            try:
                lst = await self.qq.call("get_group_list")
                if isinstance(lst, list):
                    available = [
                        {
                            "group_id": str(g.get("group_id", "")),
                            "group_name": str(g.get("group_name", "") or g.get("group_id", "")),
                            "member_count": g.get("member_count"),
                        }
                        for g in lst
                        if g.get("group_id")
                    ]
            except Exception as e:
                logger.warning(f"[群配置] 拉取 bot 群列表失败: {e}")
        return json_response({"groups": self.store.list_groups(), "available": available})

    async def api_groups_upsert(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        patch = {}
        for k in ("name", "enabled", "admins", "rules", "cleanup", "limits", "dupe_rule", "ttl"):
            if k in payload:
                patch[k] = payload[k]
        cfg = self.store.upsert_group(gid, patch)
        return json_response(
            {"saved": True, "group": dict(cfg, group_id=gid), "warnings": self.store.last_warnings}
        )

    async def api_group_delete(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        removed = self.store.remove_group(gid)
        return json_response({"removed": removed})

    async def api_rules_get(self):
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        return json_response({"rules": self.store.rules(gid)})

    async def api_rules_set(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        rules = payload.get("rules")
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not isinstance(rules, list):
            return error_response("rules 必须为列表", status_code=400)
        # 保存前自动创建缺失的目标文件夹：规则指向的文件夹若群里还没有，先创建；失败给明确错误
        if self.qq and self.qq.available:
            try:
                root = await self.qq.root_files(gid)
                exist = {fd["name"] for fd in norm_folders(root)}
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    target = str(r.get("folder") or "").strip()
                    if target and target not in exist:
                        try:
                            fid = await self.qq.create_folder(gid, target)
                        except QQFileError as e:
                            return error_response(f"目标文件夹「{target}」创建失败: {e}", status_code=502)
                        if fid:
                            exist.add(target)
            except QQFileError:
                pass  # 拉不到文件夹列表时跳过校验（整理时兜底）
        # 保存前体检：空目标文件夹的规则不会生效，返回警告提醒（不阻断保存）
        warns: list[str] = []
        seen: dict[tuple, str] = {}
        fallbacks: list[str] = []
        for idx, r in enumerate(rules, 1):
            if not isinstance(r, dict):
                continue
            label = str(r.get("source") or f"第{idx}条").strip() or f"第{idx}条"
            if not str(r.get("folder") or "").strip():
                warns.append(f"「{label}」未设置目标文件夹，该条不会移动任何文件")
            exts = r.get("exts")
            if exts not in (None, "", [], {}):
                clean_exts = normalize_exts(exts)
                if not clean_exts:
                    warns.append(f"「{label}」文件类型「{exts}」无法识别，已忽略（示例：cbz 或 cbz,cbr）")
                elif isinstance(exts, str):
                    r["exts"] = clean_exts  # 顺手把 "cbz,cbr" 写法转成列表
            # v2.7.0：规则冲突体检（只提示，不阻断）
            pat = str(r.get("pattern") or "").strip()
            key = (
                str(r.get("kind") or "").strip().lower(),
                pat,
                str(r.get("folder") or "").strip(),
                tuple(normalize_exts(r.get("exts"))),
            )
            if key in seen:
                warns.append(f"「{label}」与「{seen[key]}」条件完全相同，重复规则不会多分一次类")
            else:
                seen[key] = label
            if not pat:
                fallbacks.append(label)
        if len(fallbacks) > 1:
            warns.append(
                "有 " + str(len(fallbacks)) + " 条兜底规则（匹配留空：" + "、".join(fallbacks[:5])
                + "）：同一文件只会按「类型更具体」的那条走，注意别互相抢"
            )
        try:
            self.store.upsert_group(gid, {"rules": rules})
        except Exception as e:
            return error_response(f"写入规则失败: {e}", status_code=500)
        return json_response({"saved": True, "rules": self.store.rules(gid), "warnings": warns})

    async def api_rule_test(self):
        """试一试：输入若干文件名，返回每个会被哪条规则分到哪个文件夹（含跳过原因）。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        names = payload.get("filenames")
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not isinstance(names, list):
            return error_response("filenames 必须为列表", status_code=400)
        results = []
        for n in names[:50]:  # 单次最多 50 个
            n = str(n or "").strip()
            if not n:
                continue
            results.append(self.store.explain_rule(gid, n))
        return json_response({
            "results": results,
            "rules_total": len(self.store.rules(gid)),
        })

    async def api_cleanup_preview(self):
        """预览整理：按当前规则算一遍谁会被移动到哪，不执行任何动作。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            result = await self.scheduler.preview_cleanup(gid)
        except QQFileError as e:
            return error_response(f"读取群文件失败: {e}", status_code=502)
        return json_response(result)

    async def api_ttl_preview(self):
        """试运行时效清理：只算哪些文件会被回收/删除，不动文件。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            async with self.group_lock(gid):
                result = await self.scheduler.preview_ttl(gid)
        except QQFileError as e:
            return error_response(f"读取群文件失败: {e}", status_code=502)
        return json_response(result)

    async def _capacity_check(self, gid: str) -> dict[str, Any]:
        """容量检查共用逻辑（WebUI「立即检查一次」/旧版报告用）：文件数 / 占用 / 告警。"""
        info = await self.qq.file_system_info(gid)
        file_count = int(info.get("file_count") or 0)
        used_gb = int(info.get("used_space") or 0) / GB
        total_gb = int(info.get("total_space") or 0) / GB
        limits = (self.store.get_group(gid) or {}).get("limits") or {}
        try:
            pct = float(limits.get("warn_pct") or 100) / 100.0
        except (TypeError, ValueError):
            pct = 1.0
        warns: list[str] = []
        cl = int(limits.get("count_limit") or 0)
        sl = float(limits.get("space_limit_gb") or 0)
        if cl and file_count >= cl * pct:
            warns.append(f"文件数 {file_count} 已达上限 {cl} 的 {pct*100:.0f}%")
        if sl and used_gb >= sl * pct:
            warns.append(f"占用 {used_gb:.2f}GB 已达上限 {sl:g}GB 的 {pct*100:.0f}%")
        return {
            "file_count": file_count,
            "used_gb": round(used_gb, 2),
            "total_gb": round(total_gb, 2),
            "warnings": warns,
            "count_limit": cl,
            "space_limit_gb": sl,
        }

    async def api_check_limit(self):
        """手动检查容量（纯主动触发）：返回当前文件数/占用/是否超限，不自动发群消息。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            d = await self._capacity_check(gid)
        except QQFileError as e:
            return error_response(f"读取容量失败: {e}", status_code=400)
        return json_response({
            "file_count": d["file_count"],
            "used_gb": d["used_gb"],
            "total_gb": d["total_gb"],
            "warnings": d["warnings"],
        })

    async def api_send_tmp_get(self):
        """发送临时文件策略（全局）：当前目录、是否共享、保留秒数、残留小时、清理开关。"""
        from astrbot.api.web import json_response

        return json_response(
            {
                **self.send_dir_info(),
                "limits": {
                    "keep_seconds": [MIN_KEEP_SECONDS, MAX_KEEP_SECONDS],
                    "orphan_hours": [MIN_ORPHAN_HOURS, MAX_ORPHAN_HOURS],
                },
            }
        )

    async def api_send_tmp_set(self):
        """保存发送临时文件策略；目录留空 = 默认共享 temp，非法相对路径会被忽略。"""
        from astrbot.api.web import json_response, request

        payload = await request.json(default={})
        patch: dict[str, Any] = {}
        if "enabled" in payload:
            patch["enabled"] = bool(payload.get("enabled"))
        if "keep_seconds" in payload:
            patch["keep_seconds"] = payload.get("keep_seconds")
        if "orphan_hours" in payload:
            patch["orphan_hours"] = payload.get("orphan_hours")
        if "dir" in payload:
            patch["dir"] = str(payload.get("dir") or "").strip()
        self.store.set_send_tmp(patch)
        self.ensure_send_dir()
        self.record_event(
            "发送设置", "info",
            f"发送目录 {self.send_dir()}；保留 {self.send_settings()['keep_seconds']}s",
        )
        return json_response({"saved": True, "settings": self.send_dir_info()})

    async def api_send_tmp_sweep(self):
        """立即清扫发送目录（只删超过残留阈值的文件，发送中的跳过）。"""
        from astrbot.api.web import json_response

        info = await self.cleanup_sweep(force=True)
        return json_response({"ok": True, **info, "state": self.send_dir_info()})

    async def api_files(self):
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        folder_id = self._q("folder_id") or None
        page = vint(self._q("page", "1"), 1, 1, 10000)
        page_size = vint(self._q("page_size", "50"), 50, 10, 200)
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            data = await list_folder(self.qq, gid, folder_id or "")
            all_folders = data["folders"]
            if folder_id:
                all_folders = norm_folders(await self.qq.root_files(gid))
        except QQFileError as e:
            return error_response(str(e), status_code=502)
        files = data["files"]
        total = len(files)
        start = (page - 1) * page_size
        return json_response(
            {
                "files": files[start : start + page_size],
                "folders": data["folders"],
                "all_folders": all_folders,
                "folder_id": folder_id or "",
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": max(1, (total + page_size - 1) // page_size),
            }
        )

    async def api_delete(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not self.qq:
            return error_response("OneBot 客户端未就绪", status_code=503)
        folder_id = payload.get("folder_id") or None
        file_ids = payload.get("file_ids") or []
        items = payload.get("items") or []  # [{file_id, folder_id, busid, name}]
        all_in_folder = bool(payload.get("all_in_folder"))
        if all_in_folder:
            try:
                if folder_id:
                    raw = await self.qq.files_by_folder(gid, folder_id)
                else:
                    raw = await self.qq.root_files(gid)
                file_ids = [f["file_id"] for f in norm_files(raw)]
            except QQFileError as e:
                return error_response(str(e), status_code=502)
        # 统一成 (file_id, folder_id, busid, name)
        targets = []
        for it in items:
            if isinstance(it, dict) and it.get("file_id"):
                targets.append((str(it["file_id"]), it.get("folder_id") or None,
                                it.get("busid"), str(it.get("name") or it["file_id"])))
        for fid in file_ids:
            targets.append((str(fid), folder_id, None, str(fid)))
        if not targets:
            return error_response("没有要删除的文件", status_code=400)
        ok, fail = 0, 0
        details = []
        async with self.group_lock(gid):
            for fid, f_folder, f_busid, f_name in targets:
                try:
                    await self.qq.delete_file(gid, f_folder, fid, busid=f_busid)
                    ok += 1
                    details.append({"file_id": fid, "name": f_name, "ok": True})
                    self.audit_now("panel", gid, "删除", f_name, "ok")
                except Exception as e:
                    fail += 1
                    logger.warning(f"[WebUI删除] 群 {gid} 文件 {f_name}({fid}) 删除失败: {e}")
                    details.append({"file_id": fid, "name": f_name, "ok": False, "err": str(e)})
                    self.audit_now("panel", gid, "删除", f_name, "fail", str(e))
        return json_response({"deleted": ok, "failed": fail, "details": details})

    async def api_move(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not self.qq:
            return error_response("OneBot 客户端未就绪", status_code=503)
        file_ids = payload.get("file_ids") or []
        target_name = str(payload.get("target_folder_name") or "").strip()
        target_id = str(payload.get("target_folder_id") or "").strip() or None
        current_id = str(payload.get("current_folder_id") or "").strip() or None
        if not file_ids or not (target_name or target_id):
            return error_response("缺少 file_ids 或 target_folder_name", status_code=400)
        ok, fail = 0, 0
        errs: list[str] = []
        async with self.group_lock(gid):
            for fid in file_ids:
                try:
                    await self.qq.move_file(
                        gid,
                        str(fid),
                        target_name or target_id,
                        target_id=target_id,
                        current_id=current_id,
                    )
                    ok += 1
                except QQFileError as e:
                    fail += 1
                    errs.append(str(e))
        self.audit_now("panel", gid, "移动", f"{ok} 个文件", "ok" if not fail else "warn",
                       f"→ {target_name or target_id}" + (f"；失败 {fail}" if fail else ""))
        return json_response({"moved": ok, "failed": fail, "errors": errs[:5]})

    async def api_members(self):
        """返回该群成员列表，供管理员下拉选择（不用手输 QQ）。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=400)
        try:
            raw = await self.qq.call("get_group_member_list", group_id=gid)
        except Exception as e:
            return error_response(f"拉取群成员失败: {e}", status_code=400)
        members = []
        if isinstance(raw, list):
            for m in raw:
                uid = str(m.get("user_id", "") or "")
                if not uid:
                    continue
                name = str(m.get("card") or m.get("nickname") or m.get("user_id") or "")
                members.append({"user_id": uid, "name": name})
        return json_response({"members": members})

    async def _collect_files(self, gid: str, client: QQFileClient | None = None) -> list[dict]:
        """递归收集（v2.8.0 统一走 scanner，带深度/数量/环保护）。"""
        res = await walk_files(client or self.qq, gid)
        self.last_scan = {
            "truncated": res.get("truncated", False),
            "errors": res.get("errors") or [],
            "folders_seen": res.get("folders_seen", 0),
            "files": len(res.get("files") or []),
        }
        return res.get("files") or []

    async def api_scan(self):
        """扫描群文件：递归根目录 + 所有层级子文件夹，供前端生成分类规则。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=400)
        try:
            all_files = await self._collect_files(gid)
            root = await self.qq.root_files(gid)
        except QQFileError as e:
            return error_response(f"读取群文件失败: {e}", status_code=400)
        folders = norm_folders(root)
        by_folder: dict[str, list[dict]] = {}
        for f in all_files:
            by_folder.setdefault(f["folder"], []).append(
                {
                    "name": f["name"],
                    "size": int(f.get("size") or 0),
                    "file_id": str(f["file_id"]),
                    "folder_id": f.get("folder_id") or "",
                    "busid": int(f.get("busid") or 0),
                }
            )
        def _depth(label: str) -> int:
            if label in ("", "根目录"):
                return 0
            return str(label).count("/")
        groups = [
            {"source": label, "folder_id": "", "depth": _depth(label), "files": names}
            for label, names in by_folder.items()
        ]
        # 根目录在前，子文件夹按层级/名称排序，便于看全所有位置
        groups.sort(key=lambda g: (g["depth"], g["source"]))
        return json_response({"groups": groups, "folders": [f["name"] for f in folders]})

    async def api_export(self):
        """导出单个群的完整配置（规则/定时/容量/管理员），供下载备份或迁移到其他群。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        cfg = self.store.get_group(gid)
        return json_response(
            {
                "ok": True,
                "data": {
                    "group_id": gid,
                    "name": cfg.get("name", ""),
                    "admins": cfg.get("admins", []),
                    "rules": self.store.rules(gid),
                    "cleanup": cfg.get("cleanup", {}),
                    "limits": cfg.get("limits", {}),
                    "ttl": cfg.get("ttl", {}),           # v2.1.2：时效清理随导出
                    "dupe_rule": cfg.get("dupe_rule", {}),  # v2.1.2：查重规则随导出
                    "export_version": VERSION,
                },
            }
        )

    async def api_import(self):
        """导入群配置（覆盖目标群的规则/定时/容量/查重规则/管理员）。
        兼容三种文件：单群 {group_id,rules,...}、包一层 {ok,data:{...}}、全量 groups.json {groups:{gid:{...}}}。
        目标群优先用前端传入的 target_gid（在哪个群卡片导入就进哪个群），其次文件内 group_id。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        target = str(payload.get("target_gid", "") or "").strip()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        # 兼容 {ok, data: {...}}
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and isinstance(data["data"].get("rules"), list):
            data = data["data"]
        # 兼容全量 groups.json：{groups: {gid: {...}}}
        if isinstance(data, dict) and isinstance(data.get("groups"), dict):
            grp = data["groups"].get(target) if target else None
            if not isinstance(grp, dict):
                grp = next(iter(data["groups"].values()), {})
            data = grp if isinstance(grp, dict) else {}
        if not isinstance(data, dict):
            return error_response("导入内容不是有效的配置对象", status_code=400)

        gid = target or str(data.get("group_id", "") or "").strip()
        if not gid:
            return error_response("导入文件缺少 group_id，且未指定目标群", status_code=400)

        patch = self._clean_group_patch(data)
        if not patch:
            return error_response("导入文件没有可识别的配置内容（需要 rules/cleanup/limits/admins 等）", status_code=400)
        try:
            self.store.upsert_group(gid, patch)
        except Exception as e:
            return error_response(f"写入配置失败: {e}", status_code=500)
        return json_response({"ok": True, "saved": gid, "fields": sorted(patch.keys())})

    @staticmethod
    def _clean_group_patch(data: dict) -> dict:
        """从导出数据提取可导入字段（单群），剥离废弃字段。"""
        patch: dict = {}
        if isinstance(data.get("name"), str):
            patch["name"] = data["name"]
        if isinstance(data.get("admins"), list):
            patch["admins"] = [str(a) for a in data["admins"]]
        if isinstance(data.get("rules"), list):
            patch["rules"] = data["rules"]
        if isinstance(data.get("cleanup"), dict):
            cl = dict(data["cleanup"])
            cl.pop("auto_check", None)  # 剥离已废弃字段
            cl.pop("move_only", None)   # v2.7.0 移除的空头开关
            cl.pop("delete_empty", None)
            patch["cleanup"] = cl
        if isinstance(data.get("limits"), dict):
            lm = dict(data["limits"])
            lm.pop("auto_check", None)      # 废弃：到点自检
            lm.pop("limit_crones", None)   # 废弃：容量检查时间点
            lm.pop("report_used", None)    # v2.7.0 移除的空头开关
            patch["limits"] = lm
        if isinstance(data.get("dupe_rule"), dict):
            patch["dupe_rule"] = data["dupe_rule"]
        if isinstance(data.get("ttl"), dict):
            patch["ttl"] = data["ttl"]
        return patch

    async def api_export_all(self):
        """导出全部群的完整配置（全局备份：规则/定时/容量/查重/管理员/备注名）。
        v2.3.6：groups 顶层 + data.groups 双写，任何一层解包都能拿到。"""
        from astrbot.api.web import json_response

        out: dict = {}
        for gid in self.store.group_ids():
            cfg = self.store.get_group(gid)
            out[str(gid)] = {
                "group_id": str(gid),
                "name": cfg.get("name", ""),
                "admins": cfg.get("admins", []),
                "rules": self.store.rules(gid),
                "cleanup": cfg.get("cleanup", {}),
                "limits": cfg.get("limits", {}),
                "ttl": cfg.get("ttl", {}),
                "dupe_rule": cfg.get("dupe_rule", {}),
            }
        body = {
            "ok": True,
            "app": "groupfile-console",
            "kind": "global-backup",
            "export_version": VERSION,
            "groups": out,
            "data": {
                "app": "groupfile-console",
                "kind": "global-backup",
                "export_version": VERSION,
                "groups": out,
            },
        }
        return json_response(body)

    async def api_import_all(self):
        """导入全局备份：{groups:{群号:{完整配置}}}，按群号逐群覆盖合并（已有群覆盖、新群自动创建）。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if isinstance(data, dict) and isinstance(data.get("data"), dict) and isinstance(data["data"].get("groups"), dict):
            data = data["data"]  # 兼容 {ok, data:{groups:{...}}} 双层包装
        groups = data.get("groups") if isinstance(data, dict) and isinstance(data.get("groups"), dict) else None
        if not groups:
            return error_response("导入内容缺少 groups（需要全局备份格式 {groups:{群号:{…}}}）", status_code=400)

        saved, skipped = [], []
        for gid, grp in groups.items():
            gid = str(gid).strip()
            if not gid or not isinstance(grp, dict):
                continue
            # 目标群优先取备份里的 group_id 字段
            inner_gid = str(grp.get("group_id", "") or "").strip()
            target = inner_gid or gid
            patch = self._clean_group_patch(grp)
            if not patch:
                skipped.append(gid)
                continue
            try:
                self.store.upsert_group(target, patch)
                saved.append(target)
            except Exception:
                skipped.append(gid)
        if not saved:
            return error_response("没有可导入的群配置（" + (f"{len(skipped)} 个群数据无效，" if skipped else "") + "需要 rules/cleanup/limits/admins 等字段）", status_code=400)
        return json_response({"ok": True, "saved": saved, "skipped": skipped, "count": len(saved)})

    async def api_search(self):
        """按文件名关键词搜索群文件（递归）。GET gid + kw。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        kw = self._q("kw", "").strip()
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not kw:
            return error_response("缺少关键词 kw", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=400)
        try:
            hits = [f for f in await self._collect_files(gid) if kw.lower() in str(f["name"]).lower()]
        except QQFileError as e:
            return error_response(f"读取群文件失败: {e}", status_code=400)
        return json_response(
            {
                "total": len(hits),
                "results": [
                    {
                        "name": f["name"],
                        "size": int(f.get("size") or 0),
                        "folder": f["folder"],
                        "folder_id": f.get("folder_id") or "",
                        "file_id": str(f["file_id"]),
                        "busid": int(f.get("busid") or 0),
                    }
                    for f in hits
                ],
            }
        )

    async def api_send_file(self):
        """把群文件下载后发送到群。POST {group_id, file_id, folder_id, busid}。"""
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or "").strip()
        file_id = str(payload.get("file_id", "") or "").strip()
        if not gid or not file_id:
            return error_response("缺少 group_id 或 file_id", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=400)
        # 从收集结果里取该文件的名称
        f = {
            "file_id": file_id,
            "folder_id": payload.get("folder_id") or "",
            "busid": int(payload.get("busid") or 0),
            "name": payload.get("name") or "file",
        }
        dest: Path | None = None
        try:
            dest, err = await self._download_file(gid, f)
            if dest is None:
                return error_response(err, status_code=502)
            # WebUI 触发无 event 对象，不经过命令管道，保持直发
            fname = Path(str(f["name"] or "file")).name or "file"
            self._ensure_readable(dest)
            self._mark_inflight(dest)
            try:
                await asyncio.wait_for(
                    self.qq.call(
                        "send_group_msg",
                        group_id=int(gid),
                        message=[{"type": "file", "data": {"file": str(dest), "name": fname}}],
                    ),
                    timeout=120,
                )
            finally:
                self._unmark_inflight(dest)
        except Exception as e:
            # 失败兜底：产物按保留策略延迟清理，绝不永久残留
            if dest is not None:
                self._schedule_cleanup([dest], 60)
            return error_response(f"发送失败: {e}", status_code=502)
        # 成功：协议端已上传完，立刻删；失败路径已在上面兜底
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        return json_response({"ok": True, "name": fname})

    async def api_dupes(self):
        """查重：遍历根目录+各文件夹，按（文件名+大小）分组，返回重复组（组内按上传时间升序）。"""
        from astrbot.api.web import error_response, json_response

        gid = self._q("gid")
        if not gid:
            return error_response("缺少 gid", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=400)
        try:
            dupes = await self._find_dupe_groups(gid)
        except QQFileError as e:
            return error_response(f"读取群文件失败: {e}", status_code=400)
        except Exception as e:
            import traceback
            logger.error("[查重] 内部异常:\n" + traceback.format_exc())
            return error_response(f"查重内部错误 {type(e).__name__}: {e}", status_code=500)
        return json_response({"groups": dupes})

    @staticmethod
    def _norm_key(name: str) -> str:
        """文件名归一化：去扩展名 -> 去 [xxx] 括号内容 -> 循环去末尾 _8位以上数字（时间戳/序号）。"""
        base = str(name).rsplit(".", 1)[0].strip()
        base = re.sub(r"\[[^\]]*\]", "", base)
        while True:
            m = re.search(r"_\d{8,}$", base)
            if not m:
                break
            base = base[: m.start()]
        return base.strip().lower()

    def _dupe_key(self, gid: str, name: str) -> str:
        """按该群「查重规则」dupe_rule 生成查重 key：
        - smart（默认）：智能归一化（去扩展名/[括号]/末尾时间戳）
        - split：按分隔符切分取第 part 段（分隔符前相同即重复，如 X_短篇 与 X_长篇）
        - regex：正则提取第 1 个括号分组
        - exact：去扩展名后完全同名才算
        任何规则异常都安全回退到原名，绝不抛错。"""
        base = str(name).rsplit(".", 1)[0].strip()
        try:
            rule = (self.store.get_group(gid) or {}).get("dupe_rule") or {}
            mode = str(rule.get("mode") or "smart")
            if mode == "exact":
                return base.lower()
            if mode == "split":
                sep = str(rule.get("sep") or "_") or "_"
                seg = base.split(sep)
                try:
                    part = int(rule.get("part", 0))
                except (TypeError, ValueError):
                    part = 0
                if part < 0:
                    part += len(seg)
                return (seg[part] if 0 <= part < len(seg) else base).strip().lower()
            if mode == "regex":
                pat = str(rule.get("regex") or "").strip()
                if pat:
                    m = re.search(pat, base)
                    if m:
                        return (m.group(1) if m.groups() else m.group(0)).strip().lower()
                return base.lower()
        except Exception as e:
            logger.warning(f"[查重] 群 {gid} 自定义规则异常，回退原名: {e}")
            return base.lower()
        return self._norm_key(name)

    async def _find_dupe_groups(self, gid: str) -> list[dict]:
        """递归扫描根目录+所有子文件夹，按（归一化文件名）分组：510041_1788852726 与 510041_1788788019 视为重复（时间戳不同），组内按上传时间升序，保留最新。"""
        all_files = await self._collect_files(gid)
        groups: dict[str, list[dict]] = {}
        for f in all_files:
            key = self._dupe_key(gid, f.get("name", ""))
            if not key:
                continue
            groups.setdefault(key, []).append(f)

        def _ut(x: dict) -> int:
            try:
                return int(x.get("upload_time") or 0)
            except (TypeError, ValueError):
                return 0

        dupes = [
            sorted(v, key=lambda x: _ut(x))
            for v in groups.values()
            if len(v) > 1
        ]
        dupes.sort(key=lambda g: str(g[0]["name"]).lower())
        return dupes

    async def api_cleanup(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not (self.qq and self.qq.available):
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            async with self.group_lock(gid):
                stats = await self.scheduler.run_cleanup(gid, notify=True)
        except Exception as e:
            return error_response(str(e), status_code=502)
        self.audit_now("panel", gid, "手动分类", "", "ok" if not stats.get("failed") else "warn",
                       f"移动 {stats.get('moved', 0)} / 失败 {stats.get('failed', 0)}")
        return json_response(stats)

    async def api_create_folder(self):
        from astrbot.api.web import error_response, json_response, request

        payload = await request.json(default={})
        gid = str(payload.get("group_id", "") or self._q("gid")).strip()
        folder_name = str(payload.get("folder_name", "")).strip()
        if not gid:
            return error_response("缺少 group_id", status_code=400)
        if not folder_name:
            return error_response("缺少 folder_name", status_code=400)
        if not self.qq:
            return error_response("OneBot 客户端未就绪", status_code=503)
        try:
            fid = await self.qq.create_folder(gid, folder_name)
            if not fid:
                root = await self.qq.root_files(gid)
                fid = {d["name"]: d["folder_id"] for d in norm_folders(root)}.get(folder_name, "")
        except QQFileError as e:
            return error_response(str(e), status_code=502)
        return json_response({"created": bool(fid), "folder_id": fid, "name": folder_name})
