"""Pic Shelf 命令行：init / serve / library / scan / ls / search / show / rate / tag /
favorite / comment / note / artist / play / dupes / export / import-backup /
users / groups / stats / tui
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import click

from . import __version__, auth, catalog, db as dbm, indexer, libsvc, ops, play, search
from .access import AccessContext
from .config import DEFAULT_HOST, DEFAULT_PORT, Config
from .coordination import library_write_lock, operation_lock
from .errors import ServiceError
from . import batches, entity_edits, metadata as metasvc, names as catalog_names, source_access, saved_queries, play_rules
from .i18n import set_lang, t

# 语言预扫描（必须早于各装饰器求值）：--lang / 环境变量决定界面语言
_argv = sys.argv[1:]
for _i, _a in enumerate(_argv):
    if _a == "--lang" and _i + 1 < len(_argv):
        os.environ["PICSHELF_LANG"] = _argv[_i + 1]
    elif _a.startswith("--lang="):
        os.environ["PICSHELF_LANG"] = _a.split("=", 1)[1]
set_lang(None)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


# ---------------------------------------------------------------- 工具

def _conn(cfg: Config) -> sqlite3.Connection:
    if not cfg.db_path.exists():
        raise ServiceError("not_initialized", t("数据目录尚未初始化"),
                           next_step=t("请先运行 picshelf init --admin <管理员名>"))
    conn = dbm.open_db(cfg.db_path)
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        ctx.call_on_close(conn.close)
    return conn


def _acting_user(conn: sqlite3.Connection, name: str | None = None) -> sqlite3.Row:
    if name:
        user = auth.get_user_by_username(conn, name)
        if user is None or user["disabled"]:
            raise ServiceError("user_not_found", t("用户不存在或已禁用：{name}", name=name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
        return user
    row = conn.execute(
        "SELECT * FROM users WHERE is_admin=1 AND disabled=0 ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        raise click.ClickException(t("没有管理员用户。先运行：picshelf init --admin <管理员名>"))
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
    return t("#{v0:>6}  {fav} 评分:{rating:>3}  {dims:>11}  {v1}", v0=item['id'], fav=fav, rating=rating, dims=dims, v1=item['filename'])


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
            raise click.ClickException(t("从 stdin 读取密码失败"))
        return line
    if not sys.stdin.isatty():
        raise click.ClickException(t("非交互输入需要 --password-stdin（备份口令使用 --passphrase-stdin）"))
    pw = click.prompt(prompt, hide_input=True, confirmation_prompt=confirm, err=True)
    return pw


# ---------------------------------------------------------------- 顶层

class _I18nGroup(click.Group):
    """Group that re-localizes (sub)command help from their Chinese docstrings."""

    def get_command(self, ctx: click.Context, cmd_name: str):
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            _localize_cmd(cmd)
        return cmd

    def format_help(self, ctx: click.Context, formatter) -> None:
        _localize_cmd(self)
        super().format_help(ctx, formatter)

    def main(self, args=None, prog_name=None, complete_var=None, standalone_mode=True, **extra):
        argv = list(sys.argv[1:] if args is None else args)
        machine = _json_requested(self, argv)
        try:
            result = super().main(argv, prog_name=prog_name, complete_var=complete_var,
                                  standalone_mode=False, **extra)
            if not standalone_mode:
                return result
            raise SystemExit(result if isinstance(result, int) else 0)
        except ServiceError as exc:
            failure, exit_code = exc, 1
        except click.ClickException as exc:
            exit_code = exc.exit_code
            if not machine:
                if not standalone_mode:
                    raise
                exc.show()
                raise SystemExit(exit_code)
            failure = ServiceError("invalid_arguments" if exit_code == 2 else "operation_failed",
                                   exc.format_message(), next_step=t("请查看该命令的 --help 并检查参数后重试"))
        except click.Abort as exc:
            exit_code = 130 if isinstance(exc.__cause__, KeyboardInterrupt) else 1
            failure = ServiceError("interrupted" if exit_code == 130 else "input_required",
                                   t("操作已中断"), next_step=t("请查看当前状态后再决定是否重试"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            exit_code = 1
            failure = ServiceError("invalid_arguments", t("操作未完成：{error}。请检查参数后重试。", error=exc),
                                   next_step=t("请查看该命令的 --help 并检查参数后重试"))
        except (OSError, sqlite3.Error, OverflowError):
            exit_code = 1
            failure = ServiceError("operation_failed", t("文件或数据库操作未完成"),
                                   next_step=t("请检查数据目录、读写权限和可用空间后重试"))
        except Exception:
            exit_code = 1
            failure = ServiceError("internal_error", t("服务未能完成请求"),
                                   next_step=t("请重新读取当前状态；确认是否已保存后再试，持续失败时联系管理员"))
        if machine:
            click.echo(json.dumps(failure.as_dict(), ensure_ascii=False))
        click.echo(f"[{failure.code}] {failure.detail} {failure.next_step}", err=True)
        if not standalone_mode:
            raise click.ClickException(failure.detail)
        raise SystemExit(exit_code)


def _json_requested(command, args):
    """Inspect Click's parsed options without invoking callbacks or opening data."""
    json_groups = {"batch", "queries", "play-rules", "play-sessions", "job", "metadata", "import-sources", "manifest", "backup", "paths", "works"}
    json_commands = {"doctor", "check", "organize", "rebuild", "import-status", "import-execute", "import-cancel"}
    path = []
    machine = False
    while command is not None:
        ctx = click.Context(command, resilient_parsing=True)
        options, remaining, _ = command.make_parser(ctx).parse_args(list(args))
        machine = machine or bool(options.get("as_json"))
        if path == ["scan"] and options.get("background"):
            machine = True
        if path and (path[0] in json_groups or path[0] in json_commands or path == ["library", "configure"]):
            machine = True
        if not isinstance(command, click.Group) or not remaining:
            break
        name, *args = remaining
        path.append(name)
        command = command.commands.get(name)
    return machine


def _localize_cmd(cmd: click.Command) -> None:
    cb = getattr(cmd, "callback", None)
    doc = inspect.cleandoc(cb.__doc__ or "").strip() if cb else ""
    if doc:
        cmd.help = t(doc)
    # 选项 help 同样按当前语言重渲染（原始中文串暂存在 _orig_help）
    for p in cmd.params:
        orig = getattr(p, "_orig_help", None)
        cur = getattr(p, "help", None)
        if orig is None and cur is not None:
            setattr(p, "_orig_help", cur)
            orig = cur
        if orig:
            setattr(p, "help", t(orig))
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _localize_cmd(sub)


def _lang_cb(ctx: click.Context, param: click.Parameter, value: str | None):
    # eager：解析到 --lang 立即切换，保证 --help 渲染时语言已生效
    if value:
        set_lang(value)
    return value


@click.group(cls=_I18nGroup, context_settings=CONTEXT_SETTINGS)
@click.option("--data", "data_dir", default=None, envvar="PICSHELF_DATA",
              help="数据目录（源码运行默认项目内 ./data；安装包需明确指定）")
@click.option("--lang", type=click.Choice(["zh", "en"]), default=None,
              is_eager=True, callback=_lang_cb,
              help="界面语言：zh / en（默认跟随系统 locale）")
@click.version_option(__version__, prog_name="picshelf")
@click.pass_context
def main(ctx: click.Context, data_dir: str | None, lang: str | None):
    """Pic Shelf — 自托管图片库（Web / TUI / CLI）。"""
    if lang is not None:
        set_lang(lang)
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
    click.get_current_context().call_on_close(conn.close)
    auth.ensure_default_groups(conn)
    auth.load_secret(cfg.secret_path)
    if admin:
        if auth.get_user_by_username(conn, admin) is not None:
            click.echo(t("用户已存在，跳过：{admin}", admin=admin))
        else:
            pw = _read_password(password_stdin, t("为管理员 {admin} 设置密码", admin=admin), confirm=True)
            auth.create_user(conn, admin, pw, is_admin=True)
            click.echo(t("已创建管理员：{admin}", admin=admin))
    click.echo(t("数据目录：{v0}", v0=cfg.data_dir))
    click.echo(t("数据库：  {v0}", v0=cfg.db_path))
    click.echo(t("初始化完成。下一步：picshelf library add <库名> <目录> && picshelf scan <库名>"))


