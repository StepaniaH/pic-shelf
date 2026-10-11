"""Pic Shelf 命令行：init / serve / library / scan / ls / search / show / rate / tag /
favorite / comment / note / artist / play / dupes / export / import-backup /
users / groups / stats / tui
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import click

from . import __version__, auth, db as dbm, indexer, libsvc, ops, play, search
from .config import DEFAULT_HOST, DEFAULT_PORT, Config

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


# ---------------------------------------------------------------- 工具

def _conn(cfg: Config) -> sqlite3.Connection:
    if not cfg.db_path.exists():
        raise click.ClickException(
            f"数据目录尚未初始化（找不到 {cfg.db_path}）。先运行：picshelf init --admin <管理员名>"
        )
    return dbm.open_db(cfg.db_path)


def _acting_user(conn: sqlite3.Connection, name: str | None = None) -> sqlite3.Row:
    if name:
        user = auth.get_user_by_username(conn, name)
        if user is None or user["disabled"]:
            raise click.ClickException(f"用户不存在或已禁用：{name}")
        return user
    row = conn.execute(
        "SELECT * FROM users WHERE is_admin=1 AND disabled=0 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        raise click.ClickException("没有管理员用户。先运行：picshelf init --admin <管理员名>")
    return row


def _fmt_size(n: int | None) -> str:
    if not n:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n}"


def _print_image_row(item: dict) -> str:
    rating = "—" if item.get("my_rating", -1) < 0 else str(item["my_rating"])
    fav = "★" if item.get("favorite") else " "
    dims = f"{item.get('width') or '?'}x{item.get('height') or '?'}"
    return f"#{item['id']:>6}  {fav} 评分:{rating:>3}  {dims:>11}  {item['filename']}"


def _render_terminal(path: Path, width: int, height: int) -> str | None:
    chafa = shutil.which("chafa")
    if chafa:
        r = subprocess.run(
            [chafa, "-f", "symbols", "--size", f"{width}x{height}", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return r.stdout
    img2txt = shutil.which("img2txt")
    if img2txt:
        r = subprocess.run(
            [img2txt, "-W", str(width), "-H", str(height), "-f", "ansi", str(path)],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return r.stdout
    return None


def _read_password(password_stdin: bool, prompt: str, confirm: bool = False) -> str:
    if password_stdin:
        line = sys.stdin.readline().rstrip("\n")
        if not line:
            raise click.ClickException("从 stdin 读取密码失败")
        return line
    pw = click.prompt(prompt, hide_input=True, confirmation_prompt=confirm)
    return pw


# ---------------------------------------------------------------- 顶层

@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--data", "data_dir", default=None, envvar="PICSHELF_DATA",
              help="数据目录（默认：项目内 ./data）")
@click.version_option(__version__, prog_name="picshelf")
@click.pass_context
def main(ctx: click.Context, data_dir: str | None):
    """Pic Shelf — 自托管图片库（Web / TUI / CLI）。"""
    ctx.obj = Config.resolve(data_dir)


# ---------------------------------------------------------------- init / serve

@main.command()
@click.option("--admin", default=None, help="创建管理员用户名")
@click.option("--password-stdin", is_flag=True, help="从标准输入读取密码（自动化用）")
@click.pass_obj
def init(cfg: Config, admin: str | None, password_stdin: bool):
    """初始化数据目录：建库、生成密钥、创建默认组与管理员。"""
    cfg.ensure()
    conn = dbm.open_db(cfg.db_path)
    auth.ensure_default_groups(conn)
    auth.load_secret(cfg.secret_path)
    if admin:
        if auth.get_user_by_username(conn, admin) is not None:
            click.echo(f"用户已存在，跳过：{admin}")
        else:
            pw = _read_password(password_stdin, f"为管理员 {admin} 设置密码", confirm=True)
            auth.create_user(conn, admin, pw, is_admin=True)
            click.echo(f"已创建管理员：{admin}")
    click.echo(f"数据目录：{cfg.data_dir}")
    click.echo(f"数据库：  {cfg.db_path}")
    click.echo("初始化完成。下一步：picshelf library add <库名> <目录> && picshelf scan <库名>")


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option("--reload", is_flag=True, help="开发模式自动重载")
@click.pass_obj
def serve(cfg: Config, host: str, port: int, reload: bool):
    """启动 Web 服务（默认仅绑定 127.0.0.1）。"""
    if not cfg.db_path.exists():
        raise click.ClickException("尚未初始化。先运行：picshelf init --admin <管理员名>")
    import uvicorn

    from .api import create_app

    if reload:
        os.environ["PICSHELF_DATA"] = str(cfg.data_dir)
        uvicorn.run("picshelf.api:create_app_dev", host=host, port=port, reload=True)
        return
    app = create_app(cfg)
    click.echo(f"Pic Shelf Web → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------- library / scan

@main.group()
def library():
    """库管理。"""


@library.command("add")
@click.argument("name")
@click.argument("path", type=click.Path())
@click.option("--description", default=None)
@click.option("--mkdir", "make_dir", is_flag=True, help="目录不存在时自动创建（托管库推荐）")
@click.pass_obj
def library_add(cfg: Config, name: str, path: str, description: str | None, make_dir: bool):
    """注册一个图片库（只索引，不移动文件）。"""
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        if make_dir:
            root.mkdir(parents=True, exist_ok=True)
        else:
            raise click.ClickException(f"目录不存在：{root}（加 --mkdir 可自动创建）")
    conn = _conn(cfg)
    try:
        lib_id = libsvc.create_library(conn, name, str(root), description)
    except sqlite3.IntegrityError:
        raise click.ClickException(f"库名已存在：{name}")
    click.echo(f"已注册库 #{lib_id} {name} → {root}")
    click.echo(f"下一步：picshelf scan {name}")


@library.command("list")
@click.pass_obj
def library_list(cfg: Config):
    """列出所有库。"""
    conn = _conn(cfg)
    rows = libsvc.list_libraries(conn)
    if not rows:
        click.echo("尚无库。用 picshelf library add <库名> <目录> 添加。")
        return
    for r in rows:
        click.echo(
            f"#{r['id']:>3}  {r['name']:<16} 图片 {r['image_count']:>6} 张  专辑 {r['album_count']:>4}  "
            f"{r['root_path']}"
        )


# ---------------------------------------------------------------- scan

@main.command()
@click.argument("library_name")
@click.option("--full", is_flag=True, help="强制重算所有哈希与缩略图")
@click.pass_obj
def scan(cfg: Config, library_name: str, full: bool):
    """扫描/更新一个库（增量）。"""
    conn = _conn(cfg)
    lib = libsvc.find_library(conn, library_name)
    if lib is None:
        raise click.ClickException(f"找不到库：{library_name}")
    last = {"n": 0}

    def progress(stats: dict):
        n = stats.get("processed", 0)
        if n and n - last["n"] >= 100:
            last["n"] = n
            click.echo(f"  ... 已处理 {n} 个文件", err=True)

    click.echo(f"扫描库 #{lib['id']} {lib['name']} → {lib['root_path']}")
    stats = indexer.scan_library(conn, cfg, lib["id"], full=full, progress=progress)
    click.echo(
        f"完成：新增 {stats['added']}，更新 {stats['updated']}，移动 {stats['moved']}，"
        f"未变 {stats['unchanged']}，标记缺失 {stats['missing']}，错误 {len(stats['errors'])}"
    )
    for err in stats["errors"][:10]:
        click.echo(f"  ! {err}", err=True)


# ---------------------------------------------------------------- 浏览

def _base_where(conn, user, opts) -> tuple[list[str], dict]:
    """把 CLI 过滤选项转成 search 的 extra_where/extra_params。"""
    extra_where: list[str] = []
    extra_params: dict = {}
    if opts.get("album"):
        extra_where.append("EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id AND ai.album_id=:c_alb)")
        extra_params["c_alb"] = int(opts["album"])
    if opts.get("artist"):
        extra_where.append("EXISTS(SELECT 1 FROM image_artists ia WHERE ia.image_id=i.id AND ia.artist_id=:c_art)")
        extra_params["c_art"] = int(opts["artist"])
    if opts.get("tag"):
        extra_where.append(
            "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
            "WHERE it.image_id=i.id AND lower(tg.name)=lower(:c_tag))"
        )
        extra_params["c_tag"] = opts["tag"]
    if opts.get("favorites"):
        extra_where.append("EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)")
    if opts.get("min_rating") is not None:
        extra_where.append(
            "COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id AND r.user_id=:uid),0) >= :c_min"
        )
        extra_params["c_min"] = int(opts["min_rating"])
    if opts.get("library"):
        extra_where.append("i.library_id = :c_lib")
        extra_params["c_lib"] = int(opts["library"])
    return extra_where, extra_params


@main.command("ls")
@click.option("--query", "-q", default="", help="检索表达式")
@click.option("--library", default=None, help="库名或 ID")
@click.option("--album", default=None, help="专辑 ID")
@click.option("--artist", default=None, help="艺术家 ID")
@click.option("--tag", default=None, help="标签全名")
@click.option("--min-rating", default=None, type=int, help="我的评分下限")
@click.option("--favorites", is_flag=True, help="仅收藏")
@click.option("--sort", default="newest", type=click.Choice(list(search.SORTS)))
@click.option("-n", "--limit", default=50, show_default=True)
@click.option("--user", "user_name", default=None, help="以哪个用户视角（默认首个管理员）")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_obj
def ls_cmd(cfg, query, library, album, artist, tag, min_rating, favorites, sort, limit, user_name, as_json):
    """列表 / 检索图片。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    allowed = auth.visible_library_ids(conn, user)
    if library:
        lib = libsvc.find_library(conn, library)
        if lib is None:
            raise click.ClickException(f"找不到库：{library}")
        library = lib["id"]
    ew, ep = _base_where(conn, user, {
        "album": album, "artist": artist, "tag": tag,
        "favorites": favorites, "min_rating": min_rating, "library": library,
    })
    res = search.search_images(
        conn, user["id"], allowed, query=query, sort=sort, limit=limit,
        extra_where=ew or None, extra_params=ep or None,
    )
    if as_json:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        return
    click.echo(f"共 {res['total']} 张（显示前 {len(res['items'])}）")
    for item in res["items"]:
        click.echo(_print_image_row(item))


