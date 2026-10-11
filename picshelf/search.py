"""搜索：语法解析 → 带 ACL 过滤的 SQL。

语法（danbooru-lite）：
  tag 或 裸词      匹配 tag 名（含命名空间，如 `作者:xxx`）或文件名子串
  -token           取反
  artist:名字      艺术家（名称/别名子串）
  album:名|id|none 专辑
  rating:>=8       我的评分（未评为 0）
  avg:>=8          平均分
  fav: / fav:false 我的收藏
  ext:png,jpg      扩展名（逗号=OR）
  file:子串        仅文件名
所有值支持逗号列表 = OR；引号包裹可含空格。
"""
from __future__ import annotations

import shlex
import sqlite3
from dataclasses import dataclass, field
from .i18n import t

SORTS = {
    "newest": "i.id DESC",
    "oldest": "i.id ASC",
    "path": "i.rel_path ASC",
    "rating": "my_rating DESC, i.id DESC",
    "avg": "avg_rating DESC, i.id DESC",
    "random": "RANDOM()",
}

_KEYWORDS = ("tag", "artist", "album", "rating", "avg", "fav", "ext", "file")


@dataclass
class ParsedQuery:
    clauses: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse(query: str) -> ParsedQuery:
    out = ParsedQuery()
    try:
        tokens = shlex.split(query or "")
    except ValueError:
        tokens = (query or "").split()
    for token in tokens:
        tok = token.strip()
        if not tok:
            continue
        neg = False
        if tok.startswith("-") and len(tok) > 1:
            neg, tok = True, tok[1:]
        key, val = None, tok
        low = tok.lower()
        for k in _KEYWORDS:
            if low == k or low == k + ":":
                key, val = k, ""
                break
            if low.startswith(k + ":"):
                key, val = k, tok[len(k) + 1:]
                break
        if key is None:
            key = "term"
        values = [v.strip() for v in val.split(",") if v.strip()]
        if key == "fav" and not values:
            values = ["true"]
        if not values:
            out.errors.append(t("忽略空参数：{token}", token=token))
            continue
        out.clauses.append({"kind": key, "values": values, "neg": neg})
    return out


