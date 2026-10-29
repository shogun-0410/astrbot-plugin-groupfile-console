# groupfile_console v2.9.1 功能测试结果（群配置页）

- 日期：2026-10-30
- 环境：VM 192.168.131.130 · AstrBot 4.29.3 · SnowLuma
- 插件：v2.9.1（zip SHA256 `c3b4b6e2d9187e11da5b54dc5f191ad2c87b8f74f3acdf2a6930eaead5a441b7`）
- 范围：只改「群配置」页；其它五页未动

## 一、结论速览

| 项 | 结果 | 判定 |
| --- | --- | --- |
| 入口间距 | chip 收进同卡，上 16 / 下 12 | ✅ |
| 群行间距 | 行内 8、行间 8、padding 8×12 | ✅ |
| 统计数位置 | 标题行右侧，与搜索框分离 | ✅ |
| 手动添加 | 默认收起，展开 3 列栅格 | ✅ |
| 群卡头部 | 头像 + 群号 + 备注（两行）+ 备份/折叠 | ✅ |
| 导出/导入 | 收进 `导出 / 导入配置` 折叠区 | ✅ |
| 删除群 | 底部危险区，红色按钮 | ✅ |
| **点「配置」跳转** | 修复前 `ReferenceError`；修复后进入编辑页并渲染卡片 | ✅ |
| 勾选启用/停用 | 行内局部更新，不整页重载 | ✅ |
| 单测 | 44 项全绿 | ✅ |
| VM 部署 | `(2.9.1)` 加载 + 绑定 herobot | ✅ |

## 二、关键证据

### 1. 修复「配置」按钮（原 bug）

```
app.js:1057  旧：loadGroups().then(() => switchToEdit(gid));   // gid 未定义
app.js:1064  新：loadGroups().then(() => switchToEdit(r.id));
```

无头 Edge DOM 断言（点第一行「配置」后）：

```
<body data-pane="block" data-sel="10001" data-card="yes" data-view="groupjump-ready">
```

即：编辑页可见、下拉选中 10001、群卡已渲染。修复前此操作抛 `ReferenceError: gid is not defined`，页面无反应。

### 2. 勾选不再整页重载

```js
// 旧：await loadGroups();   → 重新请求 + 全量重绘，搜索框失焦、列表跳动
// 新：更新 groupsData → draw() 前重建 cfgMap → 重绘列表，不请求接口
```

### 3. 间距

```css
.g-shell{padding:var(--pad-card) 18px}
.g-subnav{margin-bottom:var(--sp-4)}        /* 16px */
.subnav{margin:0 0 var(--sp-3)}             /* 12px */
.g-scope-row{padding:8px 12px;gap:var(--sp-2);margin-bottom:var(--sp-2)}
.g-add-row{margin-top:var(--sp-3)}          /* 12px */
```

截图：`v291_groups.png`（管理范围）、`v291_groupedit.png`（群基础设置）。

### 4. 单测与静态检查

```
tests/test_ext_formats.py       19 OK
tests/test_v270.py              15 OK
tests/test_scheduler_v270.py     3 OK
tests/test_v280.py               7 OK
pyflakes：无输出
node --check app.js：OK
```

### 5. VM 部署

```
version: 2.9.1
[02:15:18] Plugin astrbot_plugin_groupfile_console (2.9.1) by hero: 群文件管理…
[02:15:50] [群管理] 已绑定 OneBot 客户端（herobot）
```

## 三、留待统筹复测

1. cloud 覆盖升级 v2.9.0 → v2.9.1，强刷面板后看两页间距与折叠区；
2. 点「配置」进编辑、勾选/取消即时生效；
3. 若希望「群基础设置」也支持一次改多个群，可下一版加成批量表单（当前是单群编辑）。
