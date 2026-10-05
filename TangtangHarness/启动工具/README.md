# Harness 启动工具

日常双击 `01-启动全部.bat`；结束使用双击 `02-关闭全部.bat`。`41-检查全部状态.bat` 查看服务与连接状态，`51-打开Harness控制台.bat` 打开控制台。

| 类别 | 入口 |
| --- | --- |
| 一键操作 | `01-启动全部.bat`、`02-关闭全部.bat` |
| Harness | `11-启动Harness.bat`、`12-停止Harness.bat`、`13-重启Harness.bat` |
| Core | `21-启动Core.bat`、`22-停止Core.bat` |
| SnowLuma | `31-启动SnowLuma.bat`、`32-停止SnowLuma.bat` |
| 状态检查 | `41-检查全部状态.bat` |
| 网页 | `51-打开Harness控制台.bat`、`52-打开SnowLuma管理页.bat` |

一键启动顺序为 Core → Harness → SnowLuma；关闭顺序为 Harness → Core → SnowLuma。语音随 Harness 按已保存的开关运行，QQ 保持独立，登录及验证由操作者完成。

这套入口不启动旧 NoneBot，不改写 SnowLuma 连接。仓库根目录的旧启动工具继续保留。完整行为、命令行动作与旧系统回切说明见[运行入口](../docs/运行入口.md)。