@main.command()
@click.argument("image_id", type=int)
@click.option("--json", "as_json", is_flag=True)
@click.option("--path", "print_path", is_flag=True, help="只输出文件绝对路径（脚本用）")
@click.pass_obj
def show(cfg: Config, image_id: int, as_json: bool, print_path: bool):
    """查看单张图片的完整信息。"""
    conn = _conn(cfg)
    user = _acting_user(conn)
    detail = search.get_image(conn, image_id, user["id"], auth.visible_library_ids(conn, user))
    if detail is None:
        raise click.ClickException(f"图片不存在：#{image_id}")
    lib_row = conn.execute("SELECT root_path FROM libraries WHERE id=?", (detail["library_id"],)).fetchone()
    abspath = Path(lib_row["root_path"]) / detail["rel_path"] if lib_row else Path(detail["rel_path"])
    if print_path:
        click.echo(str(abspath))
        return
    detail["comments"] = ops.list_comments(conn, image_id)
    detail["notes"] = ops.list_notes(conn, image_id)
    if as_json:
        click.echo(json.dumps(detail, ensure_ascii=False, indent=2))
        return
    rating = "未评" if detail["my_rating"] < 0 else str(detail["my_rating"])
    click.echo(f"#{detail['id']}  {detail['filename']}")
    click.echo(f"路径:   {abspath}")
    click.echo(f"尺寸:   {detail['width']}x{detail['height']}  大小: {_fmt_size(detail['size_bytes'])}  格式: {detail['ext']}")
    click.echo(f"评分:   我的 {rating}   平均 {detail['avg_rating'] or '—'}（{detail['rating_count']} 人）  收藏: {'是' if detail['favorite'] else '否'}")
    click.echo(f"标签:   {'、'.join(t['name'] for t in detail['tags']) or '（无）'}")
    click.echo(f"艺术家: {'、'.join(a['name'] for a in detail['artists']) or '（无）'}")
    click.echo(f"专辑:   {'、'.join(a['name'] for a in detail['albums']) or '（无）'}")
    click.echo(f"库:     {detail['library']['name']}")
    click.echo(f"评论 {len(detail['comments'])} 条 / 笔记 {len(detail['notes'])} 条")
    for c in detail["comments"]:
        click.echo(f"  [{c['username']}] {c['body']}")
    for n in detail["notes"]:
        click.echo(f"  (笔记) [{n['username']}] {n['body']}")


