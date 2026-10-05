# 开发：Agent 上下文与工具调用改造

本文是达妮娅聊天运行时从动态整段提示词迁移到稳定 Agent 上下文的实施台账。糖糖旧数据与资源保留，但不再参与实时对话。本文只记录项目拥有的接口和验收口径，不记录实例群号、用户身份、端点、密钥或聊天正文。阶段必须按 Q0 到 Q8 顺序验收；旧 `feature_calls` 协议在 Q8 稳定前保持兼容。

## 不变边界

- 所有现有 `#` 指令保留，并与自然语言工具入口复用同一业务处理器。
- 模型提出工具调用不等于授权。插件执行层仍检查角色、当前群范围、功能开关、依赖健康、真实 `at` 段和会话时效。
- 普通用户的稳定、类型化能力可以进入工具全集；管理、维护、审核、清理、模型/人格管理和任意上游字符串命令不得进入。
- 工具 Schema 是稳定全集。群开关、权限、依赖健康和本轮可用性只进入动态状态，不增删 Schema。
- 不修改 `GsUID.Core`、NTEUID 或 XutheringWavesUID；项目适配器只读上游数据并快速失败。
- 压缩快照是派生数据，不删除原始聊天、记忆证据或历史版本。只有平台确认送达的轮次进入活跃上下文。
- 活跃上下文按完整确认请求保持 30～50 轮：同一代只在末尾追加，超过 50 轮必须压缩回最近 30 轮；达到 30 轮后也可因 12000 token/字符保护阈值提前压缩。工具调用与结果不得从所属请求中拆开。
- 群聊气氛按会话使用“最近 30 条基线 + 确认游标后的新增消息”；当前消息不在气氛层重复注入。群消息游标只随确认送达原子推进，失败、沉默和无回执继续沿用旧游标。
- 图片窗口独立固定为最近十个消息位置；旧图片只保留 `[图片]` 占位，不保存 base64。

## Q0 基线

改造前请求由人格 system 文本和单个动态 user prompt 组成。动态 prompt 同时包含当前消息、群资料、群摘要、群聊气氛、用户历史、长期记忆、人格状态、本地知识和 `feature_calls` 协议。原生工具循环每轮重新发送相同人格与整段 prompt，再追加当轮工具调用和结果。用量记录只有输入、输出、推理、总 token 与延迟，供应商缓存字段未归一化。

基线行为由 `tests/test_tangtang_chat.py`、`tests/test_tangtang_reply.py` 和 `tests/test_conversational_skills.py` 锁定。Q0 验收快照为 163 项通过。生产基线从现有 usage JSONL 只做聚合统计，禁止输出正文、身份、群号、端点或密钥；旧记录没有缓存字段时必须标记为 `unsupported`，不能解释成缓存未命中。

## 普通用户能力台账

### Q4：现有动作原生工具化

| 能力 | 当前入口 | Q4 目标 | 交付方式 |
| --- | --- | --- | --- |
| 枝江知识检索 | 原生工具 | 保留 | 模型数据 |
| 鸣潮梗文化检索 | 原生工具 | 保留 | 模型数据 |
| 发言排行（日/周/月/累计，本群/明确集群） | `feature_calls` 与 `#` | 原生工具 | QQ 直送 |
| 今日、明日、本周 A-SOUL 日程 | `feature_calls` 与 `#` | 三个原生工具 | QQ 直送 |
| 枝江日程 | `feature_calls` 与 `#` | 原生工具 | QQ 直送 |
| 四类小游戏榜单 | `feature_calls` 与 `#` | 四个原生工具 | QQ 直送 |
| NTE/鸣潮命令 | `#nte` / `#ww` | 上游透传 | QQ 直送 |
| 本人/本群缘分查询 | `feature_calls` 与 `#` | 两个原生工具 | QQ 直送 |
| 达妮娅美图 | `feature_calls` 与 `#` | 原生工具 | QQ 直送 |

Q4 合计 16 个基础工具，其中 14 个直接送达动作和 2 个模型数据检索工具。直接送达结果只向模型返回有界状态，不回灌图片、档案原文或消息正文。

### Q5：普通用户只读已迁移

| 能力组 | 允许的类型化范围 | 禁止扩张 |
| --- | --- | --- |
| 帮助 | 普通帮助、小游戏帮助、A-SOUL 帮助 | 管理员帮助、维护诊断 |
| 状态 | 当前群功能状态、当前人格状态、本人印象、枝江守卫状态 | 修改群设置、人格或后台任务 |
| 发言档案 | 当前群内的发言记录、关键词搜索、画像；目标按现有命令边界解析 | 任意群号、跨域原文、私密证据 |
| NTE | 透传上游 NTEUID 的帮助、排行和其他命令 | 项目不生成本地 NTE 动作 |
| 鸣潮 | 透传上游 XutheringWavesUID 的帮助、排行和其他命令 | 项目不生成本地 WW 动作 |

