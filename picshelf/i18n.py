"""Bilingual (Chinese/English) message catalog and language resolution.

Convention: the Chinese message template is the message ID. English
translations live in EN below; a missing translation falls back to the
Chinese msgid.  Usage:  t("共 {n} 张", n=3)  →  "共 3 张" (zh) / "3 images" (en)

Language resolution order: explicit argument → PICSHELF_LANG env →
LC_ALL/LANG env (zh* → zh) → default "en".
"""
from __future__ import annotations

import os
from contextvars import ContextVar

SUPPORTED = ("zh", "en")
_current: ContextVar[str | None] = ContextVar("picshelf_lang", default=None)


def resolve_lang(explicit: str | None = None) -> str:
    if explicit in SUPPORTED:
        return explicit
    env = (os.environ.get("PICSHELF_LANG")
           or os.environ.get("LC_ALL")
           or os.environ.get("LANG")
           or "").lower()
    if env.startswith("zh"):
        return "zh"
    return "en"


def set_lang(lang: str | None = None) -> str:
    v = resolve_lang(lang)
    _current.set(v)
    return v


def current_lang() -> str:
    return _current.get() or resolve_lang()


def push_lang(lang: str | None):
    """Set the language for the current context; returns a token for pop_lang()."""
    return _current.set(resolve_lang(lang))


def pop_lang(token) -> None:
    """Restore the previous language context."""
    _current.reset(token)


def t(msgid: str, **kw) -> str:
    """Translate a Chinese msgid to the current language (identity for zh)."""
    s = msgid
    if current_lang() == "en":
        s = EN.get(msgid, msgid)
    if kw:
        try:
            s = s.format(**kw)
        except Exception:
            pass
    return s


