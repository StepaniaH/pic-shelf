"""终端界面（Textual）：树形浏览 + 图片列表 + 详情 + 评分/标签/收藏 + 幻灯片连播。

快捷键：
  j/k 移动 ｜ enter 查看详情刷新 ｜ p 预览图 ｜ r 评分 ｜ f 收藏 ｜ t 加标签
  c 评论 ｜ n 笔记 ｜ s 幻灯片 ｜ space 暂停/继续 ｜ / 搜索 ｜ g 刷新 ｜ q 退出
"""
from __future__ import annotations

import shutil
import subprocess
import sqlite3
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, Static, Tree

from . import auth, db as dbm, indexer, ops, search
from .config import Config


def _render_preview(path: Path, width: int = 72, height: int = 26) -> str | None:
    img2txt = shutil.which("img2txt") or shutil.which("chafa")
    if img2txt is None:
        return None
    if img2txt.endswith("chafa"):
        cmd = [img2txt, "-f", "symbols", "--size", f"{width}x{height}", str(path)]
    else:
        cmd = [img2txt, "-W", str(width), "-H", str(height), "-f", "ansi", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


class PromptScreen(ModalScreen[str | None]):
    """单行输入弹窗。"""

    def __init__(self, title: str, default: str = "", placeholder: str = ""):
        super().__init__()
        self._title = title
        self._default = default
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="prompt-box"):
            yield Label(self._title)
            yield Input(value=self._default, placeholder=self._placeholder, id="prompt-input")

    def on_mount(self) -> None:
        self.query_one("#prompt-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def key_escape(self) -> None:
        self.dismiss(None)


class PicShelfTUI(App):
    TITLE = "Pic Shelf"

    CSS = """
    #body { height: 1fr; }
    #left { width: 32; border-right: solid $panel; }
    #right { width: 1fr; }
    #image-table { height: 1fr; }
    #detail { height: 16; border-top: solid $panel; padding: 0 1; }
    #search-input { dock: top; }
    #prompt-box { width: 60; height: auto; padding: 1 2; background: $surface; border: thick $primary; margin: 4 8; }
    """

    BINDINGS = [
        Binding("q", "quit_app", "退出"),
        Binding("j", "cursor_down", "下移"),
        Binding("k", "cursor_up", "上移"),
        Binding("p", "toggle_preview", "预览"),
        Binding("r", "rate", "评分"),
        Binding("f", "favorite", "收藏"),
        Binding("t", "add_tag", "加标签"),
        Binding("c", "comment", "评论"),
        Binding("n", "note", "笔记"),
        Binding("s", "slideshow", "幻灯片"),
        Binding("space", "pause_slideshow", "暂停/继续"),
        Binding("g", "refresh", "刷新"),
        Binding("slash", "focus_search", "搜索"),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.conn: sqlite3.Connection = dbm.open_db(cfg.db_path)
        self.user = self._acting_user()
        self.allowed = auth.visible_library_ids(self.conn, self.user)
        self.current_items: list[dict] = []
        self.current_title = "全部图片"
        self.slideshow_timer = None
        self.show_preview = False

    def _acting_user(self):
        row = self.conn.execute(
            "SELECT * FROM users WHERE is_admin=1 AND disabled=0 ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("没有管理员用户。先运行：picshelf init --admin <名字>")
        return row

    # ------------------------------------------------------------ 布局

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Input(placeholder="搜索：tag / -tag / artist:名 / album:ID / rating:>=8 / fav:", id="search-input")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Tree("浏览", id="nav-tree")
            with Vertical(id="right"):
                yield DataTable(id="image-table")
                with VerticalScroll(id="detail-scroll"):
                    yield Static("选中图片后显示详情", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        tree = self.query_one("#nav-tree", Tree)
        tree.root.expand()
        self._build_tree(tree)
        table = self.query_one("#image-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "文件名", "尺寸", "评分", "收藏")
        table.focus()  # 默认焦点在列表，字母快捷键才不会被搜索框吃掉
        self.load_images({"kind": "all"})

    def _build_tree(self, tree: Tree) -> None:
        conn = self.conn
        for lib in conn.execute("SELECT * FROM libraries ORDER BY name"):
            if self.allowed is not None and lib["id"] not in self.allowed:
                continue
            lib_node = tree.root.add(f"库：{lib['name']}", data={"kind": "library", "id": lib["id"]}, expand=False)
            for album in conn.execute(
                "SELECT * FROM albums WHERE library_id=? ORDER BY kind DESC, name", (lib["id"],)
            ):
                lib_node.add_leaf(
                    f"{'★ ' if album['kind'] == 'manual' else ''}{album['name']}",
                    data={"kind": "album", "id": album["id"]},
                )
        artists = conn.execute("SELECT * FROM artists ORDER BY name").fetchall()
        if artists:
            art_node = tree.root.add("艺术家", expand=False)
            for a in artists:
                art_node.add_leaf(a["name"], data={"kind": "artist", "id": a["id"]})
        tags = conn.execute(
            "SELECT t.id, t.name, COUNT(it.image_id) n FROM tags t LEFT JOIN image_tags it ON it.tag_id=t.id "
            "GROUP BY t.id ORDER BY n DESC, t.name LIMIT 100"
        ).fetchall()
        if tags:
            tag_node = tree.root.add("标签", expand=False)
            for t in tags:
                tag_node.add_leaf(f"{t['name']} ({t['n']})", data={"kind": "tag", "name": t["name"]})
        tree.root.add_leaf("★ 我的收藏", data={"kind": "favorites"})
        tree.root.add_leaf("高分（≥8）", data={"kind": "highrating"})

    # ------------------------------------------------------------ 加载

    def _filter_clauses(self, sel: dict) -> tuple[list[str], dict]:
        ew: list[str] = []
        ep: dict = {}
        kind = sel.get("kind")
        if kind == "library":
            ew.append("i.library_id = :t_lib")
            ep["t_lib"] = sel["id"]
        elif kind == "album":
            ew.append("EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id AND ai.album_id=:t_alb)")
            ep["t_alb"] = sel["id"]
        elif kind == "artist":
            ew.append("EXISTS(SELECT 1 FROM image_artists ia WHERE ia.image_id=i.id AND ia.artist_id=:t_art)")
            ep["t_art"] = sel["id"]
        elif kind == "tag":
            ew.append(
                "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
                "WHERE it.image_id=i.id AND lower(tg.name)=lower(:t_tag))"
            )
            ep["t_tag"] = sel["name"]
        elif kind == "favorites":
            ew.append("EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)")
        elif kind == "highrating":
            ew.append(
                "COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id AND r.user_id=:uid),0) >= 8"
            )
        return ew, ep

    def load_images(self, sel: dict, query: str | None = None) -> None:
        self._current_sel = sel
        q = query if query is not None else self.query_one("#search-input", Input).value or ""
        ew, ep = self._filter_clauses(sel)
        res = search.search_images(
            self.conn, self.user["id"], self.allowed,
            query=q, sort="path", limit=500, extra_where=ew or None, extra_params=ep or None,
        )
        self.current_items = res["items"]
        self.current_title = {
            "all": "全部图片", "library": "库", "album": "专辑", "artist": "艺术家",
            "tag": "标签", "favorites": "收藏", "highrating": "高分（≥8）",
        }.get(sel.get("kind"), "")
        table = self.query_one("#image-table", DataTable)
        table.clear()
        for item in self.current_items:
            table.add_row(
                str(item["id"]),
                item["filename"][:40],
                f"{item.get('width') or '?'}x{item.get('height') or '?'}",
                "—" if item["my_rating"] < 0 else str(item["my_rating"]),
                "★" if item["favorite"] else "",
                key=str(item["id"]),
            )
        self.sub_title = f"{self.current_title} — {len(self.current_items)} 张"
        self._update_detail()

    # ------------------------------------------------------------ 事件

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            self.load_images(event.node.data)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.load_images(getattr(self, "_current_sel", {"kind": "all"}), query=event.value)
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
        detail = self.query_one("#detail", Static)
        iid = self._selected_id()
        if iid is None:
            detail.update("（无图片）")
            return
        d = search.get_image(self.conn, iid, self.user["id"], self.allowed)
        if d is None:
            detail.update("（图片已被移除）")
            return
        tags = "、".join(t["name"] for t in d["tags"]) or "（无标签）"
        artists = "、".join(a["name"] for a in d["artists"]) or "（无）"
        albums = "、".join(a["name"] for a in d["albums"]) or "（无）"
        rating = "未评" if d["my_rating"] < 0 else str(d["my_rating"])
        lines = [
            f"#{d['id']} {d['filename']}  {d['width']}x{d['height']}  {d['ext']}",
            f"评分：我的 {rating} / 平均 {d['avg_rating'] or '—'}   收藏：{'★' if d['favorite'] else '—'}",
            f"标签：{tags}",
            f"艺术家：{artists}       专辑：{albums}",
        ]
        if self.show_preview:
            path = self._current_path(iid)
            art = _render_preview(path) if path else None
            if art:
                lines.append(art)
        detail.update("\n".join(lines))

    # ------------------------------------------------------------ 动作

    def action_cursor_down(self) -> None:
        self.query_one("#image-table", DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#image-table", DataTable).action_cursor_up()

    def action_quit_app(self) -> None:
        self.exit()

    def action_refresh(self) -> None:
        self.load_images(getattr(self, "_current_sel", {"kind": "all"}))
        self.notify("已刷新", timeout=2)

    def action_focus_search(self) -> None:
        self.query_one("#search-input", Input).focus()

    def action_toggle_preview(self) -> None:
        self.show_preview = not self.show_preview
        self._update_detail()

    def action_favorite(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        state = ops.set_favorite(self.conn, iid, self.user["id"], None)
        self.notify(f"#{iid} 收藏 → {'是' if state else '否'}", timeout=2)
        self.load_images(getattr(self, "_current_sel", {"kind": "all"}))
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
        self._ask("评分（0–10，直接回车取消）", lambda v: self._apply_rate(iid, v))

    def _apply_rate(self, iid: int, value: str | None) -> None:
        if value is None or value == "":
            return
        try:
            summary = ops.set_rating(self.conn, iid, self.user["id"], int(value))
        except ValueError:
            self.notify("评分必须是 0–10 的整数", severity="error")
            return
        self.notify(f"#{iid} 评分 → {summary['my_rating']}", timeout=2)
        self.load_images(getattr(self, "_current_sel", {"kind": "all"}))
        self._select_row(iid)

    def action_add_tag(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask("添加标签（空格分隔多个）", lambda v: self._apply_tag(iid, v))

    def _apply_tag(self, iid: int, value: str | None) -> None:
        if not value:
            return
        names = [n for n in value.split() if n]
        added = ops.add_tags(self.conn, iid, names, self.user["id"])
        self.notify(f"#{iid} 已加 {len(added)} 个标签", timeout=2)
        self._update_detail()

    def action_comment(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask("评论内容", lambda v: self._apply_comment(iid, v))

    def _apply_comment(self, iid: int, value: str | None) -> None:
        if not value:
            return
        ops.add_comment(self.conn, iid, self.user["id"], value)
        self.notify("评论已添加", timeout=2)

    def action_note(self) -> None:
        iid = self._selected_id()
        if iid is None:
            return
        self._ask("笔记内容", lambda v: self._apply_note(iid, v))

    def _apply_note(self, iid: int, value: str | None) -> None:
        if not value:
            return
        ops.add_note(self.conn, iid, self.user["id"], value)
        self.notify("笔记已添加", timeout=2)

    def _ask(self, title: str, callback) -> None:
        def handle(result: str | None) -> None:
            callback(result)

        self.push_screen(PromptScreen(title), handle)

    # ------------------------------------------------------------ 幻灯片

    def action_slideshow(self) -> None:
        if self.slideshow_timer is not None:
            self.action_pause_slideshow()
            return
        self._ask("幻灯片间隔（秒，回车默认 5）", self._start_slideshow)

    def _start_slideshow(self, value: str | None) -> None:
        interval = 5.0
        try:
            if value:
                interval = max(1.0, float(value))
        except ValueError:
            pass
        if not self.current_items:
            self.notify("当前列表为空", severity="warning")
            return
        self.slideshow_timer = self.set_interval(interval, self._slideshow_next)
        self.notify(f"幻灯片开始（{interval:g}s 一张，空格暂停，q 退出）", timeout=3)

    def _slideshow_next(self) -> None:
        table = self.query_one("#image-table", DataTable)
        n = len(self.current_items)
        if n == 0:
            return
        idx = (table.cursor_row + 1) % n  # 循环轮播
        table.move_cursor(row=idx)

    def action_pause_slideshow(self) -> None:
        if self.slideshow_timer is not None:
            self.slideshow_timer.stop()
            self.slideshow_timer = None
            self.notify("幻灯片已暂停", timeout=2)

    def on_unmount(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass


def run_tui(cfg: Config) -> None:
    PicShelfTUI(cfg).run()
