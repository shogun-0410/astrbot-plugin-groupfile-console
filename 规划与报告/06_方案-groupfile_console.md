# 群文件管理（astrbot_plugin_groupfile_console v2.6.0）改造方案

- 日期：2026-10-26
- 目标：解决 cloud 部署下发文件 EACCES；补 chmod
- 顺序：群文件管理 · 第 6 个

---

## 现状（已读源码核实）

| 项 | 现状 |
| --- | --- |
| 数据根 | `data_root()` = `get_astrbot_plugin_data_path()/astrbot_plugin_groupfile_console`（`core/config.py:17-19`），落在 `plugin_data/`（root 私有 755） |
| 发送临时目录 | `data_root()/send_tmp`（`main.py:946-947`，`dest_dir.mkdir` 无 chmod） |
| 发送文件 | `main.py:483` `Comp.File(name=..., file=str(dest))`；`main.py:1662` 还有一条 OneBot 直发（`{"type":"file","data":{"file":str(dest),...}}`） |
| 是否 chmod | ❌ **无 chmod** |
| 清理 | `_delayed_cleanup([dest])` 延迟 10 分钟删；`dest.unlink` 用于直发后即删 |

## 关键结论

groupfile 发文件时，临时文件落在 `plugin_data/astrbot_plugin_groupfile_console/send_tmp/`——这个目录是 root 私有 755，**SnowLuma 非 root 连 `send_tmp` 这一级都进不去**（缺 others-x），必然 EACCES。

这与 jm/eh/wnacg 是同一类问题，且更严重：它的 `send_tmp` 完全在 plugin_data 私有区里。

## 改造内容

### 1. 发送临时目录 chmod（核心）

`main.py:947` `dest_dir.mkdir` 后补：
```python
try:
    dest_dir.chmod(0o777)
except OSError:
    pass
```

并且**逐级父目录**也要能进：`data_root()` 本身如果不给 others-x，SnowLuma 也进不去。建议在 `data_root()` 创建时（或 `_data_root` 首次调用）补：
```python
p = data_root()
p.mkdir(parents=True, exist_ok=True)
try: os.chmod(p, 0o755)   # data_root 至少 others-x
except OSError: pass
```

### 2. 产物文件 chmod

`main.py:483`（`Comp.File`）和 `main.py:1662`（OneBot 直发）之前，对 `dest`：
```python
try:
    os.chmod(dest, 0o666)
except OSError:
    pass
```

> 也可抽 `_ensure_readable(path)` 统一。

### 3. 可选：send_tmp 迁移到共享目录

若希望与漫画四件套完全一致（产物进 SnowLuma 已挂载的 `temp`），可加 `deliver_dir` 配置，把 `send_tmp` 落到 `temp/astrbot_plugin_groupfile_console/`。但这是可选优化，非 EACCES 必需——只要 send_tmp 及其父级 chmod 了，SnowLuma 就能进。

---

## 验证清单

1. `/diag perm` 确认共享目录读写正常（ground truth）
2. 实际从群文件下载并发送：SnowLuma realpath 不再 EACCES
3. `_delayed_cleanup` 10 分钟清理不变

---

## 备注（非 EACCES）

- groupfile 的 `data_root/groups.json` 是配置持久化，**不要把它 chmod 成 777**（会导致群规则可被他人写）。只 chmod 发送临时目录，不 chmod 数据/配置文件。
- 它与 SnowLuma 群文件 API（`upload_group_file`/`get_group_file`）的适配，之前 VM 日志里见过一条 `[群文件] 动作 get_login_info 失败`，这属于功能层面，在 groupfile 专属对话排查。