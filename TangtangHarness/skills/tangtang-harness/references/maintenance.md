# 维护流程与命令

H 指 Harness 根目录，R 指外层 Git 仓库。下面第一批命令在 H 执行，仓库校验在 R 执行。维护技能本身不要求重复询问已有授权；只读状态、可逆本地工作与本次明确授权的运行操作直接推进。

## 状态、启停和加载代码

```powershell
# 本 Harness 进程归属检查
.\scripts\status.ps1
# 整个链路的本地状态查询
.\scripts\stack.ps1 -Action status
# 独立外部服务守护状态，不执行恢复
.\scripts\watchdog.ps1 -Action status
```

运行中按实际配置访问 `GET http://127.0.0.1:8090/api/status`，核对 PID、mode、OneBot connected、group_membership_ready、Core 主/订阅连接、speech enabled/ready 和排队状态。脚本退出码零、端口监听、外部服务就绪和实际业务成功分别确认，不用一个替代其他项。

Python 改动需要部署时只执行：

```powershell
.\scripts\stack.ps1 -Action restart-harness
```

当前中文对应入口为 `启动工具/13-重启Harness.bat`。重新核对进程记录与实际新 PID、配置端口、`logs/stdout.log`、`logs/stderr.log`、OneBot 和受影响服务；保存的关闭项继续关闭。文档/技能编辑不需要重启。

用户要求完整服务操作时才用 `start-all` / `stop-all`：启动 Core → Harness → SnowLuma，停止 Harness → Core → SnowLuma，QQ 保持独立。独立服务动作与人工登录步骤以[运行入口](../../../docs/运行入口.md)为准。旧全栈启动会有覆盖 Harness endpoint 的风险，不能把它当新 Harness 的启动入口。

启停经过公共生命周期锁，先保存期望状态再操作进程。显式停止 Harness 使其恢复意图关闭，语音有效恢复同时要求 Harness 期望开启且实际运行；显式停止语音另保留 `stopped.flag`，重新启动 Harness 不清除此标记。`stop-all` 先关闭全部服务恢复意图，保存语音开关。不要直接调用底层进程管理器绕过意图登记，也不要用任务管理器结束进程表示永久停用。

## 服务守护与恢复证据

独立守护状态、事件和退避在 `data/supervisor.db`，本机控制台通过 `GET /api/watchdog` 查询。首次部署或用户明确要求管理守护时，在 H 执行：

```powershell
# 只注册当前用户计划任务
.\scripts\watchdog.ps1 -Action install
# 为尚未保存意图的服务采纳当前运行状态，保留先前的关闭选择
.\scripts\watchdog.ps1 -Action enable
.\scripts\watchdog.ps1 -Action status
```

`disable` 暂停外部恢复，不停止当前服务且保留服务意图；`uninstall` 先停用外部恢复再移除本 Harness 计划任务。语音未保存关闭意图且原开关允许时，内部 15 秒后台恢复仍可继续；需要保持语音停止时使用显式 `stop_speech.ps1`。`status` 显示意图、事件及计划任务状态；`check` 使用同一限时 runner，在守护启用时可能真正启动缺失进程，不作为只读诊断使用。详细动作和边界见[服务守护](../../../docs/服务守护.md)。

当前用户计划任务按根目录哈希命名，每分钟与登录时直接调用 Harness 自有 `pythonw.exe` 执行 `scripts/watchdog_runner.py` 无窗口入口。runner 子检查限时 45 秒，任务限时 50 秒供失败记录落库，最多恢复一个缺失进程。检查失败同时保存守护事件及 UTF-8 `logs/watchdog-supervisor-check.log`。失败按 60、120、300、900 秒退避，之后保持 900 秒；各服务独立计时。活进程断线、不就绪或端口冲突只记录，不自动杀进程重启或处理 QQ 登录。

运行模式、全局 `speech_enabled` 和 `extra.core.enabled` 保存在 `config/settings.json`，独立语音 `enabled` 在 `config/speech.json`；业务 SQLite 中的群语音开关只限制交付。守护启用时，外部恢复统一拉起语音并保存尝试/结果，内部语音任务只检查健康，不产生第二份恢复记录；外部守护停用期间原有语音后台恢复不补造为守护事件。显式语音启动调用语音管理器的显式入口，配置允许且成功后清除 `stopped.flag`；守护恢复不得清除此标记。

验证部署时确认计划任务的用户、可执行文件、触发器、45 秒子检查/50 秒任务限时与实际触发结果，再核对 API/控制台事件、进程 PID、创建时间和端口。事件说明当时的动作，不代替当前连接与业务验收。隔离恢复测试和实机恢复证据分别记录，未经实际恢复的服务不能宣称完成实机验收。守护修改不能重新开启保存的群业务、主动聊天、语音或旧接单者。

PowerShell 使用当前提供的引擎和 `-LiteralPath`，复杂逻辑放脚本、native 退出码显式检查；新增/修改 `.ps1` 先解析检查，后台进程使用隐藏窗口。不要猜 PID 后终止不明进程。

## 首次安装与数据迁移

仅新环境或用户明确要求安装时运行 `scripts/setup.ps1`；已有环境不要为排查重复安装全部依赖。新安装模板保持 observe，不能依据 README 的某次生产说明直接设 live。

```powershell
.\.venv\Scripts\python.exe scripts\import_config.py
.\.venv\Scripts\python.exe scripts\import_legacy.py
```

以上默认预览。看过来源、目标及当前新设置后，按迁移授权使用对应 `--apply`，只写 H。旧库必须采用只读在线 backup，不能复制活跃裸 db 或用 immutable 忽略 WAL；多库快照不宣称全局原子性。

