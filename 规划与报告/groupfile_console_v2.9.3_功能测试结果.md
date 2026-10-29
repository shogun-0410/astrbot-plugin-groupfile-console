# groupfile_console v2.9.3 功能测试结果（导出/导入收敛）

- 日期：2026-10-30
- 环境：VM 192.168.131.130 · AstrBot 4.29.3 · SnowLuma
- 插件：v2.9.3（zip SHA256 `32cb863c1450280ebe1a9ff795b2c707c63d124d81436cae29f2d2fd701b5dd3`）
- 触发：用户报告「点配置备份后，上面还有选择文件未选择文件」

## 一、结论速览

| 项 | 结果 | 判定 |
| --- | --- | --- |
| 群卡「⋯ 配置备份」死按钮 | 已删除（DOM 中不存在） | ✅ |
| 群卡备份展开 | 只见「导出本群配置 / 导入本群配置」两个子折叠，不再同屏铺开双面板 | ✅ |
| 导出交互 | 展开即自动生成 JSON，无「生成并显示」重复按钮 | ✅ |
| 导入「取消」 | 改为「清空」：清空文本 + 收起 + 清提示 | ✅ |
| 总览备份 | 改两级折叠，删「生成并显示」「导入（覆盖全部）」两个开关 | ✅ |
| 维护页备份 | 顶部两按钮收进「备份这些设置」折叠节 | ✅ |
| 三处范围文案 | 本群 / 所有群 / 维护四块，写清覆盖规则 | ✅ |
| 文件页 | 拆成浏览与筛选 / 批量操作 / 新建文件夹 / 搜索四个分区，分页独立一行 | ✅ |
| 死 id | `g-edit-hint`、`m-hint` 已删；剩余仅 `tab-*` 与 CSS 用的 `g-edit-modes` | ✅ |
| 单测 | 44 项全绿 | ✅ |
| VM 部署 | `(2.9.3)` 加载 + 绑定 herobot | ✅ |

## 二、对账证据

### 1. 死按钮与同屏铺开（修复前）

```
<h2 class="group-head"> … <button class="g-more btn btn-sm">⋯ 配置备份</button> … </h2>
<details class="g-backup">
  <summary>导出 / 导入配置（整群迁移或备份）</summary>
  <div class="g-export-panel">  空白 textarea + 生成并显示 / 复制 / 下载  ← 展开即同时出现
  <div class="g-import-panel">  空白 textarea + 选择文件 / 确认覆盖导入 / 取消
```

`g-more` 在 `app.js` 中无任何监听（脚本对账：该 class 只在 HTML 出现一次）。

### 2. 修复后 DOM

```
<details class="g-backup">
  <summary>备份 <b>本群</b> 配置（导出 / 导入，整群迁移用）</summary>
  <p class="g-backup-scope">范围：本群的规则 / 定时 / 容量 / 查重 / 管理员…</p>
  <details class="g-backup-sec"> <summary>导出本群配置</summary>   ← 展开才生成
  <details class="g-backup-sec"> <summary>导入本群配置（覆盖）</summary>
```

### 3. 三处入口与范围

| 位置 | 标题 | 子折叠 |
| --- | --- | --- |
| 群卡 | 备份 **本群** 配置 | 导出本群配置 / 导入本群配置（覆盖） |
| 总览 | 备份 **所有群** 的配置 | 导出所有群配置 / 导入所有群配置（按群号覆盖合并） |
| 维护页 | 备份 **本群维护设置**（定时/容量/老文件/查重） | 导出维护设置 / 导入维护设置（覆盖这四块） |

### 4. 脚本自检输出

```
dead ids: ['g-edit-modes', 'tab-files', 'tab-groups', 'tab-help', 'tab-maint', 'tab-overview', 'tab-rules']
生成并显示 html: 0 js: 2   ← 只剩两处注释说明
选择文件  html: 2 js: 2    ← 只保留本群/维护两处导入
确认覆盖导入 html: 2 js: 4
```

### 5. 文件页分区（截图 v293_files.png）

```
浏览与筛选   群 / 文件夹 / 类型 / 刷新 / 触发分类
（类型标签行）
上一页   第 1/N 页   下一页
批量操作     删除勾选 / 移动到… / 移动勾选 / 全选当前列表
新建文件夹   输入框 + 新建文件夹
搜索         关键词 + 搜索
```

### 6. 单测与部署

```
tests/test_ext_formats.py       19 OK
tests/test_v270.py              15 OK
tests/test_scheduler_v270.py     3 OK
tests/test_v280.py               7 OK
pyflakes：无输出；node --check app.js：OK
version: 2.9.3
[03:48:34] Plugin astrbot_plugin_groupfile_console (2.9.3) by hero: …
[03:49:06] [群管理] 已绑定 OneBot 客户端（herobot）
```

## 三、留待统筹复测

1. cloud 覆盖升级 v2.9.2 → v2.9.3，强刷后确认群卡 / 总览 / 维护三处备份折叠行为一致；
2. 实际导出 → 复制 → 导入一个群，确认覆盖生效且「清空」复位；
3. 文件页四个分区在窄屏下的换行。
