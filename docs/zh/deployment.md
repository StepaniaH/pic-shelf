# Pic Shelf 部署指南（Docker）

[English](../deployment.md) | **简体中文**

面向自托管部署：默认**仅本机访问**，不对外暴露。远程访问建议走 Tailscale 等私有网络（见「网络与访问」）。

## 快速开始

```bash
git clone https://github.com/StepaniaH/pic-shelf.git
cd pic-shelf

# 1) 准备数据目录（写权限见下方 UID/GID 说明）
mkdir -p data

# 2) 首次初始化（会提示设置管理员密码，至少 8 位）；此时尚未启动 Web 服务
docker compose run --rm picshelf picshelf init --admin admin

# 3) 启动服务（默认仅本机可访问）
docker compose up -d

# 4) 打开浏览器登录
```

`docker-compose.yml` 拉取 `stepaniah/pic-shelf` 镜像；要从当前检出构建，把 `image:` 换成 `build: .`。

## 目录与卷

| 宿主 | 容器 | 用途 |
|---|---|---|
| `./data` | `/data` | 数据库、缩略图/预览图、密钥、播放列表（必须持久化） |
| 你的图片目录（可挂多个） | 任意路径（如 `/libraries/photos`） | 图片库；索引模式建议 `:ro` 只读挂载 |

在 `docker-compose.yml` 的 `volumes:` 下添加库目录，然后在容器里注册：

```bash
docker compose exec picshelf picshelf library add photos /libraries/photos
docker compose exec picshelf picshelf scan photos           # 增量扫描
```

托管导入（hardlink / copy / move）需要库目录**可写**：把 `:ro` 去掉，或单独挂一个可写目录专门给托管库用。

## 网络与访问

- 默认 `127.0.0.1:39431`——仅本机可访问（推荐保持）。
- **Tailscale / 局域网访问**：把端口映射左侧改成对应地址，例如 `100.x.y.z:39431:39431`（tailnet 地址）或 `192.168.x.y:39431:39431`。
- **反向代理**：Pic Shelf 只提供明文 HTTP，TLS 由外层终止（Caddy / Nginx 等），上游填 `宿主IP:39431`。
- 切勿把端口直接映射到公网。服务零遥测、无外联；认证模型面向私有网络内的可信使用。

TLS 终止部署需设置 `PICSHELF_PUBLIC_ORIGIN` 为浏览器访问的完整站点来源（不含路径），以匹配 Cookie 变更请求并启用 Secure Cookie。本机 HTTP 无需该设置。`picshelf serve` 默认忽略代理头；需要接收时，`PICSHELF_TRUSTED_PROXIES` 只允许明确的代理 IP 或网段，不接受 `*`。代理必须覆盖转发头并限制上游直连。配置、会话升级影响及导入来源权限见 [认证与共享权限（英文）](../security.md)。

## 权限（UID/GID）

在 `docker-compose.yml` 里把 `user:` 设成你自己的 UID 和 GID（`id -u`、`id -g`），`./data` 需要该用户可写；必要时执行一次 `chown`。

## 升级

```bash
git pull
docker compose up -d --build        # 重新构建并滚动替换；数据库迁移在启动时自动执行
```

所有数据都在 `./data`，重建容器不受影响。

## 备份与恢复

先用 `backup inspect` 核验归档，再停止 Web、worker、TUI 与其他数据库连接；恢复不能在仍占用数据目录的服务中执行。容器已停止时可用独立的 `docker compose run --rm picshelf picshelf import-backup ...`。完整恢复边界见[备份说明（英文）](../backups.md)，容器换挂载路径使用[根目录核验（英文）](../paths.md)，排障可导出[脱敏诊断（英文）](../diagnostics.md)。

- **应用级**：`docker compose exec picshelf picshelf export /data/exports/backup.zip [--encrypt --passphrase-stdin]`（数据库一致性快照，可选含缩略图；原图不在备份中，需另外备份图片库目录）；恢复用 `picshelf import-backup`。恢复前停止服务写入；`--force` 会先用 SQLite 备份接口保留现有数据，包含已提交的 WAL 内容。恢复数据库后运行 `scan <库名> --full` 重建派生图片。
- **卷级**：停止服务后快照 `./data` 目录（含全部派生物与密钥），并另外备份原图目录。运行中的 SQLite 不能只复制主数据库文件。

## 常见问题

- **页面报错 / 无法登录**：确认已执行第 2 步 `init`（创建了管理员与密钥）。
- **扫描大库较慢**：首扫逐张解码生成缩略图，耗时取决于图片尺寸、格式、磁盘和 CPU；增量扫描跳过内容未变化且派生图片齐全的文件。
- **端口被占用**：改 compose 左侧端口，如 `127.0.0.1:39441:39431`。
- **CLI / TUI 怎么用**：CLI 全部命令通过 `docker compose exec picshelf picshelf <命令>` 执行；TUI 需要真实终端，直接用宿主 venv 运行更合适。

## 不用 Compose（docker run 等价）

```bash
docker build -t pic-shelf:local .
mkdir -p data  # 按上方 UID/GID 说明准备写权限
docker run --rm -it -v "$PWD/data:/data" pic-shelf:local picshelf init --admin admin
docker run -d --name picshelf --restart unless-stopped \
  -p 127.0.0.1:39431:39431 \
  -v "$PWD/data:/data" \
  pic-shelf:local
```
