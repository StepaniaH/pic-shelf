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

import re
import sqlite3
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from .errors import ServiceError
from .i18n import t
from .names import match_key

SORTS = {
    "newest": "i.id DESC",
    "oldest": "i.id ASC",
    "path": "i.rel_path ASC, i.id ASC",
    "rating": "my_rating DESC, i.id DESC",
    "avg": "avg_rating DESC, i.id DESC",
    "random": "RANDOM()",
}

MAX_QUERY_LENGTH = 4096
_KEYWORDS = ("tag", "artist", "album", "rating", "avg", "fav", "ext", "file",
             "rated", "width", "height", "orientation", "imported", "workflow", "missing")
_ENUMS = {"fav": ("true", "false", "1", "0", "yes", "no"),
          "rated": ("true", "false"), "orientation": ("landscape", "portrait", "square"),
          "workflow": ("inbox", "done"), "missing": ("true", "false", "any")}


def visible_images_sql(allowed_library_ids: list[int] | None) -> tuple[str, list]:
    """目录统计与关联实体共用的可见图片范围；None 表示管理员。"""
    sql = "SELECT * FROM images WHERE missing=0"
    if allowed_library_ids is None:
        return sql, []
    if not allowed_library_ids:
        return sql + " AND 0=1", []
    marks = ",".join("?" for _ in allowed_library_ids)
    return sql + f" AND library_id IN ({marks})", list(allowed_library_ids)


@dataclass
class ParsedQuery:
    clauses: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse(query: str) -> ParsedQuery:
    out = ParsedQuery()
    if not isinstance(query, str):
        out.errors.append(t("搜索表达式必须为文本；留空可匹配全部可见图片"))
        return out
    if len(query) > MAX_QUERY_LENGTH:
        out.errors.append(t("搜索内容过长，请缩短到 4096 个字符以内"))
        return out
    if any(ord(c) < 32 and not c.isspace() for c in query):
        out.errors.append(t("搜索内容含无效控制字符，请删除后重试"))
        return out
    try:
        tokens = _tokens(query)
    except ValueError:
        out.errors.append(t("引号未闭合，请用成对的引号包裹含空格的名称"))
        return out
    if len(tokens) > 128:
        out.errors.append(t("搜索条件过多，请减少到 128 项以内"))
        return out
    for chars in tokens:
        token = ''.join(c for c, _ in chars)
        neg = bool(chars and chars[0] == ('-', False))
        if neg: chars = chars[1:]
        raw = ''.join(c for c, _ in chars)
        prefix, sep, _ = raw.partition(':')
        key = prefix.lower() if sep and prefix.lower() in _KEYWORDS else 'term'
        if key != 'term': chars = chars[len(prefix)+1:]
        elif raw.lower() == 'fav': key, chars = 'fav', []
        values = ['']
        for char, quoted in chars:
            if char == ',' and not quoted: values.append('')
            else: values[-1] += char
        values = [v.strip() for v in values]
        if key == 'fav' and not chars: values = ['true']  # Published shorthand.
        valid = bool(values) and all(values) and len(values) <= 100
        if key in ("rating", "avg"):
            valid = valid and all((p := _parse_op(v)) is not None and 0 <= p[1] <= 10 for v in values)
        elif key in ('width', 'height'):
            valid = valid and all((p := _parse_op(v)) is not None and 0 <= p[1] <= 1_000_000_000
                                  and p[1].is_integer() for v in values)
        elif key in _ENUMS:
            values = [v.lower() for v in values]
            valid = valid and all(v in _ENUMS[key] for v in values)
        elif key == 'imported':
            valid = valid and all(_date_op(v, datetime.now(timezone.utc)) is not None for v in values)
        elif key == 'ext':
            valid = valid and all(re.fullmatch(r'\.?[A-Za-z0-9]{1,16}', v) for v in values)
        elif key == 'album':
            valid = valid and all(not (v.isascii() and v.isdigit()) or (len(v) <= 19 and 1 <= int(v) <= 2**63-1) for v in values)
        if not valid:
            out.errors.append(t("搜索条件无效：{token}。请检查字段、非空参数与取值范围。", token=token[:120]))
        out.clauses.append({"kind": key, "values": values, "neg": neg})
    return out


def _tokens(query, *, split_whitespace=True):
    """Preserve quoted commas so names remain literal without a second grammar."""
    tokens, chars, quote, started = [], [], None, False
    index = 0
    while index < len(query):
        char = query[index]
        if char == '\\' and quote != "'":
            index += 1
            if index == len(query): raise ValueError('trailing escape')
            chars.append((query[index], True)); started = True
        elif quote:
            if char == quote: quote = None
            else: chars.append((char, True))
        elif char in ('"', "'"):
            quote = char; started = True
        elif char.isspace() and split_whitespace:
            if started: tokens.append(chars); chars, started = [], False
        else:
            chars.append((char, False)); started = True
        index += 1
    if quote: raise ValueError('unclosed quote')
    if started: tokens.append(chars)
    return tokens


