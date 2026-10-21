"""Keyboard dialogs for local collection management."""
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static

from . import batches, jobs
from .errors import ServiceError
from .i18n import t


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            with VerticalScroll(id="confirm-scroll"):
                yield Static(Text(self.text), id="confirm-text")
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("取消"), id="confirm-no")
                yield Button(t("确认执行"), id="confirm-yes", variant="warning")

    def on_mount(self):
        self.query_one("#confirm-no").focus()

    def on_button_pressed(self, event):
        self.dismiss(event.button.id == "confirm-yes")

    def action_cancel(self):
        self.dismiss(False)


class HelpScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close"), ("f1", "dismiss", "Close")]

    def compose(self):
        with Vertical(id="help-box"):
            with VerticalScroll():
                yield Static(t("浏览\nj/k 移动 · [/] 翻页 · / 搜索 · b 导航 · d 详情 · p 图片预览\n\n整理\nx 选择 · Ctrl+X 清空选择 · r 评分（0–10，- 清除）· f 收藏\nt 标签 · a 艺术家 · l 专辑 · w 整理状态 · c 评论 · n 笔记\n\n播放\ns 开始 · ←/→ 翻图 · 空格暂停/继续 · Ctrl+P 恢复上次播放\n打开搜索、帮助或输入框会暂停播放。\n\n任务\nCtrl+S 扫描 · Ctrl+J 查看任务与批次 · g 刷新\n\nEsc 返回 · q 退出 · F1 帮助"), id="help-text", markup=False)
            yield Button(t("关闭"), id="help-close")

    def on_button_pressed(self, event):
        self.dismiss()


STATES = {"queued":"待执行", "running":"运行中", "cancelling":"取消中", "cancelled":"已取消",
          "succeeded":"已完成", "failed":"失败", "partial":"部分完成", "interrupted":"已中断",
          "planned":"待确认", "preview":"待确认", "undone":"已撤销", "undo_conflict":"撤销冲突",
          "pending":"待处理", "writing":"写入中", "ready":"已写入", "skipped":"已跳过",
          "conflict":"冲突", "undoing":"撤销中", "applied":"已应用",
          "unchanged":"未变化", "creating":"创建中"}
KINDS = {"scan":"扫描", "import":"导入", "rebuild":"重建缩略图与指纹"}
PHASES = {**STATES, **KINDS, "starting":"启动中", "waiting_for_library":"等待图库",
          "discover":"发现文件", "scan_files":"扫描文件", "importing":"导入中",
          "finished":"处理结束", "restored":"已恢复", "apply":"应用更改", "undo":"撤销更改"}


class WorkScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close")]

    def compose(self):
        with Vertical(id="work-box"):
            yield Static(t("任务与批量修改（Enter 查看详情）"), markup=False)
            yield DataTable(id="work-table")
            yield Static(id="work-detail", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("取消任务"), id="work-cancel")
                yield Button(t("重试"), id="work-retry")
                yield Button(t("撤销修改"), id="work-undo")
                yield Button(t("上一页"), id="work-prev")
                yield Button(t("下一页"), id="work-next")
                yield Button(t("关闭"), id="work-close")

    def on_mount(self):
        self.page_offset = 0
        self.records = {}
        table = self.query_one("#work-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(t("类型"), t("状态"), t("进度"), "ID")
        self.refresh_rows()
        table.focus()
        self.set_interval(1, self.refresh_rows)

    def refresh_rows(self):
        try:
            app = self.app
            selected = self.selected()
            table = self.query_one("#work-table", DataTable)
            page = jobs.list_jobs(app.conn, app.access, offset=self.page_offset, limit=20)
            edits = batches.list_batches(app.conn, app.access, offset=self.page_offset, limit=20)
            self.query_one("#work-next", Button).disabled = page['next_offset'] is None and edits['next_offset'] is None
            self.query_one("#work-prev", Button).disabled = not self.page_offset
            self.records = {}
            table.clear()
            for kind, entries in (("job",page['items']),("batch",edits['items'])):
                for value in entries:
                    key = kind + ":" + value['id']
                    self.records[key] = (kind,value)
                    done = value.get('completed',sum(n for state,n in value.get('counts',{}).items() if state != 'pending'))
                    total = value['total'] if value['total'] is not None else t("待统计")
                    table.add_row(t("批量整理") if kind == "batch" else t(KINDS.get(value['kind'],value['kind'])), t(STATES.get(value['state'],value['state'])),
                                  f"{done}/{total}", value['id'][:12], key=key)
                    if selected and selected[1]['id'] == value['id']:
                        table.move_cursor(row=len(self.records)-1)
            self.show_detail()
        except ServiceError as error:
            self.query_one("#work-detail", Static).update(f"{error.detail} {error.next_step}")

    def selected(self):
        table = self.query_one("#work-table", DataTable)
        if not table.row_count or not self.records:
            return None
        key = list(self.records)[min(table.cursor_row, len(self.records)-1)]
        return self.records[key]

    def on_data_table_row_highlighted(self, event):
        self.show_detail()

    def on_data_table_row_selected(self,event):
        selected = self.selected()
        if selected:
            self.app.push_screen(WorkDetailsScreen(selected[0],selected[1]['id']))

    def show_detail(self):
        selected = self.selected()
        if not selected:
            self.query_one("#work-detail", Static).update(t("暂无任务"))
            return
        kind, value = selected
        error = value.get('error') or {}
        text = error.get('detail') or value.get('error_code') or ""
        self.query_one("#work-detail", Static).update(f"{t('阶段')}：{t(PHASES.get(value['phase'],value['phase']))} · {text}")
        self.query_one("#work-undo", Button).disabled = kind != "batch" or value['state'] not in ('succeeded','partial','cancelled','interrupted','undo_conflict')
        self.query_one("#work-cancel", Button).disabled = value['state'] not in ('queued','running','cancelling','planned')
        self.query_one("#work-retry", Button).disabled = value['state'] not in ('failed','partial','cancelled','interrupted')

    def on_button_pressed(self, event):
        name = event.button.id
        if name == 'work-close':
            self.dismiss(); return
        if name in ('work-prev','work-next'):
            self.page_offset = max(0,self.page_offset + (20 if name == 'work-next' else -20))
            self.refresh_rows(); return
        selected = self.selected()
        if not selected:
            return
        kind,value = selected
        try:
            if name == 'work-undo':
                self.app.push_screen(ConfirmScreen(t("撤销这次修改，保留之后的改动。")),
                                     lambda yes: self.app.undo_batch(value['id']) if yes else None)
            elif name == 'work-cancel':
                (jobs.cancel if kind == 'job' else batches.cancel)(self.app.conn,self.app.access,value['id'])
            elif name == 'work-retry':
                if kind == 'job': jobs.retry(self.app.conn,self.app.access,value['id'])
                else: batches.queue(self.app.conn,self.app.access,value['id'],retry=True)
                self.app.work_manager.start()
            self.refresh_rows()
        except ServiceError as error:
            self.app.notify(f"{error.detail} {error.next_step}",severity='error')


class WorkDetailsScreen(ModalScreen):
    BINDINGS = [("escape","dismiss","Close")]

    def __init__(self,kind,identity):
        super().__init__()
        self.kind,self.identity,self.page_offset = kind,identity,0

    def compose(self):
        with Vertical(id="work-box"):
            with VerticalScroll():
                yield Static(id="work-items",markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button(t("上一页"),id="items-prev")
                yield Button(t("下一页"),id="items-next")
                yield Button(t("关闭"),id="items-close")

    def on_mount(self): self.show_page()

    def show_page(self):
        try:
            getter = jobs.get_job if self.kind == 'job' else batches.get
            value = getter(self.app.conn,self.app.access,self.identity,offset=self.page_offset,limit=100)
            total = value.get('items_total',value['total'])
            lines = [t("逐项结果") + f" {self.page_offset + 1 if total else 0}–{min(total,self.page_offset+100)} / {total}"]
            for item in value['items']:
                reason = item.get('error_code')
                if reason == 'batch_changed': reason = t('图片已被其他操作修改')
                line = f"{item.get('rel_path',item.get('item_key',''))} · {t(STATES.get(item['state'],item['state']))}"
                lines.append(line + (f" · {reason}" if reason else ""))
                if item.get('detail'): lines.append(item['detail'])
            self.query_one('#work-items',Static).update(Text('\n'.join(lines)))
            self.query_one('#items-prev',Button).disabled = not self.page_offset
            self.query_one('#items-next',Button).disabled = self.page_offset+100 >= total
        except ServiceError as error:
            self.query_one('#work-items',Static).update(f"{error.detail} {error.next_step}")

    def on_button_pressed(self,event):
        if event.button.id == 'items-close': self.dismiss();return
        self.page_offset = max(0,self.page_offset + (100 if event.button.id == 'items-next' else -100))
        self.show_page()