# ---------------------------------------------------------------- 交互

@main.command()
@click.argument("image_id", type=int)
@click.argument("value", type=int)
@click.option("--user", "user_name", default=None)
@click.pass_obj
def rate(cfg: Config, image_id: int, value: int, user_name: str | None):
    """给图片评分（0–10）。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    try:
        summary = ops.set_rating(conn, image_id, user["id"], value)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"#{image_id} 我的评分 → {summary['my_rating']}（平均 {summary['avg_rating'] or '—'}）")


@main.command()
@click.argument("image_id", type=int)
@click.option("--off", is_flag=True, help="取消收藏")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def favorite(cfg: Config, image_id: int, off: bool, user_name: str | None):
    """收藏 / 取消收藏。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    state = ops.set_favorite(conn, image_id, user["id"], False if off else True)
    click.echo(f"#{image_id} 收藏 → {'是' if state else '否'}")


@main.group()
def tag():
    """图片标签操作。"""


@tag.command("add")
@click.argument("image_id", type=int)
@click.argument("names", nargs=-1, required=True)
@click.option("--user", "user_name", default=None)
@click.pass_obj
def tag_add(cfg, image_id, names, user_name):
    """添加标签：picshelf tag add 12 色彩:红色 场景:夜晚"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    added = ops.add_tags(conn, image_id, list(names), user["id"])
    click.echo(f"#{image_id} 已添加 {len(added)} 个标签：" + "、".join(a["name"] for a in added))


@tag.command("rm")
@click.argument("image_id", type=int)
@click.argument("names", nargs=-1, required=True)
@click.pass_obj
def tag_rm(cfg, image_id, names):
    """移除标签。"""
    conn = _conn(cfg)
    n = ops.remove_tags(conn, image_id, list(names))
    click.echo(f"#{image_id} 已移除 {n} 个标签")


@tag.command("list")
@click.option("--query", "-q", default=None)
@click.option("-n", "--limit", default=50)
@click.pass_obj
def tag_list(cfg, query, limit):
    """列出标签。"""
    conn = _conn(cfg)
    for t in ops.list_tags(conn, q=query, limit=limit):
        click.echo(f"{t['count']:>6}  {t['name']}")


@main.command()
@click.argument("image_id", type=int)
@click.argument("body")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def comment(cfg, image_id, body, user_name):
    """给图片添加评论。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    c = ops.add_comment(conn, image_id, user["id"], body)
    click.echo(f"已添加评论 #{c['id']}")


