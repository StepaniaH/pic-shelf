"""库扫描：增量导入、移动检测（两阶段：先走完目录再判缺失/移动）、软删除。

阶段：
  1) walk：未变行跳过；变更行重算派生物；全新路径先挂起（pending）
  2) 走完后统计「本轮未见到」的行（gone 候选）
  3) 处理 pending：sha256 命中 gone 候选 → 视为移动（保留原行 id 与全部用户数据）；
     否则插入新行并生成缩略图
  4) 仍未见到的行标记 missing=1（软删除）
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Callable

from . import hashing
from .config import Config
from .coordination import library_write_lock
from .db import utcnow
from .i18n import t

SKIP_DIRS = {
    ".git", ".stfolder", ".stversions", "@eaDir", "$RECYCLE.BIN",
    "System Volume Information", "node_modules", ".Trash", "lost+found",
}


def _iter_image_files(root: Path, onerror=None):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=onerror):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
        )
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext in hashing.IMAGE_EXTS:
                yield Path(dirpath) / fn


def album_name_for(rel_dir: str, library_name: str) -> str:
    return library_name if not rel_dir else Path(rel_dir).name


def _ensure_album(conn: sqlite3.Connection, library_id: int, rel_dir: str, lib_name: str) -> int:
    row = conn.execute(
        "SELECT id FROM albums WHERE library_id=? AND rel_dir=? AND kind='folder'",
        (library_id, rel_dir),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO albums(library_id, name, kind, rel_dir, created_at) VALUES(?,?,?,?,?)",
        (library_id, album_name_for(rel_dir, lib_name), "folder", rel_dir, utcnow()),
    )
    return int(cur.lastrowid)


def scan_library(
    conn: sqlite3.Connection,
    cfg: Config,
    library_id: int,
    *,
    full: bool = False,
    progress: Callable[[dict], None] | None = None,
    check: Callable[[], None] | None = None,
    item_result: Callable[[str, list[str]], None] | None = None,
    only_paths: set[str] | None = None,
) -> dict:
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if lib is None:
        raise ValueError(t("库 #{library_id} 不存在", library_id=library_id))
    root = Path(lib["root_path"]).resolve()
    if not root.is_dir():
        raise ValueError(t("库目录不存在：{root}", root=root))
    with library_write_lock(cfg, root):
        try:
            return _scan_library(conn, cfg, library_id, full=full, progress=progress,
                                 check=check, item_result=item_result, only_paths=only_paths)
        except BaseException:
            conn.rollback()
            raise


def _scan_library(conn, cfg, library_id, *, full=False, progress=None, check=None,
                  item_result=None, only_paths=None) -> dict:
    lib = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    if lib is None:
        raise ValueError(t("库 #{library_id} 不存在", library_id=library_id))
    root = Path(lib["root_path"]).resolve()
    if not root.is_dir():
        raise ValueError(t("库目录不存在：{root}", root=root))

    cur = conn.execute(
        "INSERT INTO scans(library_id, started_at) VALUES(?,?)", (library_id, utcnow())
    )
    scan_id = int(cur.lastrowid)
    conn.commit()

    stats = {"added": 0, "updated": 0, "moved": 0, "missing": 0, "unchanged": 0, "errors": []}
    seen_ids: set[int] = set()
    folder_ids: dict[str, list[int]] = {}
    pending_new: list[tuple[str, str, Path, os.stat_result]] = []

    existing = {
        r["rel_path"]: r
        for r in conn.execute("SELECT * FROM images WHERE library_id=?", (library_id,))
    }
    incomplete = {r["destination"]: json.loads(r["destination_identity"]) for r in conn.execute(
        "SELECT destination,destination_identity FROM import_plan_items "
        "WHERE destination_identity IS NOT NULL AND state NOT IN ('succeeded','skipped')")}

    def keep_existing(row, rel_dir):
        seen_ids.add(int(row["id"]))
        folder_ids.setdefault(rel_dir, []).append(int(row["id"]))

    def report_item(rel, before_errors):
        conn.commit()
        if item_result:
            item_result(rel, stats["errors"][before_errors:])

    if only_paths is not None:
        # A retry has an explicit scope. Other records and folder memberships
        # remain untouched, including files that appeared after submission.
        for rel, row in existing.items():
            if rel not in only_paths:
                seen_ids.add(int(row["id"]))
                if not row["missing"]:
                    parent = Path(rel).parent.as_posix()
                    folder_ids.setdefault("" if parent == "." else parent, []).append(int(row["id"]))

    def walk_error(exc):
        # 无法遍历的子树不等于文件消失，保留其索引与关联。
        failed = Path(exc.filename or root)
        stats["errors"].append(str(exc))
        for rel, row in existing.items():
            if (root / rel).is_relative_to(failed):
                parent = Path(rel).parent.as_posix()
                keep_existing(row, "" if parent == "." else parent)

    # ---------- 阶段 1：walk ----------
    count = 0
    for path in _iter_image_files(root, onerror=walk_error):
        rel = path.relative_to(root).as_posix()
        if only_paths is not None and rel not in only_paths:
            continue
        if check:
            check()
        count += 1
        before_errors = len(stats["errors"])
        rel_dir = Path(rel).parent.as_posix()
        if rel_dir == ".":
            rel_dir = ""
        row = existing.get(rel)
        if row is not None:
            keep_existing(row, rel_dir)
        try:
            if not path.resolve().is_relative_to(root):
                raise ValueError(t("图片位于库目录之外，请检查符号链接"))
            st = path.stat()
            identity = incomplete.get(str(path))
            if identity and st.st_dev == identity["device"] and st.st_ino == identity["inode"]:
                stats["errors"].append(t("导入尚未完成：{path}，请恢复导入计划", path=rel))
                report_item(rel, before_errors)
                continue
        except (OSError, ValueError, RuntimeError) as exc:
            stats["errors"].append(f"{rel}: {exc}")
            report_item(rel, before_errors)
            continue

        try:
            if (
                row is not None
                and not full
                and row["size_bytes"] == st.st_size
                and abs((row["mtime"] or 0) - st.st_mtime) < 1e-6
                and row["thumb_rel"] and (cfg.media_dir / row["thumb_rel"]).is_file()
                and row["preview_rel"] and (cfg.media_dir / row["preview_rel"]).is_file()
            ):
                if row["missing"]:
                    conn.execute("UPDATE images SET missing=0 WHERE id=?", (row["id"],))
                stats["unchanged"] += 1
            elif row is not None:
                sha = hashing.sha256_file(path)
                ph = hashing.phash_file(path)
                thumb, prev, w, h = hashing.make_derivatives(
                    path, cfg.thumbs_dir, cfg.previews_dir, int(row["id"])
                )
                conn.execute(
                    "UPDATE images SET size_bytes=?, mtime=?, sha256=?, phash=?, "
                    "thumb_rel=?, preview_rel=?, width=?, height=?, missing=0 WHERE id=?",
                    (st.st_size, st.st_mtime, sha, ph, thumb, prev, w, h, row["id"]),
                )
                stats["updated"] += 1
            else:
                pending_new.append((rel, rel_dir, path, st))
                continue
        except Exception as exc:
            stats["errors"].append(f"{rel}: {exc}")
        report_item(rel, before_errors)
        if progress and count % 25 == 0:
            progress(dict(stats, processed=count))

    # ---------- 阶段 2：less-候选（本轮未见到的行，按 sha256 索引） ----------
    gone: dict[str, list[int]] = {}
    for rel_path, row in existing.items():
        if int(row["id"]) not in seen_ids and row["sha256"]:
            gone.setdefault(row["sha256"], []).append(int(row["id"]))

    # ---------- 阶段 3：pending 处理（移动 or 新增） ----------
    for rel, rel_dir, path, st in pending_new:
        if check:
            check()
        before_errors = len(stats["errors"])
        try:
            sha = hashing.sha256_file(path)
            candidates = gone.get(sha, [])
            if candidates:
                target = candidates.pop(0)
                conn.execute(
                    "UPDATE images SET rel_path=?, filename=?, ext=?, size_bytes=?, "
                    "mtime=?, missing=0 WHERE id=?",
                    (rel, path.name, path.suffix.lstrip(".").lower(),
                     st.st_size, st.st_mtime, target),
                )
                seen_ids.add(target)
                folder_ids.setdefault(rel_dir, []).append(target)
                stats["moved"] += 1
            else:
                ph = hashing.phash_file(path)
                cur2 = conn.execute(
                    "INSERT INTO images(library_id, rel_path, filename, ext, size_bytes, "
                    "width, height, mtime, sha256, phash, missing, imported_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,0,?)",
                    (library_id, rel, path.name, path.suffix.lstrip(".").lower(),
                     st.st_size, None, None, st.st_mtime, sha, ph, utcnow()),
                )
                iid = int(cur2.lastrowid)
                try:
                    thumb, prev, w, h = hashing.make_derivatives(
                        path, cfg.thumbs_dir, cfg.previews_dir, iid
                    )
                    conn.execute(
                        "UPDATE images SET thumb_rel=?, preview_rel=?, width=?, height=? WHERE id=?",
                        (thumb, prev, w, h, iid),
                    )
                except Exception as exc:
                    stats["errors"].append(t("{rel}: 缩略图生成失败：{exc}", rel=rel, exc=exc))
                seen_ids.add(iid)
                folder_ids.setdefault(rel_dir, []).append(iid)
                stats["added"] += 1
        except Exception as exc:
            stats["errors"].append(f"{rel}: {exc}")
        report_item(rel, before_errors)
    stats["processed"] = count

    # ---------- 阶段 4：软删除 ----------
    if check:
        check()
    for rel_path, row in existing.items():
        if int(row["id"]) not in seen_ids and not row["missing"]:
            conn.execute("UPDATE images SET missing=1 WHERE id=?", (row["id"],))
            stats["missing"] += 1

    # ---------- 专辑物化 ----------
    # 空文件夹也要清理旧成员与封面；不删除专辑本身或手工专辑。
    for album in conn.execute("SELECT rel_dir FROM albums WHERE library_id=? AND kind='folder'", (library_id,)):
        folder_ids.setdefault(album["rel_dir"], [])
    for rel_dir, ids in folder_ids.items():
        album_id = _ensure_album(conn, library_id, rel_dir, lib["name"])
        members = {
            int(r["image_id"])
            for r in conn.execute(
                "SELECT image_id FROM album_images WHERE album_id=?", (album_id,)
            )
        }
        idset = set(ids)
        for stale in members - idset:
            conn.execute(
                "DELETE FROM album_images WHERE album_id=? AND image_id=?",
                (album_id, stale),
            )
        for iid in idset - members:
            conn.execute(
                "INSERT OR IGNORE INTO album_images(album_id, image_id, position, added_at) "
                "VALUES(?,?,0,?)",
                (album_id, iid, utcnow()),
            )
        row = conn.execute(
            "SELECT cover_image_id FROM albums WHERE id=?", (album_id,)
        ).fetchone()
        if row and row["cover_image_id"] not in idset:
            first = conn.execute(
                "SELECT i.id FROM images i JOIN album_images ai ON ai.image_id=i.id "
                "WHERE ai.album_id=? AND i.missing=0 ORDER BY i.rel_path LIMIT 1",
                (album_id,),
            ).fetchone()
            conn.execute(
                "UPDATE albums SET cover_image_id=? WHERE id=?",
                (first["id"] if first else None, album_id),
            )

    conn.execute(
        "UPDATE scans SET finished_at=?, status='done', added=?, updated=?, moved=?, "
        "missing=?, unchanged=?, errors=? WHERE id=?",
        (utcnow(), stats["added"], stats["updated"], stats["moved"],
         stats["missing"], stats["unchanged"],
         json.dumps(stats["errors"][:100], ensure_ascii=False), scan_id),
    )
    conn.commit()
    if item_result and only_paths is not None:
        # Old paths in a move and already-missing paths also belong to the
        # saved work set, even though they did not yield a file during walk.
        present = {rel for rel, _, _, _ in pending_new}
        for rel, row in existing.items():
            if rel in only_paths and not (root / rel).exists() and rel not in present:
                item_result(rel, [])
    if progress:
        progress(dict(stats, done=True))
    return stats
