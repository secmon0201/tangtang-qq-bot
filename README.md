# QQ 本地数据机器人

这是一个运行在 Windows 本机的 NoneBot2 / OneBot v11 QQ 群机器人。QQ 登录由 NapCat / QQNT 完成，机器人通过 OneBot 接收事件，并将群设置和统计数据保存在本地 SQLite。

全部有效文档见 [文档总览](docs/README.md)，AI 和代码维护者还必须阅读 [AGENTS.md](AGENTS.md)。

## 产品边界

- 管理群数量不设上限。新观察到的群自动登记为独群，运行时以 SQLite 为准；`MANAGED_GROUP_IDS` 只作为首次迁移种子。
- 独群默认只使用本群数据，不与其他群互动。群主和 QQ 群管理员自动成为本群机器人管理员，可修改本群代称和功能开关。
- 私有集群只由超级管理员创建、查询、邀请、移除和解散。A海岸五群是第一个私有集群，不是机器人的默认产品边界。
- 加入集群时本群功能默认全部开启；之后各群可独立关闭功能，但群级开关不会把该群排除出集群统计。
- 群内 `#发言排行` 默认查看本群；集群成员可显式查看所在集群榜。独群的 23:50 推送只包含本群，集群成员群的推送包含集群榜。
- `#nte薄荷排行` 和 `#nte最强排行` 默认查看当前群；只有显式使用 `#nte薄荷总排行`、`#nte最强总排行` 才查看机器人总榜。
- 排行网页只通过 `/ranking/<token>/` 暴露对应群域数据。公开站不提供 A海岸专页、裸排行入口或旧短链。
- 活动和调查/问卷不属于当前运行功能。旧实现仅保留在 Git 历史中，参考方式见 [旧活动与问卷功能参考](docs/资料-旧活动与问卷功能参考.md)。

详细权限、默认开关和指令见 [权限与范围](docs/功能-权限与范围.md) 与 [全部 `#` 指令清单](docs/全部%23指令清单.md)。

## 安装

仅支持 Windows 本地部署：Python 3.13、NoneBot2、OneBot v11、NapCat / QQNT、PowerShell 和 SQLite。

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

至少确认 `.env` 中的 `MANAGED_GROUP_IDS`、`BOT_OPERATOR_IDS`、`NAPCAT_QQ_ID`、`BOT_COMMAND_PREFIX=#` 和 `BOT_REQUIRE_MENTION=false`。不要把 QQ 密码、验证码、Cookie、Token、数据库、日志或登录状态提交到 Git。

QQ 登录、二维码、验证码、滑块、短信和设备验证始终由用户手动完成。NapCat 反向 WebSocket 默认连接：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

完整安装步骤见 [安装与运行](docs/运维-安装与运行.md)。

## 运行

普通操作使用 `启动工具` 下的中文入口：

- `01-启动全部.bat`：启动完整本地环境。
- `02-重启全部.bat`：重启完整本地环境。
- `03-关闭全部.bat`：关闭完整本地环境。
- `11-仅重启机器人.bat`：普通 Python 改动后只重启 NoneBot，不重启 NapCat 或 QQ。

完整入口说明见 [启动工具说明](启动工具/README.md)。

NTE 是唯一启用的游戏接口，统一使用 `#nte`。排行、帮助和命令接管由本仓库维护；不得修改 `GsUID.Core` 或 NTEUID 源码，排行数据只读访问 `GsUID.Core\data\GsData.db`。详情见 [异环游戏接口](docs/功能-异环游戏接口.md)。

公开站以 `site-src/site.json` 为源，先暂存构建并校验，再通过 `scripts/publish_public_site.py --apply` 原子发布。不要直接修改线上发布目录。详情见 [公开展示站模块化维护](docs/运维-公开展示站模块化维护.md)。

## 校验

提交前必须运行：

```powershell
.\.venv\Scripts\python.exe scripts\validate_repository.py
.\.venv\Scripts\python.exe scripts\validate_docs.py
.\.venv\Scripts\python.exe scripts\validate_architecture.py
.\.venv\Scripts\python.exe scripts\validate_upstream_lock.py
.\.venv\Scripts\python.exe scripts\validate_nte_mode.py
.\.venv\Scripts\python.exe scripts\validate_qq_config.py --env .env
.\.venv\Scripts\python.exe -m pytest
```

仓库目录职责、提交范围和上游更新规则见 [Git 与上游](docs/运维-Git与上游.md)。
