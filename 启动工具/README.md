# 启动工具

本目录是普通用户操作机器人的唯一快捷入口。数字只用于固定排序，不代表执行顺序。

日常只需要 `01`、`02`、`03`；普通 Python 功能更新使用 `11`。其余入口是故障排查或固定域名不可用时的灾备工具，不需要按顺序执行。

| 文件 | 用途 | 是否影响 QQ/SnowLuma |
| --- | --- | --- |
| `01-启动全部.bat` | 一键启动 Core、NoneBot、SnowLuma、全部网页隧道和守护程序，并等待健康检查通过 | 会启动 |
| `02-重启全部.bat` | 一键完整关闭并重启全栈，最后执行同一套健康检查 | 会重启 |
| `03-关闭全部.bat` | 一键关闭全部可确认属于本项目的组件，并检查机器人、网关和守护进程已退出 | 会关闭 |
| `11-仅重启机器人.bat` | 普通代码更新后只重启 NoneBot | 不影响 |
| `21-启动异环登录隧道.bat` | 灾备：固定域名不可用时启动或刷新异环 Quick Tunnel | 不影响 QQ |
| `22-关闭异环登录隧道.bat` | 灾备：关闭异环 Quick Tunnel 并阻止 watchdog 自动拉起 | 不影响 QQ |
| `23-设置异环登录地址.bat` | 高级维护：手动写入已有的 HTTPS 异环登录地址 | 不影响 QQ |
| `24-启动公告网页隧道.bat` | 灾备：固定域名不可用时启动公告编辑页 Quick Tunnel | 不影响 QQ |
| `25-关闭公告网页隧道.bat` | 灾备：关闭公告编辑页 Quick Tunnel | 不影响 QQ |
| `26-启动运营网页隧道.bat` | 灾备：固定域名不可用时启动查重网页 Quick Tunnel | 不影响 QQ |
| `27-关闭运营网页隧道.bat` | 灾备：关闭查重网页 Quick Tunnel | 不影响 QQ |
| `31-启动守护程序.bat` | 故障排查：watchdog 被单独关闭后重新启动 | 不重启 |
| `32-关闭守护程序.bat` | 故障排查：临时停止 watchdog，避免其自动恢复组件 | 不关闭 QQ |

QQ 扫码、滑块、短信和设备验证始终在 QQ/SnowLuma 界面中由用户手动完成。

三个全栈快捷入口保持原文件名，内部统一调用 `scripts\start_all.ps1`、`restart_all.ps1`、`stop_all.ps1`。启动和重启只有在 SnowLuma、NoneBot 监听、唯一 OneBot 客户端、watchdog 进程与健康心跳、Core 监听和空错误日志全部通过后才返回成功；若 QQ 需要重新验证，会明确失败并保留 SnowLuma WebUI 供人工处理。

`secmon.cn` 配置完成后，使用 `scripts\configure_tangtang_named_tunnel.ps1` 完成一次性 Cloudflare 授权，再由 `scripts\start_tangtang_named_tunnel.ps1` / `stop_tangtang_named_tunnel.ps1` 启停固定入口。`https://tangtang.secmon.cn/` 是糖糖主页，各机器人网页继续使用同域名下的独立功能路径。此后 `01`、`02`、`03` 和 watchdog 会自动识别固定隧道；详细地址与安全边界见 [网页地址与固定隧道](../docs/运维-网页地址与固定隧道.md)。

当前机器已经配置固定域名时，`21` 到 `27` 都不是日常入口。保留它们只为了域名配置缺失、Cloudflare 固定隧道故障或人工诊断；误用可能临时改写功能链接，因此普通运行不要点击。