`import_legacy.py` 是全库迁移，不提供按群或只迁记忆的筛选。应用会更新 `import_cutoff`，关闭 `profile_worker_enabled` 和 `speech_profile_ai_enabled`，部分群域/群开关/被动设置可能随来源合并；不能保证保留任意既有新设置。限定群记忆、增量补缺或“先做本地实现”任务先指定 H 内独立验证目录，例如 `--root runtime/verification/import-review`，或实现并验证受限导入路径，不能把全量 `--apply` 用在生产库。

正式全量应用到现有实例时，先明确来源优先级和保存开关的处理结果；`runtime_live=True` 会拒绝导入，只有本次包含停机迁移授权时才停止 Harness。停止后仍需只读核对该标志；当前进程终止入口不保证执行 Runtime 的关闭钩子，若残留 true，保留保护并报告现有入口未完成安全解除，先用独立目标验证。不能手动清运行标志绕过保护，也不能仅为方便导入改变 live 接单状态。

```powershell
.\.venv\Scripts\python.exe scripts\import_legacy.py --snapshot-id <已有快照编号>
.\.venv\Scripts\python.exe scripts\verify_migration.py
```

已有快照的重复应用仍需对应 `--apply`。隔离验证时，所有 `import_legacy.py`、`import_config.py`、`verify_migration.py` 命令都显式传同一 `--root`；`--snapshot-id` 查找的是该目标自己的 `data/imports`，不能在后续步骤省略参数回到默认生产根。

核验脚本会初始化 Harness Store、查询新库并写新报告，属于新目录维护操作，不能当作对生产库完全只读的诊断。检查来源映射、重复累计、可变 revision、图像引用、外键、关闭项、新试运行数据和 pending 不重放。

## 既有实例补缺和依赖维护

补缺使用 `config_import.py` 的来源计算与当前值对照；只补未保存的 native key、字典缺字段和确实遗漏的来源映射，不全量重跑配置导入覆盖模型、容量、URL、密钥来源、群范围或用户选择。更改业务设置时确认它由 SQLite 而非运行 JSON 管理；第一次补缺后重复预览应无相同更新。

依赖改动留在 H 的 `pyproject.toml`、前端 package/lock 和自有环境。Core 上游更新仅在明确任务内按 R 的 `config/upstream-lock.json` 与上游约定快进，保持 vendor checkout pristine；不要改上游修兼容，不通过 reset/clean/stash 清除来源差异。

## 连接、实验和清理边界

`scripts/snowluma-connection.ps1 -ConfigPath <实际配置文件>` 默认检查；增补使用明确授权的 `-Apply`，保留旧连接。运行中配置热加载由用户在已登录 WebUI 保存，不自动登录，也不重启 QQ。

`model_profile_probe.py` 默认也会访问远端元数据并写独立验证报告；`--paid` 会真实调用模型，使用 `--profile` 限定已有授权的档案。`probe_speech.py` 会真实合成；B站探测访问外部接口；通知入口可能实际发 QQ。`migrate_business.py` 无预览，会机械重写已迁入业务模块和复制资源，日常修复不要重跑它。操作前核对当前代码和参数的副作用，遵守本次授权范围。限定实验先用隔离验证库和本地截获发送，证据不足不能扩大群范围或连续收费重试。

清理沿用 H 的已有实现，只覆盖自有图卡、截图、音频等生成缓存和旧头像版本；保留当前头像、历史媒体、快照、数据库和备份。需要删除新的范围时先只读列出准确目标与保留依据，不把“维护”当作删除数据授权。

## 生产接管与回切

生产接管是独立运维任务。确认本次授权、群/私聊范围和保存开关，停止当前接单者及对应监督，确认任务结束和监督不再恢复旧进程，必要时再做最终多库快照和增量同步，然后启用唯一新接单者。旧 8080 无监听只是一项证据，还需检查旧 watchdog gate；新连接建立也不意味着旧接单已经停止。

Harness 回切入口：

```powershell
.\scripts\switch_to_legacy.ps1
```

该脚本写 Harness 的 mode 为 observe、关闭隔离范围标志并停止 Harness，本身不启动旧 NoneBot。随后按用户授权和 R 的维护约定启动旧接单者。恢复 Harness 前先停止旧接单及其监督，核对模式/范围后再启 Harness，避免双回复。不要借回切改旧代码或删除新数据。

## 验证与提交

H 的行为测试和前端构建使用既有入口：

```powershell
.\scripts\test.ps1
```

路由性能问题可另跑 `scripts/benchmark_routes.py`，其结果只计确定性路由，不代表模型、渲染、网络和 QQ 端到端耗时。UI 修改按当前环境做浏览器行为核验，避免对 live 工具页面执行测试写操作。

提交前在 R 执行：

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_public_release.py
.\.venv\Scripts\python.exe scripts\validate_docs.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\.venv\Scripts\python.exe -m pytest
```

从适用的 AGENTS 读取最新必需检查；以上不是绕过新增检查的固定清单。必需校验失败时如实报告，不提交或绕过，也不自动修补任务外旧实现。若仅交付本地改动，说明实际执行范围。不得把运行配置、验证报告、缓存、历史或身份写进 Git。

## 技能安装与更新

在 H 执行 `skills/link-local-skill.ps1`，将权威源联接到 Codex 技能目录的 `QQBot/tangtang-harness`。默认从 `CODEX_HOME` 或用户目录解析技能根，不把本机路径写进源码。已正确联接时重复执行无操作；遇到非联接目录或其他目标立即报错，不覆盖已有技能。

后续编辑 H 内的权威源，校验 YAML frontmatter、所有本地引用和 PowerShell 语法，再验证联接仍指向该目录。聊天运行时的工具目录与此 Codex 开发技能分别维护，安装该技能不会注入 QQ 工具或切换机器人配置。
