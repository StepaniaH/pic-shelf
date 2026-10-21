"""Display names remain distinct from Unicode-aware matching keys."""
from __future__ import annotations

import unicodedata

from .errors import ServiceError
from .i18n import t


def display(value: str) -> str:
    result = unicodedata.normalize("NFC", value.strip())
    if not result or len(result) > 300 or any(unicodedata.category(c) == "Cc" for c in result):
        raise ServiceError("name_invalid", t("名称需为 1 至 300 个字符，且不能含控制字符"),
                           next_step=t("请缩短名称并移除控制字符后重试"))
    return result


def match_key(value: str | None) -> str:
    value = " ".join(unicodedata.normalize("NFC", value or "").split()).casefold()
    if ":" in value:
        namespace, name = value.split(":", 1)
        value = namespace.strip() + ":" + name.strip()
    return value


def contains_pattern(value: str) -> str:
    value = match_key(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{value}%"


def find(conn, kind: str, value: str) -> list[int]:
    if kind not in ("tag", "artist"):
        raise ValueError(t("名称类型必须是 tag 或 artist"))
    return [row[0] for row in conn.execute(
        f"SELECT id FROM {kind}s WHERE picshelf_name_key(name)=? UNION "
        f"SELECT entity_id FROM {kind}_aliases WHERE match_key=? ORDER BY 1", (match_key(value), match_key(value)))]


def resolve(conn, kind: str, value: str) -> int | None:
    if kind not in ("tag", "artist"):
        raise ValueError(t("名称类型必须是 tag 或 artist"))
    # An exact published display name still identifies its historical entity.
    exact = conn.execute(f"SELECT id FROM {kind}s WHERE name=?", (value.strip(),)).fetchone()
    if exact:
        return exact[0]
    matches = find(conn, kind, value)
    if len(matches) > 1:
        raise ServiceError("name_ambiguous", t("该名称匹配多个条目，未自动合并"), status=409,
                           next_step=t("请使用准确显示名称或条目 ID；需要合并时先查看明确的关联范围"))
    return matches[0] if matches else None


def aliases(conn, kind: str, entity_id: int) -> list[str]:
    if kind not in ("tag", "artist"):
        raise ValueError(t("名称类型必须是 tag 或 artist"))
    return [row[0] for row in conn.execute(f"SELECT name FROM {kind}_aliases WHERE entity_id=? ORDER BY id", (entity_id,))]