def _date_op(value, now):
    match = re.fullmatch(r'(>=|<=|>|<|=)?(.+)', value)
    if not match: return None
    op, body = match.group(1) or '=', match.group(2)
    relative = re.fullmatch(r'now(?:-([0-9]{1,5})([dh]))?', body)
    try:
        if relative:
            amount = int(relative.group(1) or 0)
            if amount > 36500: return None
            stamp = now - timedelta(**{'hours' if relative.group(2) == 'h' else 'days': amount})
        else:
            stamp = datetime.fromisoformat(body.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None: return None
        return op, stamp.astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def condition_fields():
    return [{'kind': kind, 'values': ['true','false'] if kind == 'fav' else list(_ENUMS.get(kind, ())),
             'example': {'rating':'>=8','avg':'>=8','width':'>=1920','height':'>=1080',
                         'imported':'>=now-7d'}.get(kind, '')} for kind in _KEYWORDS]


def invalid_query(errors):
    return ServiceError('invalid_query', ' '.join(errors),
                        next_step=t("请修正搜索条件后重试；命名空间标签可写为 tag:命名空间:名称"))


def append_condition(query, *, kind, values, neg=False):
    if isinstance(values, str):
        if len(values) > MAX_QUERY_LENGTH:
            raise invalid_query([t("搜索内容过长，请缩短到 4096 个字符以内")])
        try:
            tokens = _tokens(values, split_whitespace=False)
        except ValueError:
            raise invalid_query([t("引号未闭合，请用成对的引号包裹含空格的名称")]) from None
        values = ['']
        for char, quoted in tokens[0] if tokens else []:
            if char == ',' and not quoted:
                values.append('')
            else:
                values[-1] += char
        values = [value.strip() for value in values]
    if not isinstance(query, str) or not isinstance(kind, str) or kind not in _KEYWORDS or type(neg) is not bool or not isinstance(values, list) or not values or any(
            not isinstance(v, str) or not v.strip() for v in values):
        raise invalid_query([t("搜索字段或参数无效，请使用条件列表中的字段")])
    def quote(value):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    expression = (query.strip() + ' ' + ('-' if neg else '') + kind + ':' + ','.join(map(quote, values))).strip()
    parsed = parse(expression)
    if parsed.errors: raise invalid_query(parsed.errors)
    return expression


def _like(v: str) -> str:
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _parse_op(v: str) -> tuple[str, float] | None:
    for op in (">=", "<=", ">", "<", "="):
        if v.startswith(op):
            body = v[len(op):]
            try:
                number = float(body)
                return (op, number) if math.isfinite(number) else None
            except ValueError:
                return None
    try:
        number = float(v)
        return ("=", number) if math.isfinite(number) else None
    except ValueError:
        return None


def build_where(
    parsed: ParsedQuery,
    user_id: int,
    allowed_library_ids: list[int] | None,
    *, now: datetime | None = None,
) -> tuple[str, dict]:
    now = now or datetime.now(timezone.utc)
    where: list[str] = ["1=1" if any(c['kind'] == 'missing' for c in parsed.clauses) else "i.missing = 0"]
    params: dict = {"uid": user_id}
    if parsed.errors: return '0=1', params
    pcount = 0

    def nparam(value) -> str:
        nonlocal pcount
        name = f"p{pcount}"
        pcount += 1
        params[name] = value
        return f":{name}"

    album_scope = ''
    if allowed_library_ids is not None:
        if not allowed_library_ids:
            where.append("0=1")
        else:
            marks = ",".join(nparam(x) for x in allowed_library_ids)
            where.append(f"i.library_id IN ({marks})")
            album_scope = f' AND (al.library_id IS NULL OR al.library_id IN ({marks}))'

    for c in parsed.clauses:
        kind, values, neg = c["kind"], c["values"], c["neg"]
        piece: str | None = None

        if kind == "invalid":
            piece = "0=1"
        elif kind in ("tag", "term"):
            subs = []
            for v in values:
                key = nparam(match_key(v))
                tag_expr = (
                    "EXISTS(SELECT 1 FROM image_tags it JOIN tags tg ON tg.id=it.tag_id "
                    f"WHERE it.image_id=i.id AND (picshelf_name_key(tg.name)={key} OR EXISTS("
                    f"SELECT 1 FROM tag_aliases ta WHERE ta.entity_id=tg.id AND ta.match_key={key})))"
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
                pat = nparam(f"%{_like(match_key(v))}%")
                subs.append(
                    "EXISTS(SELECT 1 FROM image_artists ia JOIN artists a ON a.id=ia.artist_id "
                    f"WHERE ia.image_id=i.id AND (picshelf_name_key(a.name) LIKE {pat} ESCAPE '\\' "
                    f"OR picshelf_name_key(a.aliases) LIKE {pat} ESCAPE '\\' OR EXISTS("
                    f"SELECT 1 FROM artist_aliases aa WHERE aa.entity_id=a.id AND aa.match_key LIKE {pat} ESCAPE '\\')))"
                )
            piece = "(" + " OR ".join(subs) + ")"

        elif kind == "album":
            subs = []
            for v in values:
                if v.lower() == "none":
                    subs.append("NOT EXISTS(SELECT 1 FROM album_images ai JOIN albums al ON al.id=ai.album_id WHERE ai.image_id=i.id" + album_scope + ")")
                elif v.isascii() and v.isdigit():
                    subs.append(
                        "EXISTS(SELECT 1 FROM album_images ai JOIN albums al ON al.id=ai.album_id WHERE ai.image_id=i.id "
                        f"AND ai.album_id={nparam(int(v))}" + album_scope + ")"
                    )
                else:
                    subs.append(
                        "EXISTS(SELECT 1 FROM album_images ai JOIN albums al ON al.id=ai.album_id "
                        f"WHERE ai.image_id=i.id AND lower(al.name) LIKE {nparam('%' + _like(v.lower()) + '%')} ESCAPE '\\'" + album_scope + ")"
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

        elif kind in ('fav', 'rated'):
            table = 'favorites' if kind == 'fav' else 'ratings'
            expr = f"EXISTS(SELECT 1 FROM {table} f WHERE f.image_id=i.id AND f.user_id=:uid)"
            piece = '(' + ' OR '.join(expr if v in ('true', '1', 'yes') else 'NOT ' + expr for v in values) + ')'

        elif kind in ('width', 'height'):
            piece = '(' + ' OR '.join(f'i.{kind} {p[0]} {nparam(int(p[1]))}' for v in values if (p := _parse_op(v))) + ')'

        elif kind == 'orientation':
            relations = {'landscape':'>', 'portrait':'<', 'square':'='}
            piece = '(i.width>0 AND i.height>0 AND (' + ' OR '.join(f'i.width {relations[v]} i.height' for v in values) + '))'

        elif kind == 'workflow':
            marks = ','.join(nparam(v) for v in values)
            piece = "COALESCE((SELECT state FROM image_workflow w WHERE w.image_id=i.id AND w.user_id=:uid),'inbox') IN (" + marks + ')'

        elif kind == 'missing':
            piece = '(' + ' OR '.join('1=1' if v == 'any' else f'i.missing={1 if v == "true" else 0}' for v in values) + ')'

        elif kind == 'imported':
            piece = '(' + ' OR '.join(f'julianday(i.imported_at) {p[0]} julianday({nparam(p[1])})'
                                      for v in values if (p := _date_op(v, now))) + ')'

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
       i.width, i.height, i.mtime, i.sha256, i.phash, i.thumb_rel, i.preview_rel, i.imported_at, i.missing,
       i.phash_version, i.derivative_version, i.scan_error_code, i.scan_error_detail, i.verified_at,
       COALESCE((SELECT w.state FROM image_workflow w WHERE w.image_id=i.id AND w.user_id=:uid), 'inbox') AS workflow_state,
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
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    parsed = parse(query)
    if not isinstance(sort, str) or sort not in SORTS:
        parsed.errors.append(t("排序方式无效，请从排序列表选择"))
        sort = 'newest'
    if type(limit) is not int or not 0 <= limit <= 10000 or type(offset) is not int or not 0 <= offset <= 2**63-1:
        parsed.errors.append(t("分页参数无效；数量须为 0–10000，偏移须为非负整数"))
        limit, offset = 0, 0
    where, params = build_where(parsed, user_id, allowed_library_ids, now=now)
    if extra_where:
        where += " AND " + " AND ".join(extra_where)
    if extra_params:
        params = {**params, **extra_params}
    order = SORTS.get(sort, SORTS["newest"])
    # Count and rows observe one SQLite snapshot, including when a scanner commits.
    conn.execute('SAVEPOINT search_read')
    try:
        count = conn.execute(f"SELECT COUNT(*) AS n FROM images i WHERE {where}", params).fetchone()["n"]
        rows = conn.execute(_SELECT.format(where=where) + f" ORDER BY {order} LIMIT {limit} OFFSET {offset}", params).fetchall()
    finally:
        conn.execute('RELEASE search_read')
    return {"total": int(count), "items": [dict(r) for r in rows], "limit": limit, "offset": offset,
            "errors": parsed.errors, "error_codes": ['invalid_query'] * len(parsed.errors),
            "pagination": 'random_sample' if sort == 'random' else 'live_offset',
            "evaluated_at": (now or datetime.now(timezone.utc)).isoformat()}


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
    album_scope = ""
    album_params = [image_id]
    if allowed_library_ids is not None:
        marks = ",".join("?" for _ in allowed_library_ids)
        album_scope = f" AND (al.library_id IS NULL OR al.library_id IN ({marks}))"
        album_params.extend(allowed_library_ids)
    item["albums"] = [
        dict(r)
        for r in conn.execute(
            "SELECT al.id, al.name, al.kind FROM albums al "
            "JOIN album_images ai ON ai.album_id=al.id WHERE ai.image_id=?" + album_scope + " ORDER BY al.name",
            album_params,
        )
    ]
    item["library"] = dict(
        conn.execute(
            "SELECT id, name FROM libraries WHERE id=?", (item["library_id"],)
        ).fetchone()
    )
    return item
