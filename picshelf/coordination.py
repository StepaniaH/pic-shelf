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
def operation_lock(cfg: Config, name: str, *, shared=False, reentrant=True, independent=False):
    try:
        import fcntl
    except ImportError as exc:
        raise ServiceError("coordination_unavailable", t("此平台暂不支持安全的图库写入协调"),
                           next_step=t("请在支持文件锁的平台执行扫描或导入")) from exc
    identity = f"{cfg.data_dir.resolve()}:{name}"
    key = (os.getpid(), identity, shared)
    held = getattr(_local, "held", set())
    if key in held and not independent:
        if not reentrant:
            raise ServiceError("operation_busy", t("操作正在由其他进程执行"), status=409,
                               retryable=True, next_step=t("请等待当前任务结束后重试"))
        yield
        return
    directory = cfg.data_dir / "operation-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (hashlib.sha256(identity.encode()).hexdigest() + ".lock")
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServiceError("operation_busy", t("操作正在由其他进程执行"), status=409,
                               retryable=True, next_step=t("请等待当前任务结束后重试")) from exc
        if not independent:
            _local.held = held | {key}
        try:
            yield
        finally:
            if not independent:
                _local.held = held
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def library_write_lock(cfg: Config, root: Path):
    stat = root.stat()
    try:
        with operation_lock(cfg, "catalog", shared=True):
            with operation_lock(cfg, f"library:{stat.st_dev}:{stat.st_ino}"):
                yield
    except ServiceError as exc:
        if exc.code != "operation_busy":
            raise
        raise ServiceError("library_busy", t("该库正在执行其他写操作"), status=409,
                           retryable=True, next_step=t("请等待当前扫描或导入结束后重试")) from exc


@contextmanager
def worker_slot(cfg: Config):
    for number in range(2):
        from contextlib import ExitStack
        stack = ExitStack()
        try:
            stack.enter_context(operation_lock(cfg, f"worker:{number}"))
        except ServiceError as exc:
            stack.close()
            if exc.code != "operation_busy":
                raise
            continue
        with stack:
            yield
        return
    raise ServiceError("workers_busy", t("后台执行单元正在使用中"), status=409,
                       retryable=True, next_step=t("任务已排队，请稍后查看进度"))