@main.command()
@click.argument("image_id", type=int)
@click.argument("body")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def note(cfg, image_id, body, user_name):
    """给图片添加笔记。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    n = ops.add_note(conn, image_id, user["id"], body)
    click.echo(f"已添加笔记 #{n['id']}")


# ---------------------------------------------------------------- 艺术家

@main.group()
def artist():
    """艺术家管理。"""


@artist.command("add")
@click.argument("name")
@click.option("--aliases", default=None)
@click.option("--link", "links", multiple=True)
@click.pass_obj
def artist_add(cfg, name, aliases, links):
    conn = _conn(cfg)
    a = ops.create_artist(conn, name, aliases=aliases, links=list(links))
    click.echo(f"艺术家 #{a['id']} {a['name']}")


@artist.command("list")
@click.option("--query", "-q", default=None)
@click.pass_obj
def artist_list(cfg, query):
    conn = _conn(cfg)
    rows = ops.list_artists(conn, q=query)
    if not rows:
        click.echo("（无）")
    for a in rows:
        click.echo(f"#{a['id']:>4}  {a['image_count']:>6} 张  {a['name']}")


@artist.command("link")
@click.argument("image_id", type=int)
@click.argument("artist_name")
@click.pass_obj
def artist_link(cfg, image_id, artist_name):
    conn = _conn(cfg)
    row = conn.execute("SELECT id FROM artists WHERE lower(name)=lower(?)", (artist_name,)).fetchone()
    if row is None:
        raise click.ClickException(f"艺术家不存在：{artist_name}（先 artist add）")
    ops.link_artist(conn, image_id, int(row["id"]), True)
    click.echo(f"#{image_id} ← {artist_name}")


@artist.command("unlink")
@click.argument("image_id", type=int)
@click.argument("artist_name")
@click.pass_obj
def artist_unlink(cfg, image_id, artist_name):
    conn = _conn(cfg)
    row = conn.execute("SELECT id FROM artists WHERE lower(name)=lower(?)", (artist_name,)).fetchone()
    if row is None:
        raise click.ClickException(f"艺术家不存在：{artist_name}")
    ops.link_artist(conn, image_id, int(row["id"]), False)
    click.echo(f"#{image_id} 已取消关联 {artist_name}")


# ---------------------------------------------------------------- 播放

@main.command()
@click.option("--library", default=None, help="库名或 ID")
@click.option("--album", default=None, help="专辑 ID")
@click.option("--artist", default=None, help="艺术家 ID")
@click.option("--tag", default=None, help="标签全名")
@click.option("--query", "-q", default="", help="检索表达式")
@click.option("--favorites", is_flag=True)
@click.option("--min-rating", default=None, type=int)
@click.option("--order", default="sequential", type=click.Choice(["sequential", "shuffle", "random"]))
@click.option("--interval", default=8, show_default=True, type=int, help="每张停留秒数（0=手动翻页）")
@click.option("--count", default=0, show_default=True, type=int, help="播放数量上限（0=全部）")
@click.option("--loop/--no-loop", default=True, show_default=True, help="循环（轮播）")
@click.option("--player", default="mpv", type=click.Choice(["mpv", "terminal", "list"]), show_default=True)
@click.option("--user", "user_name", default=None)
@click.pass_obj
def play_cmd(cfg, library, album, artist, tag, query, favorites, min_rating,
             order, interval, count, loop, player, user_name):
    """播放：服务端生成队列（跨专辑/按艺术家/标签/评分），交给 mpv / 终端 / 仅列表。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    allowed = auth.visible_library_ids(conn, user)
    if library:
        lib = libsvc.find_library(conn, library)
        if lib is None:
            raise click.ClickException(f"找不到库：{library}")
        library = lib["id"]
    spec = {
        "source": {
            k: v for k, v in {
                "library_id": int(library) if library else None,
                "album_id": int(album) if album else None,
                "artist_id": int(artist) if artist else None,
                "tag": tag, "favorites": favorites,
                "rating_min": int(min_rating) if min_rating is not None else None,
            }.items() if v not in (None, False)
        },
        "query": query, "order": order,
        "limit": int(count or 0), "interval": int(interval), "loop": bool(loop),
    }
    session = play.create_session(conn, user["id"], allowed, spec)
    ids = play.queue_items(conn, session["session_id"], limit=10000)
    if not ids:
        raise click.ClickException("队列为空：没有符合条件的图片")
    paths: list[Path] = []
    for iid in ids:
        row = conn.execute(
            "SELECT i.rel_path, l.root_path FROM images i JOIN libraries l ON l.id=i.library_id WHERE i.id=?",
            (iid,),
        ).fetchone()
        if row:
            paths.append(Path(row["root_path"]) / row["rel_path"])
    click.echo(f"队列 {len(paths)} 张（顺序：{order}{'，循环' if loop else ''}）")

    if player == "list":
        for p in paths:
            click.echo(str(p))
        return

    if player == "terminal":
        cols, rows = shutil.get_terminal_size((100, 30))
        idx = 0
        try:
            while True:
                p = paths[idx]
                click.clear()
                click.echo(f"[{idx + 1}/{len(paths)}] {p.name}")
                art = _render_terminal(p, cols, max(10, rows - 6))
                if art:
                    click.echo(art)
                click.echo("回车下一张 / b 上一张 / q 退出" + (f"（{interval}s 自动）" if interval else ""))
                if interval > 0:
                    time.sleep(interval)
                    idx = (idx + 1) % len(paths) if loop else idx + 1
                    if idx >= len(paths) and not loop:
                        break
                    continue
                key = input().strip().lower()
                if key == "q":
                    break
                elif key == "b":
                    idx = max(0, idx - 1)
                else:
                    idx += 1
                    if idx >= len(paths):
                        if loop:
                            idx = 0
                        else:
                            break
        except (KeyboardInterrupt, EOFError):
            click.echo("\n已退出播放")
        return

    # mpv 模式
    mpv = shutil.which("mpv")
    if not mpv:
        raise click.ClickException("找不到 mpv（可改用 --player terminal 或 --player list）")
    playlist_path = cfg.playlists_dir / f"playlist-{int(time.time())}.m3u"
    playlist_path.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    cmd = [
        mpv,
        f"--playlist={playlist_path}",
        "--force-window=yes",
        "--no-resume-playback",
        "--keep-open=no",
        "--title=Pic Shelf 播放",
        f"--image-display-duration={'inf' if interval == 0 else interval}",
    ]
    if loop:
        cmd.append("--loop-playlist=inf")
    click.echo("交给 mpv 播放（→ 下一张 / ← 上一张 / q 退出）")
    subprocess.call(cmd)


