# QQ 本地数据机器人 PowerShell 手动运行手册

当前命令规则和完整使用说明以 [机器人使用说明总览](机器人使用说明总览.md) 为准：群内和私聊命令统一使用 `#` 前缀；报名和取消报名只在原消息添加“续标识”表情。

本文档是当前推荐的运行方式：当前只启用异环 NTEUID，因此从全部关闭状态启动时使用三个 PowerShell 窗口：Core、NoneBot、NapCat。机器人 QQ 为 `2120682836`。异环游戏接口默认对全部 `MANAGED_GROUP_IDS` 开放，可用 `#功能范围 游戏接口` 单独控制。原神和鸣潮暂时关闭。

QQ 登录、二维码、滑块和设备验证必须由用户在本机人工完成。本项目不会绕过 QQ 风控，也不会把密码或 Token 写入本文档。

## 一、首次准备

确认以下路径存在：

```text
C:\Users\59586\Documents\通讯程序集成管理机器人
C:\Users\59586\Documents\通讯程序集成管理机器人\.venv\Scripts\python.exe
C:\Users\59586\Documents\通讯程序集成管理机器人\NapCat.Shell\launcher.bat
```

确认 NapCat 的 OneBot v11 WebSocket 客户端已配置为：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

不要把这个 `ws://` 地址直接输入 PowerShell。它只填写在 NapCat 的 WebSocket 客户端配置中。

如果 `.env` 中 `GSUID_ENABLED=true`，首次使用前还要安装游戏扩展：

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install_gsuid.ps1
```

安装脚本会在本地拉取 `gsuid_core`、GenshinUID、XutheringWavesUID 和 NTEUID，不会把它们复制进当前项目仓库。

## 二、从一键管理器切换

如果一键管理器正在运行，先关闭它及其子进程。下面的命令只查找 `QQBotManager.exe`，不会主动关闭 QQ：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq "QQBotManager.exe" } |
  Select-Object ProcessId, ParentProcessId, CommandLine
```

确认这些进程确实都是管理器后执行：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq "QQBotManager.exe" } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F }
```

如果 NapCat 机器人实例仍在运行，先在 NapCat 控制台按 `Ctrl+C`，或正常关闭机器人 QQ 窗口。不要使用 `NapCat.Shell\KillQQ.bat`，它可能关闭所有 QQ 账号。

## 三、启动异环 Core

当前 `.env` 已启用 Core。启动前先验证异环专用模式。打开 PowerShell 窗口一：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
& "$Root\.venv\Scripts\python.exe" scripts\validate_nte_mode.py --root .
.\scripts\start_gsuid_core.ps1
```

验证成功应显示：

```text
NTE-only game mode valid: Core=enabled, GenshinUID=disabled, XutheringWavesUID=disabled, NTEUID=enabled, prefixes=nte
```

需要群友登录异环时，在启动 Core 后另开一个窗口执行：

```bat
start_nte_tunnel.bat
```

它会生成公网 HTTPS 登录地址并自动写回 NTEUID 配置；每次重启隧道后重跑同一条命令即可。关闭用 `stop_nte_tunnel.bat`，单独改地址用 `set_nte_login_url.bat`。

Core 日志应出现以下内容后保持窗口打开：

```text
Skip disabled plugin: GenshinUID
Skip disabled plugin: XutheringWavesUID
插件 NTEUID 导入成功
[NTEUID] 启动完成
AI总开关已关闭
Uvicorn running on http://127.0.0.1:8765
```

Core 必须先于 NoneBot 启动。不要在 Core 已运行时再次执行此命令，否则会占用 `8765` 端口。

## 四、启动机器人

打开 PowerShell 窗口二，执行：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& "$Root\.venv\Scripts\python.exe" scripts\validate_qq_config.py --env "$Root\.env"
& "$Root\.venv\Scripts\python.exe" -u -m bot
```

看到下面这些日志后，不要关闭此窗口：

```text
Succeeded to load plugin "commands"
Succeeded to load plugin "stats"
Uvicorn running on http://127.0.0.1:8080
```

如果看到 `Succeeded to load plugin "GenshinUID"`，这里指的是 NoneBot 的 GsUID Core 连接器，不是原神游戏插件；原神游戏插件已经由 Core 跳过加载。

这个窗口就是机器人服务窗口。需要停止机器人时，应在此窗口按 `Ctrl+C`。

## 五、启动 NapCat

打开 PowerShell 窗口三，执行：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
$NapCat = Join-Path $Root "NapCat.Shell"
Set-Location -LiteralPath $NapCat
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& ".\launcher.bat" -q 2120682836
```