Q5 将上述能力加入稳定 Schema，工具全集现为 36 项：2 个有界知识检索、22 个直接 QQ 读取动作和 12 个显式状态动作。游戏命令不进入 Agent 工具，明确的 `#nte`/`#ww` 消息直接透传上游。档案目标只能是调用者本人或本轮消息中唯一的真实 OneBot `at`；模型不能提交 QQ 号。发言记录、搜索结果、画像和全部图片只向 QQ 发送，工具结果回给模型的内容只有执行状态、动作名、结果类型和平台消息 ID，不含原文、关键词结果或图片内容。画像工具只读取已有画像，不在工具回合中触发新的画像生成。

NTE 与鸣潮不构造项目自有的 `RankRequest` / `WuwaRankRequest`，也不由模型调用；排行参数和范围由上游插件解析。

### Q6：显式状态操作已迁移

| 能力组 | 允许动作 | 附加门槛 |
| --- | --- | --- |
| 今日缘分 | 抽取、强取、离婚 | 只认本轮明确请求；强取目标必须来自本轮真实 OneBot `at` 段 |
| 俄罗斯转盘 | 装填、开枪 | 群/全局开关、进行中状态和幂等键 |
| 普通/成语炸弹 | 装填、传递 | 目标及词语使用现有校验；不得从历史或引用触发 |
| 骰子 | 开局 | 复用现有状态机和处罚边界 |
| 猜数字 | 开局、提交 | 复用现有状态机；参数为有界整数 |

状态工具幂等键固定为 `request_id + action + sequence`，并校验创建计划时的状态版本。主动聊天、压缩摘要、历史消息、引用内容和工具结果都不能触发显式操作。

Q6 增加 12 个原生写工具；稳定全集现为 36 项。状态版本在付费模型请求前从项目自有 SQLite 生成隐私安全指纹，统一执行器在批量预检和每步执行前复核，小游戏／缘分插件取得各自群锁后再次复核。幂等键先以唯一记录占位，进程异常、发送失败或模型重试都不会用同一个请求键再次改变状态。

一批最多包含一个写工具；多个写动作整批拒绝，避免第一步改变状态后剩余步骤基于旧版本继续执行。写工具仅接受原生 tool calling，旧 `feature_calls`、本地历史、引用文本、主动聊天和摘要都不能进入写执行路径。目标型动作只使用本轮除机器人外的唯一真实 `at`；猜数字限制为 0 至 999，成语炸弹时长限制为 60 至 600 秒，专业／娱乐模式使用明确枚举。

### Q7：上游安全适配已完成

游戏不再提供 Agent 访问项目自有视图的适配器；`bot/integrations/game_workflow_adapter.py` 保留为历史离线兼容代码，不加载到运行链路。

直接 `#nte`／`#ww` 入口全部使用上游解析器、发送和渲染处理器。项目只在 NoneBot 消息边界执行群范围、黑名单和全局开关门；Core、NTEUID 或 XutheringWavesUID 缺失时由上游连接器按自身行为处理，不在消息路径改写或修复依赖。

### 保留为直接指令或被动功能

- 所有现有 `#` 指令仍可直接使用；未列入 Q4 至 Q6 的普通入口默认保留指令，不自动暴露给模型。
- NTE/鸣潮未审核的上游命令继续按原消息边界透传；不会提供 `execute_command(string)` 工具。
- B 站推送、整点报时、主动聊天、被动互动、续聊、观察采集、群摘要和人格后台整理属于被动或后台功能，不是用户工具。
- 无法形成稳定参数和权限合同的临时诊断入口保留直接指令。

### 始终排除

群/系统设置、人格切换与成长管理、模型切换、主动策略、技能管理、知识审核、公告、白名单与查重、QQ 传输维护、登录诊断、数据清理、备份操作、运行时启停以及任意 NTE/WW 字符串命令永不进入普通用户工具全集。

## 分层目标与版本

```text
稳定前缀：完整普通用户工具 Schema + Persona + 固定规则 + 输出协议
追加窗口：user / assistant / tool_call / tool_result
压缩快照：facts / commitments / unresolved / topic_progress / source_turn_ids / scope / revision
动态尾部：群状态、用户状态、记忆召回、工具可用性、当前消息
```

运行开关固定为：

