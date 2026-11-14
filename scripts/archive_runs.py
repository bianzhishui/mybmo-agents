#!/usr/bin/env python3
"""按 §5.7 归档超期 run：打包 `.ecwf/archive/<runId>.tar.gz`，丢掉可再生的 `.b64`。

保留：`manifest.json` + 全部分阶段产物（含 `08-execution/`、原图 `*.png`——不可再生，必留）。
丢弃：`*.b64`（可从 png 重编码）。
默认**只归档不删除**；`--delete` 才会删原目录，且永不删 `finalStatus=running` 的 run。

用法：
    python3 scripts/archive_runs.py                 # 归档 30 天前的 run（不删）
    python3 scripts/archive_runs.py --older-than 7 --dry-run
    python3 scripts/archive_runs.py --older-than 30 --delete

退出码：0 成功 / 1 有跳过/失败（如 running）/ 2 用法错。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_status(run_dir: Path) -> str:
    mf = run_dir / "manifest.json"
    if not mf.is_file():
        return "unknown"
    try:
        return str(json.loads(mf.read_text(encoding="utf-8")).get("finalStatus", "running"))
    except json.JSONDecodeError:
        return "unknown"


def oldest_mtime(run_dir: Path) -> float:
    return max((p.stat().st_mtime for p in run_dir.rglob("*") if p.is_file()), default=run_dir.stat().st_mtime)


def archive_run(run_dir: Path, archive_dir: Path, drop_b64: bool = True) -> tuple[Path, int, int]:
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"{run_dir.name}.tar.gz"
    kept = dropped = 0
    with tarfile.open(target, "w:gz") as tar:
        for p in sorted(run_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(run_dir)
            if drop_b64 and p.suffix == ".b64":
                dropped += 1
                continue
            tar.add(p, arcname=str(rel))
            kept += 1
    return target, kept, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="归档超期 run（§5.7）")
    ap.add_argument("--base", default=None, help="工作区根（默认 $ECWF_HOME 或 <项目>/.ecwf）")
    ap.add_argument("--older-than", type=int, default=30, help="多少天前（按最新文件 mtime）")
    ap.add_argument("--delete", action="store_true", help="归档后删除原目录（running 永不删）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的归档包（默认拒绝）")
    args = ap.parse_args(argv)

    import os
    home = Path(args.base).expanduser().resolve() if args.base else Path(
        os.environ.get("ECWF_HOME") or (PROJECT_ROOT / ".ecwf")
    ).resolve()
    runs = home / "runs"
    if not runs.is_dir():
        print(f"找不到 runs 目录：{runs}", file=sys.stderr)
        return 2
    cutoff = time.time() - args.older_than * 86400
    archive_dir = home / "archive"

    rc = 0
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        status = run_status(d)
        age_days = (time.time() - oldest_mtime(d)) / 86400
        if status == "running":
            print(f"[skip] {d.name} 仍 running（不归档、不删除）")
            rc = 1
            continue
        if oldest_mtime(d) > cutoff:
            print(f"[keep] {d.name} 未到期（{age_days:.1f} 天 < {args.older_than}）status={status}")
            continue
        target = archive_dir / f"{d.name}.tar.gz"
        if args.dry_run:
            print(f"[dry-run] 将归档 {d.name}（status={status}, {age_days:.1f} 天）→ {target}")
            continue
        if target.exists() and not args.overwrite:
            print(f"[skip] {d.name} 已存在归档 {target.name}（要覆盖加 --overwrite）", file=sys.stderr)
            rc = 1
            continue
        target, kept, dropped = archive_run(d, archive_dir)
        print(f"[archived] {d.name} -> {target.name}（保留 {kept} 文件，丢 {dropped} 个 .b64）")
        if args.delete:
            # 安全：只删 runs 下的真实目录
            resolved = d.resolve()
            if runs.resolve() not in resolved.parents:
                print(f"[FAIL] 拒绝删除越界路径：{resolved}", file=sys.stderr)
                rc = 1
                continue
            shutil.rmtree(resolved)
            print(f"[deleted] {d.name}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