如果出现管理员权限窗口，点击“是”。如果 NapCat 要求登录、二维码、滑块或设备验证，请在 NapCat/QQ 窗口中人工完成。

参数 `-q 2120682836` 用于选择机器人 QQ 的本地登录会话。正常情况下日志应出现“正在快速登录”。如果本地登录会话已经过期，仍可能需要人工验证一次，这是 QQ 安全机制，不能由机器人绕过。

## 六、连接验收

机器人窗口应出现：

```text
WebSocket ... accepted
Bot 2120682836 connected
```

NapCat 窗口应显示 OneBot v11 反向 WebSocket 已启动。

启用 GenshinUID Core 时，机器人窗口还应看到官方 Core 连接器成功连接 `127.0.0.1:8765`。当前使用 NTE 前缀检查异环，可带或不带 `#`，大小写不敏感；`gs`/`ww`/`yh` 前缀不响应。游戏插件的完整维护和测试流程见：[GenshinUID 游戏插件使用维护手册](GenshinUID游戏插件使用维护手册.md)。

在任一 A海岸群（1128870029、1077416717、1083457871、1090284567、278824712）发送：

```text
#机器人状态
#发言排行 日
#A海岸发言排行 周
#查重1 1067772451 其他管理群号
#查重2 1067772451 其他管理群号
#查重3 1067772451 其他管理群号
#查重4 1067772451 其他管理群号
```

异环普通命令不需要 `@` 机器人，直接发送即可（NTE 前缀可带或不带 `#`，大小写不敏感）：

```text
#nte帮助
#nte登录
#nte查看
#nte查询
#nte角色
#nte角色练度
#nte实时信息
#nte刷新令牌
#nte公告
#nte角色列表
#nte早雾配队
#nte早雾攻略
#nte早雾图鉴
```

异环默认全部管理群响应，普通群成员可以使用普通异环功能；范围可用 `#功能范围 游戏接口 添加|移除` 单独调整（对应 `.env` 的可选 `GAME_API_GROUP_IDS`，不填写即全部管理群），热开关用 `#游戏接口 状态|开启|关闭`（对应 `.env` 的 `GAME_API_ENABLED`）。范围和热开关修改都会实时写回 `.env`，保证 `.env` 始终与当前运行状态一致。`BOT_OPERATOR_IDS` 只控制基础机器人管理命令。原神和鸣潮命令当前不响应：

```text
#gs帮助
#ww帮助
```

群内和私聊命令统一使用 `#` 前缀。跨群查重、白名单和管理员热更新命令只能由 `.env` 中 `BOT_OPERATOR_IDS` 配置的 QQ 执行。

统计模式说明：A海岸统计默认开启，只监听固定的五个 A海岸群并仅保存 QQ 号、最后昵称和每日发言次数，不保存消息内容。日、周、月、总榜各显示前 100；群内榜仅统计当前群，A海岸榜才合并五群。查重使用 `DUPLICATE_GROUP_IDS`，本地小游戏使用 `GAME_GROUP_IDS`，异环游戏接口使用 `GAME_API_GROUP_IDS`（默认全部管理群），活动使用 `ACTIVITY_GROUP_IDS`。

范围是硬性开关，不是显示用途。A海岸统计范围固定，其他群绝不进入统计表；查重和白名单只在 `DUPLICATE_GROUP_IDS` 内执行，本地小游戏在 `GAME_GROUP_IDS` 外不执行，异环 NTE 命令在游戏接口范围外不执行，活动命令在 `ACTIVITY_GROUP_IDS` 外不执行。被动互动只在持久化的被动互动群范围内读取普通非命令消息；普通群内管理命令使用 `#` 前缀，异环 NTE 支持带或不带 `#`。

