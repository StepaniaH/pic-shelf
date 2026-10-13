"""终端界面（Textual）：树形浏览 + 图片列表 + 详情 + 评分/标签/收藏 + 幻灯片连播。

快捷键：
  j/k 移动 ｜ enter 查看详情刷新 ｜ p 预览图 ｜ r 评分 ｜ f 收藏 ｜ t 加标签
  c 评论 ｜ n 笔记 ｜ s 幻灯片 ｜ space 暂停/继续 ｜ / 搜索 ｜ g 刷新 ｜ q 退出
"""
from __future__ import annotations

import shutil
import sqlite3
import asyncio
import shlex
import uuid
from dataclasses import asdict
from contextlib import closing
from pathlib import Path
from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static, Tree

from . import auth, batches, catalog, db as dbm, jobs, metadata, ops, play, play_rules, saved_queries, search
from .access import AccessContext
from .config import Config
from .errors import ServiceError
from .i18n import t
from .worker import WorkerManager
from .tui_screens import ConfirmScreen, HelpScreen, WorkScreen


async def _render_preview(path: Path, width: int = 72, height: int = 26) -> str | None:
    renderer = shutil.which("chafa") or shutil.which("img2txt")
    if renderer is None:
        return None
    cmd = ([renderer, "-f", "symbols", "--size", f"{width}x{height}", str(path)]
           if renderer.endswith("chafa") else [renderer, "-W", str(width), "-H", str(height), "-f", "ansi", str(path)])
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        output, _ = await asyncio.wait_for(process.communicate(), 20)
        return output.decode("utf-8", errors="replace") if process.returncode == 0 else None
    except (OSError, asyncio.TimeoutError):
        return None
    finally:
        if process is not None and process.returncode is None:
            try: process.kill()
            except ProcessLookupError: pass
            await process.wait()


class PromptScreen(ModalScreen[str | None]):
    """单行输入弹窗。"""

    def __init__(self, title: str, default: str = "", placeholder: str = ""):
        super().__init__()
        self._title = title
        self._default = default
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            with VerticalScroll(id="prompt-description"):
                yield Static(Text(self._title))
            yield Input(value=self._default, placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self) -> None:
        self.dismiss(None)


