# 功能：A-SOUL 与 B 站

A-SOUL 直播日程查询、B 站自动播报与接口测试命令。自动播报默认关闭，需在 `.env` 显式启用。

## 普通用户命令

| 命令 | 说明 |
| --- | --- |
| `#A魂帮助` / `#bot帮助` | 查看本功能入口。 |
| `#今日直播` / `#明日直播` / `#本周直播` | 查询当天、明天或本周剩余 A-SOUL 直播日程。 |

`-a` 参数已不再生效：带与不带 `-a` 都返回完整日程，包含心宜、思诺，不再隐藏。

## 超级管理员命令

| 命令 | 说明 |
| --- | --- |
| `#日程高亮 <YYYY-MM-DD> [序号] [粉色/红色/白金色]` | 查看或标记特别关注日程。 |
| `#取消日程高亮 <日期> <序号>` / `#日程高亮列表` / `#取消日程高亮记录 <序号>` | 维护高亮记录。 |
| `#bili_status` | 查看 B 站播报、轮询、推送群、登录和游标状态。 |
| `#bili_login` | 仅私聊，生成 B 站 App 扫码登录；登录态仅存本机 SQLite。 |
| `#bili_logout` | 清除本机 B 站登录态。 |
| `#bili_test_dynamic` / `#bili_test_comment` | 已停用，避免高频请求触发风控。 |
| `#bili_dump_dynamic` / `#bili_dump_live` | 导出原始响应到 `data/asoul_debug` 排查接口。 |
| `#bili_test_video` / `#bili_test_live` / `#bili_test_atall` / `#bili_test_all` | 验证视频、直播、@全体和综合接口。 |

## 行为规则

- 首次成功轮询只建立游标，不会回放历史动态、视频或直播；之后只推送新增内容。
- 开播通知尝试 `@全体`，机器人无管理员权限时自动回退普通消息；动态推送由 `ASOUL_BILI_PUSH_DYNAMIC` 控制，评论扫描保持关闭。
- `ASOUL_BILI_RENDER_CARDS=true` 时推送附带本地图片卡片。

## 配置

- `ASOUL_BILI_ENABLED`、`ASOUL_BILI_POLL_INTERVAL_SECONDS`：播报总开关与轮询间隔。
- `ASOUL_BILI_GROUP_IDS`：显式推送群列表，优先于 A 海岸总开关。
- `ASOUL_BILI_PUSH_A_COAST`、`ASOUL_BILI_A_COAST_GROUP_IDS`：是否额外向 A 海岸群推送及范围，不影响发言统计和档案的固定范围。
- `ASOUL_BILI_PUSH_DYNAMIC/VIDEO/LIVE/COMMENT`、`ASOUL_BILI_RENDER_CARDS`：各类推送与卡片开关。

## 维护与验证

日程解析、B 站接口和推送见 `tests/test_asoul_plugin.py`、`tests/test_asoul_service.py`、`tests/test_asoul_render.py`。