```text
TANGTANG_CONTEXT_LAYOUT=v1|shadow|v2
TANGTANG_NATIVE_ACTION_TOOLS=false|shadow|true
TANGTANG_CONTEXT_COMPACTION_ENABLED=false|true
TANGTANG_CACHE_COHORT_MODE=off|shadow|canary|on
TANGTANG_CACHE_CANARY_GROUP_IDS=<managed group IDs>
TANGTANG_CACHE_SOFT_REPLAY_CHARS=16000
TANGTANG_CACHE_HARD_REPLAY_CHARS=24000
TANGTANG_CACHE_SNAPSHOT_CHARS=6000
TANGTANG_CACHE_RECENT_ROUNDS=8
```

`shadow` 只在本地构造、序列化和比较新旧 payload，不发起第二次模型请求。布局、人格资源和工具 Schema 分别版本化；换群、换用户、状态变化或当前消息变化不得改变稳定前缀 hash。

缓存 cohort 使用每群一个已确认送达的对话主干。个人画像、长期记忆、引用、图片和当前群聊增量只留在本轮尾部，不能写回主干；旧个人会话保留审计用途且不合并。`cache-shadow` 比较候选群主干 payload，实际请求不发送缓存亲和字段；`cache-canary` 仅对指定群启用，`cache-on` 才全量启用。`canary/on` 对已验证模型档案发送由群主干 session 与 `context_epoch` 哈希派生的 `prompt_cache_key`，不包含群号、用户身份或正文；稳定前缀变化会递增 epoch 并自动换键。模型1的 Cline Pass 代理用该键稳定选择账号/上游并在转发前移除，模型2/3/4直接接收；未知档案只启用稳定前缀。接口明确拒绝字段时自动无键重试，usage 记录 `cache_affinity_key_sent`、`cache_affinity_transport` 和 `cache_affinity_fallback`。

## 阶段门槛

| 阶段 | 门槛 |
| --- | --- |
| Q0 | 本页能力 100% 分类；现有聚焦行为测试通过；记录风险与指标口径 |
| Q1 | 缓存 read/write/miss/unsupported、稳定前缀和工具 hash、分层输入量、跨工具回合指标可观测且日志脱敏 |
| Q2 | `ContextEnvelope` 同时生成两种 API payload；v1/shadow/v2 可回退；稳定序列化测试通过 |
| Q3 | 会话、确认轮次、快照和压缩任务持久化；幂等、租约、失败回退、人格/群隔离和原始数据保留测试通过 |
| Q4 | 14 个旧动作全部进入原生工具全集；整批预检、顺序执行、无双重执行；旧 `feature_calls` 仍兼容 |
| Q5 | 台账中的只读能力使用相同业务处理器，图片和档案原文不回灌模型 |
| Q6 | 状态工具只认本轮明确请求和真实 `at`；幂等及过期状态测试通过 |
| Q7 | NTE/WW 只走上游透传；失败快速返回；上游仓库完全干净 |
| Q8 | shadow、测试群和分批灰度证据齐全；全门禁通过；完成 NoneBot 单独重启、实聊与回退演练 |

## 风险清单

- 供应商缓存字段名称和语义不同；缺失默认记 `unsupported`，不能补零。生产请求只有在响应明确返回 cache read/write/miss 字段时才进入命中率统计；合成探针的推断值必须与生产统计隔离，不能用来把字段缺失改写成确定未命中。
- Chat Completions 与 Responses 的工具项形状不同；语义序列必须一致且保持严格追加前缀。
- QQ 发送可能无回执或部分成功；未确认送达的内容不能进入活跃上下文。
- 模型可能重复、越权或在状态变化后调用工具；执行层必须整批预检并逐项复核。
- 人格个人记忆、原始聊天上下文和群摘要都按当前群隔离，不能串群。
- 压缩失败、崩溃或租约过期不能阻塞聊天、推进游标或覆盖上一有效快照。
- 模型不支持 tools 时只能回退普通生成，不得伪称功能已执行。

## 最终指标

- 受控重复长上下文预热后缓存 token 比例至少 90%；供应商支持时生产中位数目标至少 80%，只报告实测值。
- 同场景非缓存输入 token 相比 Q0 下降至少 50%。
- 普通用户能力台账保持 100% 分类，14 个旧动作全部原生工具化。
- 完整仓库门禁和 pytest 通过；仅重启 NoneBot 后核验新 PID、8080、OneBot 连接、实际聊天/工具调用及空错误日志。

## Q8 灰度、指标与回退

先执行不访问模型、不发送 QQ 消息的离线回放：

```powershell
.\.venv\Scripts\python.exe scripts\replay_agent_context.py
.\.venv\Scripts\python.exe scripts\benchmark_agent_cache.py
.\.venv\Scripts\python.exe scripts\probe_provider_cache.py
```

