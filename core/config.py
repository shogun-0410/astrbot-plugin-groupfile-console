"""多群配置存储：数据持久化到 AstrBot 插件数据目录，避免更新插件时丢失。"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("gfc_config")

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

    def data_root() -> Path:
        """插件数据目录（持久化配置 + 临时发送文件）。"""
        return Path(get_astrbot_plugin_data_path()) / "astrbot_plugin_groupfile_console"

    def _data_root() -> Path:
        return data_root()

except Exception:  # 开发环境兜底
    def data_root() -> Path:
        return Path(__file__).resolve().parent.parent / ".dev_data"

    def _data_root() -> Path:
        return data_root()


DEFAULT_RULES = [
    {"source": "jm", "pattern": r"^\d+_\d+", "folder": "HV2"},
    {"source": "suwayomi", "pattern": "", "folder": "COS"},
]

# v2.7.0：发送临时文件（全局，不区分群）的默认策略。
# 只作用于「/gf 查 序号 发文件」与 WebUI 发文件下载到本地的临时产物，
# 与群文件时效清理（auto_delete / ttl）完全无关，默认语义不受影响。
DEFAULT_SEND_TMP = {
    "enabled": True,          # 是否自动清理临时文件
    "keep_seconds": 600,      # 发送后保留多少秒再删（管道/合并转发是异步的）
    "orphan_hours": 1,        # 残留扫描：超过多少小时未再使用的文件直接清掉
    "dir": "",                # 自定义发送目录（留空 = 共享 temp/<插件>/send_tmp）
}

# 扩展名清洗：允许字母/数字开头，长度 ≤16（兼容 cbz、7z、tar.gz 这类写法）
_EXT_CLEAN_RE = re.compile(r"^[a-z0-9][a-z0-9+_.-]{0,15}$")
_EXT_SPLIT_RE = re.compile(r"[,，、;；\s]+")


def normalize_exts(raw: Any) -> list[str]:
    """把规则里的 ext 统一成干净的小写列表（无点、去重、保序）。

    v2.4.0：兼容用户手写 ``".CBZ"`` / ``"*.cbr"`` / ``"cbz,cbr"`` / ``"cbz cbr"``，
    也兼容旧配置把 exts 存成字符串的情况；非法项直接丢弃，避免匹配时按字符遍历。
    """
    if raw is None:
        return []
    parts: list[Any] = []
    if isinstance(raw, str):
        parts = _EXT_SPLIT_RE.split(raw)
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, str):
                parts.extend(_EXT_SPLIT_RE.split(item))
            elif item is not None:
                parts.append(item)
    else:
        parts = [raw]
    out: list[str] = []
    for p in parts:
        e = str(p or "").strip().lower().lstrip("*").lstrip(".")
        if e and _EXT_CLEAN_RE.match(e) and e not in out:
            out.append(e)
    return out


def normalize_rule(rule: Any) -> Any:
    """复制规则并清洗 exts（不改调用方原对象）。"""
    if not isinstance(rule, dict):
        return rule
    out = dict(rule)
    out["exts"] = normalize_exts(out.get("exts"))
    return out


def normalize_rules(rules: Any) -> Any:
    if not isinstance(rules, list):
        return rules
    return [normalize_rule(r) for r in rules]


def _default_group() -> dict[str, Any]:
    return {
        "name": "",
        "enabled": True,  # 分群启用开关：False 时该群不执行任何定时/自动动作（手动命令与 WebUI 仍可用）
        "admins": [],
        "rules": json.loads(json.dumps(DEFAULT_RULES)),
        "cleanup": {
            "enabled": False,
            "crones": ["0 3 * * *"],
        },
        "limits": {
            "count_limit": 0,
            "space_limit_gb": 0,
            "warn_pct": 100,
            "notify": True,
        },
        "dupe_rule": {"mode": "smart", "sep": "_", "part": 0, "regex": ""},
        "ttl": {
            "enabled": False,        # 时效清理总开关
            "default_days": 180,     # 群级默认时效（天）；0=不启用默认，仅看文件夹覆盖
            "action": "report",      # 超龄动作：report 只报告 / recycle 移入回收文件夹 / delete 删除
            "recycle_folder": "_回收",  # recycle 动作的目标文件夹（不存在时自动创建）
            "folders": [],           # 文件夹级覆盖：[{folder, days, action}]，action 可空=继承群级
        },
    }


class GroupConfigStore:
    def __init__(self) -> None:
        self._path = _data_root() / "groups.json"
        self.groups: dict[str, dict[str, Any]] = {}
        self.admin_users: list[str] = []
        self.send_tmp: dict[str, Any] = dict(DEFAULT_SEND_TMP)
        self.load()

    def load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                raw = data.get("groups", {})
                self.groups = {str(k): v for k, v in raw.items()}
                self.admin_users = [str(a) for a in (data.get("admin_users") or [])]
                self.send_tmp = self._clean_send_tmp(data.get("send_tmp"))
                logger.info(f"[群文件控制台] 已加载 {len(self.groups)} 个群配置")
        except Exception as e:
            logger.error(f"[群文件控制台] 读取配置失败，使用默认: {e}")
            self.groups = {}
            self.send_tmp = dict(DEFAULT_SEND_TMP)

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "groups": self.groups,
                        "admin_users": self.admin_users,
                        "send_tmp": getattr(self, "send_tmp", dict(DEFAULT_SEND_TMP)),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except Exception as e:
            logger.error(f"[群文件控制台] 保存配置失败: {e}")

    # ---- 发送临时文件策略（全局） ----
    @staticmethod
    def _clean_send_tmp(raw: Any) -> dict[str, Any]:
        """收成合法值：开关布尔、秒数 30–86400、小时 1–168、目录只收绝对路径。"""
        from .sendtmp import (
            DEFAULT_KEEP_SECONDS,
            DEFAULT_ORPHAN_HOURS,
            MAX_KEEP_SECONDS,
            MAX_ORPHAN_HOURS,
            MIN_KEEP_SECONDS,
            MIN_ORPHAN_HOURS,
            clamp_int,
            is_abs_path,
        )

        out = dict(DEFAULT_SEND_TMP)
        if not isinstance(raw, dict):
            return out
        if "enabled" in raw:
            out["enabled"] = bool(raw.get("enabled"))
        out["keep_seconds"] = clamp_int(
            raw.get("keep_seconds"), DEFAULT_KEEP_SECONDS, MIN_KEEP_SECONDS, MAX_KEEP_SECONDS
        )
        out["orphan_hours"] = clamp_int(
            raw.get("orphan_hours"), DEFAULT_ORPHAN_HOURS, MIN_ORPHAN_HOURS, MAX_ORPHAN_HOURS
        )
        d = str(raw.get("dir") or "").strip()
        out["dir"] = d if is_abs_path(d) else ""
        return out

    def get_send_tmp(self) -> dict[str, Any]:
        return dict(DEFAULT_SEND_TMP, **getattr(self, "send_tmp", {}) or {})

    def set_send_tmp(self, patch: dict[str, Any]) -> dict[str, Any]:
        cur = self.get_send_tmp()
        if isinstance(patch, dict):
            cur.update(patch)
        self.send_tmp = self._clean_send_tmp(cur)
        self.save()
        return dict(self.send_tmp)

    # ---- 全局管理员（能使用管理命令的人，独立于单群 admins）----
    def get_admin_users(self) -> list[str]:
        return [str(a) for a in (self.admin_users or [])]

    def set_admin_users(self, users: list) -> None:
        self.admin_users = [str(a) for a in (users or []) if str(a).strip()]
        self.save()
        return list(self.admin_users)

    # ---- 群 ----
    def list_groups(self) -> list[dict[str, Any]]:
        out = []
        for gid, cfg in self.groups.items():
            item = dict(cfg)
            item["group_id"] = gid
            out.append(item)
        return out

    def get_group(self, group_id: str | int) -> dict[str, Any]:
        gid = str(group_id)
        if gid not in self.groups:
            return _default_group()
        return self.groups[gid]

    def upsert_group(self, group_id: str | int, patch: dict[str, Any]) -> dict[str, Any]:
        gid = str(group_id)
        cur = self.groups.get(gid)
        if cur is None:
            cur = _default_group()
            self.groups[gid] = cur
        for k in ("name", "enabled", "admins", "rules", "cleanup", "limits", "dupe_rule", "ttl"):
            if k in patch and patch[k] is not None:
                cur[k] = normalize_rules(patch[k]) if k == "rules" else patch[k]
        self.save()
        return cur

    def remove_group(self, group_id: str | int) -> bool:
        gid = str(group_id)
        if gid in self.groups:
            del self.groups[gid]
            self.save()
            return True
        return False

    def group_ids(self) -> list[str]:
        return list(self.groups.keys())

    # ---- 规则 ----
    def rules(self, group_id: str | int) -> list[dict[str, Any]]:
        cfg = self.get_group(group_id)
        rules = cfg.get("rules")
        if not isinstance(rules, list):
            return json.loads(json.dumps(DEFAULT_RULES))
        return rules

    def set_rules(self, group_id: str | int, rules: list[dict[str, Any]]) -> None:
        self.upsert_group(group_id, {"rules": rules})

    # ---- 分类匹配 ----
    def classify(self, group_id: str | int, filename: str) -> dict[str, Any] | None:
        """按规则表匹配文件名，返回命中的规则；无命中返回 None。

        pattern 语义由规则的 kind 决定（v2.7.0 起真正生效）：
        - kw：关键词，文件名包含即命中（点号、加号等按普通字符处理）；
        - regex / dsl：正则匹配（语法错误时降级为子串包含）；
        - kind 缺失：沿用旧行为——能当正则就当正则，否则子串。

        规则可带 exts 文件类型白名单（如 ["pdf","zip"]），空 = 不限类型。
        规则可带 exclude 排除词列表（如 ["预告","目录"]）：文件名含任一排除词则该规则不参与。
        规则可带 enabled（默认 True）：False = 该条停用，不参与匹配。

        规则之间平级、不看顺序：所有规则都会参与匹配，
        同一条文件被多条规则命中时，自动选「最具体」的一条
        （匹配内容越长越优先；空 pattern 的兜底规则只在没有任何规则命中时才采用）。
        """
        best = None
        best_score = -1
        for rule in self.rules(group_id):
            if not isinstance(rule, dict):
                continue  # 损坏的规则项不参与匹配，避免整页分类崩掉
            if rule.get("enabled") is False:
                continue  # 单条停用
            note = self._rule_skip_reason(rule, filename)
            if note:
                continue  # 扩展名/排除词不满足
            pattern = (rule.get("pattern") or "").strip()
            kind = str(rule.get("kind") or "").strip().lower()
            if not pattern:
                # 空 pattern = 兜底；带类型限定的兜底比"完全不限"更具体，
                # 否则会被前面的泛兜底规则（如默认 suwayomi）抢走，漫画规则永远不生效。
                score = 1 if normalize_exts(rule.get("exts")) else 0
            else:
                if not self._pattern_hit(pattern, filename, kind):
                    continue
                # 具体度评分：匹配内容越长越具体；正则/DSL 写法额外加权，避免被短关键词挤掉
                if kind in ("regex", "dsl") or re.search(r"[\\^$+*?\[\]()|]", pattern):
                    score = len(pattern) + 2
                else:
                    score = len(pattern)
            if score > best_score:
                best_score = score
                best = rule
        return best

    @staticmethod
    def _pattern_hit(pattern: str, filename: str, kind: str = "") -> bool:
        """按规则 kind 判断命中：kw=子串，regex/dsl=正则（坏正则降级子串），未标 kind 走旧行为。"""
        kind = str(kind or "").strip().lower()
        if kind == "kw":
            return pattern in filename
        try:
            return bool(re.search(pattern, filename))
        except re.error:
            return pattern in filename

    @staticmethod
    def _rule_skip_reason(rule: dict[str, Any], filename: str) -> str | None:
        """返回该规则被跳过的原因（None = 参与匹配）。供试一试/诊断显示。"""
        exts = rule.get("exts") or []
        clean_exts = normalize_exts(exts)
        if clean_exts:
            ext = os.path.splitext(filename)[1].lstrip(".").lower()
            # 普通扩展名按 splitext 比；带点的复合扩展名（如 tar.gz）按文件名后缀比
            hit = ext in clean_exts or any(
                "." in e and filename.lower().endswith("." + e) for e in clean_exts
            )
            if not hit:
                return f"类型不符（限 {'/'.join(clean_exts[:4])}）"
        for ex in rule.get("exclude") or []:
            ex = str(ex).strip()
            if ex and ex in filename:
                return f"含排除词「{ex}」"
        return None

    def explain_rule(self, group_id: str | int, filename: str) -> dict[str, Any]:
        """试一试：详细解释一个文件名会怎么被分类（命中/跳过原因）。"""
        rules = self.rules(group_id)
        hit = self.classify(group_id, filename)
        skipped: list[dict[str, Any]] = []
        disabled = 0
        for i, rule in enumerate(rules, 1):
            if not isinstance(rule, dict):
                continue
            if rule.get("enabled") is False:
                disabled += 1
                continue
            reason = self._rule_skip_reason(rule, filename)
            if reason:
                skipped.append({
                    "source": str(rule.get("source") or f"规则{i}"),
                    "folder": str(rule.get("folder") or ""),
                    "reason": reason,
                })
                continue
            pattern = (rule.get("pattern") or "").strip()
            kind = str(rule.get("kind") or "").strip().lower()
            if pattern and not self._pattern_hit(pattern, filename, kind):
                skipped.append({
                    "source": str(rule.get("source") or f"规则{i}"),
                    "folder": str(rule.get("folder") or ""),
                    "reason": f"未命中「{pattern}」",
                })
        return {
            "name": filename,
            "hit": ({"source": hit.get("source"), "folder": hit.get("folder")} if hit else None),
            "skipped": skipped[:6],
            "disabled": disabled,
        }
