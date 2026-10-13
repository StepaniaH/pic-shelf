"""Deterministic, bounded relative paths from explicit naming fields."""
from datetime import date
from pathlib import PurePosixPath
import string

from . import importer, names
from .errors import ServiceError
from .i18n import t

FIELDS = {'artists', 'tags', 'source', 'date', 'stem', 'ext'}
FALLBACKS = {'artists': 'unknown-artist', 'tags': 'untagged', 'source': 'unknown-source', 'date': 'undated'}


def _error():
    return ServiceError('naming_template_invalid', t('命名模板或字段无效'),
                        next_step=t('请使用 artists、tags、source、date、stem、ext；不允许绝对路径、父目录或格式转换'))


def validate(template, fields=None):
    fields = {} if fields is None else fields
    if (not isinstance(template, str) or not template or len(template) > 1000 or
            template.startswith('/') or '\\' in template or
            any(part in ('', '.', '..') for part in template.split('/')) or
            len(template.split('/')) > 8 or not isinstance(fields, dict) or
            set(fields) - {'artists', 'tags', 'source', 'date'}):
        raise _error()
    try:
        for _, field, spec, conversion in string.Formatter().parse(template):
            if field is not None and (field not in FIELDS or spec or conversion):
                raise _error()
    except ValueError as exc:
        raise _error() from exc
    for field, value in fields.items():
        if field in ('artists', 'tags'):
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(x, str) or not x.strip() or len(x) > 1000 for x in value):
                raise _error()
        elif value is not None and (not isinstance(value, str) or len(value) > 1000):
            raise _error()
    if fields.get('date'):
        try:
            date.fromisoformat(fields['date'])
        except ValueError as exc:
            raise _error() from exc
    return fields


def render(template, filename, fields=None):
    fields = validate(template, fields)
    file = PurePosixPath(filename)
    context = dict(FALLBACKS, stem=file.stem, ext=file.suffix)
    for field in ('artists', 'tags'):
        values = fields.get(field, [])
        # Normalize display spelling, retain distinct names, and use a fixed order.
        values = sorted({names.display(value) for value in values}, key=lambda value: (names.match_key(value), value))
        if values:
            context[field] = ' + '.join(values)
    for field in ('source', 'date'):
        if fields.get(field):
            context[field] = fields[field]
    # Slashes supplied by metadata are characters inside a component, never paths.
    components = [importer.sanitize_filename(part.format_map({key: value.replace('/', '_') for key, value in context.items()}))
                  for part in template.split('/')]
    if not components[-1].endswith(file.suffix):
        components[-1] = importer.sanitize_filename(components[-1] + file.suffix)
    result = PurePosixPath(*components)
    if len(result.as_posix().encode()) > 1600:
        raise _error()
    return result.as_posix()