@main.command()
@click.option("--host", default=DEFAULT_HOST, show_default=True)
@click.option("--port", default=DEFAULT_PORT, show_default=True, type=int)
@click.option("--reload", is_flag=True, help="开发模式自动重载")
@click.pass_obj
def serve(cfg: Config, host: str, port: int, reload: bool):
    """启动 Web 服务（默认仅绑定 127.0.0.1）。"""
    if not cfg.db_path.exists():
        raise click.ClickException(t("尚未初始化。先运行：picshelf init --admin <管理员名>"))
    import uvicorn

    from .api import create_app

    if reload:
        os.environ["PICSHELF_DATA"] = str(cfg.data_dir)
        uvicorn.run("picshelf.api:create_app_dev", host=host, port=port, reload=True, factory=True,
                    access_log=False,
                    proxy_headers=bool(cfg.trusted_proxies), forwarded_allow_ips=list(cfg.trusted_proxies))
        return
    app = create_app(cfg)
    click.echo(f"Pic Shelf Web → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info",
                access_log=False,
                proxy_headers=bool(cfg.trusted_proxies), forwarded_allow_ips=list(cfg.trusted_proxies))


# ---------------------------------------------------------------- library / scan

@main.group()
def library():
    """库管理。"""


@main.group("import-sources")
def import_sources():
    """配置非管理员可用的导入源根。"""


@import_sources.command("list")
@click.pass_obj
def source_list(cfg):
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(source_access.list_roots(conn, access), ensure_ascii=False))


@import_sources.command("allow")
@click.argument("root", type=click.Path(exists=True, file_okay=False))
@click.option("--allow-move", is_flag=True, help="同时允许移动源文件；默认只允许复制或硬链接")
@click.pass_obj
def source_allow(cfg, root, allow_move):
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(source_access.register(conn, access, root, allow_move=allow_move), ensure_ascii=False))


@import_sources.command("revoke")
@click.argument("root_id", type=click.IntRange(1, 2**63-1))
@click.pass_obj
def source_revoke(cfg, root_id):
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(source_access.revoke(conn, access, root_id)))


@main.group("batch")
def batch_cmd():
    """预演、执行和撤销元数据批次。"""


def _batch_access(conn,username):
    return AccessContext(_acting_user(conn,username)["id"],local_admin=username is None)


@batch_cmd.command("preview")
@click.argument("image_ids",nargs=-1,type=int)
@click.option("--action",required=True,help="操作 JSON，例如 {\"kind\":\"rating\",\"value\":0}")
@click.option("--query-json",default=None,help="固定全部匹配结果的查询 JSON，不能同时提供图片 ID")
@click.option("--request-key",default=None)
@click.option("--user",default=None)
@click.pass_obj
def batch_preview(cfg,image_ids,action,query_json,request_key,user):
    conn = _conn(cfg)
    try:
        operation = json.loads(action)
        query = json.loads(query_json) if query_json is not None else None
    except ValueError as exc:
        raise click.UsageError(t("批量操作或查询 JSON 无效")) from exc
    result = batches.preview(conn,_batch_access(conn,user),operation,image_ids=list(image_ids) if image_ids else None,
                             query=query,request_key=request_key)
    click.echo(json.dumps(result,ensure_ascii=False))


@batch_cmd.command("show")
@click.argument("batch_id")
@click.option("--user",default=None)
@click.option("--offset",default=0,type=click.IntRange(min=0, max=2**63-1))
@click.option("--limit",default=200,type=click.IntRange(1,1000))
@click.pass_obj
def batch_show(cfg,batch_id,user,offset,limit):
    conn = _conn(cfg)
    click.echo(json.dumps(batches.get(conn,_batch_access(conn,user),batch_id,offset=offset,limit=limit),ensure_ascii=False))


@batch_cmd.command("list")
@click.option("--user",default=None)
@click.option("--offset",default=0,type=click.IntRange(min=0, max=2**63-1))
@click.pass_obj
def batch_list(cfg,user,offset):
    conn = _conn(cfg)
    click.echo(json.dumps(batches.list_batches(conn,_batch_access(conn,user),offset=offset),ensure_ascii=False))


