"""库管理：注册、列举、统计；以及重复检测（精确 sha256 / 相似 pHash）。"""
from __future__ import annotations

import sqlite3
import os
from pathlib import Path
import tempfile
import uuid

from . import search
from .access import AccessContext
from .auth import grant_default_library
from .config import Config
from .coordination import library_write_lock
from .db import utcnow
from .errors import ServiceError
from .i18n import t


def _validate_mode(mode, default_import_mode):
    if mode not in (None, "indexed", "managed") or default_import_mode not in ("hardlink", "copy", "move"):
        raise ServiceError("library_mode_invalid", t("库用途或默认导入方式无效"),
                           next_step=t("请选择 indexed 或 managed；导入方式为 hardlink、copy 或 move"))


def _library_root(root_path: str, *, writable=False) -> Path:
    try:
        root = Path(root_path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("not a directory")
        with os.scandir(root) as entries:
            next(entries, None)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ServiceError("library_path_invalid", t("无法读取服务端图片目录"),
                           next_step=t("请检查目录是否存在、读取权限及循环链接；容器内请使用挂载后的容器路径")) from exc
    if writable:
        try:
            with tempfile.TemporaryFile(dir=root):
                pass
        except OSError as exc:
            raise ServiceError("library_root_unwritable", t("托管库目录不可写"),
                               next_step=t("请修复目录写入权限，或选择只索引模式")) from exc
    return root


def _check_overlap(conn, root: Path, *, exclude=None):
    def identities(path):
        result = set()
        for parent in (path, *path.parents):
            try:
                stat = parent.stat()
                result.add((stat.st_dev, stat.st_ino))
            except OSError:
                continue
        return result
    root_stat = root.stat()
    root_id = (root_stat.st_dev, root_stat.st_ino)
    ancestors = identities(root)
    for row in conn.execute("SELECT id,root_path,root_device,root_inode FROM libraries"):
        if row['id'] == exclude:
            continue
        other = Path(row["root_path"])
        try:
            other = other.resolve()
            stat = other.stat()
            other_id = (stat.st_dev, stat.st_ino)
        except (OSError, RuntimeError):
            other_id = (row["root_device"], row["root_inode"])
        if (root.is_relative_to(other) or other.is_relative_to(root) or
                other_id in ancestors or root_id in identities(other)):
            raise ServiceError("library_root_overlap", t("该目录与已注册库重复或重叠"), status=409,
                               next_step=t("请使用已有库，或选择互不包含的目录；符号链接不会创建独立图库"),
                               objects={"library_id": row["id"]})


def create_library(conn: sqlite3.Connection, name: str, root_path: str,
                   description: str | None = None, *, mode: str | None = None,
                   default_import_mode: str = "hardlink") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError(t("库名不能为空"))
    _validate_mode(mode, default_import_mode)
    root = _library_root(root_path, writable=mode == "managed")
    # Serialize validation with insertion so concurrent aliases cannot register twice.
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        _check_overlap(conn, root)
        cur = conn.execute(
            "INSERT INTO libraries(name, root_path, description, created_at,mode,default_import_mode) VALUES(?,?,?,?,?,?)",
            (name, str(root), description, utcnow(), mode, default_import_mode),
        )
        library_id = int(cur.lastrowid)
    grant_default_library(conn, library_id)
    return library_id


def create_managed_library(conn, cfg: Config, name: str, description=None, *, default_import_mode='hardlink'):
    """Allocate an application-owned destination when no server path is chosen."""
    root = cfg.data_dir.resolve() / 'libraries' / uuid.uuid4().hex
    if root.parent.resolve() != root.parent:
        raise ServiceError('library_path_invalid', t('托管库目录不可写'),
                           next_step=t('请修复目录写入权限，或选择只索引模式'))
    try:
        root.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise ServiceError('library_root_unwritable', t('托管库目录不可写'),
                           next_step=t('请修复目录写入权限，或选择只索引模式')) from exc
    try:
        return create_library(conn, name, str(root), description, mode='managed', default_import_mode=default_import_mode)
    except BaseException:
        # Remove only our newly allocated, still empty directory on failure.
        try:
            root.rmdir()
        except OSError:
            pass
        raise


def update_library(conn: sqlite3.Connection, cfg: Config, access: AccessContext,
                   library_id: int, **changes) -> dict:
    access.require_library(conn, library_id, "manage")
    if set(changes) - {"name", "description", "mode", "default_import_mode"}:
        raise ServiceError("library_field_invalid", t("不能通过库设置修改根目录"),
                           next_step=t("请使用已核验的根目录迁移流程"))
    library = get_library(conn, library_id)
    if library is None:
        raise ServiceError("library_not_found", t("库不存在"), status=404,
                           next_step=t("请刷新库列表后重试"))
    if "name" in changes:
        changes["name"] = (changes["name"] or "").strip()
        if not changes["name"]:
            raise ValueError(t("库名不能为空"))
    mode = changes.get("mode", library["mode"])
    default = changes.get("default_import_mode", library["default_import_mode"])
    _validate_mode(mode, default)
    root = _library_root(library["root_path"], writable=mode == "managed")
    with library_write_lock(cfg, root):
        access.require_library(conn, library_id, "manage")
        if changes:
            with conn:
                conn.execute("UPDATE libraries SET " + ",".join(f"{key}=?" for key in changes) + " WHERE id=?",
                             [*changes.values(), library_id])
    return get_library(conn, library_id)


def require_importable(library):
    if library["mode"] == "indexed":
        raise ServiceError("library_indexed", t("只索引库不接收导入文件"), status=409,
                           next_step=t("请新建托管库，或明确将此库用途改为 managed 后重新预演"))


def get_library(conn: sqlite3.Connection, library_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
    return dict(row) if row else None


def find_library(conn: sqlite3.Connection, name_or_id: str) -> dict | None:
    if str(name_or_id).isdigit():
        return get_library(conn, int(name_or_id))
    row = conn.execute(
        "SELECT * FROM libraries WHERE name=?", (name_or_id,)
    ).fetchone()
    return dict(row) if row else None


def list_libraries(conn: sqlite3.Connection, allowed: list[int] | None = None) -> list[dict]:
    sql = (
        "SELECT l.*, "
        "(SELECT COUNT(*) FROM images i WHERE i.library_id=l.id AND i.missing=0) AS image_count, "
        "(SELECT i.id FROM images i WHERE i.library_id=l.id AND i.missing=0 "
        "AND i.thumb_rel IS NOT NULL AND i.scan_error_code IS NULL ORDER BY i.id DESC LIMIT 1) AS cover_image_id, "
        "(SELECT COUNT(*) FROM albums a WHERE a.library_id=l.id) AS album_count "
        "FROM libraries l"
    )
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" WHERE l.id IN ({marks})"
        params += allowed
    sql += " ORDER BY l.id"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def global_stats(conn: sqlite3.Connection, allowed: list[int] | None = None) -> dict:
    visible, params = search.visible_images_sql(allowed)
    prefix = f"WITH visible AS ({visible}) "

    def one(sql: str) -> int:
        row = conn.execute(prefix + sql, params).fetchone()
        return int(row[0] or 0)

    library_filter = ""
    if allowed is not None:
        ids = ",".join(str(int(x)) for x in allowed)
        library_filter = f" AND id IN ({ids})" if ids else " AND 0=1"

    return {
        "images": one("SELECT COUNT(*) FROM visible"),
        "size_bytes": one("SELECT SUM(size_bytes) FROM visible"),
        "libraries": one("SELECT COUNT(*) FROM libraries WHERE 1=1" + library_filter),
        "albums": one("SELECT COUNT(*) FROM albums WHERE library_id IN (SELECT id FROM libraries WHERE 1=1" + library_filter + ")"),
        "artists": one("SELECT COUNT(*) FROM artists") if allowed is None else one(
            "SELECT COUNT(DISTINCT artist_id) FROM image_artists WHERE image_id IN (SELECT id FROM visible)"),
        "tags": one("SELECT COUNT(*) FROM tags") if allowed is None else one(
            "SELECT COUNT(DISTINCT tag_id) FROM image_tags WHERE image_id IN (SELECT id FROM visible)"),
        "users": one("SELECT COUNT(*) FROM users WHERE disabled=0") if allowed is None else 0,
        "favorites": one("SELECT COUNT(*) FROM favorites WHERE image_id IN (SELECT id FROM visible)"),
        "ratings": one("SELECT COUNT(*) FROM ratings WHERE image_id IN (SELECT id FROM visible)"),
    }


def exact_dupes(conn: sqlite3.Connection, allowed: list[int] | None = None) -> list[dict]:
    sql = (
        "SELECT sha256, COUNT(*) AS n, SUM(size_bytes) AS bytes, "
        "GROUP_CONCAT(id) AS ids FROM images WHERE missing=0 AND sha256 IS NOT NULL"
    )
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    sql += " GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY n DESC"
    out = []
    for r in conn.execute(sql, params).fetchall():
        ids = [int(x) for x in str(r["ids"]).split(",")]
        out.append({"sha256": r["sha256"], "count": int(r["n"]),
                    "size_bytes": int(r["bytes"] or 0), "image_ids": ids})
    return out


def _bitmap_candidates(buckets, version, value, threshold, count):
    # At distance r, at least 16-r of the sixteen 4-bit bands must match.
    # Each integer is a bitmap of representative indices; five bit planes count
    # matches for all representatives together without building Python sets.
    planes = [0] * 5
    for shift in range(0, 64, 4):
        carry = buckets.get((version, shift, (value >> shift) & 15), 0)
        for bit in range(5):
            previous = planes[bit]
            planes[bit] ^= carry
            carry &= previous
            if not carry:
                break
    needed = 16 - threshold
    equal, greater = (1 << count) - 1, 0
    for bit in range(4, -1, -1):
        if needed & (1 << bit):
            equal &= planes[bit]
        else:
            greater |= equal & planes[bit]
            equal &= ~planes[bit]
    matches = greater | equal
    while matches:
        first = matches & -matches
        yield first.bit_length() - 1
        matches ^= first


def similar_dupes(conn: sqlite3.Connection, allowed: list[int] | None = None,
                  threshold: int = 8) -> list[dict]:
    """按首个代表图分组；每个成员与代表图的距离不超过 threshold。

    每个哈希只解析一次，重复哈希直接命中已选组。半径不超过 8 时按
    相同位段数量生成完备候选，再核对完整距离并选最早代表。最坏仍为 O(n²)。
    """
    if type(threshold) is not int or not 0 <= threshold <= 64:
        raise ValueError(t("相似距离必须在 0–64 之间"))
    sql = "SELECT id,phash,phash_version,filename FROM images WHERE missing=0 AND phash IS NOT NULL AND scan_error_code IS NULL"
    params: list = []
    if allowed is not None:
        if not allowed:
            return []
        marks = ",".join("?" * len(allowed))
        sql += f" AND library_id IN ({marks})"
        params += allowed
    rows = conn.execute(sql + " ORDER BY id", params).fetchall()
    groups: list[list[dict]] = []
    representatives: list[tuple[int, int]] = []
    placed: dict[tuple[int, int], int] = {}
    buckets: dict[tuple[int, int, int], list[int]] = {}
    bitmaps: dict[tuple[int, int, int], int] = {}
    versions: dict[int, list[int]] = {}
    # Sparse bands are cheaper at small radii; bitmaps avoid the broad candidate
    # unions produced by the shorter bands at radii 6–8.
    bands = []
    if 0 < threshold <= 5:
        shift = 0
        for index in range(threshold + 1):
            width = 64 // (threshold + 1) + int(index < 64 % (threshold + 1))
            bands.append((shift, (1 << width) - 1))
            shift += width
    for r in rows:
        ph = int(r["phash"], 16)
        version = int(r["phash_version"])
        key = (version, ph)
        target = placed.get(key)
        known = versions.get(version, [])
        if target is None and threshold:
            candidates = known
            if len(known) > 128:
                if bands:
                    matches = [buckets.get((version, shift, (ph >> shift) & mask), []) for shift, mask in bands]
                    if max(map(len, matches), default=0) < len(known) * .4:
                        candidates = sorted({index for bucket in matches for index in bucket})
                elif threshold <= 8:
                    candidates = (known[index] for index in _bitmap_candidates(bitmaps, version, ph, threshold, len(known)))
            for gi in candidates:
                algorithm, representative = representatives[gi]
                if algorithm == version and (representative ^ ph).bit_count() <= threshold:
                    target = gi
                    break
        entry = {"id": int(r["id"]), "phash": ph, "filename": r["filename"]}
        if target is None:
            target = len(groups)
            representatives.append((version, ph))
            groups.append([entry])
            versions.setdefault(version, []).append(target)
            if bands:
                for shift, mask in bands:
                    buckets.setdefault((version, shift, (ph >> shift) & mask), []).append(target)
            elif 0 < threshold <= 8:
                bit = 1 << (len(versions[version]) - 1)
                for shift in range(0, 64, 4):
                    band = (version, shift, (ph >> shift) & 15)
                    bitmaps[band] = bitmaps.get(band, 0) | bit
        else:
            groups[target].append(entry)
        placed[key] = target
    return [
        {"count": len(g), "images": [{"id": e["id"], "filename": e["filename"],
                                      "distance": (g[0]["phash"] ^ e["phash"]).bit_count()}
                                     for e in g]}
        for g in groups if len(g) > 1
    ]
