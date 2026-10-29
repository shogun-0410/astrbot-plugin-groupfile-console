# 21 · groupfile_console v2.9.0 交付通知（精简版 · 给统筹复测）

- 日期：2026-10-30
- 插件：`astrbot_plugin_groupfile_console`（03_群文件）
- 版本：**v2.9.0**
- 安装包：`GitHub发布\03_群文件\groupfile_console\插件包\astrbot_plugin_groupfile_console_v2.9.0.zip`
  （191,874 字节 / 19 文件 / SHA256 `74c3d91853c433ec7d3bea9bf7c7c53f9a569e6a48be30f8ecbe25148beea51e`）
- 仓库（公开）：https://github.com/shogun-0410/astrbot-plugin-groupfile-console
- Release：v2.9.0
- 方案：`astrbot_vm\方案\03_群文件\09_方案-v2.9.0.md`
- 实测：`astrbot_vm\方案\03_群文件\groupfile_console_v2.9.0_功能测试结果.md`

## 这版是减法

删（v2.8.0 加过头、实际没用）：回收站与还原、任务中心与任务页、容量趋势、
权限分级 view/move/delete/admin、规则优先级 priority、独立审计页与运维设置页。

保留（都是修 bug 或低成本）：按事件平台绑定、按群互斥锁、秒/毫秒时间归一、
配置校验、扫描深度/环保护、TTL 单次删除上限、确认绑定用户+群、`/gf 列` 分页、
`/gf 预览` 试运行、定时补跑、`/gf 审计` 命令（审计文件仍在，只是不占一个面板页）。

界面：新增 8px 间距体系变量，容器 32/24/64、卡片 16×18 + 间距 16、
按钮与输入框统一 32 高、工具栏 8、表单栅格 8×16；修掉无效的 `align-align`、
重复 `.btn-sm` 与冲突的 `.toolbar{gap:10px}`。

## 真机已验证（群 1128651283）

- 加载 `(2.9.0)` 并绑定 herobot；
- `groups.json` 升 version 4，`perms/ops` 清空、规则/定时/TTL 保留；
- `/gf 状态`、`/gf 列`（分页 1/2）真实数据正常；
- 管理员 `/gf 预览`「将移动 0 / 无规则 34」不动文件；`/gf 审计` 5 条；
- 非管理员删除/预览/审计统一拒绝；
- 44 项单测全绿；面板六页截图 `v29_*.png`。

## 请统筹复测（cloud）

1. v2.8.0 → v2.9.0 覆盖升级：任务/审计/趋势页消失，规则/定时/TTL 不丢。
2. 发文件链路仍无 EACCES（未改发送目录逻辑）。
3. 面板观感：按钮与输入框同高、卡片间距一致、窄屏可滚动。