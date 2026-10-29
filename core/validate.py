"""配置校验与归一化（v2.8.0）。

目标：任何来源（面板、导入、旧配置）的脏值都不能让定时流水线崩溃或误删：
- 数值统一 clamp；
- cron 逐条校验，非法的直接丢弃并给 warning；
- 规则/权限/文件夹覆盖只保留结构正确的项；
- 保留未知字段（向前兼容），只覆盖已知字段。
"""

from __future__ import annotations

import re
from typing import Any

from .cron import is_valid_cron

TTL_ACTIONS = ("report", "recycle", "delete")
RULE_KINDS = ("", "kw", "regex", "dsl")


def clamp_int(value, default: int, lo: int, hi: int) -> int:
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def clamp_float(value, default: float, lo: float, hi: float) -> float:
    try:
        n = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def norm_str(value, default: str = "", max_len: int = 200) -> str:
    s = str(value if value is not None else "").strip()
    return s[:max_len] if s else default


def norm_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value or "").strip().lower()
    if s in ("1", "true", "yes", "on", "开", "是"):
        return True
    if s in ("0", "false", "no", "off", "关", "否"):
        return False
    return default


def _normalize_exts(raw) -> list[str]:
    from .config import normalize_exts

    return normalize_exts(raw)


def normalize_rule(rule: Any, idx: int = 0) -> dict[str, Any] | None:
    if not isinstance(rule, dict):
        return None
    out = dict(rule)
    out["source"] = norm_str(rule.get("source"), f"规则{idx}", 40)
    kind = norm_str(rule.get("kind"), "", 10).lower()
    out["kind"] = kind if kind in RULE_KINDS else ""
    out["pattern"] = norm_str(rule.get("pattern"), "", 300)
    out["folder"] = norm_str(rule.get("folder"), "", 40)
    out["exts"] = _normalize_exts(rule.get("exts"))
    excl = rule.get("exclude")
    out["exclude"] = (
        [str(x).strip() for x in excl if str(x).strip()][:20] if isinstance(excl, list) else []
    )
    out["enabled"] = rule.get("enabled") is not False
    return out


