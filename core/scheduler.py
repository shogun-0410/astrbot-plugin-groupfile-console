"""定时任务流水线：每群按 cron 触发「分类移动 → 时效清理 → 容量播报」，汇总为一条报告。

- 分类移动：按规则表把根目录/子文件夹文件移入目标文件夹（与 v1 相同）。
- 时效清理：按 upload/modify 时间计算文件年龄，超过策略天数即超龄；
  动作分 report（只报告）/ recycle（移入回收文件夹）/ delete（删除）。
- 容量播报：跟随流水线顺带检查（边界触发：未超→超 才播报，且每日最多一次）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from .cron import CronExpr
from .config import GroupConfigStore
from .qqfile import QQFileClient, QQFileError, norm_folders
from .scanner import walk_files

logger = logging.getLogger("gfc_scheduler")

TICK_SECONDS = 30
TTL_ACTIONS = ("report", "recycle", "delete")


class CleanupScheduler:
    def __init__(self, plugin) -> None:
        self.plugin = plugin
        # 容量播报状态：gid -> {"over": bool, "day": "YYYY-MM-DD"}
        self._cap_state: dict[str, dict[str, Any]] = {}
        # 去重：gid -> 上次触发流水线的 "HH:MM"（防止同一分钟内重复触发）
        self._last_fire: dict[str, str] = {}

    @property
    def store(self) -> GroupConfigStore:
        return self.plugin.store

    @property
    def client(self) -> QQFileClient | None:
        return self.plugin.qq

    def _audit(self, actor: str, gid: str, action: str, target: str = "", result: str = "ok", detail: str = "") -> None:
        fn = getattr(self.plugin, "audit_now", None)
        if callable(fn):
            try:
                fn(actor, gid, action, target, result, detail)
            except Exception:
                pass

    async def run(self) -> None:
        logger.info("[群管理] 定时任务已启动")
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[定时任务] tick 异常: {e}")
            await asyncio.sleep(TICK_SECONDS)

    async def tick(self) -> None:
        # 发送临时文件清扫（10 分钟限流在 plugin.cleanup_tick 内，与群配置无关）
        try:
            await self.plugin.cleanup_tick()
        except Exception as e:
            logger.warning(f"[定时任务] 发送目录清扫异常: {e}")
        if not self.client or not self.client.available:
            return
        if not self.plugin.plugin_enabled:
            return  # 总开关关闭：暂停所有定时动作
        now = datetime.now()
        fire_key = now.strftime("%H:%M")
        for gid in self.store.group_ids():
            cfg = self.store.get_group(gid)
            if cfg.get("enabled", True) is False:
                continue  # 该群已停用：不跑任何定时/自动动作，也不会主动发消息
            cleanup = cfg.get("cleanup") or {}
            ttl_cfg = cfg.get("ttl") or {}
            if not (cleanup.get("enabled") or ttl_cfg.get("enabled")):
                continue
            crones = [c for c in (cleanup.get("crones") or []) if c]
            # 时效策略启用但分类未配置时，挂在默认每日 3 点兜底
            if ttl_cfg.get("enabled") and not crones:
                crones = ["0 3 * * *"]
            due_now = any(CronExpr(str(c)).match(now.minute, now.hour, now.day, now.month, now.weekday()) for c in crones)
            due = due_now
            catchup = int(cleanup.get("catchup_minutes") or 0)
            if not due and catchup > 0 and crones:
                # 补跑：重启/卡顿错过的 cron，在最近 N 分钟内发生过就补一次
                last_run = float(cfg.get("last_run") or 0)
                for m in range(1, min(catchup, 1440) + 1):
                    t = now - timedelta(minutes=m)
                    if t.timestamp() <= last_run:
                        break
                    if any(CronExpr(str(c)).match(t.minute, t.hour, t.day, t.month, t.weekday()) for c in crones):
                        due = True
                        self._audit("system", gid, "补跑", result="ok", detail=f"错过 {t.strftime('%H:%M')}，已补一次")
                        break
            if not due:
                continue
            if self._last_fire.get(gid) == fire_key:
                continue  # 同一分钟已触发过（TICK 抖动/多循环残留保护）
            self._last_fire[gid] = fire_key
            await self.run_pipeline(gid, notify=True)
            self.store.touch_last_run(gid)

    # ================= 流水线 =================
    async def run_pipeline(self, gid: str, notify: bool = False, manual: bool = False) -> dict[str, Any]:
        """完整流水线：分类 → 时效 → 容量。manual=True 时容量超限不消耗当日播报额度。

        v2.8.0：同群串行（与手动删除/移动共用一个锁），避免定时与手动互相踩。
        """
        lock = self.plugin.group_lock(gid)
        async with lock:
            stats = await self.run_cleanup(gid, notify=False)
            ttl_stats = await self.run_ttl_scan(gid)
            cap_line = await self.capacity_followup(gid, force=manual)
            result = {**stats, "ttl": ttl_stats, "capacity": cap_line}
            if notify:
                report = self._format_full_report(result)
                if self._report_worth(result):
                    await self.plugin.notify_group(gid, report)
            self.plugin.record_event(
                "流水线",
                "info" if stats["failed"] == 0 and (ttl_stats.get("failed") or 0) == 0 else "warn",
                f"群 {gid} {'手动' if manual else '定时'}流水线：移动 {stats['moved']} / 失败 {stats['failed']} / "
                f"超龄 {ttl_stats.get('count', 0)} / 回收 {ttl_stats.get('recycled', 0)} / 删除 {ttl_stats.get('deleted', 0)}",
            )
            self._audit(
                "system" if not manual else "manual",
                gid,
                "整理流水线",
                result="ok" if stats["failed"] == 0 else "warn",
                detail=(
                    f"移动 {stats['moved']} / 失败 {stats['failed']} / 超龄 {ttl_stats.get('count', 0)}"
                    f" / 回收 {ttl_stats.get('recycled', 0)} / 删除 {ttl_stats.get('deleted', 0)}"
                ),
            )
            return result

    @staticmethod
    def _report_worth(result: dict[str, Any]) -> bool:
        """全部为空时保持静默（沿用 v1 无动作不通知的设计）。"""
        if result.get("moved") or result.get("failed") or (result.get("details") or []):
            return True
        ttl = result.get("ttl") or {}
        if ttl.get("count"):
            return True
        return bool(result.get("capacity"))

    async def run_cleanup(self, gid: str, notify: bool = False) -> dict[str, Any]:
        """按分类规则移动文件到目标文件夹（逐个调用 move_group_file）。"""
        stats = {
            "moved": 0,
            "failed": 0,
            "skipped": 0,
            "details": [],
            "missing": [],
            # v2.7.0：跳过原因分开统计，报告能说清「为什么没动」
            "no_rule": 0,
            "in_place": 0,
            "no_target": 0,
        }
        if not (self.client and self.client.available):
            return stats
        rules = self.store.rules(gid)
        files = await self._walk_all_files(gid)
        if not files or not rules:
            stats["no_rule"] = len(files)
            stats["skipped"] = len(files)
            return stats

        folders = await self._folder_pool(gid)
        for f in files:
            name = str(f.get("name") or "")
            if not name:
                continue
            rule = self.store.classify(gid, name)
            if not rule:
                stats["no_rule"] += 1
                stats["skipped"] += 1
                continue
            target = str(rule.get("folder") or "").strip()
            if not target:
                stats["no_target"] += 1
                stats["skipped"] += 1
                continue
            # 已在目标文件夹（按顶层路径判断）则跳过，避免重复移动虚报 moved
            folder_label = str(f.get("folder") or "").strip().lstrip("/")
            if folder_label and folder_label.split("/")[0] == target:
                stats["in_place"] += 1
                stats["skipped"] += 1
                continue
            folder_id = f.get("folder_id") or None
            # 目标文件夹不存在：自动创建后移动；创建失败则记录并提醒
            if target not in folders:
                try:
                    fid = await self.client.create_folder(gid, target)
                    if not fid:
                        # 协议端成功不回 id：回查一次
                        folders = await self._folder_pool(gid)
                        fid = folders.get(target, "")
                    folders[target] = fid
                    stats["details"].append(f"📁 已自动创建文件夹「{target}」")
                except QQFileError as e:
                    if target not in stats["missing"]:
                        stats["missing"].append(target)
                    stats["failed"] += 1
                    stats["details"].append(f"⏭ {name} -> 目标文件夹「{target}」创建失败: {e}")
                    continue
            try:
                await self.client.move_file(
                    gid,
                    str(f["file_id"]),
                    target,
                    target_id=folders.get(target),
                    current_id=folder_id,
                )
                stats["moved"] += 1
                src = str(rule.get("source") or "").strip() or "兜底"
                stats["details"].append(f"→ {name} -> {target}（规则: {src}）")
            except QQFileError as e:
                stats["failed"] += 1
                stats["details"].append(f"✗ {name}: {e}")

        if notify:
            report = self._format_cleanup_report(stats)
            await self.plugin.notify_group(gid, report)
        self.plugin.record_event(
            "分类",
            "info" if stats["failed"] == 0 else "warn",
            f"群 {gid} 自动分类：移动 {stats['moved']} / 失败 {stats['failed']} / 跳过 {stats['skipped']}"
            + (f"；首条失败：{stats['details'][0]}" if stats["details"] else ""),
        )
        return stats

    async def preview_cleanup(self, gid: str) -> dict[str, Any]:
        """预览整理：按当前规则算一遍「谁会被移动到哪」，不执行任何动作。"""
        result: dict[str, Any] = {
            "planned": [],     # [{name, from, to, rule}]
            "in_place": 0,     # 已在目标文件夹，无需移动
            "no_match": 0,     # 没有规则命中
            "missing_folders": [],  # 目标文件夹还不存在（整理时会自动创建）
            "total": 0,
        }
        if not (self.client and self.client.available):
            return result
        files = await self._walk_all_files(gid)
        if not files:
            return result
        folders = await self._folder_pool(gid)
        result["total"] = len(files)
        for f in files:
            name = str(f.get("name") or "")
            if not name:
                continue
            rule = self.store.classify(gid, name)
            if not rule:
                result["no_match"] += 1
                continue
            target = str(rule.get("folder") or "").strip()
            if not target:
                result["no_match"] += 1
                continue
            folder_label = str(f.get("folder") or "").strip().lstrip("/")
            if folder_label and folder_label.split("/")[0] == target:
                result["in_place"] += 1
                continue
            if target not in folders and target not in result["missing_folders"]:
                result["missing_folders"].append(target)
            result["planned"].append({
                "name": name,
                "from": folder_label or "根目录",
                "to": target,
                "rule": str(rule.get("source") or "").strip() or "兜底",
            })
        return result

    # ================= 时效清理 =================
    async def run_ttl_scan(self, gid: str) -> dict[str, Any]:
        """按时间计算超龄文件并执行动作（report/recycle/delete）。"""
        stats: dict[str, Any] = {
            "enabled": False,
            "count": 0,
            "recycled": 0,
            "deleted": 0,
            "failed": 0,
            "unknown_time": 0,
            "delete_capped": False,
            "scan_truncated": False,
            "details": [],
        }
        cfg = self.store.get_group(gid)
        ttl = cfg.get("ttl") or {}
        if not ttl.get("enabled") or not (self.client and self.client.available):
            return stats
        stats["enabled"] = True
        files = await self._walk_all_files(gid)
        if getattr(self, "last_scan", {}).get("truncated"):
            stats["scan_truncated"] = True
            stats["details"].append("⚠ 文件太多，本次只扫描了一部分（可用面板分页查看）")
        if not files:
            return stats

        default_days = int(ttl.get("default_days") or 0)
        default_action = str(ttl.get("action") or "report").strip() or "report"
        recycle_name = str(ttl.get("recycle_folder") or "").strip() or "_回收"
        max_delete = max(1, int(ttl.get("max_delete_per_run") or 200))
        if default_action not in TTL_ACTIONS:
            default_action = "report"
        overrides = {
            str(o.get("folder") or "").strip(): o
            for o in (ttl.get("folders") or [])
            if isinstance(o, dict) and str(o.get("folder") or "").strip()
        }
        now = time.time()

        # 回收目标（仅 recycle 动作需要）：""=已创建失败，避免逐文件重试
        recycle_id: str | None = None

        for f in files:
            name = str(f.get("name") or "")
            folder_label = str(f.get("folder") or "").strip().lstrip("/")
            top = folder_label.split("/")[0] if folder_label else ""
            if top == recycle_name:
                continue  # 回收站内的文件不再参与时效判断
            days, action = self._ttl_policy(top, folder_label, default_days, default_action, overrides)
            if days <= 0:
                continue
            ts = int(f.get("ts") or 0)
            if ts <= 0:
                # v2.7.0：协议端没给上传/修改时间时，无法判断年龄——跳过，绝不误删
                stats["unknown_time"] += 1
                if len(stats["details"]) < 8:
                    stats["details"].append(f"❔ {name}（没有上传时间，已跳过）")
                continue
            age_days = (now - ts) / 86400.0
            if age_days <= days:
                continue
            stats["count"] += 1
            if stats["count"] <= 8:
                stats["details"].append(f"⏳ {name}（{self._fmt_age(age_days)}，位于 {folder_label or '根目录'}）")

            if action == "report":
                continue
            if action == "delete" and stats["deleted"] >= max_delete:
                stats["delete_capped"] = True
                stats["details"].append(f"⚠ 达到单次删除上限 {max_delete}，后续文件本次不再删除")
                break
            try:
                if action == "recycle":
                    if recycle_id is None:
                        fid = await self._ensure_folder(gid, recycle_name)
                        recycle_id = fid or ""
                        if not recycle_id:
                            stats["failed"] += 1
                            stats["details"].append(f"✗ 回收文件夹「{recycle_name}」创建失败，跳过移入")
                            continue
                    await self.client.move_file(
                        gid,
                        str(f["file_id"]),
                        recycle_name,
                        target_id=recycle_id,
                        current_id=f.get("folder_id") or None,
                    )
                    stats["recycled"] += 1
                    self._audit("system", gid, "TTL回收", name, "ok", f"从 {folder_label or '根目录'} 移入 {recycle_name}")
                elif action == "delete":
                    await self.client.delete_file(
                        gid,
                        f.get("folder_id") or None,
                        str(f["file_id"]),
                        busid=f.get("busid") or 0,
                    )
                    stats["deleted"] += 1
                    self._audit("system", gid, "TTL删除", name, "ok", f"位于 {folder_label or '根目录'}")
            except QQFileError as e:
                stats["failed"] += 1
                if stats["count"] <= 8:
                    stats["details"].append(f"✗ {name}: {e}")
                self._audit("system", gid, f"TTL{'回收' if action == 'recycle' else '删除'}", name, "fail", str(e))

        if stats["count"] or stats["unknown_time"]:
            self.plugin.record_event(
                "时效",
                "info" if stats["failed"] == 0 else "warn",
                f"群 {gid} 时效扫描：超龄 {stats['count']} / 回收 {stats['recycled']} / 删除 {stats['deleted']} / 失败 {stats['failed']}",
            )
            self._audit(
                "system",
                gid,
                "时效扫描",
                result="warn" if stats["failed"] else "ok",
                detail=(
                    f"超龄 {stats['count']} / 回收 {stats['recycled']} / 删除 {stats['deleted']}"
                    f" / 失败 {stats['failed']} / 无时间 {stats['unknown_time']}"
                ),
            )
        return stats

    @staticmethod
    def _ttl_policy(
        top: str,
        label: str,
        default_days: int,
        default_action: str,
        overrides: dict[str, dict[str, Any]],
    ) -> tuple[int, str]:
        """返回 (天数, 动作)：优先精确路径匹配 → 顶层文件夹匹配 → 群级默认。

        文件夹覆盖里 days=0 表示「该文件夹不参与时效清理」。
        """
        for key in (label, top):
            o = overrides.get(key)
            if o is not None:
                days = int(o.get("days") or 0)
                action = str(o.get("action") or "").strip()
                if action not in TTL_ACTIONS:
                    action = default_action
                return days, action
        return default_days, default_action

    async def _ensure_folder(self, gid: str, name: str) -> str | None:
        try:
            folders = await self._folder_pool(gid)
            if name in folders:
                return folders[name]
            fid = await self.client.create_folder(gid, name)
            return fid or None
        except QQFileError:
            return None

    @staticmethod
    def _fmt_age(days: float) -> str:
        if days >= 365:
            return f"{days / 365:.1f} 年"
        if days >= 30:
            return f"{days / 30:.0f} 个月"
        return f"{days:.0f} 天"

    # ================= 容量播报 =================
    async def capacity_followup(self, gid: str, force: bool = False) -> str | None:
        """跟随流水线的容量检查：边界触发（未超→超）且每日最多提醒一次。

        force=True（手动 /gf 整）：超限时直接返回提醒，不消耗当日播报额度、不改状态。
        """
        if not (self.client and self.client.available):
            return None
        cfg = self.store.get_group(gid)
        limits = cfg.get("limits") or {}
        if limits.get("notify", True) is False:
            return None
        try:
            info = await self.client.file_system_info(gid)
        except QQFileError:
            return None
        used = int(info.get("used_space") or 0)
        total = int(info.get("total_space") or 0)
        count = int(info.get("file_count") or 0)
        count_limit = int(limits.get("count_limit") or 0)
        space_limit_gb = float(limits.get("space_limit_gb") or 0)
        warn_pct = int(limits.get("warn_pct") or 100)
        reasons = []
        if count_limit > 0 and count > count_limit:
            reasons.append(f"文件数 {count} 超过 {count_limit}")
        if space_limit_gb > 0 and used > space_limit_gb * (1024 ** 3):
            reasons.append(f"空间 {used / (1024 ** 3):.2f}GB 超过 {space_limit_gb}GB")
        if total > 0 and warn_pct < 100 and used * 100 >= total * warn_pct:
            reasons.append(f"空间已用 {used * 100 // max(total, 1)}%（告警线 {warn_pct}%）")
        over = bool(reasons)
        today = datetime.now().strftime("%Y-%m-%d")
        if not over:
            self._cap_state[gid] = {"over": False, "day": today}
            return None
        line = f"⚠ 容量提醒：{'；'.join(reasons)}"
        if total > 0:
            line += f"（总 {total / (1024 ** 3):.1f}GB）"
        if force:
            return line  # 手动触发：只展示，不记状态
        state = self._cap_state.get(gid) or {"over": False, "day": ""}
        self._cap_state[gid] = {"over": True, "day": today}
        # 超限：边界触发（当日首次或由未超转超）才播报
        if state.get("over") and state.get("day") == today:
            return None
        self.plugin.record_event("容量", "warn", f"群 {gid} {line}")
        return line

    async def preview_ttl(self, gid: str) -> dict[str, Any]:
        """试运行：只算哪些文件会被回收/删除，不动任何文件。"""
        cfg = self.store.get_group(gid)
        ttl = cfg.get("ttl") or {}
        out: dict[str, Any] = {
            "enabled": bool(ttl.get("enabled")),
            "over": 0,
            "unknown_time": 0,
            "planned": [],
            "truncated": False,
        }
        if not ttl.get("enabled") or not (self.client and self.client.available):
            return out
        files = await self._walk_all_files(gid)
        if getattr(self, "last_scan", {}).get("truncated"):
            out["truncated"] = True
        default_days = int(ttl.get("default_days") or 0)
        default_action = str(ttl.get("action") or "report")
        recycle_name = str(ttl.get("recycle_folder") or "").strip() or "_回收"
        overrides = {
            str(o.get("folder") or "").strip(): o
            for o in (ttl.get("folders") or [])
            if isinstance(o, dict) and str(o.get("folder") or "").strip()
        }
        now = time.time()
        for f in files:
            folder_label = str(f.get("folder") or "").strip().lstrip("/")
            top = folder_label.split("/")[0] if folder_label else ""
            if top == recycle_name:
                continue
            days, action = self._ttl_policy(top, folder_label, default_days, default_action, overrides)
            if days <= 0:
                continue
            ts = int(f.get("ts") or 0)
            if ts <= 0:
                out["unknown_time"] += 1
                continue
            age = (now - ts) / 86400.0
            if age <= days:
                continue
            out["over"] += 1
            if len(out["planned"]) < 100:
                out["planned"].append(
                    {
                        "name": str(f.get("name") or ""),
                        "folder": folder_label or "根目录",
                        "age_days": round(age, 1),
                        "days": days,
                        "action": action,
                    }
                )
        return out

    # ================= 报告 =================
    @staticmethod
    def _skip_line(result: dict[str, Any]) -> str | None:
        parts = []
        if result.get("no_rule"):
            parts.append(f"无规则匹配 {result['no_rule']}")
        if result.get("in_place"):
            parts.append(f"已在目标文件夹 {result['in_place']}")
        if result.get("no_target"):
            parts.append(f"规则未设目标 {result['no_target']}")
        return "跳过原因：" + " / ".join(parts) if parts else None

    def _format_full_report(self, result: dict[str, Any]) -> str:
        lines = [
            "🧹 定时整理完成",
            f"移动 {result['moved']} 个，失败 {result['failed']} 个，跳过 {result['skipped']} 个",
        ]
        skip = self._skip_line(result)
        if skip:
            lines.append(skip)
        missing = result.get("missing") or []
        if missing:
            lines.append("⚠ 以下目标文件夹自动创建失败，未移动（可手动建好后重新整理）：")
            for m in missing:
                lines.append(f"   📂 {m}")
        ttl = result.get("ttl") or {}
        if ttl.get("enabled"):
            summary = f"⏳ 时效扫描：超龄 {ttl.get('count', 0)} 个"
            if ttl.get("recycled"):
                summary += f"，已回收 {ttl['recycled']}"
            if ttl.get("deleted"):
                summary += f"，已删除 {ttl['deleted']}"
            if ttl.get("failed"):
                summary += f"，失败 {ttl['failed']}"
            if ttl.get("unknown_time"):
                summary += f"，无时间跳过 {ttl['unknown_time']}"
            lines.append(summary)
        details = result.get("details") or []
        for d in details[:8]:
            lines.append(d)
        if len(details) > 8:
            lines.append(f"... 等 {len(details)} 条")
        ttl_details = [d for d in (ttl.get("details") or []) if d.startswith("⏳") or d.startswith("✗")]
        for d in ttl_details[:8]:
            lines.append(d)
        cap = result.get("capacity")
        if cap:
            lines.append(cap)
        return "\n".join(lines)

    def _format_cleanup_report(self, stats: dict[str, Any]) -> str:
        lines = [
            "🧹 自动分类完成",
            f"移动 {stats['moved']} 个，失败 {stats['failed']} 个，跳过 {stats['skipped']} 个",
        ]
        skip = self._skip_line(stats)
        if skip:
            lines.append(skip)
        missing = stats.get("missing") or []
        if missing:
            lines.append("⚠ 以下目标文件夹自动创建失败，未移动（可手动建好后重新整理）：")
            for m in missing:
                lines.append(f"   📂 {m}")
        details = stats.get("details") or []
        for d in details[:8]:
            lines.append(d)
        if len(details) > 8:
            lines.append(f"... 等 {len(details)} 条")
        return "\n".join(lines)

    # ================= 工具 =================
    async def _walk_all_files(self, gid: str) -> list[dict[str, Any]]:
        """递归收集（v2.8.0 统一到 scanner，带深度/数量/环保护）。"""
        res = await walk_files(self.client, gid)
        self.last_scan = {
            "truncated": res.get("truncated", False),
            "errors": res.get("errors") or [],
            "folders_seen": res.get("folders_seen", 0),
            "files": len(res.get("files") or []),
        }
        return res.get("files") or []

    async def _folder_pool(self, gid: str) -> dict[str, str]:
        """顶层文件夹名 -> folder_id 映射。"""
        try:
            raw = await self.client.root_files(gid)
            return {d["name"]: d["folder_id"] for d in norm_folders(raw)}
        except QQFileError:
            return {}