跨群活动使用独立范围 `ACTIVITY_GROUP_IDS`。不填写时继承全部 `MANAGED_GROUP_IDS`；填写后只有其中的群可以查看、创建和参与活动。例如只在两个测试群开放：

```dotenv
ACTIVITY_GROUP_IDS=1067772451,278824712
```

活动创建者必须是活动群群主、管理员或机器人所有者。普通成员可以查看活动、报名和取消自己的报名。跨群活动不会执行踢人、禁言、加群审批等管理操作。

### 跨群活动手动测试

NoneBot 日志出现 `Succeeded to load plugin "activities"` 后，在活动开放群发送：

```text
#活动帮助
#活动大厅
#创建活动 周末联动 | 2026-07-20 20:00 | 2026-07-20 22:00 | 1067772451,278824712 | 通报 | 周末团建
```

`#活动帮助` 会返回一个合并转发：第一页是本地帮助图片，第二页是可复制文字。创建成功后的 ID 是数字，例如显示 `ID：500` 时使用 `#报名 500`、`#活动详情 500`。报名或取消报名只给原消息添加续标识表情，不发送文字确认。随后继续测试：

```text
#活动详情 500
#报名 500
#查看名单 500
#获奖名单 500
#我的活动
#取消报名 500
```

抽奖活动示例：

```text
#创建活动 跨群抽奖 | 2026-07-20 20:00 | 2026-07-20 22:00 | 1067772451,278824712 | 抽奖 | 报名抽奖 | 一等奖=1;二等奖=2
```

使用多个 QQ 账号在不同活动群报名，检查报名序号按先后递增、重复报名不会新增序号、名单图片包含昵称和头像、其他活动群的 QQ 号默认脱敏，以及 `#活动大厅` 是否显示跨群参与人数。使用创建者账号测试：

```text
#修改活动 500 | 新标题 | 2026-07-20 21:00 | 2026-07-20 22:00 | 新说明
#提前结束 500
#取消活动 500 测试取消
```

状态变化和抽奖结果会以本地详情图片广播到该活动的所有群，图片包含活动 ID 和操作示例，不会发送纯文字通知。结束的抽奖活动会将活动详情与按奖项分区的获奖名单图片合并转发；每位获奖者独占一行，不会挤在详情字段中。普通成员查询 `#获奖名单 500` 时 QQ 号脱敏；活动创建者和机器人所有者可在群内或私聊查询完整信息。机器人所有者可在私聊中创建和管理活动，活动创建者可在私聊中管理自己创建的活动；活动目标群仍必须属于 `ACTIVITY_GROUP_IDS`。活动结束后再次发送 `#报名 500` 应提示报名已关闭；重复执行结束或开奖不会重复累计、重复开奖或重复广播。重启 NoneBot 后，活动状态、报名数据、中奖结果和待发送广播仍从本地 SQLite 恢复。

活动大厅、活动详情和报名名单图片均使用本机 Pillow 确定性绘制，不调用互联网大模型或生图 AI，不消耗模型 Token。当前主题采用浅粉、白色、莓红和淡紫色，并用标题、标签、正文与操作示例的不同字重和颜色提升可读性。头像下载属于普通头像图片网络请求，仅在缓存缺失或过期时发生。

例如当前配置：

```dotenv
DUPLICATE_GROUP_IDS=1128870029,1077416717,1083457871,1090284567,278824712,1102823315,594225057,1067772451
GAME_GROUP_IDS=1067772451,278824712
```

在 A海岸群内使用统计命令：

```text
#发言排行 月
#A海岸发言排行 总
```

QQ 群后台活跃概况采集保持关闭，不需要配置手机端接口。

查重命令分为四种：`#查重1 群号1 群号2 [群号3 ...]` 会让所有指定群互相查重；`#查重2 起点群号 目标群号1 [目标群号2 ...]` 只检查起点群成员是否出现在后续目标群；`#查重3` 是忽略白名单的全量互查；`#查重4` 是忽略白名单的起点/目标查重。旧命令 `#查重` 仍兼容为 `#查重2`。省略群号时，`#查重1/#查重3` 使用全部管理群互相查重，`#查重2/#查重4` 使用全部管理群并把配置列表中的第一个群作为起点群。