# ---------------------------------------------------------------- 查重

@main.command()
@click.option("--mode", default="exact", type=click.Choice(["exact", "similar"]), show_default=True)
@click.option("--threshold", default=8, show_default=True, help="similar 模式的 pHash 距离阈值")
@click.pass_obj
def dupes(cfg: Config, mode: str, threshold: int):
    """重复检测（exact=sha256 相同；similar=pHash 相似）。"""
    conn = _conn(cfg)
    if mode == "similar":
        groups = libsvc.similar_dupes(conn, None, threshold=threshold)
        for g in groups:
            click.echo(f"相似组（{g['count']} 张）：")
            for e in g["images"]:
                click.echo(f"  #{e['id']:>6}  距离{e['distance']:>3}  {e['filename']}")
    else:
        groups = libsvc.exact_dupes(conn, None)
        if not groups:
            click.echo("没有完全重复的文件。")
        for g in groups:
            names = conn.execute(
                f"SELECT filename FROM images WHERE id IN ({','.join('?' * len(g['image_ids']))})",
                g["image_ids"],
            ).fetchall()
            click.echo(f"{g['count']} 份相同（可省 {_fmt_size(g['size_bytes'] - g['size_bytes'] // g['count'])}）：")
            for n in names:
                click.echo(f"  {n['filename']}")


