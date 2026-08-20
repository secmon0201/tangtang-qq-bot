# Codex 远程持续任务操作手册

## 用途

本机 NoneBot 内置一个只面向超级管理员的 Codex worker。你可以在 QQ 里创建任务、补充需求、正式启动、暂停、取消和查看结果，不必每次回到电脑前打开终端。

它不是控制当前桌面 Codex 聊天框的自动化工具。每一个 QQ 任务会建立自己的 Codex CLI 会话；同一任务 ID 的后续指令会恢复这个会话，因此能保留该任务已经完成的改动、前文需求和结论。

## 前提

1. 发送指令的 QQ 必须位于 `BOT_OPERATOR_IDS`，即机器人定义的超级管理员名单。
2. `.env` 中需要保持以下配置有效：

```dotenv
CODEX_COMPLETION_NOTIFY_ENABLED=true
CODEX_WORKER_ENABLED=true
CODEX_WORKER_COMMAND=.codex/runtime/node_modules/.bin/codex.cmd
CODEX_WORKER_POLL_SECONDS=3
CODEX_WORKER_TIMEOUT_SECONDS=3600
CODEX_WORKER_SANDBOX=workspace-write
```

3. 机器人与 NapCat/OneBot 已连接。worker 作为 NoneBot 的内部常驻服务启动，无须单独启动 Windows 服务。
4. Codex CLI 使用本机已登录的 Codex 账号。若 CLI 登录失效，任务会失败并将错误送到固定通知群；在本机执行 `..codex\runtime\node_modules\.bin\codex.cmd login` 完成登录后，再用重试命令恢复。

## 指令库

所有指令都用 `#` 开头，大小写写法均可使用对应别名。以下命令均仅超级管理员可用。

| 指令 | 作用 | 例子 |
| --- | --- | --- |
| `#Codex <需求>` | 创建一个待启动任务，标题由需求前半段自动生成。 | `#Codex 修复猜数字说明图文字溢出，并补充测试。` |
| `#Codex 新 <标题> \| <需求>` | 创建一个带自定义标题的待启动任务。 | `#Codex 直播防护优化 \| 调整直播期间小游戏提醒文案。` |
| `#Codex 续 <ID> <补充需求>` | 向未取消任务追加一轮。若该任务正在执行，当前轮结束后自动接着做；其余状态需再启动。 | `#Codex 续 12 再把这部分写入使用手册。` |
| `#启动Codex <ID>` | 正式激活任务中所有待执行轮次。 | `#启动Codex 12` |
| `#暂停Codex <ID>` | 停止正在执行的一轮；尚未执行的续办内容保留，之后可再次启动。 | `#暂停Codex 12` |
| `#停止Codex <ID>` | `暂停Codex` 的同义命令。 | `#停止Codex 12` |
| `#取消Codex <ID>` | 停止当前轮并丢弃全部未执行内容。取消后不能再续办或重试。 | `#取消Codex 12` |
| `#Codex 重试 <ID>` | 将最近失败、停止或重启中断的一轮复制回队列。仍需启动才执行。 | `#Codex 重试 12` |
| `#Codex 状态 [ID]` | 查看指定任务；省略 ID 时查看最近任务。 | `#Codex 状态 12` |
| `#Codex 列表` | 列出最近任务及其状态、待执行轮数。 | `#Codex 列表` |
| `#Codex 结果 <ID>` | 将最近一次成功结果或错误作为合并转发发回当前会话。 | `#Codex 结果 12` |
| `#Codex帮助` | 查看简版指令说明。 | `#Codex帮助` |

## 推荐流程

### 新任务

```text
#Codex 直播防护优化 | 调整直播期间小游戏提醒文案，并补充单元测试。
#启动Codex 12
```

机器人会先返回任务 ID。启动后，worker 在数秒内取走任务并开始执行。执行结束后，结果会折叠为“Codex 执行结果”发送到群 `1067772451`，并 `@595861835` 提示查看。

### 在已有工作上继续

```text
#Codex 续 12 再把修改同步到机器人使用说明总览，并检查现有帮助页。
#启动Codex 12
```

worker 会使用任务 #12 保存的 Codex thread ID 执行 `codex exec resume`。这会保留该任务的独立上下文，不会把它当成全新思路。

### 正在执行时追加

```text
#Codex 续 12 把测试运行结果也写进最终结论。
```

不需要再次启动。当前轮完成后，worker 自动运行新增轮次，并继续同一个 Codex 会话。

## 状态说明

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| 待启动 | 已有待执行内容，但没有激活。 | `#启动Codex ID` |
| 已排队 | 已激活，等待唯一 worker 空闲。 | 等待，或暂停。 |
| 执行中 | Codex 正在处理一轮。 | 可续办、暂停或取消。 |
| 停止中 | 已请求终止子进程。 | 等待状态变为待启动或已取消。 |
| 已完成 | 没有待执行内容，最近一轮成功。 | 可续办后再启动。 |
| 失败，等待重试 | 启动、认证、超时或执行出现问题。 | `#Codex 重试 ID` 后启动，或续办补充要求。 |
| 已取消 | 队列内容已明确丢弃。 | 需要重新创建任务。 |

## 上下文和桌面端的关系

- 当前 Codex 桌面聊天与 QQ worker 会话不是同一条聊天，QQ 无法可靠地向当前桌面文本框自动输入内容。
- QQ 任务自身是可持续的：只要使用同一任务 ID 续办，CLI 会恢复同一 Codex 会话。
- 一个任务适合一个连续主题。例如“直播防护”持续使用任务 #12；与它无关的“猜数字玩法图片”另建任务。
- 如果桌面端已经有关键上下文，需要你手动把相关结论作为 `#Codex 续 ID ...` 的补充写进去；不要假设两个会话自动共享历史。

## 运行边界

- 每台机器同时只运行一个 QQ Codex 任务，避免并发改同一工作区。
- worker 固定在当前机器人项目目录运行，使用 `workspace-write` 或 `read-only`；配置禁止 `danger-full-access`。
- 每轮最长默认 3600 秒，超时会终止并标记失败。
- NoneBot 重启时，正在执行的一轮会标记为中断，不会自动重跑。使用 `#Codex 重试 ID` 后由超级管理员决定是否再次执行。
- 任务内容、轮次状态、Codex thread ID、最终结果和错误保存在本机 SQLite 数据库中，仅用于持续执行和查询。不要在 QQ 需求里填写密码、Token、Cookie 或其他密钥。
- worker 的固定提示要求 Codex 不读取、输出或修改 `.env`、认证文件和令牌；完成通知只转发最终结论，不转发思考过程或终端日志。

## 本机维护

修改 worker 配置或升级运行时后，按以下顺序操作：

```powershell
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\启动工具\11-仅重启机器人.bat
```

查看 NoneBot 日志：

```text
logs\bot.out.log
logs\bot.err.log
```

验证 Codex CLI 是否存在：

```powershell
.\.codex\runtime\node_modules\.bin\codex.cmd --version
```

不要删除 `.codex\runtime`，否则 `CODEX_WORKER_COMMAND` 会失效。升级时可在项目根目录重新执行：

```powershell
& 'C:\Program Files\nodejs\npm.cmd' install --prefix '.codex\runtime' --no-save @openai/codex@latest
```
