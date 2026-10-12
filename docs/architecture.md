# Pic Shelf 架构说明

## 技术栈与选型理由

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.11 + FastAPI + Uvicorn | 与开发者既有项目技术栈一致；自动 API 文档（`/docs`）；单进程即可服务 |
| 存储 | SQLite（WAL 模式） | 单文件、零运维、事务安全；个人/家庭规模（≤10 万图）足够；预留 Postgres 迁移路径 |
| 编码 | Pillow + imagehash | SHA-256 精确检索/去重；pHash 相似检索；WebP 缩略图（400px）与预览图（1600px） |
| CLI | Click | 组合命令清晰，测试友好（CliRunner） |
| TUI | Textual | 现代 TUI 框架；异步、可测（run_test） |
| Web | 原生 JS（零构建） | 无 node 工具链依赖，MVP 更快落地；后期桌面端可直接复用（Tauri/Electron 载同一前端） |
| 加密（MVP） | scrypt 口令哈希 + HMAC 令牌 + Fernet 加密备份 | 无额外服务依赖；全库静态加密另行决策（见 decisions.md D1） |

## 模块与数据流

```
import 流:  目录 → indexer.walk → hashing(sha256/phash/thumb) → images 表 + album(文件夹组)
查询流:     search.parse(语法) → SQL(带 ACL 过滤) → /api/v1/images
播放流:     过滤条件 → play.build_session(顺序/乱序/定量/循环) → play_items 队列
            → Web 幻灯片计时器 / CLI 交给 mpv / TUI 定时连播
认证流:     登录(scrypt 校验) → HMAC 令牌(HttpOnly Cookie 或 Bearer) → 每请求解析 → 能力检查
```

## 数据库表（schema v1）

| 表 | 说明 |
|---|---|
| `users` / `groups` / `user_groups` | 用户、组、成员关系；组携带能力集合(`capabilities` JSON) |
| `libraries` / `library_grants` | 库注册（根目录）与"组→库"访问级别（view/edit/manage） |
| `albums` / `album_images` | 文件夹专辑（自动物化）与手工专辑；封面；category（漫画/系列等分类的载体） |
| `images` | 相对路径、尺寸、mtime、SHA-256、pHash、缩略图路径、missing 软删除标记 |
| `artists` / `image_artists` | 艺术家实体（别名/链接/备注）与图片关联 |
| `tags` / `image_tags` | 命名空间标签（`作者:xxx`、`系列:xxx`）与图片关联 |
| `ratings` / `favorites` | 每用户 0–10 评分、收藏 |
| `comments` / `notes` | 评论与文本笔记（`geometry` 列预留给图上区域注释） |
| `play_sessions` / `play_items` | 播放会话（过滤条件 spec、顺序模式、游标）与物化队列 |
| `scans` | 扫描历史（增量统计、错误列表） |
| `meta` | schema_version 等 |

## 权限模型

- 能力（capability）：`view` `rate` `comment` `note` `tag_edit` `manage_library` `admin`。
- 用户的有效能力 = 所属各组的并集；`is_admin` 用户等于拥有全部能力。
- 库级访问：`library_grants`（组 × 库 → view/edit/manage），`manage` 含 `edit` 含 `view`。
- 媒体文件端点也强制 `view` 校验（缩略图/预览/原图一致）。
- 默认种子：组 `admins`（全能力）、`members`（view/rate/comment/note/tag_edit）；新库创建时自动给 admins=manage、members=view。

## 安全模型

- 口令：scrypt（N=2^14, r=8, p=1）+ 16B 随机盐，标准格式字符串存储。
- 令牌：`base64url(uid.exp.nonce).HMAC-SHA256`；密钥 `data/secret.key`（0600），首次 init 生成。
- Cookie：`HttpOnly; SameSite=Lax`（配合同源前端与 `<img>` 加载）；CLI/脚本用 Bearer。
- 默认绑定 `127.0.0.1`；对外暴露需显式改 `--host` 并经过网络层决策（decisions.md）。
- 备份：`picshelf export`（zip）；`--encrypt` 时用口令派生密钥 Fernet 加密。

## 已知边界与扩展路径

| 场景 | 现状 | 扩展路径 |
|---|---|---|
| >10 万张图 | SQLite 足够（索引齐全） | 迁移 Postgres（SQL 基本标准；`db.py` 是唯一入口） |
| 相似检索规模化 | pHash 全量比对（库内 ≤1 万舒适） | CLIP 嵌入 + 向量索引（R12 一并做） |
| 全库静态加密 | 仅备份加密 | LUKS/gocryptfs 挂载层 or 应用层 per-file envelope（决策 D1） |
| 实时多人协作 | 无推送（客户端轮询） | SSE/WebSocket 通知（P2） |
| 缩略图缓存失效 | 按 image id 覆盖写 | 内容寻址（path=sha256）自动免疫（P2） |