# ---------------------------------------------------------------- 导出 / 恢复

BACKUP_MAGIC = b"PICSHELF1"  # 备份格式魔数；切片偏移一律用 len()，勿写死数字

@main.command("export")
@click.argument("out_path", type=click.Path())
@click.option("--encrypt", is_flag=True, help="用口令加密备份")
@click.option("--passphrase-stdin", is_flag=True)
@click.option("--with-thumbs", is_flag=True, help="包含缩略图/预览图")
@click.pass_obj
def export_cmd(cfg: Config, out_path: str, encrypt: bool, passphrase_stdin: bool, with_thumbs: bool):
    """导出备份（数据库快照，可选加密）。"""
    conn = _conn(cfg)
    out = Path(out_path).expanduser()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "picshelf.db"
        dst = sqlite3.connect(str(snap))
        conn.backup(dst)
        dst.close()
        meta = {
            "app": "picshelf", "version": __version__,
            "schema": dbm.schema_version(conn),
            "exported_at": dbm.utcnow(),
        }
        zip_path = Path(tmp) / "backup.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, "picshelf.db")
            zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
            if with_thumbs:
                for sub in ("thumbs", "previews"):
                    base = cfg.media_dir / sub
                    if base.is_dir():
                        for f in base.rglob("*.webp"):
                            zf.write(f, f"media/{sub}/{f.relative_to(base).as_posix()}")
        data = zip_path.read_bytes()
    if encrypt:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64 as b64
        passphrase = _read_password(passphrase_stdin, "设置备份口令", confirm=not passphrase_stdin)
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
        key = b64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        token = Fernet(key).encrypt(data)
        out = out if out.suffix == ".fernet" else out.with_suffix(out.suffix + ".fernet")
        out.write_bytes(BACKUP_MAGIC + salt + token)
        click.echo(f"已导出加密备份：{out}（{_fmt_size(out.stat().st_size)}）")
    else:
        out.write_bytes(data)
        click.echo(f"已导出备份：{out}（{_fmt_size(out.stat().st_size)}）")


@main.command("import-backup")
@click.argument("backup_path", type=click.Path(exists=True))
@click.option("--passphrase-stdin", is_flag=True)
@click.option("--force", is_flag=True, help="覆盖现有数据库（先自动备份现有库）")
@click.pass_obj
def import_backup(cfg: Config, backup_path: str, passphrase_stdin: bool, force: bool):
    """从备份恢复数据库。"""
    raw = Path(backup_path).expanduser().read_bytes()
    if raw.startswith(BACKUP_MAGIC):
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        import base64 as b64
        salt, token = raw[len(BACKUP_MAGIC):len(BACKUP_MAGIC) + 16], raw[len(BACKUP_MAGIC) + 16:]
        passphrase = _read_password(passphrase_stdin, "输入备份口令")
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=200_000)
        key = b64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        try:
            raw = Fernet(key).decrypt(token)
        except Exception:
            raise click.ClickException("口令错误或备份文件损坏")
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        if "picshelf.db" not in zf.namelist():
            raise click.ClickException("备份格式不正确（缺少 picshelf.db）")
        db_bytes = zf.read("picshelf.db")
    if not db_bytes.startswith(b"SQLite format 3"):
        raise click.ClickException("数据库文件无效")
    cfg.ensure()
    if cfg.db_path.exists() and not force:
        raise click.ClickException("数据目录已有数据库。如确认覆盖请加 --force（现有库会先自动备份）")
    if cfg.db_path.exists():
        bak = cfg.db_path.with_name(f"picshelf.db.bak-{int(time.time())}")
        shutil.copy2(cfg.db_path, bak)
        click.echo(f"现有数据库已备份到：{bak}")
    cfg.db_path.write_bytes(db_bytes)
    click.echo(f"已恢复数据库到：{cfg.db_path}")