@batch_cmd.command("execute")
@click.argument("batch_id")
@click.option("--yes",is_flag=True)
@click.option("--background",is_flag=True)
@click.option("--retry",is_flag=True)
@click.option("--user",default=None)
@click.pass_obj
def batch_execute(cfg,batch_id,yes,background,retry,user):
    if not yes:raise ServiceError("confirmation_required", t("请先查看批次，再使用 --yes 确认执行"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn = _conn(cfg)
    access = _batch_access(conn,user)
    result = batches.queue(conn,access,batch_id,retry=retry) if background else batches.execute(conn,cfg,access,batch_id,retry=retry)
    click.echo(json.dumps(result,ensure_ascii=False))
    if result['state'] not in ('succeeded', 'queued'):
        raise click.exceptions.Exit(1)


@batch_cmd.command("cancel")
@click.argument("batch_id")
@click.option("--user",default=None)
@click.pass_obj
def batch_cancel(cfg,batch_id,user):
    conn = _conn(cfg)
    click.echo(json.dumps(batches.cancel(conn,_batch_access(conn,user),batch_id),ensure_ascii=False))


@batch_cmd.command("undo")
@click.argument("batch_id")
@click.option("--yes",is_flag=True)
@click.option("--user",default=None)
@click.pass_obj
def batch_undo(cfg,batch_id,yes,user):
    if not yes:raise ServiceError("confirmation_required", t("请先查看批次，再使用 --yes 确认撤销"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn = _conn(cfg)
    result = batches.undo(conn,cfg,_batch_access(conn,user),batch_id)
    click.echo(json.dumps(result,ensure_ascii=False))
    if result['state'] != 'undone':
        raise click.exceptions.Exit(1)


@library.command("add")
@click.argument("name")
@click.argument("path", type=click.Path())
@click.option("--description", default=None)
@click.option("--mkdir", "make_dir", is_flag=True, help="目录不存在时自动创建（托管库推荐）")
@click.option("--mode", type=click.Choice(["indexed", "managed"]), default=None,
              help="indexed=只索引；managed=托管收纳；省略保留兼容行为")
@click.option("--default-import-mode", type=click.Choice(["hardlink", "copy", "move"]), default="hardlink", show_default=True)
@click.pass_obj
def library_add(cfg: Config, name: str, path: str, description: str | None, make_dir: bool,
                mode: str | None, default_import_mode: str):
    """注册一个图片库（只索引，不移动文件）。"""
    root = Path(path).expanduser().absolute()
    if not root.is_dir():
        if make_dir:
            root.mkdir(parents=True, exist_ok=True)
        else:
            raise click.ClickException(t("目录不存在：{root}（加 --mkdir 可自动创建）", root=root))
    conn = _conn(cfg)
    try:
        lib_id = libsvc.create_library(conn, name, str(root), description, mode=mode,
                                      default_import_mode=default_import_mode)
    except sqlite3.IntegrityError:
        raise ServiceError("state_conflict", t("库名已存在：{name}", name=name), status=409, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    click.echo(t("已注册库 #{lib_id} {name} → {root}", lib_id=lib_id, name=name, root=root))
    click.echo(t("下一步：picshelf scan {name}", name=name))


@library.command("configure")
@click.argument("library_name")
@click.option("--mode", type=click.Choice(["indexed", "managed"]))
@click.option("--default-import-mode", type=click.Choice(["hardlink", "copy", "move"]))
@click.pass_obj
def library_configure(cfg: Config, library_name: str, mode: str | None, default_import_mode: str | None):
    """设置库用途与默认导入方式，不移动或重写原文件。"""
    conn = _conn(cfg)
    library = libsvc.find_library(conn, library_name)
    if library is None:
        raise ServiceError("library_not_found", t("库不存在"), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    changes = {key: value for key, value in {"mode": mode, "default_import_mode": default_import_mode}.items() if value is not None}
    result = libsvc.update_library(conn, cfg, AccessContext(_acting_user(conn)["id"], local_admin=True), library["id"], **changes)
    click.echo(json.dumps(result, ensure_ascii=False))


@library.command("list")
@click.pass_obj
def library_list(cfg: Config):
    """列出所有库。"""
    conn = _conn(cfg)
    rows = libsvc.list_libraries(conn)
    if not rows:
        click.echo(t("尚无库。用 picshelf library add <库名> <目录> 添加。"))
        return
    for r in rows:
        click.echo(
            t("#{v0:>3}  {v1:<16} 图片 {v2:>6} 张  专辑 {v3:>4}  {v4}", v0=r['id'], v1=r['name'], v2=r['image_count'], v3=r['album_count'], v4=r['root_path'])
        )


# ---------------------------------------------------------------- scan

@main.command()
@click.argument("library_name")
@click.option("--full", is_flag=True, help="强制重算所有哈希与缩略图")
@click.option("--background", is_flag=True, help="只排队，交由 Web 服务或 picshelf worker 执行")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_obj
def scan(cfg: Config, library_name: str, full: bool, background: bool, as_json: bool):
    """扫描/更新一个库（增量）。"""
    conn = _conn(cfg)
    lib = libsvc.find_library(conn, library_name)
    if lib is None:
        raise ServiceError("library_not_found", t("找不到库：{library_name}", library_name=library_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    from . import jobs
    administrator = conn.execute("SELECT id FROM users WHERE is_admin=1 AND disabled=0 ORDER BY id LIMIT 1").fetchone()
    if administrator is None and not background:
        # Before accounts are initialized, the original local scan command is
        # still useful. Persistent user-owned tasks require an active account.
        stats = indexer.scan_library(conn, cfg, lib["id"], full=full)
        if as_json:
            click.echo(json.dumps(stats, ensure_ascii=False))
        else:
            click.echo(t("完成：新增 {v0}，更新 {v1}，移动 {v2}，未变 {v3}，标记缺失 {v4}，错误 {v5}",
                         v0=stats['added'], v1=stats['updated'], v2=stats['moved'], v3=stats['unchanged'],
                         v4=stats['missing'], v5=len(stats['errors'])))
        if stats["errors"]:
            raise click.exceptions.Exit(1)
        return
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    if not background:
        with library_write_lock(cfg, Path(lib["root_path"])):
            pass  # Preserve the synchronous command's immediate busy error.
    queued = jobs.submit(conn, access, "scan", lib["id"], full=full)
    if background:
        click.echo(json.dumps(queued, ensure_ascii=False))
        return
    if not as_json:
        click.echo(t("扫描库 #{v0} {v1} → {v2}", v0=lib['id'], v1=lib['name'], v2=lib['root_path']), err=True)
        click.echo(t("任务 ID：{job_id}", job_id=queued["id"]), err=True)
    status = jobs.wait(cfg, access, queued["id"])
    stats = status["result"]
    if as_json or stats is None:
        click.echo(json.dumps(status, ensure_ascii=False))
        if status["state"] != "succeeded":
            raise click.exceptions.Exit(1)
        return
    click.echo(
        t("完成：新增 {v0}，更新 {v1}，移动 {v2}，未变 {v3}，标记缺失 {v4}，错误 {v5}", v0=stats['added'], v1=stats['updated'], v2=stats['moved'], v3=stats['unchanged'], v4=stats['missing'], v5=len(stats['errors']))
    )
    for err in stats["errors"][:10]:
        click.echo(f"  ! {err}", err=True)
    if status["state"] != "succeeded":
        raise click.exceptions.Exit(1)


# ---------------------------------------------------------------- 浏览

@main.command("rebuild")
@click.argument("library_name")
@click.option("--outdated-only", is_flag=True, help="仅重建算法版本过时的派生图和指纹")
@click.option("--background", is_flag=True, help="只排队，交由 Web 服务或 picshelf worker 执行")
@click.pass_obj
def rebuild(cfg: Config, library_name: str, outdated_only: bool, background: bool):
    """重建派生图片与指纹，保留原图及人工元数据；输出 JSON。"""
    from . import jobs
    conn = _conn(cfg)
    library = libsvc.find_library(conn, library_name)
    if library is None:
        raise ServiceError("library_not_found", t("找不到库：{library_name}", library_name=library_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    queued = jobs.submit(conn, access, "rebuild", library["id"], outdated_only=outdated_only)
    result = queued if background else jobs.wait(cfg, access, queued["id"])
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not background and result["state"] != "succeeded":
        raise click.exceptions.Exit(1)

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
@click.option("--album", default=None, type=click.IntRange(min=1, max=2**63-1), help="专辑 ID")
@click.option("--artist", default=None, type=click.IntRange(min=1, max=2**63-1), help="艺术家 ID")
@click.option("--tag", default=None, help="标签全名")
@click.option("--min-rating", default=None, type=click.IntRange(0, 10), help="我的评分下限")
@click.option("--favorites", is_flag=True, help="仅收藏")
@click.option("--sort", default="newest", type=click.Choice(list(search.SORTS)))
@click.option("-n", "--limit", default=50, type=click.IntRange(0, 10000), show_default=True)
@click.option("--offset", default=0, type=click.IntRange(min=0, max=2**63-1), show_default=True, help="跳过的结果数量")
@click.option("--user", "user_name", default=None, help="以哪个用户视角（默认首个管理员）")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_obj
def ls_cmd(cfg, query, library, album, artist, tag, min_rating, favorites, sort, limit, offset, user_name, as_json):
    """列表 / 检索图片。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    if library:
        lib = libsvc.find_library(conn, library)
        if lib is None:
            raise ServiceError("library_not_found", t("找不到库：{library}", library=library), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
        library = lib["id"]
    res = catalog.query_images(conn, AccessContext(user["id"]), catalog.ImageQuery(
        query=query, sort=sort, limit=limit, offset=offset, library_id=library,
        album_id=album, artist_id=artist, tag=tag, favorites=favorites, rating_min=min_rating))
    if as_json:
        click.echo(json.dumps(res, ensure_ascii=False, indent=2))
        if res['errors']: raise click.exceptions.Exit(2)
        return
    for error in res["errors"]:
        click.echo(error, err=True)
    if offset and res["items"]:
        click.echo(t("共 {total} 张（显示 {start}–{end}）", total=res["total"],
                     start=offset + 1, end=offset + len(res["items"])))
    else:
        click.echo(t("共 {v0} 张（显示前 {v1}）", v0=res['total'], v1=len(res['items'])))
    for item in res["items"]:
        click.echo(_print_image_row(item))
    if res['errors']: raise click.exceptions.Exit(2)


main.add_command(ls_cmd, "search")


@main.group('queries')
def queries_group():
    """保存查询与智能专辑（动态结果；JSON 输出）。"""


@queries_group.command('save')
@click.argument('name')
@click.option('--query', '-q', default='')
@click.option('--sort', default='newest', type=click.Choice(list(search.SORTS)))
@click.option('--kind', default='query', type=click.Choice(['query','smart']))
@click.option('--display', default='{}', help='显示偏好 JSON')
@click.option('--user', default=None)
@click.pass_obj
def query_save(cfg,name,query,sort,kind,display,user):
    conn=_conn(cfg)
    try: preferences=json.loads(display)
    except ValueError: raise click.UsageError(t("JSON 格式无效，请检查输入"))
    value=saved_queries.create(conn,AccessContext(_acting_user(conn,user)['id']),
                              name=name,query=query,sort=sort,kind=kind,display=preferences)
    click.echo(json.dumps(value,ensure_ascii=False,indent=2))


@queries_group.command('list')
@click.option('--user',default=None)
@click.option('--offset',default=0,type=click.IntRange(min=0, max=2**63-1))
@click.pass_obj
def query_list(cfg,user,offset):
    conn=_conn(cfg)
    click.echo(json.dumps(saved_queries.list_queries(conn,AccessContext(_acting_user(conn,user)['id']),offset=offset),ensure_ascii=False,indent=2))


@queries_group.command('show')
@click.argument('query_id',type=click.IntRange(min=1, max=2**63-1))
@click.option('--user',default=None)
@click.pass_obj
def query_show(cfg,query_id,user):
    conn=_conn(cfg)
    click.echo(json.dumps(saved_queries.get(conn,AccessContext(_acting_user(conn,user)['id']),query_id),ensure_ascii=False,indent=2))


@queries_group.command('run')
@click.argument('query_id',type=click.IntRange(min=1, max=2**63-1))
@click.option('--user',default=None)
@click.option('--limit',default=60,type=click.IntRange(0,10000))
@click.option('--offset',default=0,type=click.IntRange(min=0, max=2**63-1))
@click.pass_obj
def query_run(cfg,query_id,user,limit,offset):
    conn=_conn(cfg)
    click.echo(json.dumps(saved_queries.run(conn,AccessContext(_acting_user(conn,user)['id']),query_id,limit=limit,offset=offset),ensure_ascii=False,indent=2))


@queries_group.command('edit')
@click.argument('query_id',type=click.IntRange(min=1, max=2**63-1))
@click.option('--version',required=True,type=click.IntRange(min=1, max=2**63-1))
@click.option('--changes',required=True,help='要修改的规则字段 JSON')
@click.option('--user',default=None)
@click.pass_obj
def query_edit(cfg,query_id,version,changes,user):
    conn=_conn(cfg)
    try: fields=json.loads(changes)
    except ValueError: raise click.UsageError(t("JSON 格式无效，请检查输入"))
    if not isinstance(fields,dict) or 'version' in fields:
        raise click.UsageError(t("更新字段或版本无效，请重新打开查询编辑"))
    click.echo(json.dumps(saved_queries.update(conn,AccessContext(_acting_user(conn,user)['id']),query_id,
                                              version=version,**fields),ensure_ascii=False,indent=2))


@queries_group.command('export')
@click.argument('query_id',type=click.IntRange(min=1, max=2**63-1))
@click.option('--user',default=None)
@click.pass_obj
def query_export(cfg,query_id,user):
    conn=_conn(cfg)
    click.echo(json.dumps(saved_queries.export(conn,AccessContext(_acting_user(conn,user)['id']),query_id),ensure_ascii=False,indent=2))


@main.command()
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.option("--json", "as_json", is_flag=True)
@click.option("--path", "print_path", is_flag=True, help="只输出文件绝对路径（脚本用）")
@click.pass_obj
def show(cfg: Config, image_id: int, as_json: bool, print_path: bool):
    """查看单张图片的完整信息。"""
    conn = _conn(cfg)
    user = _acting_user(conn)
    detail = search.get_image(conn, image_id, user["id"], auth.visible_library_ids(conn, user))
    if detail is None:
        raise ServiceError("image_not_found", t("图片不存在：#{image_id}", image_id=image_id), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
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
    rating = t("未评") if detail["my_rating"] < 0 else str(detail["my_rating"])
    click.echo(f"#{detail['id']}  {detail['filename']}")
    click.echo(t("路径:   {abspath}", abspath=abspath))
    click.echo(t("尺寸:   {v0}x{v1}  大小: {v2}  格式: {v3}", v0=detail['width'], v1=detail['height'], v2=_fmt_size(detail['size_bytes']), v3=detail['ext']))
    click.echo(t("评分:   我的 {rating}   平均 {v0}（{v1} 人）  收藏: {v2}", rating=rating, v0=detail['avg_rating'] if detail['avg_rating'] is not None else '—', v1=detail['rating_count'], v2=t('是') if detail['favorite'] else t('否')))
    click.echo(t("标签:   {v0}", v0='、'.join(t['name'] for t in detail['tags']) or t('（无）')))
    click.echo(t("艺术家: {v0}", v0='、'.join(a['name'] for a in detail['artists']) or t('（无）')))
    click.echo(t("专辑:   {v0}", v0='、'.join(a['name'] for a in detail['albums']) or t('（无）')))
    click.echo(t("库:     {v0}", v0=detail['library']['name']))
    click.echo(t("评论 {v0} 条 / 笔记 {v1} 条", v0=len(detail['comments']), v1=len(detail['notes'])))
    for c in detail["comments"]:
        click.echo(f"  [{c['username']}] {c['body']}")
    for n in detail["notes"]:
        click.echo(t("  (笔记) [{v0}] {v1}", v0=n['username'], v1=n['body']))


# ---------------------------------------------------------------- 交互

@main.command()
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("value", type=int)
@click.option("--user", "user_name", default=None)
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_obj
def rate(cfg: Config, image_id: int, value: int, user_name: str | None, as_json: bool):
    """给图片评分（0–10）。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    summary = catalog.rate_image(conn, AccessContext(user["id"], local_admin=True), image_id, value)
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False))
        return
    click.echo(t("#{image_id} 我的评分 → {v0}（平均 {v1}）", image_id=image_id, v0=summary['my_rating'], v1=summary['avg_rating'] if summary['avg_rating'] is not None else '—'))


@main.command()
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.option("--off", is_flag=True, help="取消收藏")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def favorite(cfg: Config, image_id: int, off: bool, user_name: str | None):
    """收藏 / 取消收藏。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    state = ops.set_favorite(conn, image_id, user["id"], False if off else True)
    click.echo(t("#{image_id} 收藏 → {v0}", image_id=image_id, v0=t('是') if state else t('否')))


@main.group()
def tag():
    """图片标签操作。"""


@tag.command("add")
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("names", nargs=-1, required=True)
@click.option("--user", "user_name", default=None)
@click.pass_obj
def tag_add(cfg, image_id, names, user_name):
    """添加标签：picshelf tag add 12 色彩:红色 场景:夜晚"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    added = ops.add_tags(conn, image_id, list(names), user["id"])
    click.echo(t("#{image_id} 已添加 {v0} 个标签：", image_id=image_id, v0=len(added)) + "、".join(a["name"] for a in added))


@tag.command("rm")
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("names", nargs=-1, required=True)
@click.pass_obj
def tag_rm(cfg, image_id, names):
    """移除标签。"""
    conn = _conn(cfg)
    n = ops.remove_tags(conn, image_id, list(names))
    click.echo(t("#{image_id} 已移除 {n} 个标签", image_id=image_id, n=n))


@tag.command("list")
@click.option("--query", "-q", default=None)
@click.option("-n", "--limit", default=50)
@click.pass_obj
def tag_list(cfg, query, limit):
    """列出标签。"""
    conn = _conn(cfg)
    for tg in ops.list_tags(conn, q=query, limit=limit):
        click.echo(f"{tg['count']:>6}  {tg['name']}")


@main.command()
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("body")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def comment(cfg, image_id, body, user_name):
    """给图片添加评论。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    c = ops.add_comment(conn, image_id, user["id"], body)
    click.echo(t("已添加评论 #{v0}", v0=c['id']))


@main.command()
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("body")
@click.option("--user", "user_name", default=None)
@click.pass_obj
def note(cfg, image_id, body, user_name):
    """给图片添加笔记。"""
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    n = ops.add_note(conn, image_id, user["id"], body)
    click.echo(t("已添加笔记 #{v0}", v0=n['id']))


# ---------------------------------------------------------------- 艺术家

@main.command("organize")
@click.argument("image_ids", type=click.IntRange(min=1, max=2**63-1), nargs=-1, required=True)
@click.option("--state", type=click.Choice(["inbox", "done"]), required=True)
@click.option("--user", "user_name", default=None)
@click.pass_obj
def organize(cfg, image_ids, state, user_name):
    """标记明确选中的图片为待整理或已整理，输出 JSON。"""
    conn = _conn(cfg)
    result = metasvc.mark(conn, AccessContext(_acting_user(conn, user_name)["id"]), list(image_ids), state)
    click.echo(json.dumps(result, ensure_ascii=False))


@main.group("metadata")
def metadata_commands():
    """查看来源与整理状态，预演共享名称和关联合并。"""


@metadata_commands.command("show")
@click.argument("image_id", type=click.IntRange(min=1, max=2**63-1))
@click.option("--user", "user_name", default=None)
@click.pass_obj
def metadata_show(cfg, image_id, user_name):
    """导出单图元数据 JSON（不包含原图内容）。"""
    conn = _conn(cfg)
    result = metasvc.get(conn, AccessContext(_acting_user(conn, user_name)["id"]), image_id)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@metadata_commands.command("source")
@click.argument("image_id", type=click.IntRange(min=1, max=2**63-1))
@click.option("--original-filename", default=None)
@click.option("--collected-at", default=None)
@click.option("--created-time", default=None)
@click.option("--url", default=None)
@click.option("--note", default=None)
@click.pass_obj
def metadata_source(cfg, image_id, **fields):
    """添加人工来源记录；不访问来源地址。"""
    conn = _conn(cfg)
    result = metasvc.add_source(conn, AccessContext(_acting_user(conn)["id"]), image_id, **fields)
    click.echo(json.dumps(result, ensure_ascii=False))


@metadata_commands.command("alias-preview")
@click.argument("kind", type=click.Choice(["tag", "artist"]))
@click.argument("entity_id", type=click.IntRange(min=1, max=2**63-1))
@click.argument("alias")
@click.pass_obj
def alias_preview(cfg, kind, entity_id, alias):
    """预演添加别名，显示匹配和受影响的图片。"""
    conn = _conn(cfg)
    result = entity_edits.preview_alias(conn, AccessContext(_acting_user(conn)["id"]), kind, entity_id, alias)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@metadata_commands.command("merge-preview")
@click.argument("kind", type=click.Choice(["tag", "artist"]))
@click.argument("source_id", type=click.IntRange(min=1, max=2**63-1))
@click.argument("target_id", type=click.IntRange(min=1, max=2**63-1))
@click.argument("image_ids", type=click.IntRange(min=1, max=2**63-1), nargs=-1, required=True)
@click.pass_obj
def merge_preview(cfg, kind, source_id, target_id, image_ids):
    """预演转移所选图片的标签或艺术家关联，保留来源实体。"""
    conn = _conn(cfg)
    result = entity_edits.preview_merge(conn, AccessContext(_acting_user(conn)["id"]), kind, source_id, target_id, list(image_ids))
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@metadata_commands.command("execute")
@click.argument("plan_id")
@click.option("--yes", is_flag=True)
@click.pass_obj
def metadata_execute(cfg, plan_id, yes):
    """确认执行已保存的元数据预演。"""
    if not yes:
        raise ServiceError("confirmation_required", t("结构化执行需要 --yes；请先检查预演结果"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn = _conn(cfg)
    result = entity_edits.execute(conn, AccessContext(_acting_user(conn)["id"]), plan_id)
    click.echo(json.dumps(result, ensure_ascii=False))

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
    click.echo(t("艺术家 #{v0} {v1}", v0=a['id'], v1=a['name']))


@artist.command("list")
@click.option("--query", "-q", default=None)
@click.pass_obj
def artist_list(cfg, query):
    conn = _conn(cfg)
    rows = ops.list_artists(conn, q=query)
    if not rows:
        click.echo(t("（无）"))
    for a in rows:
        click.echo(t("#{v0:>4}  {v1:>6} 张  {v2}", v0=a['id'], v1=a['image_count'], v2=a['name']))


@artist.command("link")
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("artist_name")
@click.pass_obj
def artist_link(cfg, image_id, artist_name):
    conn = _conn(cfg)
    artist_id = catalog_names.resolve(conn, "artist", artist_name)
    if artist_id is None:
        raise ServiceError("artist_not_found", t("艺术家不存在：{artist_name}（先 artist add）", artist_name=artist_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    ops.link_artist(conn, image_id, artist_id, True)
    click.echo(f"#{image_id} ← {artist_name}")


@artist.command("unlink")
@click.argument("image_id", type=click.IntRange(1, 2**63-1))
@click.argument("artist_name")
@click.pass_obj
def artist_unlink(cfg, image_id, artist_name):
    conn = _conn(cfg)
    artist_id = catalog_names.resolve(conn, "artist", artist_name)
    if artist_id is None:
        raise ServiceError("artist_not_found", t("艺术家不存在：{artist_name}", artist_name=artist_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    ops.link_artist(conn, image_id, artist_id, False)
    click.echo(t("#{image_id} 已取消关联 {artist_name}", image_id=image_id, artist_name=artist_name))


# ---------------------------------------------------------------- 播放

@main.group('play-rules')
def play_rules_group():
    """保存和启动个人播放规则（JSON 输出）。"""


def _play_context(cfg,user):
    conn=_conn(cfg)
    return conn,AccessContext(_acting_user(conn,user)['id'])


def _play_spec(text):
    try:return json.loads(text)
    except ValueError:raise click.UsageError(t('JSON 格式无效，请检查输入'))


@play_rules_group.command('save')
@click.argument('name')
@click.option('--spec',required=True,help='播放配置 JSON')
@click.option('--user',default=None)
@click.pass_obj
def play_rule_save(cfg,name,spec,user):
    conn,access=_play_context(cfg,user)
    click.echo(json.dumps(play_rules.create(conn,access,name=name,spec=_play_spec(spec)),ensure_ascii=False,indent=2))


@play_rules_group.command('edit')
@click.argument('rule_id',type=click.IntRange(min=1, max=2**63-1))
@click.option('--version',required=True,type=click.IntRange(min=1, max=2**63-1))
@click.option('--name',required=True)
@click.option('--spec',required=True,help='播放配置 JSON')
@click.option('--user',default=None)
@click.pass_obj
def play_rule_edit(cfg,rule_id,version,name,spec,user):
    conn,access=_play_context(cfg,user)
    click.echo(json.dumps(play_rules.update(conn,access,rule_id,version=version,name=name,spec=_play_spec(spec)),ensure_ascii=False,indent=2))


@play_rules_group.command('list')
@click.option('--user',default=None)
@click.option('--offset',default=0,type=click.IntRange(min=0, max=2**63-1))
@click.pass_obj
def play_rule_list(cfg,user,offset):
    conn,access=_play_context(cfg,user)
    click.echo(json.dumps(play_rules.list_rules(conn,access,offset=offset),ensure_ascii=False,indent=2))


def _rule_command(name,operation):
    @click.command(name)
    @click.argument('rule_id',type=click.IntRange(min=1, max=2**63-1))
    @click.option('--user',default=None)
    @click.pass_obj
    def command(cfg,rule_id,user):
        conn,access=_play_context(cfg,user)
        click.echo(json.dumps(operation(conn,access,rule_id),ensure_ascii=False,indent=2))
    play_rules_group.add_command(command)


for _command,_operation in (('show',play_rules.get),('start',play_rules.start),('export',play_rules.export)):
    _rule_command(_command,_operation)


@main.group('play-sessions')
def play_sessions_group():
    """恢复和控制播放会话（JSON 输出）。"""


@play_sessions_group.command('show')
@click.argument('token')
@click.option('--user',default=None)
@click.pass_obj
def play_session_show(cfg,token,user):
    conn,access=_play_context(cfg,user)
    click.echo(json.dumps(play.session_state(conn,access,token),ensure_ascii=False,indent=2))


@play_sessions_group.command('control')
@click.argument('token')
@click.argument('action',type=click.Choice(['next','prev','goto','pause','resume','reshuffle']))
@click.option('--position',type=click.IntRange(0,9999),default=None,help='从 0 起的播放位置')
@click.option('--request-key',default=None)
@click.option('--expected-version',type=click.IntRange(min=0, max=2**63-1),default=None)
@click.option('--user',default=None)
@click.pass_obj
def play_session_control(cfg,token,action,position,request_key,expected_version,user):
    conn,access=_play_context(cfg,user)
    paused=True if action=='pause' else False if action=='resume' else None
    result=play.navigate(conn,access,token,'pause' if action=='resume' else action,position=position,
                         paused=paused,request_key=request_key,expected_version=expected_version)
    click.echo(json.dumps(result,ensure_ascii=False,indent=2))

@main.command()
@click.option("--library", default=None, help="库名或 ID")
@click.option("--album", default=None, type=click.IntRange(min=1, max=2**63-1), help="专辑 ID")
@click.option("--artist", default=None, type=click.IntRange(min=1, max=2**63-1), help="艺术家 ID")
@click.option("--tag", default=None, help="标签全名")
@click.option("--query", "-q", default="", help="检索表达式")
@click.option("--favorites", is_flag=True)
@click.option("--min-rating", default=None, type=click.IntRange(0, 10))
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
    if player == "terminal" and (not sys.stdin.isatty() or not sys.stdout.isatty()):
        raise click.ClickException(t("终端播放需要交互终端；管道请使用 --player list"))
    conn = _conn(cfg)
    user = _acting_user(conn, user_name)
    allowed = auth.visible_library_ids(conn, user)
    if library:
        lib = libsvc.find_library(conn, library)
        if lib is None:
            raise ServiceError("library_not_found", t("找不到库：{library}", library=library), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
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
    session = play.create_for_user(conn, AccessContext(user['id']), spec)
    ids = play.queue_items(conn, session["session_id"], limit=10000)
    if not ids:
        raise click.ClickException(t("队列为空：没有符合条件的图片"))
    paths: list[Path] = []
    for iid in ids:
        row = conn.execute(
            "SELECT i.rel_path, l.root_path FROM images i JOIN libraries l ON l.id=i.library_id WHERE i.id=?",
            (iid,),
        ).fetchone()
        if row:
            paths.append(Path(row["root_path"]) / row["rel_path"])
    click.echo(t("队列 {v0} 张（顺序：{order}{v1}）", v0=len(paths), order=order, v1=t('，循环') if loop else ''), err=True)
    if session['truncated']:
        click.echo(t('匹配 {matched} 张，实际队列 {total} 张；达到 10000 张上限',matched=session['matched_total'],total=session['total']),err=True)

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
                click.echo(t("回车下一张 / b 上一张 / q 退出") + (t("（{interval}s 自动）", interval=interval) if interval else ""))
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
        except EOFError:
            click.echo("\n" + t("已退出播放"), err=True)
        return

    # mpv 模式
    mpv = shutil.which("mpv")
    if not mpv:
        raise click.ClickException(t("找不到 mpv（可改用 --player terminal 或 --player list）"))
    playlist_path = cfg.playlists_dir / f"playlist-{int(time.time())}.m3u"
    playlist_path.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    cmd = [
        mpv,
        f"--playlist={playlist_path}",
        "--force-window=yes",
        "--no-resume-playback",
        "--keep-open=no",
        t("--title=Pic Shelf 播放"),
        f"--image-display-duration={'inf' if interval == 0 else interval}",
    ]
    if loop:
        cmd.append("--loop-playlist=inf")
    click.echo(t("交给 mpv 播放（→ 下一张 / ← 上一张 / q 退出）"))
    subprocess.call(cmd)


# ---------------------------------------------------------------- 查重

@main.command()
@click.option("--mode", default="exact", type=click.Choice(["exact", "similar"]), show_default=True)
@click.option("--threshold", default=8, type=click.IntRange(0, 64), show_default=True, help="similar 模式的 pHash 距离阈值")
@click.pass_obj
def dupes(cfg: Config, mode: str, threshold: int):
    """重复检测（exact=sha256 相同；similar=pHash 相似）。"""
    conn = _conn(cfg)
    if mode == "similar":
        groups = libsvc.similar_dupes(conn, None, threshold=threshold)
        for g in groups:
            click.echo(t("相似组（{v0} 张）：", v0=g['count']))
            for e in g["images"]:
                click.echo(t("  #{v0:>6}  距离{v1:>3}  {v2}", v0=e['id'], v1=e['distance'], v2=e['filename']))
    else:
        groups = libsvc.exact_dupes(conn, None)
        if not groups:
            click.echo(t("没有完全重复的文件。"))
        for g in groups:
            names = conn.execute(
                f"SELECT filename FROM images WHERE id IN ({','.join('?' * len(g['image_ids']))})",
                g["image_ids"],
            ).fetchall()
            click.echo(t("{v0} 份相同（可省 {v1}）：", v0=g['count'], v1=_fmt_size(g['size_bytes'] - g['size_bytes'] // g['count'])))
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
    from . import backups
    _conn(cfg).close()
    passphrase = (_read_password(passphrase_stdin, t("设置备份口令"), confirm=not passphrase_stdin)
                  if encrypt else None)
    result = backups.export(cfg, out_path, with_thumbs=with_thumbs, passphrase=passphrase)
    message = "已导出加密备份：{out}（{v0}）" if encrypt else "已导出备份：{out}（{v0}）"
    click.echo(t(message, out=result['path'], v0=_fmt_size(result['size_bytes'])))


@main.command("import-backup")
@click.argument("backup_path", type=click.Path(exists=True))
@click.option("--passphrase-stdin", is_flag=True)
@click.option("--force", is_flag=True, help="覆盖现有数据库（先自动备份现有库）")
@click.pass_obj
def import_backup(cfg: Config, backup_path: str, passphrase_stdin: bool, force: bool):
    """从备份恢复数据库。"""
    from . import backups
    with Path(backup_path).open('rb') as stream:
        encrypted = stream.read(len(backups.MAGIC)) == backups.MAGIC
    passphrase = _read_password(passphrase_stdin, t("输入备份口令")) if encrypted else None
    cfg.ensure()
    result = backups.restore(cfg, backup_path, force=force, passphrase=passphrase)
    if result['safety_backup']:
        click.echo(t("现有数据库已备份到：{bak}", bak=result['safety_backup']))
    click.echo(t("已恢复数据库到：{v0}", v0=cfg.db_path))


@main.group('backup')
def backup_group():
    """检查备份清单、校验和与恢复边界。"""


@backup_group.command('inspect')
@click.argument('file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--passphrase-stdin', is_flag=True)
def backup_inspect(file, passphrase_stdin):
    from . import backups
    with file.open('rb') as stream:
        encrypted = stream.read(len(backups.MAGIC)) == backups.MAGIC
    passphrase = _read_password(passphrase_stdin, t("输入备份口令")) if encrypted else None
    click.echo(json.dumps(backups.inspect(file, passphrase=passphrase), ensure_ascii=False, indent=2))


@main.command('check')
@click.option('--full', is_flag=True, help='逐个核对原图 SHA-256；默认只检查可达性')
@click.pass_obj
def check_cmd(cfg, full):
    """只读检查原图、派生图、关联与封面；输出 JSON。"""
    from . import checks
    result = checks.check(cfg, full=full)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if set(result['counts']) - {'derivative_unreferenced'}:
        raise click.exceptions.Exit(1)


@main.command('doctor')
@click.option('--full', is_flag=True, help='同时核对原图哈希；默认检查可达性')
@click.pass_obj
def doctor_cmd(cfg, full):
    """输出脱敏的本地诊断 JSON，不上传或自动修复。"""
    from .diagnostics import doctor
    result = doctor(cfg, full=full)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['ready']:
        raise click.exceptions.Exit(1)


# ---------------------------------------------------------------- 托管库导入

@main.command("import")
@click.argument("source_dir", type=click.Path(exists=True, file_okay=False))
@click.argument("library_name")
@click.option("--mode", type=click.Choice(["hardlink", "copy", "move"]), default=None,
              help="hardlink=原文件不动且零额外空间（跨盘自动降级 copy）；copy=复制留档；move=移入整理")
@click.option("--template", default=None, help="按命名模板生成目标相对路径")
@click.option("--template-fields", default=None, help="命名字段 JSON：artists、tags、source、date")
@click.option("--rename/--keep-names", default=True, show_default=True,
              help="文件名规范化（去掉文件系统危险字符）")
@click.option("--apply", "do_apply", is_flag=True, help="真正执行（默认只预演）")
@click.option("--yes", is_flag=True, help="跳过执行确认")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.pass_obj
def import_cmd(cfg: Config, source_dir: str, library_name: str, mode: str,
               rename: bool, do_apply: bool, yes: bool, as_json: bool, template: str | None, template_fields: str | None):
    """把外部目录导入托管的库：预演优先，--apply 执行，完成后自动扫描入库。"""
    if do_apply and not yes and not sys.stdin.isatty():
        raise ServiceError("confirmation_required", t("非交互执行需要 --yes；请先检查预演结果"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    from . import import_plans

    conn = _conn(cfg)
    lib = libsvc.find_library(conn, library_name)
    if lib is None:
        raise ServiceError("library_not_found", t("找不到库：{library_name}（先运行 picshelf library add）", library_name=library_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    try:
        access = AccessContext(_acting_user(conn)["id"], local_admin=True)
        try:
            fields = json.loads(template_fields) if template_fields else None
        except ValueError as exc:
            raise click.UsageError(t('JSON 格式无效，请检查输入')) from exc
        plan = import_plans.create_plan(conn, access, lib["id"], source_dir, mode=mode, rename=rename,
                                        template=template, template_fields=fields)
        mode = plan['mode']
        mode = plan["mode"]
    except ValueError as exc:
        raise click.ClickException(str(exc))

    if as_json:
        if not do_apply:
            click.echo(json.dumps(plan, ensure_ascii=False, indent=2))
            return
        if not yes:
            raise ServiceError("confirmation_required", t("结构化执行需要 --yes；请先检查预演结果"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
        result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
        if result["state"] != "succeeded":
            raise click.exceptions.Exit(1)
        return

    s = plan["stats"]
    new_n = s["hardlink"] + s["copy"] + s["move"]
    click.echo(t("预演：源 {v0} → 库 #{v1} {v2}（模式：{mode}）", v0=plan['source'], v1=lib['id'], v2=lib['name'], mode=mode))
    click.echo(
        t("共 {v0} 个文件：新导入 {new_n}，跳过 {v1}（重复/已存在），错误 {v2}", v0=plan['items_total'], new_n=new_n, v1=s['skip'], v2=s['error'])
    )
    click.echo(t("计划 ID：{plan_id}（可用 import-status 查看，import-execute 执行）", plan_id=plan["plan_id"]))
    labels = {"hardlink": t("硬链"), "copy": t("复制"), "move": t("移动"), "skip": t("跳过"), "error": t("错误")}
    for item in plan["items"][:20]:
        dst = item["dst"] or "—"
        reason = f"  ← {item['reason']}" if item["reason"] else ""
        click.echo(f"  [{labels.get(item['action'], item['action'])}] {item['src']} → {dst}{reason}")
    if plan["items_total"] > 20:
        click.echo(t("  …（共 {v0} 项，只显示前 20）", v0=plan['items_total']))

    if not do_apply:
        click.echo(t("这是预演，未改动源文件或目标文件。使用 import-execute 执行已保存的计划。"))
        return
    if new_n == 0:
        click.echo(t("没有需要导入的文件。"))
        return
    if not yes:
        if mode == "move":
            if not click.confirm(t("确认把 {new_n} 个文件【移动】进库？原位置将不再保留（建议先备份）", new_n=new_n), err=True):
                raise click.ClickException(t("已取消"))
        else:
            if not click.confirm(t("确认导入 {new_n} 个文件？", new_n=new_n), err=True):
                raise click.ClickException(t("已取消"))

    result = import_plans.execute_plan(conn, cfg, access, plan["plan_id"])
    done = result["done"]
    click.echo(
        t("执行完成：硬链 {v0}，复制 {v1}，移动 {v2}，跳过 {v3}，错误 {v4}", v0=done['hardlink'], v1=done['copy'], v2=done['move'], v3=done['skip'], v4=done['error'])
    )
    for err in done["errors"][:10]:
        click.echo(f"  ! {err}", err=True)
    if result["scan"] is not None:
        stats = result["scan"]
        click.echo(
            t("已扫描入库：新增 {v0}，更新 {v1}，未变 {v2}", v0=stats['added'], v1=stats['updated'], v2=stats['unchanged'])
        )
    if result["state"] != "succeeded":
        raise click.exceptions.Exit(1)


@main.command("import-status")
@click.argument("plan_id")
@click.option("--offset", default=0, type=click.IntRange(min=0, max=2**63-1))
@click.option("--limit", default=300, type=click.IntRange(1, 1000))
@click.pass_obj
def import_status(cfg: Config, plan_id: str, offset: int, limit: int):
    """以 JSON 查看已保存的导入计划和逐项结果。"""
    from . import import_plans
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(import_plans.get_plan(conn, access, plan_id, offset=offset, limit=limit),
                          ensure_ascii=False, indent=2))


@main.command("import-execute")
@click.argument("plan_id")
@click.option("--yes", is_flag=True, help="跳过执行确认")
@click.option("--retry", is_flag=True, help="继续取消的计划或重试失败项目，成功项不会重复执行")
@click.option("--json", "as_json", is_flag=True, help="输出 JSON")
@click.option("--background", is_flag=True, help="只排队，交由 Web 服务或 picshelf worker 执行")
@click.pass_obj
def import_execute(cfg: Config, plan_id: str, yes: bool, retry: bool, as_json: bool, background: bool):
    """执行已查看的导入计划；不会重新扫描源目录生成另一份计划。"""
    from . import import_plans
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    try:
        plan = import_plans.get_plan(conn, access, plan_id)
        if not yes:
            if as_json or not sys.stdin.isatty():
                raise ServiceError("confirmation_required", t("结构化执行需要 --yes；请先检查预演结果"), status=400, next_step=t("请查看该命令的 --help 并检查参数后重试"))
            if not click.confirm(t("确认执行计划 {plan_id}（模式：{mode}）？", plan_id=plan_id, mode=plan["mode"]), err=True):
                raise click.ClickException(t("已取消"))
        from . import jobs
        queued = jobs.submit(conn, access, "import", plan["library"]["id"],
                             plan_id=plan_id, request_key="import:" + plan_id)
        if retry:
            queued = jobs.retry(conn, access, queued["id"])
        if background:
            click.echo(json.dumps(queued, ensure_ascii=False))
            return
        status = jobs.wait(cfg, access, queued["id"])
        result = status["result"]
        if result is None:
            click.echo(json.dumps(status, ensure_ascii=False))
            raise click.exceptions.Exit(1)
        result["job_id"] = status["id"]
    except ServiceError as exc:
        if not as_json:
            raise
        click.echo(json.dumps(exc.as_dict(), ensure_ascii=False))
        raise click.exceptions.Exit(1)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result["state"] != "succeeded":
        raise click.exceptions.Exit(1)


@main.command("import-cancel")
@click.argument("plan_id")
@click.pass_obj
def import_cancel(cfg: Config, plan_id: str):
    """请求取消导入；已经完成的项目会保留。"""
    from . import import_plans
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(import_plans.cancel_plan(conn, access, plan_id), ensure_ascii=False, indent=2))


@main.command("worker")
@click.option("--once", is_flag=True, help="最多执行一个排队任务后退出")
@click.pass_obj
def worker_command(cfg: Config, once: bool):
    """运行本地后台任务执行器；按 Ctrl+C 安全停止。"""
    from .worker import work
    raise click.exceptions.Exit(work(cfg, once=once))


@main.group("job")
def job_group():
    """查看、取消与重试后台任务。"""


@job_group.command("list")
@click.option("--offset", default=0, type=click.IntRange(min=0, max=2**63-1))
@click.pass_obj
def job_list(cfg: Config, offset: int):
    from . import jobs
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(jobs.list_jobs(conn, access, offset=offset), ensure_ascii=False, indent=2))


@job_group.command("status")
@click.argument("job_id")
@click.option("--offset", default=0, type=click.IntRange(min=0, max=2**63-1))
@click.option("--limit", default=100, type=click.IntRange(1, 1000))
@click.pass_obj
def job_status(cfg: Config, job_id: str, offset: int, limit: int):
    from . import jobs
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(jobs.get_job(conn, access, job_id, offset=offset, limit=limit), ensure_ascii=False, indent=2))


@job_group.command("cancel")
@click.argument("job_id")
@click.pass_obj
def job_cancel(cfg: Config, job_id: str):
    from . import jobs
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    click.echo(json.dumps(jobs.cancel(conn, access, job_id), ensure_ascii=False, indent=2))


@job_group.command("retry")
@click.argument("job_id")
@click.option("--background", is_flag=True, help="只排队，交由 Web 服务或 picshelf worker 执行")
@click.pass_obj
def job_retry(cfg: Config, job_id: str, background: bool):
    from . import jobs
    conn = _conn(cfg)
    access = AccessContext(_acting_user(conn)["id"], local_admin=True)
    queued = jobs.retry(conn, access, job_id)
    result = queued if background else jobs.wait(cfg, access, job_id)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not background and result["state"] != "succeeded":
        raise click.exceptions.Exit(1)


@main.group('manifest')
def manifest_group():
    """导出可移植元数据，或预演并确认导入。"""


@manifest_group.command('export')
@click.option('--library', 'library_names', multiple=True, help='只导出指定库，可重复；默认全部可见库')
@click.option('--user', default=None)
@click.pass_obj
def manifest_export(cfg, library_names, user):
    from . import manifest
    conn = _conn(cfg)
    ids = []
    for name in library_names:
        library = libsvc.find_library(conn, name)
        if library is None:
            raise ServiceError('library_not_found', t('库不存在'), status=404,
                               next_step=t('请先运行 library list 检查库名'))
        ids.append(library['id'])
    click.echo(json.dumps(manifest.export(conn, _batch_access(conn, user), ids if library_names else None), ensure_ascii=False, indent=2))


@manifest_group.command('preview')
@click.argument('file', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--mappings', required=True, help='稳定 ID 到本地 ID 的 JSON 映射，必须包含 libraries 和 users')
@click.option('--user', default=None)
@click.pass_obj
def manifest_preview(cfg, file, mappings, user):
    from . import manifest
    if file.stat().st_size > manifest.MAX_BYTES:
        raise manifest._error('manifest_too_large', 413)
    try:
        document = json.loads(file.read_text(encoding='utf-8'))
        maps = json.loads(mappings)
    except (ValueError, UnicodeError) as exc:
        raise click.UsageError(t('JSON 格式无效，请检查输入')) from exc
    conn = _conn(cfg)
    result = manifest.preview(conn, _batch_access(conn, user), document, maps)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['preview']['ready']:
        raise click.exceptions.Exit(1)


@manifest_group.command('show')
@click.argument('plan_id')
@click.option('--user', default=None)
@click.pass_obj
def manifest_show(cfg, plan_id, user):
    from . import manifest
    conn = _conn(cfg)
    click.echo(json.dumps(manifest.get_plan(conn, _batch_access(conn, user), plan_id), ensure_ascii=False, indent=2))


@manifest_group.command('execute')
@click.argument('plan_id')
@click.option('--yes', is_flag=True)
@click.option('--user', default=None)
@click.pass_obj
def manifest_execute(cfg, plan_id, yes, user):
    from . import manifest
    if not yes:
        raise ServiceError('confirmation_required', t('结构化执行需要 --yes；请先检查预演结果'),
                           next_step=t('请先检查元数据导入预演，再明确确认执行'))
    conn = _conn(cfg)
    click.echo(json.dumps(manifest.execute(conn, cfg, _batch_access(conn, user), plan_id), ensure_ascii=False, indent=2))


@main.group('works')
def works_group():
    """手工关联同一作品的文件版本，保留各图片元数据。"""


def _work_members(value):
    try:
        return json.loads(value)
    except ValueError as exc:
        raise click.UsageError(t('JSON 格式无效，请检查输入')) from exc


@works_group.command('create')
@click.argument('name')
@click.option('--members', required=True, help='JSON 数组，每项包含 image_id 和 role')
@click.option('--preferred', type=click.IntRange(1, 2**63-1), default=None)
@click.pass_obj
def work_create(cfg, name, members, preferred):
    from . import works
    conn = _conn(cfg)
    click.echo(json.dumps(works.create(conn, _batch_access(conn, None), name, _work_members(members), preferred), ensure_ascii=False, indent=2))


@works_group.command('show')
@click.argument('work_id', type=click.IntRange(1, 2**63-1))
@click.option('--user', default=None)
@click.pass_obj
def work_show(cfg, work_id, user):
    from . import works
    conn = _conn(cfg)
    click.echo(json.dumps(works.get(conn, AccessContext(_acting_user(conn, user)['id']), work_id), ensure_ascii=False, indent=2))


@works_group.command('link')
@click.argument('work_id', type=click.IntRange(1, 2**63-1))
@click.option('--members', required=True)
@click.pass_obj
def work_link(cfg, work_id, members):
    from . import works
    conn = _conn(cfg)
    click.echo(json.dumps(works.link(conn, _batch_access(conn, None), work_id, _work_members(members)), ensure_ascii=False, indent=2))


@works_group.command('pending')
@click.argument('work_id', type=click.IntRange(1, 2**63-1))
@click.pass_obj
def work_pending(cfg, work_id):
    from . import works
    conn = _conn(cfg)
    click.echo(json.dumps(works.pending(conn, _batch_access(conn, None), work_id), ensure_ascii=False, indent=2))


@works_group.command('drop-pending')
@click.argument('work_id', type=click.IntRange(1, 2**63-1))
@click.argument('image_uid')
@click.option('--yes', is_flag=True)
@click.pass_obj
def work_drop_pending(cfg, work_id, image_uid, yes):
    if not yes:
        raise ServiceError('confirmation_required', t('结构化执行需要 --yes；请先检查预演结果'),
                           next_step=t('请先查看作品关联和待匹配引用，再明确确认解除'))
    from . import works
    conn = _conn(cfg)
    click.echo(json.dumps(works.drop_pending(conn, _batch_access(conn, None), work_id, image_uid), ensure_ascii=False, indent=2))


for _name, _operation in (('unlink', 'unlink'), ('prefer', 'prefer')):
    def _work_action(cfg, work_id, image_id, yes=False, _op=_operation):
        from . import works
        if _op == 'unlink' and not yes:
            raise ServiceError('confirmation_required', t('结构化执行需要 --yes；请先检查预演结果'),
                               next_step=t('请先查看作品关联和待匹配引用，再明确确认解除'))
        conn = _conn(cfg)
        click.echo(json.dumps(getattr(works, _op)(conn, _batch_access(conn, None), work_id, image_id), ensure_ascii=False, indent=2))
    _work_action = click.pass_obj(_work_action)
    if _operation == 'unlink':
        _work_action = click.option('--yes', is_flag=True)(_work_action)
    _work_action = click.argument('image_id', type=click.IntRange(1, 2**63-1))(_work_action)
    _work_action = click.argument('work_id', type=click.IntRange(1, 2**63-1))(_work_action)
    works_group.command(_name)(_work_action)


# ---------------------------------------------------------------- 用户 / 组 / 统计

@main.group('paths')
def paths_group():
    """核验并重绑定库根目录，或预演托管库文件重组。"""


@paths_group.command('preview')
@click.argument('library_name')
@click.argument('target_path', type=click.Path(exists=True, file_okay=False))
@click.pass_obj
def paths_preview(cfg, library_name, target_path):
    from . import relocations
    conn = _conn(cfg)
    library = libsvc.find_library(conn, library_name)
    if library is None:
        raise click.ClickException(t('库不存在'))
    result = relocations.preview(conn, _batch_access(conn, None), library['id'], target_path)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['preview']['ready']:
        raise click.exceptions.Exit(1)


@paths_group.command('show')
@click.argument('plan_id')
@click.pass_obj
def paths_show(cfg, plan_id):
    from . import relocations
    conn = _conn(cfg)
    click.echo(json.dumps(relocations.get(conn, _batch_access(conn, None), plan_id), ensure_ascii=False, indent=2))


@paths_group.command('execute')
@click.argument('plan_id')
@click.option('--yes', is_flag=True)
@click.pass_obj
def paths_execute(cfg, plan_id, yes):
    from . import relocations
    if not yes:
        raise ServiceError('confirmation_required', t('结构化执行需要 --yes；请先检查预演结果'),
                           next_step=t('请先检查库路径迁移预演，再明确确认执行'))
    conn = _conn(cfg)
    click.echo(json.dumps(relocations.execute(conn, cfg, _batch_access(conn, None), plan_id), ensure_ascii=False, indent=2))


@paths_group.command('reorganize')
@click.argument('library_name')
@click.option('--template', required=True)
@click.pass_obj
def paths_reorganize(cfg, library_name, template):
    from . import reorganize
    conn = _conn(cfg)
    library = libsvc.find_library(conn, library_name)
    if library is None:
        raise click.ClickException(t('库不存在'))
    result = reorganize.preview(conn, cfg, _batch_access(conn, None), library['id'], template)
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if result['stats']['error']:
        raise click.exceptions.Exit(1)

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
        raise ServiceError("state_conflict", t("用户已存在：{username}", username=username), status=409, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    pw = _read_password(password_stdin, t("为 {username} 设置密码", username=username), confirm=True)
    uid = auth.create_user(conn, username, pw, is_admin=admin)
    click.echo(t("已创建用户 #{uid} {username}{v0}", uid=uid, username=username, v0=t('（管理员）') if admin else ''))


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
            flags.append(t("管理员"))
        if r["disabled"]:
            flags.append(t("已禁用"))
        click.echo(t("#{v0:>3}  {v1:<16} {v2:<12} 组：{v3}", v0=r['id'], v1=r['username'], v2=','.join(flags), v3=r['groups'] or '—'))


@users.command("passwd")
@click.argument("username")
@click.option("--password-stdin", is_flag=True)
@click.pass_obj
def users_passwd(cfg, username, password_stdin):
    conn = _conn(cfg)
    user = auth.get_user_by_username(conn, username)
    if user is None:
        raise ServiceError("user_not_found", t("用户不存在：{username}", username=username), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    pw = _read_password(password_stdin, t("为 {username} 设置新密码", username=username), confirm=True)
    auth.set_password(conn, user["id"], pw)
    conn.commit()
    click.echo(t("密码已更新"))


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
        raise ServiceError("state_conflict", t("组已存在：{name}", name=name), status=409, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn.commit()
    click.echo(t("已创建组 #{v0} {name}（{v1}）", v0=cur.lastrowid, name=name, v1='、'.join(cap_list)))


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
        raise ServiceError("group_not_found", t("组不存在：{group_name}", group_name=group_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    if lib is None:
        raise ServiceError("library_not_found", t("库不存在：{library_name}", library_name=library_name), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn.execute(
        "INSERT INTO library_grants(group_id, library_id, access) VALUES(?,?,?) "
        "ON CONFLICT(group_id, library_id) DO UPDATE SET access=excluded.access",
        (g["id"], lib["id"], access),
    )
    conn.commit()
    click.echo(t("组 {group_name} 对库 {v0} 的访问级别 → {access}", group_name=group_name, v0=lib['name'], access=access))


@groups.command("revoke")
@click.argument("group_name")
@click.argument("library_name")
@click.pass_obj
def groups_revoke(cfg, group_name, library_name):
    conn = _conn(cfg)
    g = conn.execute("SELECT id FROM groups WHERE name=?", (group_name,)).fetchone()
    lib = libsvc.find_library(conn, library_name)
    if g is None or lib is None:
        raise ServiceError("library_not_found", t("组或库不存在"), status=404, next_step=t("请查看该命令的 --help 并检查参数后重试"))
    conn.execute("DELETE FROM library_grants WHERE group_id=? AND library_id=?", (g["id"], lib["id"]))
    conn.commit()
    click.echo(t("已移除组 {group_name} 对库 {v0} 的授权", group_name=group_name, v0=lib['name']))


@main.command()
@click.pass_obj
def stats(cfg: Config):
    """全局统计。"""
    conn = _conn(cfg)
    s = libsvc.global_stats(conn, None)
    click.echo(
        t("图片 {v0} 张（{v1}）  库 {v2}  专辑 {v3}  艺术家 {v4}  标签 {v5}  用户 {v6}  评分 {v7}  收藏 {v8}", v0=s['images'], v1=_fmt_size(s['size_bytes']), v2=s['libraries'], v3=s['albums'], v4=s['artists'], v5=s['tags'], v6=s['users'], v7=s['ratings'], v8=s['favorites'])
    )


# ---------------------------------------------------------------- TUI

@main.command()
@click.pass_obj
def tui(cfg: Config):
    """启动终端界面（TUI）。"""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException(t("TUI 需要交互终端；管道请使用 ls --json"))
    if not cfg.db_path.exists():
        raise click.ClickException(t("尚未初始化。先运行：picshelf init --admin <管理员名>"))
    from .tui import run_tui

    run_tui(cfg)


if __name__ == "__main__":
    main()
