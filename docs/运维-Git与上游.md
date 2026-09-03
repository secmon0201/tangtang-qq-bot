# 仓库边界与上游更新

## 一个主仓库包含什么

QQ 机器人使用一个自研主仓库，统一提交以下内容：

- `bot/plugins`：NoneBot 事件入口和命令接管，包括 NTE/鸣潮排行榜、帮助图与指令接管。
- `bot/application`：跨插件用例编排和本地功能注册，不依赖具体插件实现。
- `bot/services`：数据库、渲染、NTE/鸣潮只读数据适配和其他业务服务。
- `bot/integrations`：对外部运行时的项目侧兼容层。
- `bot/resources`：机器人自有、可复现的静态资源。
- `scripts`、`tests`、`docs`、`config`：运维、验证、说明和非敏感配置。
- `启动工具`：面向中文用户的双击入口；具体实现仍由 `scripts` 统一维护。
- `skills`：仓库自有的 Codex 维护 Skill，链接到本机后由后续会话自动使用。

NTE 与鸣潮是主仓库中的两个独立功能边界，不是单独摘出的仓库。这样排行榜、帮助图、指令门、共享头像和机器人权限规则可以在一次提交中保持一致。

本项目只维护 Windows 本机运行方式，不保留其他操作系统或虚拟机部署脚本。

## 不进入主仓库的内容

- `GsUID.Core` 及其 `GenshinUID`、`NTEUID`、`XutheringWavesUID` 子仓库。
- `NapCat.Shell`、Lagrange、`.venv`、构建工具和下载的可执行文件。
- `.env`、数据库、日志、备份、登录状态、Cookie、Token 和运行时缓存。

这些目录由 `.gitignore` 排除，`scripts/validate_repository.py` 会再次检查当前 Git 候选文件和所有本地分支、标签可达的历史路径。CI 使用完整克隆执行同一检查，避免已经删除的运行数据继续留在可推送历史中。第三方仓库的 URL、分支和当前 commit 记录在 `config/upstream-lock.json`，不复制第三方历史。

## 依赖方向

```text
plugins -> application -> services
   |                         |
   +------ integrations -----+

QQ/NapCat -> NoneBot 主仓库 -> 官方 Core 连接器 -> GsUID Core -> NTEUID / XutheringWavesUID
```

插件之间不得直接导入，`services` 和 `application` 也不得反向导入插件。每个 `bot/plugins/*.py` 功能入口必须在 `bot/application/plugin_registry.py` 中恰好注册一次；注册表只保存模块名和启用元数据，不导入插件。`scripts/validate_architecture.py` 和 `tests/test_plugin_registry.py` 会共同检查依赖规则、循环依赖、重复实现、漏注册与重复注册。

游戏接口本地接管只读取 Core 的公开运行数据或上游资源：

- 排行榜数据通过 SQLite 只读连接读取 `GsData.db`。
- 帮助图、排行图和命令接管实现位于主仓库。
- 鸣潮总榜只覆盖本机器人本地绑定；没有 A 海岸鸣潮榜。既有数据仅通过 dry-run 优先、五群范围受限的导入工具迁移。
- Core 通过 `scripts/run_gsuid_core.py` 启动，Windows 写入兼容和禁用插件过滤在内存中安装，不修改上游文件。

## 更新与提交

日常提交整个主仓库：

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe -m pytest
git add --all
git commit
```

更新上游运行依赖前先停止 Core，再执行：

```powershell
.\scripts\stop_gsuid_core.ps1
.\scripts\install_gsuid.ps1
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\scripts\start_gsuid_core.ps1
```

安装脚本发现上游仓库有本地改动时会停止。不要 stash 后继续更新；先把必要适配迁回主仓库的 `bot/integrations` 或 `bot/services`，让上游目录恢复干净。

上游更新后，`config/upstream-lock.json` 的 commit 变化应与本地适配和测试一起提交。GitHub 远端应使用私有空仓库；首次推送前只需配置远端 URL，不需要把外部运行目录上传。