# ---------------------------------------------------------------- 托管库导入（v0.2）

@main.command("import")
@click.argument("source_dir", type=click.Path(exists=True, file_okay=False))
@click.argument("library_name")
@click.option("--mode", type=click.Choice(["hardlink", "copy", "move"]), default="hardlink",
              show_default=True,
              help="hardlink=原文件不动且零额外空间（跨盘自动降级 copy）；copy=复制留档；move=移入整理")
@click.option("--rename/--keep-names", default=True, show_default=True,
              help="文件名规范化（去掉文件系统危险字符）")
@click.option("--apply", "do_apply", is_flag=True, help="真正执行（默认只预演）")
@click.option("--yes", is_flag=True, help="跳过执行确认")
@click.pass_obj
def import_cmd(cfg: Config, source_dir: str, library_name: str, mode: str,
               rename: bool, do_apply: bool, yes: bool):
    """把外部目录导入托管的库：预演优先，--apply 执行，完成后自动扫描入库。"""
    from . import importer

    conn = _conn(cfg)
    lib = libsvc.find_library(conn, library_name)
    if lib is None:
        raise click.ClickException(f"找不到库：{library_name}（先运行 picshelf library add）")
    try:
        plan = importer.plan_import(conn, lib, source_dir, mode=mode, rename=rename)
    except ValueError as exc:
        raise click.ClickException(str(exc))

    s = plan["stats"]
    new_n = s["hardlink"] + s["copy"] + s["move"]
    click.echo(f"预演：源 {plan['source']} → 库 #{lib['id']} {lib['name']}（模式：{mode}）")
    click.echo(
        f"共 {len(plan['items'])} 个文件：新导入 {new_n}，跳过 {s['skip']}（重复/已存在），错误 {s['error']}"
    )
    labels = {"hardlink": "硬链", "copy": "复制", "move": "移动", "skip": "跳过", "error": "错误"}
    for item in plan["items"][:20]:
        dst = item["dst"] or "—"
        reason = f"  ← {item['reason']}" if item["reason"] else ""
        click.echo(f"  [{labels.get(item['action'], item['action'])}] {item['src']} → {dst}{reason}")
    if len(plan["items"]) > 20:
        click.echo(f"  …（共 {len(plan['items'])} 项，只显示前 20）")

    if not do_apply:
        click.echo("这是预演，未改动任何文件。确认无误后加 --apply 执行。")
        return
    if new_n == 0:
        click.echo("没有需要导入的文件。")
        return
    if not yes:
        if mode == "move":
            if not click.confirm(f"确认把 {new_n} 个文件【移动】进库？原位置将不再保留（建议先备份）"):
                raise click.ClickException("已取消")
        else:
            if not click.confirm(f"确认导入 {new_n} 个文件？"):
                raise click.ClickException("已取消")

    done = importer.execute_import(plan, apply=True)
    click.echo(
        f"执行完成：硬链 {done['hardlink']}，复制 {done['copy']}，移动 {done['move']}，"
        f"跳过 {done['skip']}，错误 {done['error']}"
    )
    for err in done["errors"][:10]:
        click.echo(f"  ! {err}", err=True)
    if done["hardlink"] or done["copy"] or done["move"]:
        stats = indexer.scan_library(conn, cfg, lib["id"])
        click.echo(
            f"已扫描入库：新增 {stats['added']}，更新 {stats['updated']}，未变 {stats['unchanged']}"
        )


# ---------------------------------------------------------------- 用户 / 组 / 统计

@main.group()
def users():
    """用户管理。"""


@users.command("add")
@click.argument("username")
@click.option("--admin", is_flag=True)
@click.option("--password-stdin", is_flag=True)
@click.pass_obj
def users_add(cfg, username, admin, password_stdin):
    conn = _conn(cfg)
    if auth.get_user_by_username(conn, username):
        raise click.ClickException(f"用户已存在：{username}")
    pw = _read_password(password_stdin, f"为 {username} 设置密码", confirm=True)
    uid = auth.create_user(conn, username, pw, is_admin=admin)
    click.echo(f"已创建用户 #{uid} {username}{'（管理员）' if admin else ''}")


@users.command("list")
@click.pass_obj
def users_list(cfg):
    conn = _conn(cfg)
    rows = conn.execute(
        "SELECT u.id, u.username, u.is_admin, u.disabled, "
        "(SELECT GROUP_CONCAT(g.name) FROM groups g JOIN user_groups ug ON ug.group_id=g.id "
        " WHERE ug.user_id=u.id) AS groups FROM users u ORDER BY u.id"
    ).fetchall()
    for r in rows:
        flags = []
        if r["is_admin"]:
            flags.append("管理员")
        if r["disabled"]:
            flags.append("已禁用")
        click.echo(f"#{r['id']:>3}  {r['username']:<16} {','.join(flags):<12} 组：{r['groups'] or '—'}")


