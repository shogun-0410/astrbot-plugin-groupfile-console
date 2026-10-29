# groupfile_console（v2.6.0）功能测试结果 —— 给 groupfile 专属对话

> 测试环境：AstrBot 4.29.1（VM），mock OneBot 群 900001（**无真实 QQ 群文件 API**）。
> 测试时间：2026-10-26。
> 命令前缀：`/gf`（=/群文件），单字动词（帮助/状态/诊/列/查/删/移/建/整/绑/解/开/关/管/确认/取消）。

## 一、结论速览（受 mock 环境限制，只测到命令层）

| 功能 | 命令 | 结果 | 判定 |
| --- | --- | --- | --- |
| 帮助 | `/gf 帮助` | ✅ 帮助文本完整 | ✅ |
| 诊断 | `/gf 诊` | ✅ 诊断报告正常（OneBot 绑定状态/群配置/最近动作） | ✅ |
| 状态 | `/gf 状态` | ⚠️ 报错 `get_group_root_files 失败: code=4294967189 msg=unknown error` | ⚠️ 需真实 QQ 验证 |
| 文件列/查/删/移/建/整 | — | ⏳ 未测（需真实 QQ 群文件 API） | — |

## 二、发现的问题 / 需注意

### 1. `/gf 状态` 报 unknown error（可能不是 bug，是 mock 环境限制）

`/gf 状态` 会调 `get_group_root_files`（SnowLuma 群文件 API），mock 客户端没实现这个 action → 返回 `code=4294967189 unknown error`。

**需要真实 QQ 环境验证**：真实 SnowLuma 下 `get_group_root_files` 是否正常，`unknown error` 到底是 mock 没实现，还是插件对 SnowLuma 群文件 API 的调用有问题。

### 2. 帮助文本版本号显示 `v2.0`（小瑕疵）

`/gf 帮助` 输出 "群文件管理 v2.0"，但实际版本是 v2.6.0。帮助文案里的版本号没更新。

### 3. 诊断里"已配置群 1064704616"与测试群不一致

`/gf 诊` 显示"已配置群 1 个：1064704616"，但测试用的是 900001。说明配置里有历史群的残留，或群绑定是旧数据。属数据问题，不影响功能。

## 三、发送目录（deliver_dir）待办

groupfile 的 `send_tmp` 落 `plugin_data/astrbot_plugin_groupfile_console/send_tmp/`（私有），cloud 协议端读不到。需新增弹目录（留空= `temp/astrbot_plugin_groupfile_console/`）。注意 `data_root`（groups.json 配置）绝不能 chmod 777。

## 四、待补测（需真实 QQ 群文件）

- 文件列表 / 搜索 / 发送 / 删除 / 移动 / 建文件夹
- 定时整理流水线
- 规则分类（130+ 类型）
- 多群配置

## 五、总结

groupfile 命令层正常，但**核心功能（群文件操作）在 mock 环境无法验证**，必须在真实 SnowLuma + 真实 QQ 群里测。`/gf 状态` 的 unknown error 需要重点确认是 mock 限制还是真实 bug。