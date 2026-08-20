# GenshinUID 游戏插件使用维护手册

本文档只介绍本项目中的游戏接口：异环 NTEUID。它运行在本机 `GsUID.Core`，QQ 消息仍通过 NapCatQQ -> OneBot v11 -> NoneBot2 进入。原神 GenshinUID 和鸣潮 XutheringWavesUID 保持关闭，不在本项目范围内。

## 当前状态

- GsUID Core：日志显示版本 `0.10.7`，本地端口 `8765`。
- 原神：已停用，`gs` 命令不响应。
- 鸣潮：已停用，`ww` 命令不响应。
- 异环：已启用，NoneBot 侧识别 `nte` 前缀，可带或不带 `#`，大小写不敏感。Core 必须包含 `nte`/`NTE` 强制前缀；即使旧 Core 配置仍保留 `yh`，也会被 NoneBot 的前置门静默拦截，无法从 QQ 入口触达。
- 游戏接口独立于本地小游戏：不受小游戏范围、小游戏总开关和枝江直播守卫影响。
- 游戏接口范围默认是全部 `MANAGED_GROUP_IDS`，可用 `#功能范围 游戏接口 添加|移除 QQ群号` 单独控制；热开关用 `#游戏接口 状态|开启|关闭`（仅超级管理员）。
- 范围修改实时同步 `.env` 的 `GAME_API_GROUP_IDS`，热开关实时同步 `.env` 的 `GAME_API_ENABLED`；`.env` 始终与当前运行状态一致，修改后重启仍保持。
- 未配置群的游戏消息会在 NoneBot 事件预处理阶段丢弃；不带 `#` 的 `gs/ww/yh` 消息一律不响应。
- `#nte帮助`、糖糖定制排行和单张最强排行由本项目的 `bot.plugins.nte_game_ui` 在连接器前接管；其余异环命令和单张“角色最强面板”继续走上游 NTEUID。
- 排行榜只读 `GsUID.Core\data\GsData.db`，按 `ntegroupmember.bot_id=onebot` 过滤；不会写入或修改 Core 的代码、配置、数据和素材。
- `MANAGED_GROUP_IDS` 最多 10 个群。冒烟测试默认使用其中第一个群。

异环插件源码和游戏资源由上游项目维护，游戏查询需要访问各自的数据服务、图片 CDN 或登录服务。机器人不会保存 QQ 密码，也不会代替用户输入手机号、短信验证码、Cookie 或游戏登录令牌。

本项目不在 GsUID Core 或 UID 插件仓库保留源码补丁。`scripts\run_gsuid_core.py` 启动前从 `bot.integrations.gsuid_core_compat` 安装 Windows 原子写入兼容和禁用插件过滤；排行榜、帮助图、指令接管和 NTE-only 校验也都位于本项目。`scripts\install_gsuid.ps1` 发现任何上游脏文件会直接停止，必须先把必要适配迁到主仓库再更新。

## 首次安装或更新

在项目根目录打开 PowerShell：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\scripts\install_gsuid.ps1
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py --root .
```

安装脚本会同步以下本地目录并安装依赖：

```text
GsUID.Core
GsUID.Core\gsuid_core\plugins\GenshinUID
GsUID.Core\gsuid_core\plugins\XutheringWavesUID
GsUID.Core\gsuid_core\plugins\NTEUID
```

更新前至少关闭 Core；涉及 NoneBot 连接器升级时再重启机器人。更新可能替换插件源码和资源，脚本会把当前 commit 写入 `config\upstream-lock.json`，更新后应重新运行测试。不要把 `GsUID.Core\data` 上传到网盘或 Git；其中可能包含本地账号会话和插件配置。

## 每次启动

### 1. 启动 Core

打开 PowerShell 窗口一：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\start_gsuid_core.ps1
```

等待日志出现以下信息后保持窗口打开：

