# groupfile_console v2.7.0 功能测试结果（VM 真机）

- 日期：2026-10-29
- 环境：VM `192.168.131.130` · AstrBot 4.29.3 · SnowLuma（小叮当1号 3667544486）
- 测试群：**1128651283「新群测验」**（6 人，专用测试群，非生产群）
- 插件：v2.7.0（zip SHA256 `516a584888bb223952c89471c93a78e82392a7c601bffecf29660aa53adee7e5`）
- 命令注入方式：mock 事件（group=1128651283）+ 插件绑定的真实 herobot 客户端；
  真实上传另用 SnowLuma 自带 OneBot HTTP（`127.0.0.1:3000`，容器内）复核协议端读文件能力。

## 一、结论速览

| 功能 | 命令/操作 | 结果 | 判定 |
| --- | --- | --- | --- |
| 状态 | `/gf 状态` | 真实群 34 个文件 / 0.22GB/10GB；发送目录「共享目录」、保留 60s | ✅ |
| 诊断 | `/gf 诊` | v2.7.0；目录/来源/共享/清理策略/上次清扫齐全 | ✅ |
| 列 | `/gf 列` | 真实根目录 34 个文件、大小正确 | ✅ |
| 搜 | `/gf 查 体检` / `/gf 查 v270` | 真实命中（1 个 / 1 个），结果可带序号发送 | ✅ |
| 发文件 | `/gf 查 体检 1` | 下载成功→落共享 temp→协议端 `READ_OK`→真实上传 `message_id=1907567559` | ✅ 无 EACCES |
| 清理延迟 | 保留 60s | +30s 文件仍在；+75s 已删 | ✅ |
| 残留清扫 | 3 小时旧文件 + 新文件 | 启动清扫删旧留新（`发送清理：清扫 1 个 / 保留 1 个`） | ✅ |
| 上传中不误删 | `plan_sweep` 单测 | in-flight 命中不删 | ✅（单测） |
| 清理失败兜底 | 代码路径 | 下载失败即删；直发失败 60s 延迟清理 | ✅（代码审查） |
| 规则 kind | 单测 | `.apk.1` 不再误命中 `xapkz1` | ✅ |
| TTL 无时间 | 单测 | `upload_time=0` 只报告、不删除 | ✅ |
| 整理跳过原因 | 单测 | in_place / no_rule 计数、报告含「跳过原因」 | ✅ |
| 删除 | `/gf 删 1` + `/gf 确认` | 真实删除自己上传的测试文件，成功 1 / 失败 0 | ✅ |
| 建文件夹/移动 | `/gf 移 1 GF测试` | 协议端拒绝：`code=4294967260 只允许群主和管理员操作`；插件错误提示清晰 | ⚠ 环境权限限制 |
| 面板 | 六页 + 发送卡片 | 截图见 `dev_artifacts/screenshots/v270_*.png` | ✅ |
| 单测 | 19 + 15 + 3 项 | 全绿；JS 语法/前端扩展名逻辑通过 | ✅ |

## 二、关键证据

### 1. 发送目录与权限（v2.7.0 实测）

```
$ ls -la .../temp/astrbot_plugin_groupfile_console/send_tmp/
-rw-rw-rw- 1 root root 15826 1793273473267_体检报告_1028_050758.xlsx
$ stat -c '%a %n' .../astrbot_plugin_groupfile_console .../send_tmp
755 .../astrbot_plugin_groupfile_console
777 .../send_tmp
$ docker exec snowluma test -r /AstrBot/data/temp/.../xxx.xlsx
READ_OK
```

真实 OneBot 上传（用插件产物的原始路径）：

```
file:///AstrBot/data/temp/astrbot_plugin_groupfile_console/send_tmp/1793271592792_体检报告_1028_050758.xlsx
→ {"status":"ok","retcode":0,"data":{"message_id":1907567559}}
```

对照（旧行为，v2.6.2）：

```
file:///AstrBot/data/plugin_data/astrbot_plugin_groupfile_console/send_tmp/...
→ ENOENT: no such file or directory, realpath '/AstrBot/data/plugin_data/...'
```

### 2. 清理时序（保留 60s）

```
11:31:35 发送完成，文件生成
11:32:05 (+30s) 文件仍在
11:32:51 (+76s) 文件已删除；同目录 fresh_v270_test.tmp（新文件）保留
```

### 3. `/gf 状态` 输出（节选）

```
📊 群 1128651283 文件状态
· 文件数：34 个 / 文件夹 0 个
· 空间：0.22GB / 10.00GB
· 发送临时文件：共享目录 · 保留 60s · 残留 1h
  /AstrBot/data/temp/astrbot_plugin_groupfile_console/send_tmp（默认共享 temp）
```

### 4. 整理/规则单测

```
test_kw_treats_dot_as_literal ... ok
test_regex_still_regex ... ok
test_missing_kind_backward_compatible ... ok
test_bad_regex_falls_back_to_substring ... ok
test_no_timestamp_skipped ... ok      # TTL：无时间不删
test_old_enough_deleted ... ok
test_in_place_and_no_rule_counted ... ok
test_inflight_never_removed ... ok
```

## 三、遗留与限制

1. **建文件夹 / 移动的真实操作无法在本群验证**：机器人 小叮当1号 不是 1128651283 的群主/管理员，
   SnowLuma 返回 `只允许群主和管理员操作`。插件已把该错误原样提示；等有管理员权限的群再复验移动。
2. **面板真实登录未验证**：VM dashboard 是用户密码（无明文），本次用 `uitest + mock 桥 + Edge 无头截图`
   验证六页渲染与新卡片；后端接口按代码注册（`/send_tmp`、`/send_tmp_sweep`），
   实际线上保存/清理建议 cloud 面板点一次。
3. **cloud 侧**：本会话无 cloud 终端（手册：用户只能操作 WebUI），未直接操作生产机。
   给出 cloud 逐项清单（§四）。
4. 测试群多出的测试文件已删除：`gf_move_test.txt`、`v270_realpath_test.xlsx`；
   空文件夹 `GF测试` 因无权限未创建成功，不存在残留。

## 四、cloud 上线验证清单（交给统筹/用户）

1. 装 v2.7.0 → 面板「插件 → 群文件管理 → 控制台」`Ctrl+F5`，版本显示 v2.7.0。
2. 「定时与维护 → 发送临时文件」应显示 **共享 temp**、保留 600s、残留 1h；保存一次再刷新，值应保持。
3. 群里 `/gf 状态` → 发送目录一行显示「共享目录」。
4. `/gf 列` → 真实文件；`/gf 查 <关键词> 1` → 群内收到文件，日志无 `EACCES/ENOENT`。
5. 把保留秒数改 60 → 再发一次 → 约 1 分钟后共享目录自动清空（面板点「立即清理残留」也能清）。
6. 若日志出现 `协议端可能读不到` 警告：说明 `deliver_dir` 被改成非共享目录，改回留空即可。