查重结果会把全部重复成员合并为一张本地图片，图片高度随人数增长；图例高度按实际群数和换行动态匹配。文字回退内容超过消息长度限制时也会自动分段发送，不再提供 `页2` 分页。同一次扫描结果使用临时快照，5 分钟内不会因群成员变化而改变总数。图片报告顶部会显示群编号映射；`#查重1` 中所有群标记为“参与查重”，`#查重2` 中会标记“起点”和“目标”。

## 七、后台运行方式

如果不需要在 PowerShell 中持续显示机器人日志，可以使用脚本后台启动机器人：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\start.ps1
Get-Content .\logs\bot.out.log -Wait
```

NapCat 仍需在另一个窗口手动启动：

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人\NapCat.Shell"
& ".\launcher.bat" -q 2120682836
```

当前只启用异环时，后台启动机器人前先在另一个窗口运行 Core：

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人"
.\scripts\start_gsuid_core.ps1
```

后台脚本只负责机器人，不负责 NapCat。

## 八、完整关闭流程

当前启用异环 Core，推荐按“先 NapCat、再机器人、最后 Core”的顺序关闭。

1. 在 NapCat 控制台按 `Ctrl+C`，或正常关闭机器人 QQ 窗口。
2. 等待 NapCat 不再发送消息。
3. 回到机器人 PowerShell 窗口按 `Ctrl+C`。
4. 等待出现 Uvicorn shutdown 或进程返回 PowerShell 提示符。
5. 如果机器人是通过 `start.ps1` 后台启动的，执行：

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\stop.ps1
```

6. 回到 Core 窗口按 `Ctrl+C`；如果 Core 窗口无响应，执行：

```powershell
.\scripts\stop_gsuid_core.ps1
```

如果 Core 因配置修改需要重启，必须先停止旧 Core，再按“Core -> NoneBot -> NapCat”顺序重新启动；不要重复启动已有的 NoneBot 或 Core。

确认 8080 已释放：

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
```

没有输出表示机器人监听服务已经停止。不要使用以下命令关闭 QQ：

```powershell
Stop-Process -Name QQ -Force
```

它会误关闭普通 QQ 账号。

## 九、进程卡住时的清理

只在机器人 PowerShell 无法响应 `Ctrl+C` 时使用。先查看精确进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq "python.exe" -and $_.CommandLine -match "-m bot" } |
  Select-Object ProcessId, ParentProcessId, CommandLine
```

确认 PID 是机器人后，只停止该 PID：

```powershell
Stop-Process -Id <机器人PID> -Force
```

不要根据进程名称批量停止 QQ，也不要删除 `data\bot.db`。数据库、群配置和统计数据都保存在本地。

## 十、常见问题

### GenshinUID Core 无法连接

确认 Core 已先启动并监听 `127.0.0.1:8765`，`.env` 中的 `gsuid_core_host`、`gsuid_core_port` 与 Core 配置一致。Core 与机器人同机时 `gsuid_core_ws_token` 可以为空；如果 Core 配置了 `WS_TOKEN`，两边必须完全一致。

### PowerShell 禁止运行脚本

在当前 PowerShell 窗口执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

关闭窗口后，该设置自动失效，不会修改系统全局策略。

### 把 `ws://...` 输入 PowerShell 后报错

这是正常现象。WebSocket 地址不是 PowerShell 命令，只需要填写在 NapCat WebSocket 客户端配置中。

### NapCat 显示没有 `-q` 指令

必须从 NapCat 目录执行：

```powershell
& ".\launcher.bat" -q 2120682836
```

不要只执行 `.\launcher.bat`，否则 NapCat 会进入二维码登录选择流程。

### NapCat 显示 WebSocket 已启动，但机器人没有回复

先检查机器人窗口是否包含：

```text
Uvicorn running on http://127.0.0.1:8080
Bot 2120682836 connected
```

如果没有 `Bot ... connected`，优先检查机器人窗口是否仍在运行、NapCat URL 是否为 `ws://127.0.0.1:8080/onebot/v11/ws`，以及两边 Token 是否一致。不要把 Token 粘贴到群聊或本文档中。

### 需要备份本地数据

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\backup.ps1
```

备份不会上传数据，也不会删除当前数据库。
