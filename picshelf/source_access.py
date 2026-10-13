"""Administrator-approved filesystem sources for delegated import managers."""
from pathlib import Path
import os

from . import auth
from .db import utcnow
from .errors import ServiceError
from .i18n import t


def _denied():
    return ServiceError("import_source_forbidden", t("导入源目录或操作方式未获管理员允许"), status=403,
                        next_step=t("请联系管理员配置允许的源根，或选择已获准的目录"))


def register(conn, access, root_path, *, allow_move=False):
    access.require_capability(conn, "admin")
    try:
        root = Path(root_path).expanduser().resolve(strict=True)
        stat = root.stat()
        if not root.is_dir():
            raise OSError
        with os.scandir(root):
            pass
    except (OSError, RuntimeError) as exc:
        raise ServiceError("import_source_invalid", t("导入源目录不可读"),
                           next_step=t("请检查服务端目录、挂载和读取权限")) from exc
    with conn:
        conn.execute("INSERT INTO import_source_roots(root_path,device,inode,enabled,allow_move,created_by,created_at) "
                     "VALUES(?,?,?,1,?,?,?) ON CONFLICT(root_path) DO UPDATE SET "
                     "device=excluded.device,inode=excluded.inode,enabled=1,allow_move=excluded.allow_move,"
                     "created_by=excluded.created_by,created_at=excluded.created_at",
                     (str(root), stat.st_dev, stat.st_ino, int(allow_move), access.user_id, utcnow()))
    return dict(conn.execute("SELECT * FROM import_source_roots WHERE root_path=?", (str(root),)).fetchone())


def list_roots(conn, access):
    access.require_capability(conn, "admin")
    return [dict(row) for row in conn.execute("SELECT * FROM import_source_roots ORDER BY id")]


def revoke(conn, access, root_id):
    access.require_capability(conn, "admin")
    with conn:
        conn.execute("UPDATE import_source_roots SET enabled=0 WHERE id=?", (root_id,))
    return {"ok": True}


def require_source(conn, access, source, *, mode=None):
    user = access.user(conn)
    if access.local_admin or auth.has_cap(conn, user, "admin"):
        return
    try:
        candidate = Path(source).expanduser().resolve(strict=True)
        for row in conn.execute("SELECT * FROM import_source_roots WHERE enabled=1"):
            root = Path(row["root_path"])
            if not candidate.is_relative_to(root):
                continue
            stat = root.stat()
            if root.resolve(strict=True) == root and (stat.st_dev, stat.st_ino) == (row["device"], row["inode"]):
                if mode != "move" or row["allow_move"]:
                    return
    except (OSError, RuntimeError):
        pass
    raise _denied()