def normalize_rules(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    warns: list[str] = []
    if not isinstance(raw, list):
        return [], ["规则必须是列表，已忽略"]
    out: list[dict[str, Any]] = []
    for i, r in enumerate(raw[:200], 1):
        clean = normalize_rule(r, i)
        if clean is None:
            warns.append(f"第 {i} 条规则不是对象，已忽略")
            continue
        out.append(clean)
    if len(raw) > 200:
        warns.append("规则超过 200 条，已截断")
    return out, warns


def normalize_cleanup(raw: Any) -> tuple[dict[str, Any], list[str]]:
    warns: list[str] = []
    src = raw if isinstance(raw, dict) else {}
    out = dict(src)
    out["enabled"] = norm_bool(src.get("enabled"), False)
    crones = src.get("crones")
    if isinstance(crones, str):
        crones = [crones]
    crones = crones if isinstance(crones, list) else []
    clean: list[str] = []
    for c in crones[:20]:
        c = str(c or "").strip()
        if not c:
            continue
        if is_valid_cron(c):
            if c not in clean:
                clean.append(c)
        else:
            warns.append(f"定时表达式「{c}」无效，已忽略")
    out["crones"] = clean
    out["catchup_minutes"] = clamp_int(src.get("catchup_minutes"), 60, 0, 1440)
    # 旧字段直接丢弃（后端从未使用）
    out.pop("move_only", None)
    out.pop("delete_empty", None)
    return out, warns


def normalize_limits(raw: Any) -> tuple[dict[str, Any], list[str]]:
    src = raw if isinstance(raw, dict) else {}
    out = dict(src)
    out["count_limit"] = clamp_int(src.get("count_limit"), 0, 0, 1_000_000)
    out["space_limit_gb"] = clamp_float(src.get("space_limit_gb"), 0, 0, 1_000_000)
    out["warn_pct"] = clamp_int(src.get("warn_pct"), 100, 1, 100)
    out["notify"] = norm_bool(src.get("notify"), True)
    out.pop("report_used", None)
    return out, []


def normalize_ttl(raw: Any) -> tuple[dict[str, Any], list[str]]:
    warns: list[str] = []
    src = raw if isinstance(raw, dict) else {}
    out = dict(src)
    out["enabled"] = norm_bool(src.get("enabled"), False)
    out["default_days"] = clamp_int(src.get("default_days"), 180, 0, 3650)
    action = norm_str(src.get("action"), "report", 10).lower()
    out["action"] = action if action in TTL_ACTIONS else "report"
    out["recycle_folder"] = norm_str(src.get("recycle_folder"), "_回收", 40) or "_回收"
    out["max_delete_per_run"] = clamp_int(src.get("max_delete_per_run"), 200, 1, 1000)
    folders = src.get("folders")
    clean_folders: list[dict[str, Any]] = []
    if isinstance(folders, list):
        for item in folders[:50]:
            if not isinstance(item, dict):
                continue
            folder = norm_str(item.get("folder"), "", 80)
            if not folder:
                continue
            act = norm_str(item.get("action"), "", 10).lower()
            clean_folders.append(
                {
                    "folder": folder,
                    "days": clamp_int(item.get("days"), 0, 0, 3650),
                    "action": act if act in TTL_ACTIONS else "",
                }
            )
    elif folders not in (None, []):
        warns.append("文件夹覆盖必须是列表，已忽略")
    out["folders"] = clean_folders
    return out, warns


def normalize_dupe(raw: Any) -> tuple[dict[str, Any], list[str]]:
    warns: list[str] = []
    src = raw if isinstance(raw, dict) else {}
    out = dict(src)
    mode = norm_str(src.get("mode"), "smart", 10).lower()
    out["mode"] = mode if mode in ("smart", "split", "regex", "exact") else "smart"
    out["sep"] = norm_str(src.get("sep"), "_", 3) or "_"
    out["part"] = clamp_int(src.get("part"), 0, -10, 10)
    regex = norm_str(src.get("regex"), "", 200)
    if regex:
        try:
            re.compile(regex)
        except re.error:
            warns.append(f"查重正则「{regex}」无效，已清空")
            regex = ""
    out["regex"] = regex
    return out, warns


def normalize_admins(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,，、;\s]+", raw)
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for a in raw:
        a = str(a or "").strip()
        if a.isdigit() and a not in out:
            out.append(a)
    return out[:100]


def normalize_group_patch(patch: Any) -> tuple[dict[str, Any], list[str]]:
    """返回 (清洗后的 patch, warnings)。只处理传入的字段，未传的不动。"""
    warns: list[str] = []
    if not isinstance(patch, dict):
        return {}, ["配置必须是对象"]
    out = dict(patch)
    if "name" in patch:
        out["name"] = norm_str(patch.get("name"), "", 60)
    if "enabled" in patch:
        out["enabled"] = norm_bool(patch.get("enabled"), True)
    if "admins" in patch:
        out["admins"] = normalize_admins(patch.get("admins"))
    if "rules" in patch:
        rules, w = normalize_rules(patch.get("rules"))
        out["rules"] = rules
        warns.extend(w)
    if "cleanup" in patch:
        val, w = normalize_cleanup(patch.get("cleanup"))
        out["cleanup"] = val
        warns.extend(w)
    if "limits" in patch:
        val, w = normalize_limits(patch.get("limits"))
        out["limits"] = val
        warns.extend(w)
    if "ttl" in patch:
        val, w = normalize_ttl(patch.get("ttl"))
        out["ttl"] = val
        warns.extend(w)
    if "dupe_rule" in patch:
        val, w = normalize_dupe(patch.get("dupe_rule"))
        out["dupe_rule"] = val
        warns.extend(w)
    return out, warns
