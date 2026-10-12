# TASKS — Pic Shelf

> 任务驱动（非日历驱动）：按依赖层推进，完成一项勾一项。
> 图例：`[x]` 完成 ｜ `[~]` 进行中 ｜ `[ ]` 待办 ｜ `[!]` 需维护者决策（见 docs/decisions.md）
> **当前状态：v0.0.1（托管导入 + 中英双语 + Docker 化）开发完成，已开源（GitHub `StepaniaH/pic-shelf`，远端仅 main）** · 62/62 测试全绿 · 开发分支 `dev`（仅本地）

## T0 · 调研与立项
- [x] 竞品调研（`docs/research/2026-10-11-open-source-image-viewers.md`，stars/活跃度快照 2026-10-11）
- [x] 架构定稿（`docs/architecture.md`）
- [x] 决策清单（`docs/decisions.md`）

## T1 · 项目骨架
- [x] git 仓库初始化（main 种子提交 → 切 `dev` 开发）
- [x] pyproject / venv / 依赖（uv，缓存收纳在项目内 `.uv-cache`）
- [x] AGENTS.md（2026-10-11）

## T2 · 数据层与认证
- [x] config / db 迁移 / 13 张表 + 索引（SQLite WAL）
- [x] auth：scrypt 口令、HMAC 令牌、组能力、库授权
- [x] tests: test_auth / test_db（迁移幂等 + 跨线程回归）

## T3 · 编码与导入
- [x] hashing：sha256 / pHash / 缩略图+预览图（WebP）
- [x] indexer：增量扫描、两阶段移动检测、软删除、扫描记录
- [x] tests: test_hashing / test_indexer（增删改移全覆盖）

## T4 · 检索与播放引擎
- [x] search：danbooru-lite 语法 + ACL 过滤 + 6 种排序
- [x] play：会话队列（顺序/乱序/随机·定量·循环）、next/prev/goto/reshuffle
- [x] tests: test_search / test_play

## T5 · REST API
- [x] 全部路由（auth/libraries/albums/images/media/tags/artists/ratings/favorites/comments/notes/play/import/admin/dupes/stats）
- [x] tests: test_api（含 ACL 403、媒体鉴权、文件夹专辑保护、扫码）
- [x] **修复**：FastAPI 线程池下 SQLite 跨线程（check_same_thread=False + 回归测试）

## T6 · CLI（Click）
- [x] init / serve / library / scan / ls / show / search / rate / tag / comment / note / favorite / artist / play（mpv·terminal·list）/ dupes / export（含 --encrypt）/ import-backup / import / users / groups / stats
- [x] tests: test_cli（全流程：导入→评分→播放→导出加密→恢复）

## T7 · TUI（Textual）
- [x] 树形浏览（库/专辑/艺术家/标签/收藏）+ 列表 + 详情 + 预览（img2txt）
- [x] 评分 / Tag / 收藏 / 评论 / 笔记快捷键；幻灯片连播（空格暂停）
- [x] tests: test_tui（run_test 冒烟：导航+评分+标签落库验证）

## T8 · Web 前端（零构建）
- [x] 登录 / 首页统计 / 网格浏览（专辑·艺术家·标签·收藏·搜索·库）
- [x] 查看器（评分·标签·艺术家·专辑·评论·笔记·收藏·下载·尺寸切换·键盘导航）
- [x] 幻灯片（间隔/数量/顺序/循环/尺寸/字幕；空格暂停、Esc 退出、aria-live、减少动效）
- [x] 管理页（用户/用户组/库授权/扫描/导入向导）
- [x] 浏览器实测（真机验证：各页面交互全通，0 JS 错误）
- [x] **布局修复轮（2026-10-11）**：flex 收缩/换行、长文本换行、窄宽适配、表格滚动、栅格下限保护；
      5 宽度（1280/1000/900/768/640）× 7 页面程序化审计零问题 + 视觉复核通过
- [x] 截图存档（`docs/screenshots/2026-10-11-library-en.png` / `-viewer-en.png` / `-library-zh.png`，真实素材）

