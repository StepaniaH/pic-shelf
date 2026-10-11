# AGENTS.md — Pic Shelf 开发者入口

给任何 AI 代理 / 协作者的速查。先读本文件，再读 `TASKS.md`（任务状态）、`docs/architecture.md`（设计）、`docs/decisions.md`（决策与待决事项）。

## 项目概览

自托管图片库：导入 → 编码（哈希/缩略图）→ 检索 / 评分 / Tag / 艺术家 → 规则化播放；**Web + TUI + CLI** 三端。
当前版本：v0.1 + v0.2 托管导入（CLI + Web 向导）已落地（2026-10-11，58 项测试全绿）。规划路线：**开源 → Docker 部署 / 安装包**。项目定名 **Pic Shelf**（2026-10-11，见 `docs/decisions.md` D11）。

## 工作流约定

- **main 分支保护**：main 只放经评审的稳定版本（当前仅种子提交）；日常开发在 `dev`，合并需评审确认。
- **任务驱动**：按 `TASKS.md` 分层推进，一任务一提交（`feat:` / `fix:` / `test:` / `docs:` 前缀）。
- **不 push**：无 remote；未经维护者批准不添加 remote、不 push、不对外暴露服务。
- **真实测试**：完成声明必须附真实运行结果（pytest / curl / CLI 输出）；禁止「应该能跑」式断言。

## 常用命令

```bash
cd ~/Developer/passion-projects/pic-shelf
export UV_CACHE_DIR=$PWD/.uv-cache        # 依赖缓存留在项目内

.venv/bin/python -m pytest tests/ -q      # 测试
.venv/bin/picshelf serve                     # Web → http://127.0.0.1:39431
.venv/bin/picshelf tui                       # 终端界面
.venv/bin/picshelf import <源目录> <库名> --apply   # 托管导入（默认预演）
.venv/bin/picshelf --help                    # 全部命令
```

## 硬性约束

- 运行数据全部在 `./data/`（数据库 / 缩略图 / 密钥 / 播放列表），已 gitignore，绝不入库。
- git 跟踪文件中不允许出现个人绝对路径、IP、主机名、口令；素材测试用环境变量 `PICSHELF_TEST_MATERIALS`。
- **不越界**：不修改本仓库与测试素材目录之外的任何文件；基建变更（Caddy / DNS / 防火墙 / 部署）先提案等批准。
- 零遥测。
- UI 中文；数值自解释（不出现未定义缩写）；颜色语义：蓝=导航/可交互、绿=正常、橙=警示与破坏性确认；破坏性操作必须确认。
- 面向用户的报错必须可操作（给出下一步），不带 stack trace。

## 结构速查

| 模块 | 职责 |
|---|---|
| `picshelf/config.py` | 数据目录解析（`--data` / `PICSHELF_DATA` / 默认 `./data`） |
| `picshelf/db.py` | SQLite(WAL) 连接与版本化迁移（注意 `check_same_thread=False`，见注释） |
| `picshelf/hashing.py` | sha256 / pHash / 缩略图生成 |
| `picshelf/indexer.py` | 库扫描（两阶段移动检测、软删除） |
| `picshelf/importer.py` | 托管库导入（hardlink/copy/move，预演优先） |
| `picshelf/search.py` | 搜索语法 → 带 ACL 过滤的 SQL |
| `picshelf/auth.py` | 用户 / 组 / 能力 / 令牌 |
| `picshelf/play.py` | 播放会话队列（顺序/乱序/随机/定量/循环） |
| `picshelf/ops.py` | 评分 / 标签 / 艺术家关联 / 评论 / 笔记 / 专辑（API 与 CLI 共用） |
| `picshelf/libsvc.py` | 库管理与查重（sha256 精确 / pHash 相似） |
| `picshelf/api/` | FastAPI 应用（`create_app(cfg)` 工厂） |
| `picshelf/cli.py` | Click CLI（入口 `main`） |
| `picshelf/tui.py` | Textual 应用（`run_tui(cfg)`） |
| `picshelf/web/` | 零构建前端（原生 ES modules） |

## 数据库

SQLite（WAL）；schema 变更 = `db.py` 的 `MIGRATIONS` 追加新版本块 + `SCHEMA_VERSION` 加一；禁止破坏性修改已发布字段。表清单见 `docs/architecture.md`。

## 提交前自检

1. `.venv/bin/python -m pytest tests/ -q` 全绿
2. `git status` 干净、无 `data/` 泄漏
3. 新增行为有对应测试
4. 界面 / API 变更同步更新 README 或 docs

## 待办与决策

见 `TASKS.md`（任务状态）与 `docs/decisions.md`（D1 加密结论；D2 已决策：本机自用 → 开源 → Docker；D10 库模式已批准推进；D11 命名已定案）。