@users.command("passwd")
@click.argument("username")
@click.option("--password-stdin", is_flag=True)
@click.pass_obj
def users_passwd(cfg, username, password_stdin):
    conn = _conn(cfg)
    user = auth.get_user_by_username(conn, username)
    if user is None:
        raise click.ClickException(f"用户不存在：{username}")
    pw = _read_password(password_stdin, f"为 {username} 设置新密码", confirm=True)
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(pw), user["id"]))
    conn.commit()
    click.echo("密码已更新")


@main.group()
def groups():
    """用户组管理。"""


@groups.command("list")
@click.pass_obj
def groups_list(cfg):
    conn = _conn(cfg)
    for r in conn.execute("SELECT * FROM groups ORDER BY is_system DESC, name").fetchall():
        try:
            caps = "、".join(json.loads(r["capabilities"] or "[]"))
        except Exception:
            caps = "?"
        click.echo(f"#{r['id']:>3}  {r['name']:<16} {caps}")


@groups.command("add")
@click.argument("name")
@click.option("--caps", default="view", show_default=True,
              help="逗号分隔的能力：view,rate,comment,note,tag_edit,manage_library,admin")
@click.option("--description", default=None)
@click.pass_obj
def groups_add(cfg, name, caps, description):
    conn = _conn(cfg)
    cap_list = [c.strip() for c in caps.split(",") if c.strip() in auth.ALL_CAPS]
    try:
        cur = conn.execute(
            "INSERT INTO groups(name, description, capabilities, is_system, created_at) VALUES(?,?,?,0,?)",
            (name, description, json.dumps(cap_list), dbm.utcnow()),
        )
    except sqlite3.IntegrityError:
        raise click.ClickException(f"组已存在：{name}")
    conn.commit()
    click.echo(f"已创建组 #{cur.lastrowid} {name}（{ '、'.join(cap_list) }）")


@groups.command("grant")
@click.argument("group_name")
@click.argument("library_name")
@click.argument("access", type=click.Choice(["view", "edit", "manage"]))
@click.pass_obj
def groups_grant(cfg, group_name, library_name, access):
    conn = _conn(cfg)
    g = conn.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
    lib = libsvc.find_library(conn, library_name)
    if g is None:
        raise click.ClickException(f"组不存在：{group_name}")
    if lib is None:
        raise click.ClickException(f"库不存在：{library_name}")
    conn.execute(
        "INSERT INTO library_grants(group_id, library_id, access) VALUES(?,?,?) "
        "ON CONFLICT(group_id, library_id) DO UPDATE SET access=excluded.access",
        (g["id"], lib["id"], access),
    )
    conn.commit()
    click.echo(f"组 {group_name} 对库 {lib['name']} 的访问级别 → {access}")


@groups.command("revoke")
@click.argument("group_name")
@click.argument("library_name")
@click.pass_obj
def groups_revoke(cfg, group_name, library_name):
    conn = _conn(cfg)
    g = conn.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
    lib = libsvc.find_library(conn, library_name)
    if g is None or lib is None:
        raise click.ClickException("组或库不存在")
    conn.execute("DELETE FROM library_grants WHERE group_id=? AND library_id=?", (g["id"], lib["id"]))
    conn.commit()
    click.echo(f"已移除组 {group_name} 对库 {lib['name']} 的授权")


@main.command()
@click.pass_obj
def stats(cfg: Config):
    """全局统计。"""
    conn = _conn(cfg)
    s = libsvc.global_stats(conn, None)
    click.echo(
        f"图片 {s['images']} 张（{_fmt_size(s['size_bytes'])}）  库 {s['libraries']}  "
        f"专辑 {s['albums']}  艺术家 {s['artists']}  标签 {s['tags']}  "
        f"用户 {s['users']}  评分 {s['ratings']}  收藏 {s['favorites']}"
    )


# ---------------------------------------------------------------- TUI

@main.command()
@click.pass_obj
def tui(cfg: Config):
    """启动终端界面（TUI）。"""
    if not cfg.db_path.exists():
        raise click.ClickException("尚未初始化。先运行：picshelf init --admin <管理员名>")
    from .tui import run_tui

    run_tui(cfg)


if __name__ == "__main__":
    main()
