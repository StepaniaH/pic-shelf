# Pic Shelf 部署指南（Docker）

面向自托管部署：默认**仅本机访问**，不对外暴露。远程访问建议走 Tailscale 等私有网络（见「网络与访问」）。

## 快速开始

```bash
git clone https://github.com/StepaniaH/pic-shelf.git
cd pic-shelf

# 1) 构建镜像并准备数据目录（写权限见下方 UID/GID 说明）
docker compose build
mkdir -p data

# 2) 首次初始化；此时尚未启动 Web 服务
docker compose run --rm picshelf picshelf init --admin admin

# 3) 启动服务（默认仅本机可访问）
docker compose up -d

# 4) 打开浏览器登录
```

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

## 权限（UID/GID）

容器以 UID 1000 运行；`./data` 需要该 UID 可写。宿主 UID 不是 1000 时二选一：

```bash
sudo chown -R 1000:1000 ./data     # 方案 A：把数据目录归属给 1000

# 方案 B：取消 docker-compose.yml 中 user: 的注释，改成你的 UID:GID
```

## 升级

```bash
git pull
docker compose up -d --build        # 重新构建并滚动替换；数据库迁移在启动时自动执行
```

所有数据都在 `./data`，重建容器不受影响。

## 备份与恢复

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