```text
Core 0.10.5 启动完成
[NTEUID] 启动完成
Uvicorn running on http://127.0.0.1:8765
AI总开关已关闭
```

### 2. 启动本项目机器人

打开 PowerShell 窗口二：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& "$Root\.venv\Scripts\python.exe" scripts\validate_qq_config.py --env "$Root\.env"
& "$Root\.venv\Scripts\python.exe" -u -m bot
```

等待日志出现：

```text
Succeeded to load plugin "scope"
Succeeded to load plugin "GenshinUID"
Uvicorn running on http://127.0.0.1:8080
```

也可以后台启动机器人：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\start.ps1
Get-Content .\logs\bot.out.log -Wait
```

### 3. 启动 NapCatQQ

确认 NapCat 的 WebSocket 客户端地址是：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

这个地址只填写在 NapCat 配置中，不要直接输入 PowerShell。打开 PowerShell 窗口三：

```powershell
$Root = "C:\Users\59586\Documents\通讯程序集成管理机器人"
$NapCat = Join-Path $Root "NapCat.Shell"
Set-Location -LiteralPath $NapCat
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

& ".\launcher.bat" -q 2120682836
```

如果 QQ 要求二维码、滑块或设备验证，只在 NapCat/QQ 窗口中人工完成。`-q 2120682836` 只选择机器人 QQ 的本地会话，不能保证 QQ 每次都免验证。

连接成功的标志是机器人日志出现：

```text
Bot 2120682836 connected
```

## 常用异环命令

异环命令识别 `nte` 前缀，可带或不带 `#`，大小写不敏感（Core 内部前缀是 `nte`/`NTE`，连接器会自动剥掉 `#`）。注意：账号绑定列表命令是 `#nte查看`，不是 `#nte绑定查看`；`#nte绑定` 本身也不是一个可执行命令。

### 登录和账号

```text
#nte登录            生成异环登录链接
#nte查看            查看已绑定的塔吉多账号和角色
#nte刷新令牌        登录失效时尝试续签
#nte退出登录        退出当前账号
#nte全部登出        退出全部账号
```

首次使用时，在群里发送 `#nte登录`，用户打开机器人返回的本地/可访问登录页面，按页面完成登录。登录由账号持有人本人完成，机器人不会代替输入手机号或验证码。登录页面的公网可访问地址需要在 Core 配置中单独设置；如果只监听 `127.0.0.1`，群友无法从自己的电脑打开（本机维护者可用 localhost 完成登录）。

### 查询和图片

```text
#nte帮助
#nte原版帮助        上游帮助图快照
#nte查询            角色总览
#nte角色            角色总览别名
#nte刷新面板        拉取最新角色数据
#nte角色练度        全角色练度图
#nte实时信息        体力/活力信息图
#nte成就            成就进度
#nte房产            房产信息
#nte载具            载具信息
#nte探索            探索进度
```

### 本地排行榜

```text
#nte薄荷排行                 糖糖定制 A 海岸角色评分榜
#nte薄荷群排行               糖糖定制当前群角色评分榜
#nte最强排行                 糖糖定制 A 海岸各角色最强榜
#nte群最强排行               糖糖定制当前群各角色最强榜
```

默认分流固定如下：A海岸五群内发送不带范围参数的排行榜时显示五群总榜，其他管理群显示 bot 全榜；任意群在命令里加 `群` 时只显示本群榜。跨群榜展示“最近更新群”：同一 UID 在多个群参榜时取 `updated_at` 最新的一行，时间相同则取群号较小的一行。排序为评分降序、评级 S/A/B、最近更新群号、UID；若本人不在当前页，图片末尾会附上真实名次。

上游兼容的 `bot` 参数仍可用，但不会出现在新版帮助图或常用指令介绍中。

### 攻略、配队和公告

```text
#nte角色列表
#nte早雾配队        角色名可以替换为其他异环角色
#nte早雾攻略
#nte早雾图鉴
#nte公告
#nte签到日历
#nte签到
```

