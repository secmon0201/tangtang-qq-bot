# AI 发言画像说明

糖糖负责日常群聊，并把启用群的发言原文持久化到本地聊天记录库（`tangtang.db`）。A 海岸发言画像直接读取这份历史，不再维护独立的发言档案库。

## 可用范围

- A 海岸五个固定群内的成员可在群内使用 `#发言画像 QQ号`、`#画像 QQ号`，也可将 QQ 号替换为单个 `@成员`。
- 超级管理员可在私聊中使用相同命令；私聊仅支持 QQ 号目标。
- 档案命令 `#发言记录`、`#发言搜索` 仍只允许超级管理员私聊使用。

## 数据与处理方式

- 仅使用糖糖本地聊天记录库中 A 海岸五群的发言原文和本地统计数据。
- 每条原文最多用于一次 AI 画像；之后只将新增发言与既有画像合并更新。
- 画像需有超过 100 条已存档纯文本发言；100 条及以下会直接提示发言量不足，不调用 AI。
- “表达小雷达”和“发言倾向”均按该用户所有已存档的 A 海岸历史发言计算，不只计算本次新增发言。
- 目标用户没有任何已存档发言时会直接提示未发言。
- 已有画像且没有新增发言时，直接发送原画像长图，不会再次请求 AI 或重复统计。
- AI 未启用时，`#发言画像` 仍会生成本地统计摘要，但不会发送原文到远程服务。
- 每个用户同一时间只允许一个画像任务（重复申请会提示“正在处理中”）；全局并发由 `PROFILE_MAX_CONCURRENT` 控制（默认 3），超出的任务按先后顺序排队，不会同时涌向 API。

## 配置与隐私

画像 AI 复用糖糖的接口、模型与思考能力（`TANGTANG_*`），并可通过 `PROFILE_*` 独立覆盖：`PROFILE_ENABLED`、`PROFILE_API_URL`、`PROFILE_API_KEY`、`PROFILE_API_STYLE`、`PROFILE_MODEL`、`PROFILE_REASONING_EFFORT`、`PROFILE_TIMEOUT_SECONDS`、`PROFILE_MAX_INPUT_CHARS`（默认 20000）、`PROFILE_MAX_OUTPUT_TOKENS`、`PROFILE_MAX_RESPONSE_CHARS`（默认 16000）、`PROFILE_RETRY_MAX_ATTEMPTS/RETRY_BASE_SECONDS`（请求失败重试，默认 3 次、基数 2 秒，仅对 429/5xx 指数退避并尊重 Retry-After）。画像阶段参数同样可配：`PROFILE_CHUNK_CHARS`（发言分块大小，默认 16000）、`PROFILE_MERGE_CHUNK_CHARS`（合并专用分块，默认 12000）、`PROFILE_MAX_RECORDS_PER_RUN`（单次处理条数，默认 100000）、`PROFILE_EVIDENCE_CONCURRENCY`（证据提取与合并阶段任务内并发，默认 5）、`PROFILE_EVIDENCE_REASONING_EFFORT`（证据/合并思考等级，默认 low，最终画像仍用 `PROFILE_REASONING_EFFORT`）、`PROFILE_EVIDENCE_MAX_CHARS/MAX_TOKENS/MAX_RESPONSE_CHARS`（证据与合并阶段软硬上限，默认 4000/8000/16000）、`PROFILE_FINAL_MAX_CHARS/MAX_TOKENS/MAX_RESPONSE_CHARS`（最终画像软硬上限，默认 4000/8000/16000）。接口密钥只保存在本机 `.env`，不得提交到 Git、发送到群聊或写入文档。

完整的数据范围、更新方式和指令说明见 [机器人使用说明总览](机器人使用说明总览.md) 与 [全部 `#` 指令清单](全部#指令清单.md)。
