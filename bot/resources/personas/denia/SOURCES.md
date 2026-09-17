# 素材来源

角色与剧情原作：《鸣潮》，库洛游戏。

设计参考：[SSDeutschland/Denia-chat](https://github.com/SSDeutschland/Denia-chat)，public 分支，修订 becf50be0f9ce78c7678ac75dc9c0dbbbf2de9f4。参考核心人设、公开剧情事实、标签语料、观点和表情语义索引。persona.md 为适配本项目群聊边界的整理稿，示例为本项目新编。

本目录不导入上游用户档案、私人记忆、工具指令或 L1 思考样本。第三方角色、图片和台词不因仓库的 MIT 软件许可而自动获得同样许可；沿用上游非商业素材使用声明并保留原始来源。完整上游与许可证保存在独立研究归档中。

达妮娅声线参考：[villia/dania](https://modelscope.cn/models/villia/dania)。权重、参考音频和环境为独立本地数据，不进入公开源码。

聊天表情当前使用低等画质版本：`expressions/` 内图片按原比例缩小至最长边 300 像素；最长边不超过 300 像素的文件保持原样。保留原文件名、格式以及动图帧数、逐帧时长和循环设置，`expression_catalog.json` 的 SHA-256 对应当前文件。`asset-manifest.json` 保留最初导入素材的来源校验值。

原图版本和切换前的中等画质版本只在本机 `data/backups/denia-expressions-<时间>/original/`、`medium/` 留档，不参与运行时表情选择、不提交 Git。备份清单记录原图来源、备份校验值、压缩前后尺寸及动画信息；恢复时须同时核对表情目录和目录清单。
