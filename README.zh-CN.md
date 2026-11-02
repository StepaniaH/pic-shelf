<img src="picshelf/web/icons/icon-192.png" width="80" height="80" alt="Pic Shelf">

# Pic Shelf

[English](README.md) | **简体中文**

自托管图片库，提供 Web、TUI 和 CLI。浏览图片，用标签和专辑整理收藏，按保存的搜索条件播放幻灯片。全端支持中英双语，零遥测，无需外部服务。

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

将 `/path/to/images` 替换为图片目录。初始化时设置管理员密码，启动后打开 `serve` 输出的地址。终端界面使用 `.venv/bin/picshelf tui`。

原图留在注册的目录中，数据库和缩略图保存在数据目录。所有命令应使用同一个数据目录；安装包运行时需设置 `PICSHELF_DATA` 或 `--data`，详见[安装说明](docs/packaging.md)。

要将文件收纳到托管库，请看[导入流程](docs/usage.md#import-and-scanning)。Web 支持选择图片、选择文件夹，以及直接拖入，电脑上的原文件会保留。已有文件放在服务器上时，可以浏览选择源文件夹，自动尝试硬链接、必要时复制。手动路径和复制/移动方式放在高级选项中。

添加图库、新建专辑和导入图片均在当前页面的弹窗内完成选择、预览和执行，完成后刷新原列表。

## Docker

```sh
docker compose build
mkdir -p data
docker compose run --rm picshelf picshelf init --admin admin
docker compose up -d
```

挂载图片目录，并让容器 UID 1000 能写入数据卷。服务默认绑定本机。挂载、远程访问与升级方法见[部署说明](docs/zh/deployment.md)。预构建镜像尚未发布。

## 维护收藏

原图需与[数据库备份](docs/backups.md)分别保存。[可移植元数据](docs/metadata-format.md)用于在不同安装之间转移标签等整理结果。[诊断工具](docs/diagnostics.md)用于排查文件缺失、扫描错误和存储占用。

动画和多页图片使用首帧或首页生成预览；格式支持取决于 Pillow，详见[支持的格式](docs/usage.md#scan-safety-and-derivative-versions)。暂不支持视频和漫画归档。
