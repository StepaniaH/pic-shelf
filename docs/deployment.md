# Pic Shelf 部署指南（Docker）

面向自托管部署：默认**仅本机访问**，不对外暴露。远程访问建议走 Tailscale 等私有网络（见「网络与访问」）。

## 快速开始

```bash
git clone git@github.com:StepaniaH/pic-shelf.git
cd pic-shelf

# 1) 构建并启动（默认绑定 127.0.0.1:39431）
docker compose up -d --build

# 2) 首次初始化（创建数据库、密钥与管理员）
docker compose exec picshelf picshelf init --admin admin

# 3) 打开 http://127.0.0.1:39431 登录
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

- **应用级**：`docker compose exec picshelf picshelf export backup.zip [--encrypt --passphrase-stdin]`（数据库快照，可选含缩略图）；恢复用 `picshelf import-backup`。
- **卷级**：直接快照 `./data` 目录（含全部派生物与密钥）。

## 常见问题

- **页面报错 / 无法登录**：确认已执行第 2 步 `init`（创建了管理员与密钥）。
- **扫描大库较慢**：首扫逐张解码生成缩略图（约 3 张/秒·单线程），大库建议后台执行；之后都是秒级增量重扫。
- **端口被占用**：改 compose 左侧端口，如 `127.0.0.1:39441:39431`。
- **CLI / TUI 怎么用**：CLI 全部命令通过 `docker compose exec picshelf picshelf <命令>` 执行；TUI 需要真实终端，直接用宿主 venv 运行更合适。

## 不用 Compose（docker run 等价）

```bash
docker build -t pic-shelf:local .
docker run -d --name picshelf --restart unless-stopped \
  -p 127.0.0.1:39431:39431 \
  -v "$PWD/data:/data" \
  pic-shelf:local
docker exec picshelf picshelf init --admin admin
```
