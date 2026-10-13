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

from . import hashing, metadata
from .config import Config
from .coordination import library_write_lock
from .db import utcnow
from .errors import ServiceError
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

    stat = root.stat()
    root_identity = (stat.st_dev, stat.st_ino)
    if lib["root_device"] is not None and (lib["root_device"], lib["root_inode"]) != root_identity:
        raise ServiceError("library_root_changed", t("库目录身份已变化，未标记缺失图片"), status=409,
                           next_step=t("请恢复原目录或挂载；如需迁移，请先核对根目录和图片内容"))

    cur = conn.execute(
        "INSERT INTO scans(library_id, started_at) VALUES(?,?)", (library_id, utcnow())
    )
    scan_id = int(cur.lastrowid)
    conn.commit()
    try:
        if progress:
            progress({"scan_id": scan_id, "phase": "starting"})
        return _scan_files(conn, cfg, library_id, lib, root, root_identity, scan_id, full=full,
                           progress=progress, check=check, item_result=item_result, only_paths=only_paths)
    except BaseException:
        conn.rollback()
        conn.execute("UPDATE scans SET finished_at=?,status='interrupted' WHERE id=?", (utcnow(), scan_id))
        conn.commit()
        raise


def _scan_files(conn, cfg, library_id, lib, root, root_identity, scan_id, *, full=False,
                progress=None, check=None, item_result=None, only_paths=None):
    def guard():
        if check:
            check()
        try:
            current = root.stat()
            valid = (Path(lib["root_path"]).resolve() == root and root.is_dir() and
                     (current.st_dev, current.st_ino) == root_identity)
        except (OSError, RuntimeError):
            valid = False
        if not valid:
            raise ServiceError("library_root_changed", t("库目录身份已变化，未标记缺失图片"), status=409,
                               next_step=t("请恢复原目录或挂载；如需迁移，请先核对根目录和图片内容"))

    stats = {"added": 0, "updated": 0, "moved": 0, "missing": 0, "unchanged": 0, "errors": [], "ambiguous_moves": []}
    seen_ids: set[int] = set()
    folder_ids: dict[str, list[int]] = {}
    pending_new: list[tuple[str, str, Path, os.stat_result]] = []
    blocked_paths: set[str] = set()

    existing = {
        r["rel_path"]: r
        for r in conn.execute("SELECT * FROM images WHERE library_id=?", (library_id,))
    }
    root_confirmed = not existing or lib["root_device"] is not None
    known_hashes = {row["sha256"] for row in existing.values() if row["sha256"]}
    incomplete = {r["destination"]: (None if r['image_id'] is not None and not r['catalog_published'] else
                                      json.loads(r["destination_identity"])) for r in conn.execute(
        "SELECT i.destination,i.destination_identity,i.image_id,i.catalog_published FROM import_plan_items i "
        "JOIN import_plans p ON p.id=i.plan_id WHERE "
        "(i.destination_identity IS NOT NULL AND i.state NOT IN ('succeeded','skipped')) "
        "OR (i.image_id IS NOT NULL AND i.catalog_published=0 AND p.started_at IS NOT NULL)")}

    def keep_existing(row, rel_dir):
        seen_ids.add(int(row["id"]))
        folder_ids.setdefault(rel_dir, []).append(int(row["id"]))

    def report_item(rel, before_errors):
        conn.commit()
        if item_result:
            item_result(rel, stats["errors"][before_errors:])

    def record_error(row, exc, code="image_read_error"):
        if row is not None:
            if isinstance(exc, (PermissionError, FileNotFoundError)):
                code = "image_read_error"
            conn.execute("UPDATE images SET scan_error_code=?,scan_error_detail=? WHERE id=?",
                         (getattr(exc, "code", code), str(exc)[:4096], row["id"]))

    def verify_unchanged(path, before, sha):
        after = path.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or hashing.sha256_file(path) != sha):
            raise ServiceError("image_changed", t("扫描期间图片内容变化，请保存文件后重新扫描"), retryable=True)

    def encode(image_id, path, st, sha):
        ph = hashing.phash_file(path)
        thumb, preview, width, height = hashing.make_derivatives(path, cfg.thumbs_dir, cfg.previews_dir, image_id)
        verify_unchanged(path, st, sha)
        conn.execute(
            "UPDATE images SET size_bytes=?,mtime=?,sha256=?,phash=?,thumb_rel=?,preview_rel=?,width=?,height=?,"
            "missing=0,scan_error_code=NULL,scan_error_detail=NULL,phash_version=?,derivative_version=?,verified_at=? WHERE id=?",
            (st.st_size, st.st_mtime, sha, ph, thumb, preview, width, height,
             hashing.PHASH_VERSION if ph is not None else 0, hashing.DERIVATIVE_VERSION, utcnow(), image_id))

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
        before_errors = len(stats["errors"])
        stats["errors"].append(str(exc))
        for rel, row in existing.items():
            if (root / rel).is_relative_to(failed):
                guard()
                blocked_paths.add(rel)
                parent = Path(rel).parent.as_posix()
                keep_existing(row, "" if parent == "." else parent)
                record_error(row, exc, "directory_unreadable")
                report_item(rel, before_errors)

    # ---------- 阶段 1：walk ----------
    count = 0
    for path in _iter_image_files(root, onerror=walk_error):
        guard()
        rel = path.relative_to(root).as_posix()
        if only_paths is not None and rel not in only_paths:
            continue
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
            if str(path) in incomplete and (identity is None or
                    (st.st_dev == identity["device"] and st.st_ino == identity["inode"])):
                stats["errors"].append(t("导入尚未完成：{path}，请恢复导入计划", path=rel))
                report_item(rel, before_errors)
                continue
        except (OSError, ValueError, RuntimeError) as exc:
            stats["errors"].append(f"{rel}: {exc}")
            record_error(row, exc)
            report_item(rel, before_errors)
            continue

        try:
            if not root_confirmed and row is not None and row["sha256"]:
                root_confirmed = hashing.sha256_file(path) == row["sha256"]
            if (
                row is not None
                and not full
                and not row["scan_error_code"]
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
                encode(int(row["id"]), path, st, sha)
                stats["updated"] += 1
            else:
                pending_new.append((rel, rel_dir, path, st))
                continue
        except Exception as exc:
            stats["errors"].append(f"{rel}: {exc}")
            record_error(row, exc, "image_decode_error")
        report_item(rel, before_errors)
        if progress and count % 25 == 0:
            progress(dict(stats, processed=count))

    # ---------- 阶段 2：less-候选（本轮未见到的行，按 sha256 索引） ----------
    gone: dict[str, list[int]] = {}
    for rel_path, row in existing.items():
        if int(row["id"]) not in seen_ids and row["sha256"]:
            gone.setdefault(row["sha256"], []).append(int(row["id"]))

    # ---------- 阶段 3：pending 处理（移动 or 新增） ----------
    pending_hashes = {}
    by_hash = {}
    for rel, _, path, _ in pending_new:
        guard()
        try:
            sha = hashing.sha256_file(path)
            if sha in known_hashes:
                root_confirmed = True
            pending_hashes[rel] = sha
            by_hash.setdefault(sha, []).append(rel)
        except OSError as exc:
            before_errors = len(stats["errors"])
            stats["errors"].append(f"{rel}: {exc}")
            report_item(rel, before_errors)
    ambiguous = {sha for sha, paths in by_hash.items() if gone.get(sha) and (len(paths) != 1 or len(gone[sha]) != 1)}
    for sha in sorted(ambiguous):
        stats["ambiguous_moves"].append({"sha256": sha, "image_ids": sorted(gone[sha]), "paths": sorted(by_hash[sha])})
    for rel, rel_dir, path, st in pending_new:
        guard()
        if rel not in pending_hashes:
            continue
        before_errors = len(stats["errors"])
        try:
            sha = pending_hashes[rel]
            if sha in ambiguous:
                raise ServiceError("ambiguous_move", t("多个相同内容文件无法唯一匹配原记录：{path}；请恢复原路径后重扫", path=rel))
            verify_unchanged(path, st, sha)
            candidates = gone.get(sha, [])
            if candidates:
                target = candidates.pop(0)
                previous = conn.execute("SELECT * FROM images WHERE id=?", (target,)).fetchone()
                conn.execute(
                    "UPDATE images SET rel_path=?, filename=?, ext=?, size_bytes=?, "
                    "mtime=?, missing=0,scan_error_code=NULL,scan_error_detail=NULL WHERE id=?",
                    (rel, path.name, path.suffix.lstrip(".").lower(),
                     st.st_size, st.st_mtime, target),
                )
                seen_ids.add(target)
                folder_ids.setdefault(rel_dir, []).append(target)
                stats["moved"] += 1
                conn.commit()
                if (full or not previous["thumb_rel"] or not previous["preview_rel"] or
                        not (cfg.media_dir / previous["thumb_rel"]).is_file() or
                        not (cfg.media_dir / previous["preview_rel"]).is_file()):
                    try:
                        encode(target, path, st, sha)
                    except Exception as exc:
                        record_error(previous, exc, "image_decode_error")
                        stats["errors"].append(f"{rel}: {exc}")
            else:
                cur2 = conn.execute(
                    "INSERT INTO images(library_id, rel_path, filename, ext, size_bytes, "
                    "width, height, mtime, sha256, phash, missing, imported_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,0,?)",
                    (library_id, rel, path.name, path.suffix.lstrip(".").lower(),
                     st.st_size, None, None, st.st_mtime, sha, None, utcnow()),
                )
                iid = int(cur2.lastrowid)
                metadata.record_indexed_import(conn, library_id, iid, path, sha)
                conn.commit()  # Decode does not hold SQLite's writer lock.
                try:
                    encode(iid, path, st, sha)
                except Exception as exc:
                    stats["errors"].append(t("{rel}: 缩略图生成失败：{exc}", rel=rel, exc=exc))
                    record_error({"id": iid}, exc, "image_decode_error")
                seen_ids.add(iid)
                folder_ids.setdefault(rel_dir, []).append(iid)
                stats["added"] += 1
        except Exception as exc:
            stats["errors"].append(f"{rel}: {exc}")
        report_item(rel, before_errors)
    stats["processed"] = count

    # ---------- 阶段 4：软删除 ----------
    guard()
    if not root_confirmed:
        raise ServiceError("library_root_unverified", t("首次核对根目录时未找到匹配的旧图片，未标记缺失"), status=409,
                           next_step=t("请检查挂载和库路径，恢复可核对的原图片后重新扫描"))
    if lib["root_device"] is None:
        conn.execute("UPDATE libraries SET root_device=?,root_inode=? WHERE id=?", (*root_identity, library_id))
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
            if rel in only_paths and rel not in blocked_paths and not (root / rel).exists() and rel not in present:
                item_result(rel, [])
    if progress:
        progress(dict(stats, done=True))
    return stats
