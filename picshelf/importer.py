"""托管库导入器（v0.2）：把外部目录的图片导入 Pic Shelf 管理的库目录。

模式：
  hardlink  原文件不动；库内出现指向同一份数据的硬链接（同盘零额外空间，跨盘自动降级 copy）
  copy      复制进库，原库留档
  move      移入库（经典"整理"模式；执行前需确认，原位置不再保留）

安全约定：
  - 默认预演（dry-run），显式 apply 才真正动文件；
  - 目标路径强制约束在目标库根目录内（越界硬拒绝）；
  - sha256 命中（库内已有 / 本次已计划）→ 跳过，天然去重；
  - 同名冲突自动加序号（a.png → a-1.png）；
  - 源目录与目标库互为父子关系的场景直接拒绝。

注意：若源目录由 Syncthing 等同步工具管理，硬链接与源共享同一 inode，
同步工具可能感知不到链接关系——建议托管库目录不要放进同步目录。
"""
from __future__ import annotations

import errno
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Callable

from . import hashing

MODES = ("hardlink", "copy", "move")
_ILLEGAL = set('\x00<>:"\\|?*') | {chr(c) for c in range(32)}


def sanitize_filename(name: str) -> str:
    cleaned = "".join("_" if ch in _ILLEGAL else ch for ch in name).strip().strip(".")
    return cleaned or "unnamed"


def plan_import(
    conn: sqlite3.Connection,
    library,
    source_root: str | Path,
    mode: str = "hardlink",
    *,
    rename: bool = True,
) -> dict:
    """生成导入计划（只读，不动任何文件）。library 为 libraries 表行或 dict。"""
    root = Path(library["root_path"]).resolve()
    source_root = Path(source_root).expanduser().resolve()
    if mode not in MODES:
        raise ValueError(f"未知模式：{mode}（可选 {'/'.join(MODES)}）")
    if not source_root.is_dir():
        raise ValueError(f"源目录不存在：{source_root}")
    if source_root == root or root in source_root.parents or source_root in root.parents:
        raise ValueError("源目录与目标库目录不能互为父子关系（含相同）")

    from .indexer import _iter_image_files

    have = {
        r["sha256"]
        for r in conn.execute(
            "SELECT sha256 FROM images WHERE library_id=? AND sha256 IS NOT NULL AND missing=0",
            (library["id"],),
        )
    }
    try:
        dst_dev = root.stat().st_dev
    except OSError:
        dst_dev = source_root.stat().st_dev

    planned: set[str] = set()
    items: list[dict] = []
    counts = {"hardlink": 0, "copy": 0, "move": 0, "skip": 0, "error": 0}

    for path in _iter_image_files(source_root):
        try:
            rel = path.relative_to(source_root)
            sha = hashing.sha256_file(path)
            if sha in have:
                items.append({"src": str(path), "dst": None, "action": "skip",
                              "reason": "库内已有相同内容"})
                counts["skip"] += 1
                continue
            if sha in planned:
                items.append({"src": str(path), "dst": None, "action": "skip",
                              "reason": "与本次计划中的另一文件内容相同"})
                counts["skip"] += 1
                continue

            target_name = sanitize_filename(path.name) if rename else path.name
            stem, ext = os.path.splitext(target_name)
            suffix = 1
            rel_dst = rel.parent / target_name
            dst = root / rel_dst
            skipped_dup = False
            while dst.exists():
                if dst.is_file() and hashing.sha256_file(dst) == sha:
                    skipped_dup = True
                    break
                rel_dst = rel.parent / f"{stem}-{suffix}{ext}"
                dst = root / rel_dst
                suffix += 1
            if skipped_dup:
                items.append({"src": str(path), "dst": None, "action": "skip",
                              "reason": "目标位置已有相同文件"})
                counts["skip"] += 1
                continue

            action = mode
            if mode == "hardlink" and path.stat().st_dev != dst_dev:
                action = "copy"  # 跨盘硬链接不可用，自动降级
            items.append({"src": str(path), "dst": str(dst), "action": action, "reason": ""})
            counts[action] += 1
            planned.add(sha)
        except Exception as exc:  # 单文件失败不中断计划
            items.append({"src": str(path), "dst": None, "action": "error", "reason": str(exc)})
            counts["error"] += 1

    return {
        "library": {"id": library["id"], "name": library["name"]},
        "root": str(root),
        "source": str(source_root),
        "mode": mode,
        "items": items,
        "stats": counts,
    }


def execute_import(
    plan: dict,
    *,
    apply: bool = False,
    progress: Callable[[int, dict], None] | None = None,
) -> dict:
    """执行导入计划。apply=False 时只做统计（等价再次预演）。"""
    root = Path(plan["root"])
    done = {"hardlink": 0, "copy": 0, "move": 0, "skip": 0, "error": 0, "errors": []}
    n = 0
    for item in plan["items"]:
        n += 1
        action = item["action"]
        if action in ("skip", "error"):
            done[action] += 1
            if action == "error":
                done["errors"].append(f"{item['src']}: {item['reason']}")
            continue
        if not apply:
            done[action] += 1
            continue

        src = Path(item["src"])
        dst = Path(item["dst"]).resolve()
        if root != dst.parent and root not in dst.parents:
            done["error"] += 1
            done["errors"].append(f"{src}: 目标路径越界，已拒绝")
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if action == "hardlink":
                try:
                    os.link(src, dst)
                    done["hardlink"] += 1
                except OSError as exc:
                    if exc.errno == errno.EXDEV:
                        shutil.copy2(src, dst)
                        done["copy"] += 1
                    else:
                        raise
            elif action == "copy":
                shutil.copy2(src, dst)
                done["copy"] += 1
            else:  # move
                shutil.move(str(src), str(dst))
                done["move"] += 1
        except Exception as exc:
            done["error"] += 1
            done["errors"].append(f"{src}: {exc}")
        if progress:
            progress(n, item)
    return done
