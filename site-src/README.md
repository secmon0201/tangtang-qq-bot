# 公开展示站模块化源码

`site-src/` 是公开展示站的维护源，`site/` 是由 Git 忽略的本机线上兼容产物。不要提交或直接编辑该产物，也不要在公开站点运行期间逐个覆盖其中的文件。

- `site.json`：页面、路由、导航、页面元信息和板块顺序的唯一清单。
- `pages/<page>/*.html`：可独立增删改、排序和跨页迁移的板块。
- `components/`：页头、移动导航、页脚、弹窗和提示等共享结构。
- `scripts/`、`styles/`：按职责拆分，构建时仍分别合并为一个带内容指纹的 JavaScript 和 CSS 文件。
- `static/`：图片、vendor 和响应头等可复现静态输入；构建器不从当前 `site/` 兼容产物反向取源。
- `content/releases.json`：公开更新日志；空数组时保留现有“敬请期待”页面。

常用维护入口：

```powershell
.\.venv\Scripts\python.exe scripts\manage_public_site.py list
.\.venv\Scripts\python.exe scripts\manage_public_site.py update-page --key experience --title "群友体验 · 糖糖"
.\.venv\Scripts\python.exe scripts\manage_public_site.py move-section --page home --key portals --to-page experience --after social
.\.venv\Scripts\python.exe scripts\manage_public_site.py add-release --version v1.1.0 --date 2026-08-29 --title "体验更新" --item "新增内容" --item "修复内容"
.\.venv\Scripts\python.exe scripts\build_public_site.py --output <暂存目录>
.\.venv\Scripts\python.exe scripts\publish_public_site.py
```

`publish_public_site.py` 默认只构建和验证；只有显式添加 `--apply` 才会写入完整版本目录，并通过单个指针文件原子切换。发布失败不会改变当前指针，旧版本目录可用于 `--rollback <版本目录> --apply`。

构建器只复制当前注册页面和板块实际引用的静态资源，文本资源预生成 Brotli/Gzip，图片只在像素完全一致且至少节省 10% 时转为无损 WebP。文件名内容指纹、长期缓存和网关压缩协商由构建器与网关共同维护，不要手工改构建产物名称。
