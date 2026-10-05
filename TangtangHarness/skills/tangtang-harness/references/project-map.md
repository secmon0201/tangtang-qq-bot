# 工程定位与证据入口

本文中的路径均相对 Harness 根目录 H。阅读相关模块及其调用者，不用路由注册数或历史测试数替代行为确认。

## 从症状定位所有者

| 问题或改动 | 主要实现 | 对应行为证据 |
| --- | --- | --- |
| 启动、事件接收、任务所有权、群同步 | `tangtang_harness/runtime.py`、`app.py`、`onebot.py`、`types.py` | `tests/test_runtime_and_console.py`、`test_store_runtime.py`、`test_production_chat_behavior.py` |
| 命令/自然语言/组合路由、目标及权限 | `router.py`、`tools.py`、`chat_settings.py`、`console_business.py`、`business/` | `test_router_business.py`、`test_tools_business.py`、`test_console_business.py` |
| 模型请求、流式、媒体回复、语音降级 | `chat.py`、`models.py`、`context.py`、`reply_media.py`、`media.py`、`external.py` | `test_chat_runtime.py`、`test_models_runtime.py`、`test_mixed_failure_behavior.py`、`test_reply_media_runtime.py` |
| 上下文、窗口、记忆、后台及费用 | `context.py`、`windowing.py`、`billing.py`、`store.py`、`chat.py` | `test_context_runtime.py`、`test_windowing.py`、`test_billing.py`、`test_learning_runtime.py`、`test_group_persona_switches.py` |
| 迁移、配置优先级及业务数据 | `migration.py`、`config_import.py`、`config.py`、`scripts/import_legacy.py`、`scripts/import_config.py` | `test_migration.py`、`test_config_import_runtime.py`、`test_initialization_runtime.py`、`test_fact_import_runtime.py` |
| Core 发送/接收、原生媒体与订阅 | `core_protocol.py`、`external.py`、`runtime.py`、`scripts/import_core_routes.py` | `test_protocol_integrations.py`、`test_core_production.py`；[上游转发说明](../../../docs/游戏上游转发.md) |
| B站订阅、日程、登录与持久投递 | `tools.py`、`bili_web_qr.py`、`business/asoul.py`、`runtime.py` | `test_tools_business.py`、`test_bili_web_qr.py`、`test_live_guard_calendar.py` |
| 聊天公共话题素材、固定群工程通知 | `topics.py`、`notifications.py`、`runtime.py` | `test_topics.py`、`test_notifications.py` |
| 控制台、业务网页与公网网关 | `app.py`、`analytics.py`、`analytics_business.py`、`business_pages.py`、`public_gateway.py`、`frontend/src/` | `test_analytics.py`、`test_analytics_business.py`、`test_ranking_web.py`、`test_public_gateway.py`；[控制台说明](../../../docs/控制台.md) |
| 独立启停、端口、语音与清理 | `harness_process.py`、`speech_runtime.py`、`runtime.py` 中清理逻辑、`scripts/stack.ps1` | `test_harness_process.py`、`test_stack_operations.py`、`test_speech_startup_independence.py`、`test_generated_cleanup.py` |
| 服务意图、外部守护、恢复退避与事件 | `supervisor.py`、`scripts/watchdog.ps1`、`scripts/watchdog_runner.py`、`runtime.py`、`app.py`、`frontend/src/` | `test_supervisor.py`、`test_watchdog_console.py`；[服务守护说明](../../../docs/服务守护.md) |

上表首个文件给出目录，同行缩写文件仍位于 `tangtang_harness`，测试仍位于 `tests`。表用于定位，函数签名、字段和现行行为以代码为准。

## 文档如何取用

- [迁移与运行边界](../../../docs/迁移与运行边界.md)：数据、共享连接、唯一发送者及服务隔离的依据。
- [旧功能迁移台账](../../../docs/旧功能迁移台账.md)、[旧插件接线核对](../../../docs/旧插件功能接线核对.md)、`resources/business-migration.json`：旧功能的来源与迁入/排除范围。算法文件已复制不代表入口、后台与交付接线完成。
- [运行入口](../../../docs/运行入口.md)：当前脚本、快捷方式及回切操作。历史验收记录里的快捷方式可能已替换，先确认真实文件存在。
- [服务守护](../../../docs/服务守护.md)：独立计划任务、服务期望状态、语音多重开关、恢复退避及控制台事件。旧 QQBot 看门狗不监督 Harness。
- [生产修复验收](../../../docs/生产修复验收.md)、[功能运行检查](../../../docs/功能运行检查.md)、[实施验收记录](../../../docs/实施验收记录.md)：诊断线索和当时的证据；不同批次会保留不同状态，不能推断现在的模型、开关、连接或测试总数。
- [缓存与窗口实施计划](../../../docs/缓存优化与上下文窗口实施计划.md)：优化目标和候选设计。具体表名、epoch、阈值与换段路径须核对 `store.py`、`windowing.py`、`context.py`、`chat.py` 和测试，不能把计划表格视为已实现 schema。

当前窗口状态实际保存在 Store settings 的 `context_window:<session>`，请求 telemetry 使用 `context_epoch`；不能假定计划里的独立窗口/记忆快照表已经存在。当前上下文采用已发布压缩快照和动态资料选择；“新窗口独立记忆基座冻结、按版本跨轮只注入一次”需另行核对实现，不能宣称全量已交付。费用换段投影也包含本地重建假设，真实重建成本和供应商缓存有效期仍需实际 usage 验证。

## 运行证据与配置位置

`config/settings.example.json` 是公开模板；实例设置在 ignored 的 `config/settings.json`，密钥在 H 的 `.env`。业务 native key、群开关和历史在 Harness SQLite 中；控制台运行配置与业务 SQLite 设置有不同所有者，不能只修改一个 JSON 就认定业务设置生效。

Harness 进程记录在 `data/process.json`，输出日志在 `logs/stdout.log`、`logs/stderr.log`。`scripts/status.ps1` 检查本进程归属；运行中 `GET /api/status` 读取模式、PID、OneBot、群同步、Core、语音与排队状态。默认本机端口为 8090，旧接单通常为 8080，独立语音通常为 9890，公网业务网关通常为 18769；使用实例实际配置，不把默认端口当硬编码部署事实。

服务守护状态、意图和恢复历史在独立 `data/supervisor.db`；`scripts/watchdog.ps1 -Action status` 与 `GET /api/watchdog` 用于诊断，`check` 可能执行恢复。检查“停止后为何又启动”先看保存的期望状态及意图事件，区分显式停止与直接结束进程；检查“没被拉起”再看总开关、依赖开关、归属结果、退避和计划任务触发记录，不直接重写开关。

`scripts/watchdog_runner.py` 管理 45 秒子检查边界，当前用户计划任务为失败记录预留到 50 秒；失败事件及摘要日志在 `data/supervisor.db` 与 `logs/watchdog-supervisor-check.log`。守护启用时它是语音恢复的统一所有者，`runtime.py` 中 15 秒语音任务只查健康；守护停用时该内部任务仍尊重保存的语音关闭意图和停止标记。

请求、工具、background job、通知队列与平台回执记录用于串起事件链。优先从现有进程的只读 API 或 SQLite `mode=ro` 聚合查询取证；不要为了诊断调用可能建表/写默认值的 `Store`、旧 Database 或全局 runtime constructor。检查表结构后查询，避免用旧文档字段猜 SQL。