class PicShelfTUI(App):
    TITLE = "Pic Shelf"
    PAGE_SIZE = 500

    CSS = """
    #body { height: 1fr; }
    #left { width: 28; border-right: solid $panel; padding: 0 1; }
    #right { width: 1fr; }
    #image-table { height: 1fr; }
    #detail-scroll { height: 13; max-height: 45%; border-top: solid $panel; }
    #image-table:focus, #nav-tree:focus { border: solid $primary; }
    #detail { height: auto; padding: 1 2; }
    #list-status { height: auto; min-height: 1; color: $text-muted; padding: 0 1; }
    #search-input { dock: top; }
    PromptScreen, ConfirmScreen, HelpScreen, WorkScreen, WorkDetailsScreen { align: center middle; }
    #confirm-box, #help-box, #work-box { width: 90; max-width: 95%; height: 85%; padding: 1; background: $surface; border: solid $primary; }
    #confirm-scroll, #work-table { height: 1fr; }
    .dialog-buttons { height: 3; }
    .dialog-buttons Button { min-width: 8; width: 1fr; padding: 0; }
    #work-detail { height: auto; max-height: 4; }
    #help-text { height: auto; }
    #prompt-box { width: 60; max-width: 95%; max-height: 95%; height: auto; padding: 1 2; background: $surface; border: solid $primary; }
    #prompt-description { height: auto; max-height: 10; }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit_interrupt", "", show=False, priority=True),
        Binding("f1", "help", t("帮助"), priority=True),
        Binding("q", "quit_app", t("退出")),
        Binding("x", "select_image", t("选择")),
        Binding("ctrl+x", "clear_selection", t("清空选择"), show=False, priority=True),
        Binding("a", "artist", t("艺术家"), show=False),
        Binding("l", "album", t("专辑"), show=False),
        Binding("w", "workflow", t("整理状态"), show=False),
        Binding("d", "toggle_detail", t("详情"), show=False),
        Binding("ctrl+j", "jobs", t("后台任务"), show=False, priority=True),
        Binding("ctrl+s", "scan", t("扫描更新"), show=False, priority=True),
        Binding("ctrl+p", "resume_playback", t("恢复播放"), show=False, priority=True),
        Binding("right", "play_next", t("下一张"), show=False, priority=True),
        Binding("left", "play_previous", t("上一张"), show=False, priority=True),
        Binding("j", "cursor_down", t("下移")),
        Binding("k", "cursor_up", t("上移")),
        Binding("p", "toggle_preview", t("预览")),
        Binding("r", "rate", t("评分")),
        Binding("f", "favorite", t("收藏")),
        Binding("t", "add_tag", t("加标签")),
        Binding("c", "comment", t("评论")),
        Binding("n", "note", t("笔记")),
        Binding("s", "slideshow", t("幻灯片")),
        Binding("space", "pause_slideshow", t("暂停/继续")),
        Binding("g", "refresh", t("刷新")),
        Binding("slash", "focus_search", t("搜索")),
        Binding("b", "toggle_navigation", t("导航")),
        Binding("left_square_bracket", "previous_page", t("上一页")),
        Binding("right_square_bracket", "next_page", t("下一页")),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.conn: sqlite3.Connection = dbm.open_db(cfg.db_path)
        self.user = self._acting_user()
        self.allowed = auth.visible_library_ids(self.conn, self.user)
        self.current_items: list[dict] = []
        self.current_title = t("全部图片")
        self.slideshow_timer = None
        self.show_preview = False
        self.page_offset = 0
        self.total = 0
        self.slideshow_paused = False
        self.selected_ids = set()
        self.access = AccessContext(self.user["id"], local_admin=True)
        self.work_manager = WorkerManager(cfg)
        self.playback = None
        self.preview_worker = None
        self.detail_version = 0
        self._queue_offset = 0
        self._last_batch = None
        self._last_batch_state = None
        self._shutting_down = False

    def _acting_user(self):
        row = self.conn.execute(
            "SELECT * FROM users WHERE is_admin=1 AND disabled=0 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError(t("没有管理员用户。先运行：picshelf init --admin <名字>"))
        return row

    # ------------------------------------------------------------ 布局

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder=t("搜索：tag / -tag / artist:名 / album:ID / rating:>=8 / fav:"), id="search-input")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Tree(t("浏览"), id="nav-tree")
            with Vertical(id="right"):
                yield Static(id="list-status", markup=False)
                yield DataTable(id="image-table")
                with VerticalScroll(id="detail-scroll"):
                    yield Static(t("选中图片后显示详情"), id="detail")
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one("#nav-tree", Tree)
        tree.root.expand()
        self._build_tree(tree)
        table = self.query_one("#image-table", DataTable)
        table.cursor_type = "row"
        table.add_column(t("选择"), width=3, key="selected")
        table.add_column("ID", width=5)
        table.add_column(t("文件名"), width=max(18, min(48, self.size.width - 44)))
        table.add_column(t("尺寸"), width=11)
        table.add_column(t("评分"), width=4)
        table.add_column(t("收藏"), width=4)
        table.focus()  # 默认焦点在列表，字母快捷键才不会被搜索框吃掉
        self.load_images({"kind": "all"})
        self.query_one("#left").display = self.size.width >= 100
        self.query_one("#detail-scroll").display = self.size.height >= 30
        self.set_interval(.5, self._poll_work)

    def _build_tree(self, tree: Tree) -> None:
        conn = self.conn
        tree.root.add_leaf(t("全部图片"), data={"kind": "all"})
        for lib in conn.execute("SELECT * FROM libraries ORDER BY name"):
            if self.allowed is not None and lib["id"] not in self.allowed:
                continue
            lib_node = tree.root.add(Text(t("库：{v0}", v0=lib['name'])), data={"kind": "library", "id": lib["id"]}, expand=False)
            for album in conn.execute(
                "SELECT * FROM albums WHERE library_id=? ORDER BY kind DESC, name", (lib["id"],)
            ):
                lib_node.add_leaf(
                    Text(f"{'★ ' if album['kind'] == 'manual' else ''}{album['name']}"),
                    data={"kind": "album", "id": album["id"]},
                )
        artists = conn.execute("SELECT * FROM artists ORDER BY name").fetchall()
        if artists:
            art_node = tree.root.add(t("艺术家"), expand=False)
            for a in artists:
                art_node.add_leaf(Text(a["name"]), data={"kind": "artist", "id": a["id"]})
        tags = conn.execute(
            "SELECT t.id, t.name, COUNT(it.image_id) n FROM tags t LEFT JOIN image_tags it ON it.tag_id=t.id "
            "GROUP BY t.id ORDER BY n DESC, t.name LIMIT 100"
        ).fetchall()
        if tags:
            tag_node = tree.root.add(t("标签"), expand=False)
            for tg in tags:
                tag_node.add_leaf(Text(f"{tg['name']} ({tg['n']})"), data={"kind": "tag", "name": tg["name"]})
        tree.root.add_leaf(t("★ 我的收藏"), data={"kind": "favorites"})
        tree.root.add_leaf(t("高分（≥8）"), data={"kind": "highrating"})
        tree.root.add_leaf(t("待整理"), data={"kind":"inbox"})
        for label, values, kind in (
            (t("保存的查询"), saved_queries.list_queries(conn,self.access)['items'], "query"),
            (t("播放规则"), play_rules.list_rules(conn,self.access)['items'], "play-rule"),
        ):
            node = tree.root.add(label, expand=False)
            for value in values:
                node.add_leaf(Text(value['name']), data={"kind":kind,"id":value['id']})

    # ------------------------------------------------------------ 加载

    def load_images(self, sel: dict, query: str | None = None, *, offset: int | None = None) -> None:
        if self.playback is not None:
            self._stop_playback()
        if offset is not None:
            self.page_offset = max(0, offset)
        elif sel != getattr(self, "_current_sel", None) or query is not None:
            self.page_offset = 0
        self._current_sel = sel
        q = query if query is not None else self.query_one("#search-input", Input).value or ""
        kind = sel.get("kind")
        spec = catalog.ImageQuery(
            query=q, sort="path", limit=self.PAGE_SIZE, offset=self.page_offset,
            library_id=sel.get("id") if kind == "library" else None,
            album_id=sel.get("id") if kind == "album" else None,
            artist_id=sel.get("id") if kind == "artist" else None,
            tag=sel.get("name") if kind == "tag" else None,
            favorites=kind == "favorites", rating_min=8 if kind == "highrating" else None, workflow="inbox" if kind == "inbox" else None)
        if kind == "query":
            rule = saved_queries.get(self.conn,self.access,sel['id'])
            spec = catalog.ImageQuery(query=rule['query'],sort=rule['sort'],limit=self.PAGE_SIZE,offset=self.page_offset)
            self.query_one("#search-input",Input).value = rule['query']
        self._query_spec = spec
        try:
            res = catalog.query_images(self.conn, self.access, spec)
        except ServiceError as error:
            self.notify(f"{error.detail} {error.next_step}",severity="error")
            return
        self.total = res["total"]
        if not res["items"] and self.page_offset and self.total:
            return self.load_images(sel, offset=max(0, ((self.total - 1) // self.PAGE_SIZE) * self.PAGE_SIZE))
        self.current_items = res["items"]
        self.current_title = {
            "all": t("全部图片"), "library": t("库"), "album": t("专辑"), "artist": t("艺术家"),
            "tag": t("标签"), "favorites": t("收藏"), "highrating": t("高分（≥8）"), "inbox":t("待整理"), "query":t("保存的查询"),
        }.get(sel.get("kind"), "")
        self._paint_table()
        for error in res["errors"]:
            self.notify(error, severity="warning")

    def _paint_table(self):
        table = self.query_one("#image-table",DataTable)
        table.clear()
        for index,item in enumerate(self.current_items):
            table.add_row("✓" if item['id'] in self.selected_ids else "", str(item['id']), Text(item['filename']),
                          f"{item.get('width') or '?'}×{item.get('height') or '?'}",
                          "—" if item['my_rating'] < 0 else str(item['my_rating']), "★" if item['favorite'] else "", key=self._row_key(item,index))
        self.sub_title = t("{v0} — {v1} 张",v0=self.current_title,v1=self.total)
        self._status()
        self._update_detail()

    def _status(self):
        if self._shutting_down: return
        value = t("第 {start}–{end} 张 / 共 {total} 张 · 已选 {selected} · F1 帮助",start=self.page_offset+1 if self.current_items else 0,
                  end=self.page_offset+len(self.current_items),total=self.total,selected=len(self.selected_ids))
        if self.playback:
            value += " · " + t("播放 {position}/{total} · {state}",position=self.playback['position'],total=self.playback['total'],
                               state=t("已暂停") if self.playback['paused'] else t("播放中"))
        self.query_one("#list-status",Static).update(value)

    # ------------------------------------------------------------ 事件

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            if event.node.data['kind'] == 'play-rule':
                try: self._activate_playback(play_rules.start(self.conn,self.access,event.node.data['id']))
                except ServiceError as error: self.notify(f"{error.detail} {error.next_step}",severity="error")
            else: self.load_images(event.node.data)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "image-table": return
        if event.cursor_row != self.query_one("#image-table",DataTable).cursor_row: return
        if self.playback and self._queue_offset + event.cursor_row != self.playback['cursor']:
            self._play_control("goto",position=self._queue_offset + event.cursor_row,paint=False)
        self._update_detail()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            sel = getattr(self, "_current_sel", {"kind":"all"})
            self.load_images({"kind":"all"} if sel['kind'] == 'query' else sel, query=event.value)
            self.query_one("#image-table", DataTable).focus()  # 搜索后焦点回到列表

    def _selected_id(self) -> int | None:
        table = self.query_one("#image-table", DataTable)
        if not self.current_items:
            return None
        idx = table.cursor_row
        if idx is None or idx >= len(self.current_items):
            return None
        return int(self.current_items[idx]["id"])

    def _current_path(self, image_id: int) -> Path | None:
        row = self.conn.execute(
            "SELECT i.rel_path, l.root_path FROM images i JOIN libraries l ON l.id=i.library_id WHERE i.id=?",
            (image_id,),
        ).fetchone()
        return Path(row["root_path"]) / row["rel_path"] if row else None

    def _update_detail(self) -> None:
        self.detail_version += 1
        version = self.detail_version
        if self.preview_worker: self.preview_worker.cancel()
        detail = self.query_one("#detail", Static)
        iid = self._selected_id()
        if iid is None:
            detail.update(t("（无图片）"))
            return
        try:
            self.access.require_image(self.conn,iid,include_missing=True)
            d = search.get_image(self.conn, iid, self.user["id"], self.access.allowed_libraries(self.conn))
        except ServiceError as error:
            detail.update(Text(f"{error.detail} {error.next_step}")); return
        if d is None:
            detail.update(t("（图片已被移除）"))
            return
        tags = "、".join(tg["name"] for tg in d["tags"]) or t("（无标签）")
        artists = "、".join(a["name"] for a in d["artists"]) or t("（无）")
        albums = "、".join(a["name"] for a in d["albums"]) or t("（无）")
        rating = t("未评") if d["my_rating"] < 0 else str(d["my_rating"])
        lines = [
            f"#{d['id']} {d['filename']}  {d['width']}x{d['height']}  {d['ext']}",
            t("评分：我的 {rating} / 平均 {v0}   收藏：{v1}", rating=rating, v0=d['avg_rating'] if d['avg_rating'] is not None else '—', v1='★' if d['favorite'] else '—'),
            t("标签：{tags}", tags=tags),
            t("艺术家：{artists}       专辑：{albums}", artists=artists, albums=albums),
        ]
        content = Text("\n".join(lines))
        info = metadata.get(self.conn,self.access,iid)
        content.append("\n" + t("整理状态") + ": " + t("已整理" if info['workflow']['state'] == 'done' else "待整理"))
        content.append("\n" + t("路径") + ": " + d['rel_path'])
        detail.update(content)
        if self.show_preview:
            path = self._current_path(iid)
            async def preview():
                art = await _render_preview(path) if path else None
                if version != self.detail_version or not self.show_preview: return
                result = content.copy()
                result.append("\n")
                result.append(Text.from_ansi(art) if art else Text(t("预览不可用；可继续整理，或安装 chafa / img2txt。")))
                detail.update(result)
            self.preview_worker = self.run_worker(preview(),group="preview",exclusive=True,exit_on_error=False)

    # ------------------------------------------------------------ 动作

    def action_cursor_down(self) -> None:
        if self.playback: self.action_play_next(); return
        self.query_one("#image-table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        if self.playback: self.action_play_previous(); return
        self.query_one("#image-table", DataTable).action_cursor_up()

    def action_quit_app(self) -> None:
        self.exit()

    def action_quit_interrupt(self) -> None:
        self.exit(return_code=130)

    def action_refresh(self) -> None:
        selected = self._selected_id()
        tree = self.query_one("#nav-tree", Tree)
        tree.clear()
        self._build_tree(tree)
        self._reload()
        if selected is not None:
            self._select_row(selected)
        self.notify(t("已刷新"), timeout=2)

    def action_focus_search(self) -> None:
        self._pause_playback()
        self.query_one("#search-input", Input).focus()

    def action_toggle_navigation(self) -> None:
        left = self.query_one("#left")
        left.display = not left.display
        self.query_one("#nav-tree" if left.display else "#image-table").focus()

    def action_next_page(self) -> None:
        if self.playback:
            self._play_control('goto',position=min(self.total-1,self._queue_offset+self.PAGE_SIZE));return
        if self.page_offset + self.PAGE_SIZE < self.total:
            self.load_images(self._current_sel, offset=self.page_offset + self.PAGE_SIZE)

    def action_previous_page(self) -> None:
        if self.playback:
            self._play_control('goto',position=max(0,self._queue_offset-self.PAGE_SIZE));return
        if self.page_offset:
            self.load_images(self._current_sel, offset=self.page_offset - self.PAGE_SIZE)

    def action_toggle_preview(self) -> None:
        self.show_preview = not self.show_preview
        self._update_detail()

    def action_favorite(self) -> None:
        if self.selected_ids:
            self._ask(t("收藏批次：on 收藏 / off 取消"), lambda value: self._prepare_batch({"kind":"favorite","value":value == "on"}) if value in ("on","off") else None)
            return
        iid = self._selected_id()
        if iid is None:
            return
        try:
            self.access.require_image(self.conn,iid,capability="rate")
            state = ops.set_favorite(self.conn, iid, self.user["id"], None)
        except ServiceError as error:
            self.notify(f"{error.detail} {error.next_step}",severity='error');return
        self.notify(t("#{iid} 收藏 → {v0}", iid=iid, v0=t('是') if state else t('否')), timeout=2)
        self._reload()
        self._select_row(iid)

    def _select_row(self, image_id: int) -> None:
        for i, item in enumerate(self.current_items):
            if int(item["id"]) == image_id:
                self.query_one("#image-table", DataTable).move_cursor(row=i)
                break

    def action_rate(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask(t("评分 0–10；- 清除；回车取消"), lambda v: self._apply_rate(iid,v))

    def _apply_rate(self, iid: int, value: str | None) -> None:
        if value is None or value == "":
            return
        try:
            rating = None if value == "-" else int(value)
            if self.selected_ids:
                self._prepare_batch({"kind":"rating","value":rating}); return
            summary = catalog.rate_image(self.conn,self.access,iid,rating)
        except ServiceError as exc:
            self.notify(f"{exc.detail} {exc.next_step}", severity="error")
            return
        except ValueError:
            self.notify(t("评分必须是 0–10 的整数"), severity="error")
            return
        self.notify(t("#{iid} 评分 → {v0}", iid=iid, v0=summary['my_rating']), timeout=2)
        self._reload()
        self._select_row(iid)

    def action_add_tag(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask(t("标签用空格分隔；名称含空格时加引号"), lambda v: self._apply_tag(iid, v))

    def _apply_tag(self, iid: int, value: str | None) -> None:
        if not value:
            return
        names = shlex.split(value)
        if self.selected_ids:
            self._prepare_batch({"kind":"tags_add","names":names}); return
        self.access.require_image(self.conn,iid,"edit","tag_edit")
        added = ops.add_tags(self.conn, iid, names, self.user["id"])
        self.notify(t("#{iid} 已加 {v0} 个标签", iid=iid, v0=len(added)), timeout=2)
        self._update_detail()

    def action_comment(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask(t("评论内容"), lambda v: self._apply_comment(iid, v))

    def _apply_comment(self, iid: int, value: str | None) -> None:
        if not value:
            return
        self.access.require_image(self.conn,iid,capability="comment")
        ops.add_comment(self.conn, iid, self.user["id"], value)
        self.notify(t("评论已添加"), timeout=2)

    def action_note(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask(t("笔记内容"), lambda v: self._apply_note(iid, v))

    def _apply_note(self, iid: int, value: str | None) -> None:
        if not value:
            return
        self.access.require_image(self.conn,iid,capability="note")
        ops.add_note(self.conn, iid, self.user["id"], value)
        self.notify(t("笔记已添加"), timeout=2)

    def _ask(self, title: str, callback) -> None:
        self._pause_playback()
        def handle(result: str | None) -> None:
            try: callback(result)
            except ServiceError as error: self.notify(f"{error.detail} {error.next_step}",severity="error")
            except (ValueError,OSError) as error: self.notify(t("输入无效，请检查后重试。"),severity="error")
        self.push_screen(PromptScreen(title), handle)

    def check_action(self, action, parameters):
        if action in ("help","quit_interrupt"): return True
        if action in ("play_next","play_previous") and not self.playback: return False
        if isinstance(self.screen,ModalScreen) or isinstance(self.focused,Input):
            own = {binding.action for binding in self.BINDINGS}
            if action in own: return False
        return super().check_action(action,parameters)

    def on_key(self,event):
        if event.key == "escape" and isinstance(self.focused,Input) and not isinstance(self.screen,ModalScreen):
            self.query_one("#image-table",DataTable).focus();event.stop()

    def action_help(self):
        if isinstance(self.screen,HelpScreen): self.pop_screen();return
        self._pause_playback()
        self.push_screen(HelpScreen())

    def action_toggle_detail(self):
        detail = self.query_one("#detail-scroll")
        detail.display = not detail.display

    def action_select_image(self):
        iid = self._selected_id()
        if iid is None: return
        if iid in self.selected_ids: self.selected_ids.remove(iid)
        else: self.selected_ids.add(iid)
        self._selection_marks()
        self._status()

    def action_clear_selection(self):
        self.selected_ids.clear()
        self._selection_marks()
        self._status()

    def _row_key(self,item,index):
        return f"{item['id']}:{self._queue_offset+index}" if self.playback else str(item['id'])

    def _selection_marks(self):
        table = self.query_one("#image-table",DataTable)
        for index,item in enumerate(self.current_items):
            table.update_cell(self._row_key(item,index),"selected","✓" if item['id'] in self.selected_ids else "")

    def _prepare_batch(self,action):
        ids = sorted(self.selected_ids) or ([self._selected_id()] if self._selected_id() is not None else [])
        if not ids: return
        preview = batches.preview(self.conn,self.access,action,image_ids=ids)
        labels = {'rating':t('评分'),'favorite':t('收藏'),'tags_add':t('加标签'),'artist_add':t('关联艺术家'),
                  'album_add':t('加入手工专辑'),'workflow':t('整理状态')}
        value = action.get('value',action.get('names',action.get('artist_id',action.get('album_id'))))
        text = t("固定 {n} 张 · {operation}：{value}",n=preview['total'],operation=labels.get(action['kind'],action['kind']),value=value)
        text += "\n\n" + "\n".join(f"#{item['image_id']} {item['library_name']} / {item['rel_path']}" for item in preview['items'])
        if preview['total'] > len(preview['items']): text += "\n" + t("仅显示前 {n} 张；完整范围已固定。",n=len(preview['items']))
        self._pause_playback()
        def apply(yes):
            if not yes:
                batches.cancel(self.conn,self.access,preview['id']);return
            try:
                batches.queue(self.conn,self.access,preview['id'])
                self._last_batch = preview['id'];self._last_batch_state = None
                self.work_manager.start()
                self.notify(t("批次已排队；Ctrl+J 查看进度与取消。"))
            except ServiceError as error: self.notify(f"{error.detail} {error.next_step}",severity='error')
        self.push_screen(ConfirmScreen(text),apply)

    def action_artist(self):
        entries = ops.list_artists(self.conn,limit=100)
        choices = " · ".join(f"{item['id']}: {item['name']}" for item in entries)
        self._ask(t("输入已有艺术家 ID") + "\n" + choices,
                  lambda value: self._prepare_batch({'kind':'artist_add','artist_id':int(value)}) if value else None)

    def action_album(self):
        entries = ops.list_albums(self.conn,kind='manual')
        choices = " · ".join(f"{item['id']}: {item['name']}" for item in entries)
        self._ask(t("输入手工专辑 ID") + "\n" + choices,
                  lambda value: self._prepare_batch({'kind':'album_add','album_id':int(value)}) if value else None)

    def action_workflow(self):
        self._ask(t("整理状态：done 已整理 / inbox 待整理"),
                  lambda value: self._prepare_batch({'kind':'workflow','value':value}) if value else None)

    def action_jobs(self):
        self._pause_playback()
        self.push_screen(WorkScreen())

    def action_scan(self):
        iid = self._selected_id()
        lid = self._current_sel.get('id') if self._current_sel['kind'] == 'library' else None
        if lid is None and iid is not None:
            lid = self.access.require_image(self.conn,iid)['library_id']
        if lid is None:
            self.notify(t("请先在导航中选择要扫描的库。")); return
        try:
            jobs.submit(self.conn,self.access,'scan',lid,request_key=uuid.uuid4().hex)
            self.work_manager.start()
            self.action_jobs()
        except ServiceError as error: self.notify(f"{error.detail} {error.next_step}",severity='error')

    def undo_batch(self,batch_id):
        def undo():
            with closing(dbm.open_db(self.cfg.db_path)) as conn:
                try:
                    result = batches.undo(conn,self.cfg,self.access,batch_id)
                    self.call_from_thread(self.notify,t("撤销结果：{state}",state=result['state']))
                except ServiceError as error:
                    self.call_from_thread(self.notify,f"{error.detail} {error.next_step}",severity='error')
        self.run_worker(undo,thread=True,exit_on_error=False,group='undo')

    def _poll_work(self):
        if not self._last_batch: return
        try:
            value = batches.get(self.conn,self.access,self._last_batch,limit=1)
            if value['state'] != self._last_batch_state:
                self._last_batch_state = value['state']
                if value['state'] not in ('queued','running','cancelling'):
                    self.notify(t("批次结束：{state}；Ctrl+J 查看逐项结果。",state=value['state']))
                    if not isinstance(self.screen,ModalScreen): self._reload()
        except ServiceError as error:
            self._last_batch = None;self.notify(f"{error.detail} {error.next_step}",severity='error')

    def _reload(self):
        selected = self._selected_id()
        if self.playback: self._display_playback(); return
        else: self.load_images(getattr(self,'_current_sel',{'kind':'all'}))
        if selected is not None: self._select_row(selected)

    # ------------------------------------------------------------ 固定播放会话

    def _pause_playback(self):
        if self.playback and not self.playback['paused']:
            self._play_control("pause",paused=True,paint=False)
        if self.slideshow_timer: self.slideshow_timer.pause()
        self.slideshow_paused = True

    def _stop_playback(self):
        self._pause_playback()
        if self.slideshow_timer: self.slideshow_timer.stop()
        self.slideshow_timer = None
        self.playback = None

    def action_slideshow(self):
        if self.playback:
            self.action_pause_slideshow(); return
        self._ask(t("幻灯片间隔（秒，回车默认 5）"), self._start_slideshow)

    def _start_slideshow(self, value):
        if value is None: return
        try:
            interval = int(value or "5")
            if not 1 <= interval <= 86400: raise ValueError
        except ValueError:
            self.notify(t("请输入 1–86400 的整数秒数"),severity="warning"); return
        query = asdict(self._query_spec)
        source = {key:value for key,value in query.items() if key not in ('query','sort','limit','offset') and value is not None and value is not False}
        state = play.create_for_user(self.conn,self.access,{'source':source,'query':query['query'],'sort':query['sort'],
                                     'order':'sequential','interval':interval,'loop':True})
        self._activate_playback(state,paused=False)

    def _activate_playback(self,state,*,paused=True):
        if self.slideshow_timer: self.slideshow_timer.stop()
        self.playback = state
        if not state['total']:
            self.notify(t("当前列表为空"),severity="warning");self._stop_playback();return
        if state['cursor'] < 0: self._play_control('next',paint=False)
        self._play_control('pause',paused=paused,paint=False)
        self.slideshow_timer = self.set_interval(max(1,state['interval']),self._slideshow_next)
        self.slideshow_paused = paused
        if paused or state['interval'] == 0: self.slideshow_timer.pause()
        self._display_playback()
        if state['warnings']:
            self.notify(t("播放队列 {n} 张；匹配 {matched} 张；上限 10000",n=state['total'],matched=state['matched_total']),severity='warning')

    def _display_playback(self):
        state = self.playback
        self._queue_offset = max(0,state['cursor']) // self.PAGE_SIZE * self.PAGE_SIZE
        ids = play.queue_items(self.conn,state['session_id'],self._queue_offset,self.PAGE_SIZE)
        self.current_items = []
        for iid in ids:
            item = search.get_image(self.conn,iid,self.user['id'],self.access.allowed_libraries(self.conn))
            self.current_items.append(item or {'id':iid,'filename':t('文件缺失'),'my_rating':-1,'favorite':False})
        self.total = state['total'];self.page_offset = self._queue_offset
        self.current_title = t("播放会话")
        self._paint_table()
        self.query_one("#image-table",DataTable).move_cursor(row=max(0,state['cursor']-self._queue_offset))
        if state['unavailable']:
            self._pause_playback();self.notify(t("文件缺失，播放已暂停；可手动跳到下一张。"),severity='warning')

    def _play_control(self,action,*,paint=True,**kwargs):
        if not self.playback: return
        try:
            self.playback = play.navigate(self.conn,self.access,self.playback['token'],action,
                request_key=uuid.uuid4().hex,expected_version=self.playback['version'],**kwargs)
            self.slideshow_paused = self.playback['paused']
            if paint: self._display_playback()
            else: self._status()
        except ServiceError as error:
            if self.slideshow_timer: self.slideshow_timer.pause()
            self.slideshow_paused = True
            self.notify(f"{error.detail} {error.next_step}",severity='error')

    def _slideshow_next(self):
        if self.slideshow_paused or isinstance(self.screen,ModalScreen) or isinstance(self.focused,Input): return
        self._play_control('next')

    def action_play_next(self): self._play_control('next')
    def action_play_previous(self): self._play_control('prev')

    def action_pause_slideshow(self):
        if not self.playback: return
        self._play_control('pause',paused=not self.playback['paused'],paint=False)
        if self.slideshow_timer:
            if self.playback['paused'] or self.playback['interval'] == 0: self.slideshow_timer.pause()
            else: self.slideshow_timer.resume()

    def action_resume_playback(self):
        row = self.conn.execute("SELECT token FROM play_sessions WHERE user_id=? ORDER BY id DESC LIMIT 1",(self.user['id'],)).fetchone()
        if row:
            try: self._activate_playback(play.session_state(self.conn,self.access,row[0]),paused=True)
            except ServiceError as error: self.notify(f"{error.detail} {error.next_step}",severity='error')
        else: self.notify(t("没有可恢复的播放会话"))

    def on_unmount(self):
        self._shutting_down = True
        self._pause_playback()
        if self.preview_worker: self.preview_worker.cancel()
        self.work_manager.close()
        self.conn.close()


def run_tui(cfg: Config) -> None:
    app = PicShelfTUI(cfg)
    app.run()
    if app.return_code:
        raise SystemExit(app.return_code)