`replay_agent_context.py` 对 Responses 与 Chat Completions 构造相同的两轮语义序列，验证上一轮请求是下一轮的严格前缀、工具调用／结果顺序一致、shadow 候选含完整工具 Schema 且没有第二次付费请求。报告只含字符数、条目数和 hash，默认写入忽略的 `reports/agent-context-replay.json`。`benchmark_agent_cache.py` 默认同样只构造合成上下文并记录零网络请求；只有人工明确加 `--live` 才会用当前 `config_loader` 和 `TangtangProvider` 连续请求，首轮预热、后续统计真实缓存字段。`probe_provider_cache.py --live` 使用冷请求、严格追加、TTL 后追加和不同前缀对照验证通用 payload；只有通用探针未命中时才运行 `--live --prompt-cache-key` 验证缓存亲和能力。该开关只影响合成探针，默认不发送字段。live 报告使用纯合成长前缀，不包含群聊、身份、端点、实际缓存键或密钥；HTTP 失败仍写入脱敏状态和已尝试次数并返回非零。供应商不返回缓存字段时保持 `unsupported`，不把字段缺失改写成确定未命中；合成探针结果与生产请求统计严格分开。

生产 usage 聚合使用：

```powershell
.\.venv\Scripts\python.exe scripts\report_agent_usage.py `
  --baseline-start 2026-09-21T00:00:00+08:00 `
  --baseline-end 2026-09-21T12:00:00+08:00 `
  --candidate-start 2026-09-21T12:00:00+08:00 `
  --output reports\agent-usage.json
```

时间窗口必须替换为实际灰度边界。脚本按 `request_trace` 去重，只汇总终态模型请求；输出 token、缓存 read/write/miss、缓存比例中位数、非缓存输入、延迟、模型／工具回合、布局和 hash，不复制正文、群／用户 ID、明细或密钥。另以 `payload_builds` 聚合 `model_started` 的布局、Schema hash、分层字符数和 shadow 比较结果，因此即使供应商请求失败也能证明本地候选构造，但不能把它当成模型成功或缓存命中。老记录或缺字段记录继续计入请求量，但缓存状态为 `unsupported`。

上线顺序固定如下：

1. 离线回放和完整门禁通过。
2. 运行 `.\.venv\Scripts\python.exe scripts\configure_agent_rollout.py --mode cache-shadow --apply`，原子设置 v2 上下文、缓存 cohort 的 `shadow` 模式和压缩开关，然后仅重启 NoneBot。该模式仍只发送一次旧 payload，候选仅本地构造。
3. 在配置的测试群连续完成十轮普通文本聊天，每轮等待实际送达后再发送下一轮；普通聊天不携带工具 Schema。明确 `#` 指令的回归验证与缓存十轮样本分开执行，核对回复、QQ 回执、usage、OneBot 流量和错误日志。
4. 依次在测试群、少量管理群、全部已启用群观察；缓存 cohort 使用同一 `TANGTANG_CACHE_COHORT_MODE` 的 `shadow`、`canary`、`on` 状态推进，群范围由 canary 列表控制。
5. 运行 `scripts\configure_agent_rollout.py --mode cache-canary --canary-group-ids <已管理测试群> --apply`，仅让测试群启用群主干；通过后使用 `--mode cache-on --apply` 扩大范围。每次仅重启 NoneBot并重复实聊、只读工具和健康检查。
6. 执行一次开关回退演练：用 `scripts\configure_agent_rollout.py --mode rollback --apply` 切到 `v1/false/false`，仅重启 NoneBot并验证，再用 `--mode v2 --apply` 恢复 `v2/true/true`。每次写入都在 `data/backups` 保存 `.env` 恢复副本；不得删除会话、轮次、快照或压缩任务。

每次重启都必须核验新 PID、`127.0.0.1:8080` 监听归属、已建立 OneBot 连接／实际流量以及空 `logs/bot.err.log`，并确认 SnowLuma、QQ 和 Core 未被重启。固定测试群通知只能使用 `scripts/notify_test_group.py`，不得向脚本传群号。

旧 `feature_calls` 提示协议在 v2 原生工具稳定后可从新请求中停止注入；兼容解析器需保留一个回退观察期，防止重试中的旧响应失效。同一请求仍由去重门保证只执行一个协议。只有受控 live 基准实际达到 90%、供应商支持时生产中位数达到目标、同场景非缓存输入下降至少 50%，才能把相应指标标记为完成；未达到时记录实测值和继续观察，不修改报告冒充通过。