需要账号的命令在未登录时应返回“请先登录/登录失效”提示；公告、角色列表、配队、攻略和图鉴可以在不登录时测试，但仍需要网络和上游资源服务。

完整命令以 `#nte帮助` 返回的本地帮助图为准；`#nte原版帮助` 发送上游帮助快照。原神和鸣潮保持关闭，`#gs帮助`、`#ww帮助` 不响应。

### 刷新原版帮助快照

上游 NTEUID 更新后，在项目根目录执行一次：

```powershell
.\.venv\Scripts\python.exe scripts\export_nte_original_help.py
```

脚本临时调用上游帮助渲染并写入 `data\nte_original_help.png`；该生成物不提交 Git。机器人不修改 `GsUID.Core` 中的任何文件。

## 公网登录地址（群友登录）

`#nte登录` 生成的链接地址由 NTEUID 配置 `NTELoginUrl` 决定；该值为空时会退回使用 Core 的 `HOST:PORT`。当前 Core 监听 `127.0.0.1:8765`，所以链接是 `http://localhost:8765/nte/i/...`，只有运行机器人的这台电脑能打开。

本项目已内置一键方案：cloudflared 快速隧道 + 只转发 `/nte/*` 的本地受限代理。一次执行即可完成“下载 cloudflared → 启动代理 → 启动隧道 → 探测新的 HTTPS 地址 → 写入 `NTELoginUrl` → 重启 Core”：

```bat
启动工具\21-启动异环登录隧道.bat
```

`启动工具` 中提供三个双击即可用的异环入口：`21-启动异环登录隧道.bat`、`22-关闭异环登录隧道.bat`、`23-设置异环登录地址.bat`。它们内部会自动执行 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`，不需要手打权限命令；等价底层脚本仍在 `scripts\` 下。

脚本第一次会从 GitHub 下载 `tools\cloudflared.exe`；之后每次运行秒级完成。

### 关键点

- 快速隧道地址形如 `https://xxxx.trycloudflare.com`，**每次重启隧道地址都会变**。地址变化后，双击 `启动工具\21-启动异环登录隧道.bat` 即可：脚本会停掉旧隧道、拿到新地址、写入 `NTELoginUrl` 并自动重启 Core 生效。
- 登录接入方式保持默认 `NTELoginTransport=local`，只换对外域名；旧链接本身只有 10 分钟有效期，地址变了直接重新发 `#nte登录` 即可。
- 安全：cloudflared 指向的是本地 `127.0.0.1:18765` 受限代理（`scripts\nte_login_proxy.py`），该代理只放行 `/nte/i/*`、`/nte/done`、`/nte/sendSmsCode`、`/nte/login`、`/nte/status/*`，其余路径一律 404。Core 的 `/ws/*` 和 `/api/*` 不会经隧道暴露，Core 也继续只监听 `127.0.0.1`。
- 关闭隧道和代理：双击 `启动工具\22-关闭异环登录隧道.bat`。关闭后 `#nte登录` 会退回到 localhost 链接，并写入“禁用标记”，watchdog 不会再把隧道自动拉起。
- 机器人或电脑重启后，先正常启动 Core 和机器人，再双击一次 `启动工具\21-启动异环登录隧道.bat`。
- `watch_napcat` 现在会顺带守护隧道：每 30 秒检查 cloudflared、登录代理和 18765 端口；异常时自动重跑启动脚本（15 分钟冷却）。你主动用 `启动工具\22-关闭异环登录隧道.bat` 关闭后不会被自动拉起。

### 重新配置流程（隧道地址变化后）

```powershell
启动工具\21-启动异环登录隧道.bat
# 脚本输出新的 https://xxxx.trycloudflare.com 后，直接在群里再发一次：
# #nte登录
```

需要固定域名时，再把快速隧道换成 Cloudflare 命名隧道（需要 Cloudflare 账号），受限代理保持不变；当前无域名环境下使用快速隧道即可。

