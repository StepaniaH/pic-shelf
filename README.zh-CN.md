<img src="picshelf/web/icons/icon-192.png" width="80" height="80" alt="Pic Shelf">

# Pic Shelf

[English](README.md) | **简体中文**

自托管图片库，提供 Web、TUI 和 CLI。浏览图片，用标签和专辑整理收藏，按保存的搜索条件播放幻灯片。零遥测，无需外部服务。

[![Docker Pulls](https://img.shields.io/docker/pulls/stepaniah/pic-shelf)](https://hub.docker.com/r/stepaniah/pic-shelf)
[![Docker Hub](https://img.shields.io/docker/v/stepaniah/pic-shelf?label=docker%20hub&sort=semver)](https://hub.docker.com/r/stepaniah/pic-shelf)
[![GitHub release](https://img.shields.io/github/v/tag/StepaniaH/pic-shelf?label=version)](https://github.com/StepaniaH/pic-shelf/tags)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

[使用说明](docs/usage.md) · [部署](docs/zh/deployment.md) · [架构](docs/zh/architecture.md) · [参与开发](CONTRIBUTING.md)

## 功能

- 索引已有目录，或通过复制、硬链接、移动导入图片。
- 评分、收藏，管理标签、艺术家、专辑和笔记。
- 批量编辑前预览，修改后可撤销；标记待整理和已整理。
- 按名称、评分、尺寸、日期搜索，保存为查询或智能专辑。
- 保存幻灯片设置，在 Web、TUI 或 mpv 中播放和续播。
- 关联文件版本、迁移图库目录、导出元数据、备份目录信息。
- 通过用户和组权限共享图库，详见[权限与安全](docs/security.md)。

## 快速开始

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/)。

```sh
git clone https://github.com/StepaniaH/pic-shelf.git
cd pic-shelf
uv sync --locked --extra dev --python 3.11
export PICSHELF_DATA="$PWD/data"
.venv/bin/picshelf init --admin admin
.venv/bin/picshelf library add photos /path/to/images --mode indexed
.venv/bin/picshelf scan photos
.venv/bin/picshelf serve
```

将 `/path/to/images` 替换为图片目录。初始化时设置管理员密码（至少 8 位），启动后打开 `serve` 输出的地址。终端界面使用 `.venv/bin/picshelf tui`。

原图留在注册的目录中，数据库和缩略图保存在数据目录。所有命令应使用同一个数据目录；安装包运行时需设置 `PICSHELF_DATA` 或 `--data`，详见[安装说明](docs/packaging.md)。

要将文件收纳到托管库，请看[导入流程](docs/usage.md#import-and-scanning)。Web 端支持拖入文件、选择文件夹，或从已批准的服务器路径导入；源目录与库在同一文件系统时用硬链接，否则复制。

## Docker

```yaml
services:
  picshelf:
    image: stepaniah/pic-shelf:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:39431:39431"
    volumes:
      - ./data:/data
      - /path/to/images:/libraries/photos:ro
    user: "1000:1000"
```

`user` 填你自己的 UID 和 GID（`id -u`、`id -g`），`./data` 需要该用户可写。
先建目录，初始化一次，然后启动：

```sh
mkdir -p data
docker compose run --rm picshelf picshelf init --admin admin
docker compose up -d
```

每个要索引的图库加一行 volumes。镜像同时发布在 `ghcr.io/stepaniah/pic-shelf`。
远程访问、升级和权限细节见[部署说明](docs/zh/deployment.md)。

## 维护收藏

原图需与[数据库备份](docs/backups.md)分别保存。[可移植元数据](docs/metadata-format.md)用于在不同安装之间转移标签等整理结果。[诊断工具](docs/diagnostics.md)用于排查文件缺失、扫描错误和存储占用。

动画和多页图片使用首帧或首页生成预览；格式支持取决于 Pillow，详见[支持的格式](docs/usage.md#scan-safety-and-derivative-versions)。暂不支持视频和漫画归档。
