"""Process-safe library write coordination for one catalog installation."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading

from .config import Config
from .errors import ServiceError
from .i18n import t

_local = threading.local()


@contextmanager
def library_write_lock(cfg: Config, root: Path):
    try:
        import fcntl
    except ImportError as exc:
        raise ServiceError("coordination_unavailable", t("此平台暂不支持安全的图库写入协调"),
                           next_step=t("请在支持文件锁的平台执行扫描或导入")) from exc
    stat = root.stat()
    identity = f"{cfg.data_dir.resolve()}:{stat.st_dev}:{stat.st_ino}"
    key = (os.getpid(), identity)
    held = getattr(_local, "held", set())
    if key in held:
        yield
        return
    directory = cfg.data_dir / "operation-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServiceError("library_busy", t("该库正在执行其他写操作"), status=409,
                               retryable=True, next_step=t("请等待当前扫描或导入结束后重试")) from exc
        _local.held = held | {key}
        try:
            yield
        finally:
            _local.held = held
            fcntl.flock(handle, fcntl.LOCK_UN)
