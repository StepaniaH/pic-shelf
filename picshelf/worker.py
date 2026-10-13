"""Local worker processes. Queue state lives in SQLite, never in this supervisor."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from . import batches, db, jobs
from .config import Config
from .errors import ServiceError


def work(cfg, *, once=False, parent_pid=None, idle_timeout=None):
    stop = threading.Event()
    received_signal = 0

    def interrupt(signum, _frame):
        nonlocal received_signal
        received_signal = signum
        stop.set()

    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    idle_since, delay = time.monotonic(), .1

    def should_stop():
        return stop.is_set() or (parent_pid is not None and os.getppid() != parent_pid)

    while not should_stop():
        try:
            worked = jobs.run_next(cfg, should_stop=should_stop) or batches.run_next(cfg, should_stop=should_stop)
        except (ServiceError, OSError):
            if once:
                return 1
            worked = False
        if once:
            return 128 + received_signal if received_signal else 0
        if worked:
            idle_since, delay = time.monotonic(), .1
        else:
            if idle_timeout is not None and time.monotonic() - idle_since >= idle_timeout:
                return 0
            stop.wait(delay)
            delay = min(2, delay * 1.5)
    return 128 + received_signal if received_signal else 0


class WorkerManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self.processes = []
        self.lock = threading.Lock()
        self.closed = False

    def start(self):
        with self.lock:
            if self.closed or not self.cfg.db_path.exists():
                return
            try:
                conn = db.open_db(self.cfg.db_path)
                try:
                    pending = conn.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running','cancelling') LIMIT 1").fetchone()
                    if not pending:
                        pending = conn.execute("SELECT 1 FROM metadata_batches WHERE state='queued' LIMIT 1").fetchone()
                finally:
                    conn.close()
            except ServiceError:
                return  # Request handlers still return actionable DB errors.
            if not pending:
                return
            self.processes = [process for process in self.processes if process.poll() is None]
            while len(self.processes) < 2:
                self.processes.append(subprocess.Popen(
                    [sys.executable, "-m", "picshelf.worker", "--data", str(self.cfg.data_dir),
                     "--parent-pid", str(os.getpid())], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True))

    def close(self):
        with self.lock:
            self.closed = True
            for process in self.processes:
                if process.poll() is None:
                    process.terminate()
            deadline = time.monotonic() + 5
            for process in self.processes:
                try:
                    process.wait(timeout=max(.01, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self.processes.clear()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--parent-pid", type=int)
    args = parser.parse_args()
    return work(Config(Path(args.data)), once=args.once, parent_pid=args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