## 测试

### 自动单元测试

```powershell
Set-Location -LiteralPath "C:\Users\59586\Documents\通讯程序集成管理机器人"
.\.venv\Scripts\python.exe -m pytest
```

### NTEUID OneBot 冒烟测试

保持 Core 和机器人运行。推荐使用独立的第二实例冒烟测试，它使用自己的端口 `18081`、临时数据库和独立 Core Bot ID，不会断开 NapCat 或 QQ 登录：

```powershell
$OutputEncoding = [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
.\.venv\Scripts\python.exe -u scripts\smoke_game_api.py --server
# 另开窗口执行：
.\.venv\Scripts\python.exe -u scripts\smoke_game_api.py --client
```

客户端会覆盖：`#nte`/`nte`/`#NTE`/`NTE` 正例（新版/原版帮助、角色榜、最强榜、公告、角色列表、配队、攻略、图鉴、查看、登录、退出登录）、`#游戏接口 状态|开启|关闭`、`#功能范围 游戏接口 添加|移除`，以及 `#gs`/`#ww`/`#yh`、范围外群等负例，全部 `PASS` 才算通过。

如需使用与 NapCat 同路径的完整 OneBot 冒烟测试，也可以运行旧脚本。由于 NapCat 与冒烟脚本不能同时占用同一个 OneBot `self_id` 连接，运行前先暂时关闭 NapCat 的 WebSocket 客户端；测试完成后再启动 NapCat：

```powershell
.\.venv\Scripts\python.exe -u scripts\smoke_test_gsuid.py
```

脚本默认只测试异环 NTEUID，会自动使用 `.env` 中第一个管理群和第一个操作者 QQ，覆盖帮助、登录、未登录提示、公告、列表、配队、攻略和图鉴。所有命令都带 `#nte` 前缀。图片或公告下载命令最多等待 25 秒。原神和鸣潮已停用，不纳入通过标准。

确认两个已停用插件和无效前缀没有响应：

```powershell
.\.venv\Scripts\python.exe -u scripts\smoke_test_gsuid.py `
  --command "#gs帮助" --command-timeout 8 --idle-timeout 3 --expect-no-output
.\.venv\Scripts\python.exe -u scripts\smoke_test_gsuid.py `
  --command "#ww帮助" --command-timeout 8 --idle-timeout 3 --expect-no-output
.\.venv\Scripts\python.exe -u scripts\smoke_test_gsuid.py `
  --command "#yh帮助" --command-timeout 8 --idle-timeout 3 --expect-no-output
```

同一个 OneBot 连接不能同时由 NapCat 和旧冒烟脚本占用；执行旧脚本前需要暂时关闭 NapCat 的 WebSocket 客户端，测试结束后再启动 NapCat。`scripts\smoke_game_api.py` 不需要这一步。

### 验证未配置群不响应

选一个不在 `MANAGED_GROUP_IDS` 的虚拟群号，例如 `999999999`：

```powershell
$env:SMOKE_GROUP_ID = "999999999"
.\.venv\Scripts\python.exe -u scripts\smoke_test_gsuid.py `
  --command "#nte帮助" --command-timeout 8 --idle-timeout 3 --expect-no-output
Remove-Item Env:SMOKE_GROUP_ID
```

正确结果是 `PASS_NO_OUTPUT`。如果这个群后来加入管理列表，就换一个未配置群号。

## 已完成的测试结论

本次 `scripts\smoke_game_api.py` 第二实例冒烟测试结果（24 项全部通过）：