# ---------------------------------------------------------------- EN catalog
# Keyed by the Chinese message template. Appended per module as the codebase
# gets localized; every user-visible Chinese string must have an entry here.
EN: dict[str, str] = {
    "Pic Shelf — 图片库": "Pic Shelf — Image Library",
    "#{v0:>6}  {fav} 评分:{rating:>3}  {dims:>11}  {v1}": "#{v0:>6}  {fav} rating:{rating:>3}  {dims:>11}  {v1}",
    "数据目录（默认：项目内 ./data）": "Data directory (default: ./data inside the project)",
    "界面语言：zh / en（默认跟随系统 locale）": "UI language: zh / en (default: system locale)",
    "数据目录：{v0}": "Data dir: {v0}",
    "数据库：  {v0}": "Database: {v0}",
    "初始化完成。下一步：picshelf library add <库名> <目录> && picshelf scan <库名>": "Initialized. Next: picshelf library add <name> <directory> && picshelf scan <name>",
    "创建管理员用户名": "Admin username to create",
    "从标准输入读取密码（自动化用）": "Read password from stdin (for automation)",
    "开发模式自动重载": "Auto-reload in development",
    "已注册库 #{lib_id} {name} → {root}": "Registered library #{lib_id} {name} → {root}",
    "下一步：picshelf scan {name}": "Next: picshelf scan {name}",
    "目录不存在时自动创建（托管库推荐）": "Create the directory if missing (recommended for managed libraries)",
    "扫描库 #{v0} {v1} → {v2}": "Scanning library #{v0} {v1} → {v2}",
    "完成：新增 {v0}，更新 {v1}，移动 {v2}，未变 {v3}，标记缺失 {v4}，错误 {v5}": "Done: added {v0}, updated {v1}, moved {v2}, unchanged {v3}, missing {v4}, errors {v5}",
    "强制重算所有哈希与缩略图": "Force recompute of all hashes and thumbnails",
    "共 {v0} 张（显示前 {v1}）": "{v0} images (showing first {v1})",
    "检索表达式": "Search expression",
    "库名或 ID": "Library name or ID",
    "专辑 ID": "Album ID",
    "艺术家 ID": "Artist ID",
    "标签全名": "Full tag name",
    "我的评分下限": "Minimum personal rating",
    "仅收藏": "Favorites only",
    "以哪个用户视角（默认首个管理员）": "Act as this user (default: first admin)",
    "输出 JSON": "Output JSON",
    "未评": "Not rated",
    "路径:   {abspath}": "Path:    {abspath}",
    "尺寸:   {v0}x{v1}  大小: {v2}  格式: {v3}": "Size:    {v0}x{v1}  bytes: {v2}  format: {v3}",
    "评分:   我的 {rating}   平均 {v0}（{v1} 人）  收藏: {v2}": "Rating:  mine {rating}   avg {v0} ({v1} users)  favorite: {v2}",
    "标签:   {v0}": "Tags:    {v0}",
    "艺术家: {v0}": "Artists: {v0}",
    "专辑:   {v0}": "Albums:  {v0}",
    "库:     {v0}": "Library: {v0}",
    "评论 {v0} 条 / 笔记 {v1} 条": "{v0} comments / {v1} notes",
    "只输出文件绝对路径（脚本用）": "Print only absolute file paths (for scripts)",
    "#{image_id} 我的评分 → {v0}（平均 {v1}）": "#{image_id} my rating → {v0} (avg {v1})",
    "#{image_id} 收藏 → {v0}": "#{image_id} favorite → {v0}",
    "取消收藏": "Remove from favorites",
    "#{image_id} 已移除 {n} 个标签": "#{image_id} removed {n} tag(s)",
    "已添加评论 #{v0}": "Comment #{v0} added",
    "已添加笔记 #{v0}": "Note #{v0} added",
    "艺术家 #{v0} {v1}": "Artist #{v0} {v1}",
    "#{image_id} 已取消关联 {artist_name}": "#{image_id} unlinked {artist_name}",
    "队列 {v0} 张（顺序：{order}{v1}）": "Queue: {v0} images (order: {order}{v1})",
    "--title=Pic Shelf 播放": "--title=Pic Shelf playback",
    "交给 mpv 播放（→ 下一张 / ← 上一张 / q 退出）": "Handing playback to mpv (→ next / ← prev / q quit)",
    "每张停留秒数（0=手动翻页）": "Seconds per image (0 = manual advance)",
    "播放数量上限（0=全部）": "Max images to play (0 = all)",
    "循环（轮播）": "Loop (carousel)",
    "similar 模式的 pHash 距离阈值": "pHash distance threshold for similar mode",
    "用口令加密备份": "Encrypt the backup with a passphrase",
    "包含缩略图/预览图": "Include thumbnails/previews",
    "已恢复数据库到：{v0}": "Database restored to: {v0}",
    "覆盖现有数据库（先自动备份现有库）": "Overwrite the existing database (auto-backup first)",
    "预演：源 {v0} → 库 #{v1} {v2}（模式：{mode}）": "Dry run: source {v0} → library #{v1} {v2} (mode: {mode})",
    "共 {v0} 个文件：新导入 {new_n}，跳过 {v1}（重复/已存在），错误 {v2}": "{v0} files: new {new_n}, skipped {v1} (duplicate/existing), errors {v2}",
    "硬链": "hardlink",
    "复制": "copy",
    "移动": "move",
    "跳过": "skip",
    "错误": "error",
    "执行完成：硬链 {v0}，复制 {v1}，移动 {v2}，跳过 {v3}，错误 {v4}": "Done: hardlinked {v0}, copied {v1}, moved {v2}, skipped {v3}, errors {v4}",
    "hardlink=原文件不动且零额外空间（跨盘自动降级 copy）；copy=复制留档；move=移入整理": "hardlink=originals untouched, zero extra space (falls back to copy across devices); copy=keep a copy; move=move files in",
    "文件名规范化（去掉文件系统危险字符）": "Normalize filenames (strip filesystem-hostile characters)",
    "真正执行（默认只预演）": "Actually execute (default: dry run only)",
    "跳过执行确认": "Skip the confirmation prompt",
    "为 {username} 设置密码": "Set a password for {username}",
    "已创建用户 #{uid} {username}{v0}": "User created #{uid} {username}{v0}",
    "为 {username} 设置新密码": "Set a new password for {username}",
    "密码已更新": "Password updated",
    "已创建组 #{v0} {name}（{v1}）": "Group created #{v0} {name} ({v1})",
    "逗号分隔的能力：view,rate,comment,note,tag_edit,manage_library,admin": "Comma-separated capabilities: view,rate,comment,note,tag_edit,manage_library,admin",
    "组 {group_name} 对库 {v0} 的访问级别 → {access}": "Group {group_name} access to library {v0} → {access}",
    "已移除组 {group_name} 对库 {v0} 的授权": "Removed group {group_name} grants on library {v0}",
    "图片 {v0} 张（{v1}）  库 {v2}  专辑 {v3}  艺术家 {v4}  标签 {v5}  用户 {v6}  评分 {v7}  收藏 {v8}": "Images {v0} ({v1})  libraries {v2}  albums {v3}  artists {v4}  tags {v5}  users {v6}  ratings {v7}  favorites {v8}",
    "数据目录尚未初始化（找不到 {v0}）。先运行：picshelf init --admin <管理员名>": "Data directory not initialized (missing {v0}). Run: picshelf init --admin <name>",
    "没有管理员用户。先运行：picshelf init --admin <管理员名>": "No admin user found. Run: picshelf init --admin <name>",
    "尚未初始化。先运行：picshelf init --admin <管理员名>": "Not initialized yet. Run: picshelf init --admin <name>",
    "尚无库。用 picshelf library add <库名> <目录> 添加。": "No libraries yet. Add one with: picshelf library add <name> <directory>",
    "#{v0:>3}  {v1:<16} 图片 {v2:>6} 张  专辑 {v3:>4}  {v4}": "#{v0:>3}  {v1:<16} images {v2:>6}  albums {v3:>4}  {v4}",
    "找不到库：{library_name}": "Library not found: {library_name}",
    "图片不存在：#{image_id}": "Image not found: #{image_id}",
    "  (笔记) [{v0}] {v1}": "  (note) [{v0}] {v1}",
    "#{image_id} 已添加 {v0} 个标签：": "#{image_id} added {v0} tag(s):",
    "（无）": "(none)",
    "#{v0:>4}  {v1:>6} 张  {v2}": "#{v0:>4}  {v1:>6} imgs  {v2}",
    "艺术家不存在：{artist_name}（先 artist add）": "Artist not found: {artist_name} (run artist add first)",
    "艺术家不存在：{artist_name}": "Artist not found: {artist_name}",
    "队列为空：没有符合条件的图片": "Empty queue: no images matched",
    "找不到 mpv（可改用 --player terminal 或 --player list）": "mpv not found (use --player terminal or --player list)",
    "设置备份口令": "Set backup passphrase",
    "已导出加密备份：{out}（{v0}）": "Encrypted backup exported: {out} ({v0})",
    "已导出备份：{out}（{v0}）": "Backup exported: {out} ({v0})",
    "输入备份口令": "Enter backup passphrase",
    "数据库文件无效": "Invalid database file",
    "数据目录已有数据库。如确认覆盖请加 --force（现有库会先自动备份）": "A database already exists. Pass --force to overwrite (auto-backup first)",
    "现有数据库已备份到：{bak}": "Current database backed up to: {bak}",
    "找不到库：{library_name}（先运行 picshelf library add）": "Library not found: {library_name} (run picshelf library add first)",
    "  …（共 {v0} 项，只显示前 20）": "  … ({v0} items total, showing first 20)",
    "这是预演，未改动任何文件。确认无误后加 --apply 执行。": "This was a dry run — nothing changed. Add --apply to execute.",
    "没有需要导入的文件。": "Nothing to import.",
    "已扫描入库：新增 {v0}，更新 {v1}，未变 {v2}": "Scanned: added {v0}, updated {v1}, unchanged {v2}",
    "用户已存在：{username}": "User already exists: {username}",
    "#{v0:>3}  {v1:<16} {v2:<12} 组：{v3}": "#{v0:>3}  {v1:<16} {v2:<12} groups: {v3}",
    "用户不存在：{username}": "User not found: {username}",
    "组不存在：{group_name}": "Group not found: {group_name}",
    "库不存在：{library_name}": "Library not found: {library_name}",
    "组或库不存在": "Group or library not found",
    "用户不存在或已禁用：{name}": "User not found or disabled: {name}",
    "从 stdin 读取密码失败": "Failed to read password from stdin",
    "用户已存在，跳过：{admin}": "User already exists, skipping: {admin}",
    "为管理员 {admin} 设置密码": "Set password for admin {admin}",
    "已创建管理员：{admin}": "Admin created: {admin}",
    "目录不存在：{root}（加 --mkdir 可自动创建）": "Directory does not exist: {root} (pass --mkdir to create it)",
    "库名已存在：{name}": "Library name already exists: {name}",
    "  ... 已处理 {n} 个文件": "  ... processed {n} files",
    "找不到库：{library}": "Library not found: {library}",
    "相似组（{v0} 张）：": "Similar groups ({v0} images):",
    "没有完全重复的文件。": "No exact duplicates found.",
    "{v0} 份相同（可省 {v1}）：": "{v0} identical copies (saves {v1}):",
    "备份格式不正确（缺少 picshelf.db）": "Invalid backup format (missing picshelf.db)",
    "管理员": "admin",
    "已禁用": "disabled",
    "组已存在：{name}": "Group already exists: {name}",
    "是": "Yes",
    "否": "No",
    "  #{v0:>6}  距离{v1:>3}  {v2}": "  #{v0:>6}  dist {v1:>3}  {v2}",
    "口令错误或备份文件损坏": "Wrong passphrase or corrupted backup",
    "确认把 {new_n} 个文件【移动】进库？原位置将不再保留（建议先备份）": "Move {new_n} files INTO the library? Originals will be gone from their old location (back up first).",
    "已取消": "Cancelled",
    "确认导入 {new_n} 个文件？": "Import {new_n} files?",
    "回车下一张 / b 上一张 / q 退出": "Enter: next / b: prev / q: quit",
    "已退出播放": "Playback stopped",
    "（{interval}s 自动）": "({interval}s auto)",
    "全部图片": "All images",
    "{v0} — {v1} 张": "{v0} — {v1} images",
    "退出": "Quit",
    "下移": "Move down",
    "上移": "Move up",
    "预览": "Preview",
    "评分": "Rate",
    "收藏": "Favorite",
    "加标签": "Tag",
    "评论": "Comment",
    "笔记": "Note",
    "幻灯片": "Slideshow",
    "暂停/继续": "Pause/resume",
    "刷新": "Refresh",
    "搜索": "Search",
    "文件名": "Filename",
    "尺寸": "Size",
    "★ 我的收藏": "★ Favorites",
    "高分（≥8）": "High rated (≥8)",
    "（无标签）": "(no tags)",
    "评分：我的 {rating} / 平均 {v0}   收藏：{v1}": "Rating: mine {rating} / avg {v0}   favorite: {v1}",
    "标签：{tags}": "Tags: {tags}",
    "艺术家：{artists}       专辑：{albums}": "Artists: {artists}       Albums: {albums}",
    "已刷新": "Refreshed",
    "#{iid} 收藏 → {v0}": "#{iid} favorite → {v0}",
    "评分（0–10，直接回车取消）": "Rating (0–10, Enter to cancel)",
    "#{iid} 评分 → {v0}": "#{iid} rating → {v0}",
    "添加标签（空格分隔多个）": "Add tags (space-separated)",
    "#{iid} 已加 {v0} 个标签": "#{iid} added {v0} tag(s)",
    "评论内容": "Comment text",
    "评论已添加": "Comment added",
    "笔记内容": "Note text",
    "笔记已添加": "Note added",
    "幻灯片间隔（秒，回车默认 5）": "Slideshow interval (seconds, Enter = 5)",
    "幻灯片开始（{interval:g}s 一张，空格暂停，q 退出）": "Slideshow started ({interval:g}s per image, space to pause, q to quit)",
    "没有管理员用户。先运行：picshelf init --admin <名字>": "No admin user found. Run: picshelf init --admin <name>",
    "库：{v0}": "Library: {v0}",
    "艺术家": "Artists",
    "标签": "Tags",
    "（无图片）": "(no images)",
    "（图片已被移除）": "(image was removed)",
    "当前列表为空": "The current list is empty",
    "幻灯片已暂停": "Slideshow paused",
    "搜索：tag / -tag / artist:名 / album:ID / rating:>=8 / fav:": "Search: tag / -tag / artist:name / album:ID / rating:>=8 / fav:",
    "库": "Libraries",
    "专辑": "Albums",
    "评分必须是 0–10 的整数": "Rating must be an integer 0–10",
    "浏览": "Browse",
    "选中图片后显示详情": "Select an image to see details",
    "图片 #{image_id} 不存在": "Image #{image_id} not found",
    "标签不能为空": "Tag cannot be empty",
    "新名称不能为空": "New name cannot be empty",
    "标签已存在：{new_name}": "Tag already exists: {new_name}",
    "艺术家名不能为空": "Artist name cannot be empty",
    "艺术家 #{artist_id} 不存在": "Artist #{artist_id} not found",
    "评论内容不能为空": "Comment cannot be empty",
    "评论 #{comment_id} 不存在": "Comment #{comment_id} not found",
    "只能删除自己的评论": "You can only delete your own comments",
    "笔记内容不能为空": "Note cannot be empty",
    "笔记 #{note_id} 不存在": "Note #{note_id} not found",
    "只能删除自己的笔记": "You can only delete your own notes",
    "专辑名不能为空": "Album name cannot be empty",
    "专辑 #{album_id} 不存在": "Album #{album_id} not found",
    "文件夹专辑自动维护，不能手工增删；请创建手工专辑": "Folder albums are maintained automatically; create a manual album instead",
    "文件夹专辑自动维护，不能手工增删": "Folder albums are maintained automatically",
    "评分范围 0–10": "Rating range is 0–10",
    "口令不能为空": "Password cannot be empty",
    "管理员组": "Administrators",
    "普通成员组": "Members",
    "未知模式：{mode}（可选 {v0}）": "Unknown mode: {mode} (choices: {v0})",
    "源目录不存在：{source_root}": "Source directory does not exist: {source_root}",
    "源目录与目标库目录不能互为父子关系（含相同）": "Source and target library directories must not be nested or equal",
    "{src}: 目标路径越界，已拒绝": "{src}: target path escapes the library, refused",
    "库内已有相同内容": "Identical content already in the library",
    "与本次计划中的另一文件内容相同": "Identical to another file in this plan",
    "目标位置已有相同文件": "A file with the same name already exists at the target",
    "库 #{library_id} 不存在": "Library #{library_id} not found",
    "库目录不存在：{root}": "Library directory does not exist: {root}",
    "{rel}: 缩略图生成失败：{exc}": "{rel}: thumbnail generation failed: {exc}",
    "库名不能为空": "Library name cannot be empty",
    "忽略空参数：{token}": "Ignoring empty term: {token}",
    "缺少权限：manage_library": "Missing permission: manage_library",
    "目录不存在：{root}": "Directory does not exist: {root}",
    "库不存在": "Library not found",
    "缺少权限：tag_edit": "Missing permission: tag_edit",
    "专辑不存在": "Album not found",
    "图片不存在": "Image not found",
    "文件不存在": "File not found",
    "未知媒体类型": "Unknown media type",
    "库名已存在：{v0}": "Library name already exists: {v0}",
    "用户名不能为空": "Username cannot be empty",
    "密码至少 6 位": "Password must be at least 6 characters",
    "用户名已存在：{username}": "Username already exists: {username}",
    "用户不存在": "User not found",
    "组名不能为空": "Group name cannot be empty",
    "组不存在": "Group not found",
    "系统组不可删除": "System groups cannot be deleted",
    "不能禁用自己的账号": "You cannot disable your own account",
    "不能取消自己的管理员身份": "You cannot remove your own admin flag",
    "组名已存在：{name}": "Group name already exists: {name}",
    "系统组（admins/members）的能力不可修改": "System groups (admins/members) have fixed capabilities",
    "access 必须是 view / edit / manage": "access must be one of view / edit / manage",
    "用户名或密码错误": "Wrong username or password",
    "原密码不正确": "Current password is incorrect",
    "新密码至少 6 位": "New password must be at least 6 characters",
    "order 必须是 sequential / shuffle / random": "order must be sequential / shuffle / random",
    "播放会话不存在": "Playback session not found",
    "执行需要 confirm=true 明确确认": "Execution requires confirm=true",
    "未登录": "Not signed in",
    "登录已过期，请重新登录": "Session expired, please sign in again",
    "无权访问该库": "No access to this library",
    "缺少权限：{cap}": "Missing permission: {cap}",
    "Pic Shelf — 自托管图片库（Web / TUI / CLI）。": "Pic Shelf — self-hosted image library (Web / TUI / CLI).",
    "初始化数据目录：建库、生成密钥、创建默认组与管理员。": "Initialize the data directory: create the DB, generate the secret key, seed default groups and an admin.",
    "启动 Web 服务（默认仅绑定 127.0.0.1）。": "Start the web server (binds to 127.0.0.1 by default).",
    "库管理。": "Library management.",
    "注册一个图片库（只索引，不移动文件）。": "Register an image library (index in place, never moves files).",
    "列出所有库。": "List all libraries.",
    "扫描/更新一个库（增量）。": "Scan / update a library (incremental).",
    "列表 / 检索图片。": "List / search images.",
    "查看单张图片的完整信息。": "Show full details of one image.",
    "给图片评分（0–10）。": "Rate an image (0–10).",
    "收藏 / 取消收藏。": "Add / remove favorite.",
    "图片标签操作。": "Tag operations on images.",
    "添加标签：picshelf tag add 12 色彩:红色 场景:夜晚": "Add tags: picshelf tag add 12 color:red scene:night",
    "移除标签。": "Remove a tag.",
    "列出标签。": "List tags.",
    "给图片添加评论。": "Add a comment to an image.",
    "给图片添加笔记。": "Add a note to an image.",
    "艺术家管理。": "Artist management.",
    "播放：服务端生成队列（跨专辑/按艺术家/标签/评分），交给 mpv / 终端 / 仅列表。": "Playback: the server builds the queue (cross-album / by artist / tags / rating) and hands it to mpv / terminal / list only.",
    "重复检测（exact=sha256 相同；similar=pHash 相似）。": "Duplicate detection (exact=identical sha256; similar=close pHash).",
    "导出备份（数据库快照，可选加密）。": "Export a backup (database snapshot, optionally encrypted).",
    "从备份恢复数据库。": "Restore the database from a backup.",
    "把外部目录导入托管的库：预演优先，--apply 执行，完成后自动扫描入库。": "Import an external directory into a managed library: dry-run first, --apply to execute, auto-scans afterwards.",
    "用户管理。": "User management.",
    "用户组管理。": "Group management.",
    "全局统计。": "Global statistics.",
    "启动终端界面（TUI）。": "Start the terminal UI (TUI)."
}