## T9 · 端到端与交付
- [x] 真实素材全链路（70 张导入、3 专辑、0 错误；查重抓到一对实际重复文件）
- [x] 全套 pytest 通过 + git 工作区干净
- [x] README / 架构 / 决策 / 调研 / AGENTS 文档齐备
- [x] dev 分支按任务分提交（按里程碑评审后并入 `main`）

## v0.0.1 · 托管导入
- [x] `picshelf/importer.py`：预演计划 + hardlink/copy/move + sha256 去重 + 同名自动改名 + 越界防护 + 跨盘降级
- [x] CLI `picshelf import`（预演优先 / `--apply` / `--yes` / 完成后自动扫描）+ `library add --mkdir`
- [x] Web 管理页「导入」向导（`/api/v1/import/plan|execute`，需 manage 权限；预演表格 + 确认弹窗 + 执行）
- [x] tests: test_import（11 项，含 API 流程与权限拒绝）
- [x] 真实素材沙盒演示（CLI 与 Web 两条链路：预演 → 硬链接执行 → 自动入库 → 数据核对）
- [ ] 命名模板增强（基于 tag/艺术家的目录模板）

## v0.0.1 · 中英双语（i18n）
- [x] 目录式映射：`picshelf/i18n.py` + `web/js/i18n.js`（中文原文为 msgid，缺译回退中文，零构建零依赖）
- [x] 全端接入：CLI（`--lang` / 帮助渲染时本地化）/ TUI / Web（顶栏中·EN 切换 + `data-i18n` 骨架）/ API（Accept-Language）
- [x] 机械转换（AST + 词法）+ 占位符一致性校验 0 不一致；嵌套模板漏译专项清扫（约 55 处）
- [x] tests/test_i18n.py（解析优先级 / 回退 / CLI / API）；全套 **62 passed**
- [x] README 双语（EN 库页面+查看器 / ZH 库页面，真实壁纸素材）+ 语言互切链接

## v0.0.1 · Docker 化 + 部署文档
- [x] `Dockerfile`（python:3.11-slim / 非 root UID 1000 / healthcheck / `serve --host 0.0.0.0`）
- [x] `docker-compose.yml`（默认仅绑 127.0.0.1；data 卷 + 库目录只读挂载示例）
- [x] `docs/deployment.md`（初始化 / 卷 / 网络 / 权限 / 升级 / 备份 / FAQ）+ README 双语 Docker 章节
- [x] 实测：镜像构建 → 容器 init → 扫描 → 健康检查 → 页面访问全通（仅本地验证，未发布任何 registry）

## 后续（Roadmap）
- [ ] `[!]` D1 加密：已结论「现阶段不做」（触发条件与零成本补做路径见 decisions D1）
- [ ] 镜像发布（Docker Hub / GHCR，自 v0.0.1 起）与安装包；远程访问 = tailnet 直连（Caddy 可选）（见 decisions D2 / B0）
- [ ] CBZ/ZIP 归档导入 + ComicInfo.xml；视频支持
- [ ] 本地模型识图（Ollama 视觉打标 / CLIP 相似检索）
- [ ] 图上区域注释（notes.geometry 已预留）；分享链接；SSE 实时刷新；桌面端（Tauri 评估）

## 验证记录（交付证据摘要）
- `pytest tests/ -q` → **62 passed**（含 test_i18n 4 项）
- 真实素材扫描：`新增 70，更新 0，移动 0，缺失 0，错误 0`
- 托管导入沙盒：CLI 预演→执行→自动扫描（inode 验证）；Web 向导 E2E（预演 2 → 执行 2 → 入库 2）
- 布局审计：5 宽度 × 7 页面零问题 + 视觉复核（见 T8）
- 浏览器实测（CDP 驱动）：服务端日志无异常，0 JS 错误
- 隐私检查：git 跟踪文件无个人绝对路径 / 无口令
- 服务运行：`127.0.0.1:39431`（仅本机）
