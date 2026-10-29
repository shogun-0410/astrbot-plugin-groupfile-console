# groupfile_console v2.9.2 功能测试结果（五页统一 + 批量编辑）

- 日期：2026-10-30
- 环境：VM 192.168.131.130 · AstrBot 4.29.3 · SnowLuma
- 插件：v2.9.2（zip SHA256 `52e147b539a89d18fd3c871d2bcf10bdbe48199d788cd704073e1c231d7fd3ef`）

## 一、结论速览

| 项 | 结果 | 判定 |
| --- | --- | --- |
| 规则行间距 | `padding 12×16 / margin 8`，行内 `gap 8` | ✅ |
| 文件行间距 | `gap 8 / padding 8×12 / margin 8` | ✅ |
| 类型筛选条 | `gap 4 / margin 8` | ✅ |
| 维护页（定时/容量/老文件/查重/发送） | 五个子页统一样式 | ✅ |
| 群列表行 | `gap 8 / padding 8×12 / 行距 8` | ✅ |
| 单个群编辑 | 折叠卡不变（含导出导入折叠、危险区） | ✅ |
| **批量编辑** | 列表勾选 + 全选 + 启用/停用/追加/移除管理员 | ✅ |
| 批量计数 | 「已选 N 个群」实时更新 | ✅ |
| 单测 | 44 项全绿 | ✅ |
| VM 部署 | `(2.9.2)` 加载 + 绑定 herobot | ✅ |

## 二、关键证据

### 1. 间距（CSS 实际值）

```
.rules-row        padding: 12px 16px;  margin-bottom: 8px
.rules-row .row   gap: 8px
.file-item        gap: 8px; padding: 8px 12px; margin-bottom: 8px
#f-files          gap: 8px
.type-bar         gap: 4px; margin: 8px 0 0
.sf-head          gap: 8px; margin-bottom: 8px
.dsl-row          gap: 8px; padding: 8px 12px
.cron-wrap/-list  gap: 8px
.admin-chips      gap: 8px; margin: 8px 0
.copy-groups      gap: 8px 16px
.r-ext-panel-head gap: 8px
.g-scope-row      gap: 8px; padding: 8px 12px; margin-bottom: 8px
```

### 2. 批量编辑（截图 `v292_groupbatch.png`）

```
群基础设置
 ├─ 单个群 / 批量编辑（二级 chip）
 ├─ 说明：勾选多个群后，用下方批量操作一次应用（只改勾选的群）   未选群
 ├─ 群列表：[ ] 测试群（10001）…… 启用中 · 管理员 0 人 · 规则 7 条
 │          [ ] 测试群2（10002）…… 启用中 · 管理员 0 人 · 规则 1 条
 └─ 操作栏：全选 · 启用选中 · 停用选中 · [管理员 QQ] · 追加管理员 · 移除管理员
```

### 3. 截图清单

| 文件 | 内容 |
| --- | --- |
| `v292_groupedit.png` | 单个群编辑（折叠卡） |
| `v292_groupbatch.png` | 批量编辑 |
| `v292_rules.png` | 分类规则（规则行间距） |
| `v292_maintcron.png` | 定时与维护 |
| `v292_files.png` | 文件管理（分页 + 筛选条） |

### 4. 单测与部署

```
tests/test_ext_formats.py       19 OK
tests/test_v270.py              15 OK
tests/test_scheduler_v270.py     3 OK
tests/test_v280.py               7 OK
pyflakes：无输出；node --check app.js：OK

version: 2.9.2
[02:47:13] Plugin astrbot_plugin_groupfile_console (2.9.2) by hero: …
[02:47:45] [群管理] 已绑定 OneBot 客户端（herobot）
```

## 三、留待统筹复测

1. 批量编辑在真实多群下勾选/启用/停用/管理员增删的效果（VM 测试群只有 2 个）；
2. 五页在窄屏（<900px）下的换行与滚动；
3. cloud 升级回归 v2.9.1 → v2.9.2。
