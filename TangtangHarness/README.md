# TangtangHarness

独立自建的 QQ Agent 运行时。SnowLuma 负责 QQ/OneBot v11 接入，Harness 负责会话、上下文构建、多模型请求、本地业务工具和执行记录。新项目不依赖 NoneBot 运行，也不修改外部旧框架。

所有新代码、依赖、配置和运行数据均放在本目录。公开模板默认使用 observe：仅捕获消息，不调用远端模型，不发送 QQ，不补发旧历史任务。当前工作区已完成切换并运行于 live，旧 NoneBot 停止接单；旧回切入口仍然保留。

## 控制台

聊天默认使用持久化的原样模型日志，只追加本轮消息和真实送达事实；输入预算自动跟随当前模型档案容量，默认 90% 切窗、按 50% 总输入预算保留完整近期事件组，不受固定轮数限制。真实输入 usage 仅校准容量估计。自动画像、成长、认知、群摘要和模型压缩退出默认路径。缓存布局、容量与取舍见[上下文缓存主干](docs/上下文缓存主干.md)。

前端使用 React、TypeScript、Vite 与 Apache ECharts。主看板包括总览、流量日志、缓存与费用、群与上下文、个人资料、后台与执行、业务与运行，并保留模型设置、实际请求查看、离线预览和实验入口。曲线可缩放、切换数据表、导出匿名 CSV/JSON；没有数据时显示空态，供应商未报告的指标显示未知。

```powershell
Set-Location TangtangHarness/frontend
npm.cmd install --cache .npm-cache
npm.cmd run build
```

开发时执行 `npm.cmd run dev`，页面为 `http://127.0.0.1:5173`，API 代理至 Harness `127.0.0.1:8090`。部署版由 Harness 提供构建后的 `frontend/dist`，默认仅本机使用。