- Core 0.10.5 启动成功，当前配置为 GenshinUID 关闭、XutheringWavesUID 关闭、NTEUID 开启。
- 异环：帮助、查看绑定、查询、角色、练度、实时信息、刷新令牌、签到、签到日历、登录、退出登录、公告、角色列表、角色配队、角色攻略、角色图鉴均以 NTE 前缀有输出，带或不带 `#` 均可，大小写不敏感。
- 原神和鸣潮：`#gs帮助`、`#ww帮助` 无输出，不作为当前维护目标。
- 前缀拦截：裸 `gs/ww/yh` 和 `#gs/#ww/#yh` 无输出；NTE 前缀带或不带 `#` 均可识别。
- 未配置群：`#nte帮助` 无输出，范围拦截通过。
- 独立开关：小游戏总开关关闭、直播守卫暂停、小游戏群范围移除时，`#nte帮助` 仍正常响应。

### 当前已知异常

1. 原神和鸣潮保持关闭，不纳入测试；它们不响应 `#gs`/`#ww` 前缀。
2. 未登录状态下无法验证异环真实角色面板、刷新、签到成功结果。完成 `#nte登录` 后，需要由账号持有人本人手动登录，再补测 `#nte刷新面板`、`#nte查询`、`#nte角色练度`、`#nte实时信息` 和 `#nte签到`。

## 关闭流程

按“NapCat -> 机器人 -> Core”的顺序关闭：

1. 回到 NapCat 窗口按 `Ctrl+C`，或正常关闭机器人 QQ。
2. 回到机器人前台窗口按 `Ctrl+C`。后台启动的机器人执行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\stop.ps1
```

3. 回到 Core 窗口按 `Ctrl+C`。如果 Core 窗口无响应：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\stop_gsuid_core.ps1
```

不要使用 `Stop-Process -Name QQ -Force`，它可能关闭普通 QQ。也不要使用 `NapCat.Shell\KillQQ.bat`，它可能关闭全部 QQ 进程。

## 维护、备份和日志

### 数据备份

备份本项目 SQLite：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\backup.ps1
```

备份文件在 `data\backups`。如果要保留异环登录状态和 Core 配置，停止全部进程后，另外复制整个 `GsUID.Core\data` 到只属于自己的离线备份位置。不要上传其中的会话、令牌或账号数据。

### 查看日志

```powershell
Get-Content .\logs\bot.out.log -Wait
Get-Content .\GsUID.Core\data\logs\2026-07-17.log -Wait
```

日期变化后把最后一个文件名替换为当天日期。插件异常重点看 Core 日志中的 `error`、`exception`、`ConnectTimeout` 和 `ConnectError`。

### 更新插件

1. 停止 NoneBot 和 Core；NapCat/QQ 不需要重启，保持 OneBot 传输环境不变。
2. 备份 `data\bot.db` 和 `GsUID.Core\data`。
3. 执行 `scripts\install_gsuid.ps1`。
4. 如果 GitHub 网络暂时不可用，保留脚本输出的 stash，不要强制覆盖本地补丁；网络恢复后重新执行更新。
5. 重新启动 Core 和 NoneBot；确认日志正常后再恢复业务验证。
6. 执行单元测试和冒烟测试。

不要直接删除 `GsUID.Core\data`，否则可能丢失插件配置、资源缓存和登录状态。

## AI 和 Token 说明

当前 Core 配置为 AI 总开关关闭，启动日志也明确记录：

```text
AI总开关已关闭，跳过 AI 重依赖导入与子系统初始化
```

因此当前不会调用 OpenAI、Gemini、Anthropic 或其他互联网大模型，不会消耗互联网大模型 Token。项目的查重、统计和状态图片由本地 Pillow 直接绘制，也不调用生图模型。

但游戏插件本身会访问互联网普通服务，例如异环公告图片、角色数据、登录服务、鸣潮资源 CDN、原神数据接口。这会消耗网络流量，可能因网络故障而失败，但不等于调用大模型，也不会产生大模型 Token 费用。

只有在 Core 网页配置中主动打开 AI 总开关并填写外部模型 Provider/API Key 后，才可能发生大模型调用。当前不要打开该开关；如果以后打开，必须先确认 Provider、费用和隐私策略。