def _like(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_op(v: str) -> tuple[str, float] | None:
    for op in (">=", "<=", ">", "<", "="):
        if v.startswith(op):
            body = v[len(op):]
            try:
                return op, float(body)
            except ValueError:
                return None
    try:
        return "=", float(v)
    except ValueError:
        return None


def build_where(
    parsed: ParsedQuery,
    user_id: int,
    allowed_library_ids: list[int] | None,
) -> tuple[str, dict]:
    where: list[str] = ["i.missing = 0"]
    params: dict = {"uid": user_id}
    pcount = 0

    def nparam(value) -> str:
        nonlocal pcount
        name = f"p{pcount}"
        pcount += 1
        params[name] = value
        return f":{name}"

    if allowed_library_ids is not None:
        if not allowed_library_ids:
            where.append("0=1")
        else:
            marks = ",".join(nparam(x) for x in allowed_library_ids)
            where.append(f"i.library_id IN ({marks})")

    for c in parsed.clauses:
        kind, values, neg = c["kind"], c["values"], c["neg"]
        piece: str | None = None

        if kind in ("tag", "term"):
            subs = []
            for v in values:
                tag_expr = (
                    "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
                    "WHERE it.image_id=i.id AND lower(tg.name)=lower(" + nparam(v) + "))"
                )
                if kind == "tag":
                    subs.append(f"({tag_expr})")
                else:
                    subs.append(
                        f"({tag_expr} OR i.filename LIKE {nparam('%' + _like(v) + '%')} ESCAPE '\\')"
                    )
            piece = "(" + " OR ".join(subs) + ")"

        elif kind == "artist":
            subs = []
            for v in values:
                pat = nparam(f"%{v.lower()}%")
                subs.append(
                    "EXISTS(SELECT 1 FROM image_artists ia JOIN artists a ON a.id=ia.artist_id "
                    f"WHERE ia.image_id=i.id AND (lower(a.name) LIKE {pat} "
                    f"OR lower(COALESCE(a.aliases,'')) LIKE {pat}))"
                )
            piece = "(" + " OR ".join(subs) + ")"

        elif kind == "album":
            subs = []
            for v in values:
                if v.lower() == "none":
                    subs.append("NOT EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id)")
                elif v.isdigit():
                    subs.append(
                        "EXISTS(SELECT 1 FROM album_images ai WHERE ai.image_id=i.id "
                        f"AND ai.album_id={nparam(int(v))})"
                    )
                else:
                    subs.append(
                        "EXISTS(SELECT 1 FROM album_images ai JOIN albums al ON al.id=ai.album_id "
                        f"WHERE ai.image_id=i.id AND lower(al.name) LIKE {nparam('%' + v.lower() + '%')})"
                    )
            piece = "(" + " OR ".join(subs) + ")"

        elif kind in ("rating", "avg"):
            subs = []
            for v in values:
                parsed_op = _parse_op(v)
                if parsed_op is None:
                    continue
                op, num = parsed_op
                if kind == "rating":
                    subs.append(
                        "COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id "
                        f"AND r.user_id=:uid),0) {op} {nparam(num)}"
                    )
                else:
                    subs.append(
                        "COALESCE((SELECT AVG(r.value) FROM ratings r WHERE r.image_id=i.id),0) "
                        f"{op} {nparam(num)}"
                    )
            if subs:
                piece = "(" + " OR ".join(subs) + ")"

        elif kind == "fav":
            want = values[0].lower() not in ("false", "0", "no")
            expr = "EXISTS(SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid)"
            piece = expr if want else f"NOT {expr}"

        elif kind == "ext":
            norm = [v.lower().lstrip(".") for v in values]
            marks = ",".join(nparam(x) for x in norm)
            piece = f"lower(replace(i.ext,'.','')) IN ({marks})"

        elif kind == "file":
            subs = [
                f"i.filename LIKE {nparam('%' + _like(v) + '%')} ESCAPE '\\'" for v in values
            ]
            piece = "(" + " OR ".join(subs) + ")"

        if piece:
            where.append(("NOT " if neg else "") + piece)

    return " AND ".join(where), params


_SELECT = """
SELECT i.id, i.library_id, i.rel_path, i.filename, i.ext, i.size_bytes,
       i.width, i.height, i.mtime, i.sha256, i.phash, i.thumb_rel, i.preview_rel, i.imported_at,
       COALESCE((SELECT r.value FROM ratings r WHERE r.image_id=i.id AND r.user_id=:uid), -1) AS my_rating,
       (SELECT ROUND(AVG(r.value), 2) FROM ratings r WHERE r.image_id=i.id) AS avg_rating,
       (SELECT COUNT(*) FROM ratings r WHERE r.image_id=i.id) AS rating_count,
       COALESCE((SELECT 1 FROM favorites f WHERE f.image_id=i.id AND f.user_id=:uid), 0) AS favorite,
       (SELECT COUNT(*) FROM image_tags it WHERE it.image_id=i.id) AS tag_count
FROM images i
WHERE {where}
"""


def search_images(
    conn: sqlite3.Connection,
    user_id: int,
    allowed_library_ids: list[int] | None,
    *,
    query: str = "",
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
    extra_where: list[str] | None = None,
    extra_params: dict | None = None,
) -> dict:
    parsed = parse(query)
    where, params = build_where(parsed, user_id, allowed_library_ids)
    if extra_where:
        where += " AND " + " AND ".join(extra_where)
    if extra_params:
        params = {**params, **extra_params}
    order = SORTS.get(sort, SORTS["newest"])
    limit = max(0, min(int(limit), 10000))
    offset = max(0, int(offset))

    count = conn.execute(
        f"SELECT COUNT(*) AS n FROM images i WHERE {where}", params
    ).fetchone()["n"]
    rows = conn.execute(
        _SELECT.format(where=where) + f" ORDER BY {order} LIMIT {limit} OFFSET {offset}",
        params,
    ).fetchall()
    return {"total": int(count), "items": [dict(r) for r in rows], "limit": limit, "offset": offset, "errors": parsed.errors}


def get_image(
    conn: sqlite3.Connection,
    image_id: int,
    user_id: int,
    allowed_library_ids: list[int] | None,
) -> dict | None:
    where = "i.id = :iid AND i.missing = 0"
    params = {"iid": image_id, "uid": user_id}
    if allowed_library_ids is not None:
        if not allowed_library_ids:
            return None
        marks = ",".join(f":lib{i}" for i in range(len(allowed_library_ids)))
        where += f" AND i.library_id IN ({marks})"
        for i, lib in enumerate(allowed_library_ids):
            params[f"lib{i}"] = lib
    row = conn.execute(_SELECT.format(where=where), params).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["tags"] = [
        dict(r)
        for r in conn.execute(
            "SELECT t.id, t.name, t.namespace FROM tags t "
            "JOIN image_tags it ON it.tag_id=t.id WHERE it.image_id=? ORDER BY t.name",
            (image_id,),
        )
    ]
    item["artists"] = [
        dict(r)
        for r in conn.execute(
            "SELECT a.id, a.name FROM artists a "
            "JOIN image_artists ia ON ia.artist_id=a.id WHERE ia.image_id=? ORDER BY a.name",
            (image_id,),
        )
    ]
    item["albums"] = [
        dict(r)
        for r in conn.execute(
            "SELECT al.id, al.name, al.kind FROM albums al "
            "JOIN album_images ai ON ai.album_id=al.id WHERE ai.image_id=? ORDER BY al.name",
            (image_id,),
        )
    ]
    item["library"] = dict(
        conn.execute(
            "SELECT id, name FROM libraries WHERE id=?", (item["library_id"],)
        ).fetchone()
    )
    return item