QQ 返回的业务在线链接使用 Harness SQLite 中的 `web_base_url`。公网实例保存实际 HTTPS origin 并启用 `public_gateway_enabled`，复用已有 Cloudflare Named Tunnel 接入默认 `127.0.0.1:18769` 的业务网关；完整控制台继续在 `127.0.0.1:8090` 本机使用。首次迁移读取旧 `.env` 的非空 `PUBLIC_SITE_BASE_URL` / `PUBLIC_SHORT_HOST`，空值不会生成公网地址。真实域名留在本机运行配置，修改后只重启 Harness；配置方法及管理页链接有效期见[业务网页的公网地址](docs/运行入口.md#业务网页的公网地址)。

## 运行与迁移说明

后续编码与运维可使用 [Harness 制作、修复与维护 skill](skills/tangtang-harness/SKILL.md)。它按制作、修复、维护分别提供入口，保留当前工程的数据、调用和运行边界。首次在本目录执行 `skills/link-local-skill.ps1` 联接到本地 Codex，之后可使用 `$tangtang-harness` 或自然语言请求调用；技能更新直接编辑本目录权威源，不影响运行开关。

- [启动工具与运行入口](docs/运行入口.md)
- [服务守护与恢复记录](docs/服务守护.md)
- [控制台与指标口径](docs/控制台.md)
- [生产功能修复验收](docs/生产修复验收.md)
- [游戏上游转发](docs/游戏上游转发.md)
- [迁移与运行边界](docs/迁移与运行边界.md)
- [旧功能、全部命令与来源台账](docs/旧功能迁移台账.md)
- [本轮实施与验收记录](docs/实施验收记录.md)

旧全栈启动可能重写 SnowLuma 配置并移除新 endpoint，真实联调后需显式检查/补回。SQLite 导入采用来源只读在线快照，新库独立写入。Core 游戏指令保持唯一转发者；语音监督与状态使用独立服务，避免共享权重切换影响旧机器人。

## 安装、启动与停止

在本目录执行。使用独立 Python 3.13 虚拟环境；全部新运行文件留在本目录。

```powershell
.\scripts\setup.ps1
.\scripts\start.ps1
# 控制台：http://127.0.0.1:8090
.\scripts\stop.ps1
```

中文操作入口统一在 `启动工具`。日常双击 `01-启动全部.bat`，结束使用双击 `02-关闭全部.bat`，排查运行情况双击 `41-检查全部状态.bat`。Harness、Core、SnowLuma 各有独立启停入口，Harness 另有重启入口；`51-打开Harness控制台.bat` 打开控制台，默认地址为 `http://127.0.0.1:8090/`。完整文件清单与行为见[运行入口](docs/运行入口.md)。

一键启动按 Core → Harness → SnowLuma 执行，一键关闭按 Harness → Core → SnowLuma 执行。语音随 Harness 按已保存的开关运行；QQ 保持独立，登录及验证由操作者完成。新入口不启动旧 NoneBot，也不改写 SnowLuma 连接。仓库根目录的旧 `启动工具` 保留，供旧系统独立使用。

启动检查独立端口和实际服务 PID。单独停止 Harness 只处理其拥有的进程；单独操作 Core 或 SnowLuma 不重启 Harness。新安装默认 `observe`，当前实例的 live 状态以控制台 `/api/status` 为准；尚未连接 SnowLuma 时也可以查看导入历史、模型设置及本地工具目录。

Harness 自有看门狗通过 Windows 当前用户计划任务，每分钟和登录时检查期望开启的自有服务。显式停止先登记关闭意图，恢复不会撤销已保存的关闭项；直接结束进程则按意外退出处理。守护状态和恢复历史独立存入 `data/supervisor.db`，控制台通过 `/api/watchdog` 展示。首次安装、启用及恢复边界见[服务守护](docs/服务守护.md)。

`config/settings.example.json` 是公开模板；真实配置为 ignored 的 `config/settings.json`，密钥使用本目录 `.env`。模型的 `context_limit` 必须填写供应商实际容量；导入时缺少容量保持为空，不能根据模型名称猜测。单价未填时只显示 token，缓存字段缺失显示未知。

## 已实现内容

- 独立 OneBot 反向 WS、RPC、回执、消息去重、会话记录和观察模式。
- Responses 与 Chat Completions、多模型共享已送达历史、流式耗时、实际请求存档及失败尝试记录。
- 固定人格、持久化原样模型日志和真实 QQ 送达事实；同一窗口仅追加，容量到限整段切换。
- 本地中转路由、业务工具、组合请求、结构化结果及追问；纯工具不会触发聊天模型或 AI 整理。
- 成熟本地业务算法与渲染成果：统计、档案、五种小游戏、缘分、知识、图库、群域、查重、公告、帮助及业务网页。
- 合并、续聊、主动策略及显式记忆管理、遗忘恢复；默认停止自动认知、成长、摘要、AI 压缩和画像，旧布局仅供显式兼容回退。
- B站订阅、直播守卫、报时、日榜、通知及新缓存清理；持久投递队列只在确认回执后完成。
- 独立 Core 连接和可选独立语音进程、合成队列及音频缓存。详见[本地业务验收](docs/本地业务验收.md)和[独立语音运行时](docs/独立语音运行时.md)。
- 独立外部服务守护、持久启停意图和恢复事件；只恢复缺失自有进程，保留语音开关并记录重试退避。

新运行时不导入 `bot.*`、NoneBot 或 DSH。业务移植后的配置、数据库、事件与发送边界均使用新实现；原来源清单保存在 `resources/business-migration.json`。

本地网页的数据盘点、图表清单与指标口径见[可视化需求与实施方案](docs/可视化需求与实施方案.md)，已实现页面和运行方法见[控制台](docs/控制台.md)。模型 usage、上下文本地估算、配置单价估算与未知字段分别展示；CPU/内存和容量当前值不补画历史曲线。

## 快照与配置导入

导入脚本默认预览，`--apply` 仅写新目录。SQLite 来源以只读在线 backup 保存一致性快照，包括尚未 checkpoint 的 WAL。

```powershell
.\.venv\Scripts\python.exe scripts\import_config.py
.\.venv\Scripts\python.exe scripts\import_config.py --apply
.\.venv\Scripts\python.exe scripts\import_legacy.py
.\.venv\Scripts\python.exe scripts\import_legacy.py --apply
.\.venv\Scripts\python.exe scripts\verify_migration.py
```

同一批快照可通过 `--snapshot-id <快照编号>` 重复导入验证，不重新读取活跃旧库。来源映射控制重复计数及可变版本；新框架的记忆更正、遗忘和试运行记录保留。旧未完成任务留在快照中，不变为待执行任务。当前实例已完成停旧接单后的最终跨库快照，后续重新导入仍需显式执行。

## 并行测试入口

`live` 测试默认使用 `#harness` 前缀，例如 `#harness 今天发言排行`。自动后台任务需要明确设置 `extra.isolated_scope_enabled`、`group_ids` 和独立的 `extra.private_user_ids`。群功能及管理员权限继续以新库为准，开启全局聊天不会重新开启已关闭群功能。

SnowLuma 新连接地址为 `ws://127.0.0.1:8090/onebot/v11/ws`。`scripts/snowluma-connection.ps1 -ConfigPath <实际配置文件>` 默认只检查，`-Apply` 可保留旧连接并增补新项。运行中的连接需要在已登录的 WebUI 保存热加载；本项目不自动登录 WebUI，也不重启 QQ。旧全栈脚本覆盖连接后，显式再次检查即可。

新安装的 Core 和语音默认关闭；当前生产实例已按独立配置启用 Core 与语音。日常直接使用 `#nte` / `#ww`，`#harness` 仍可用于明确测试。主 Core 连接独立发送，旧订阅兼容连接只接收；同一命令只有一个生产转发者。新语音使用 9890、自有 YAML 和缓存，不能使用旧监督器或切换共享权重。

## 验证

```powershell
.\scripts\test.ps1
.\.venv\Scripts\python.exe scripts\benchmark_routes.py
```

行为测试使用临时新数据库、合成身份、模拟 QQ/Core/TTS/模型，覆盖真实协议边界、业务状态、回执、上下文、缓存语义、迁移和独立启停。控制台桌面与手机布局及离线实验已经过浏览器验证。实际验证记录写入本目录 ignored 的 `runtime/verification`，不包含公开源码中的实例身份。

全功能检查、初始化修复、当前关闭项及尚未完成的真实联调见[功能运行检查](docs/功能运行检查.md)。

QQ 聊天、本地工具、B站扫码和推送已完成限定范围实测，日常接单由 Harness 执行。2026-10-05 又通过真实 Core 获取 NTE/鸣潮帮助图卡，并完成真实模型媒体协议和独立 TTS 合成；这些新增结果在本地截获，没有额外向 QQ 发送测试消息。具体业务范围、真实证据和后台错误处理见[生产功能修复验收](docs/生产修复验收.md)，旧框架及其运行数据完整保留。
