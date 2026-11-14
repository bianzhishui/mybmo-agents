#!/usr/bin/env python3
"""ECWF 脚本的公共层：run 目录解析、路径常量。

只被 `scripts/` 下的其它脚本 import，不单独执行。
`RUN_ID_RE` 用来把"像 run 的目录"和不规范的临时目录（如 `test-001`）区分开——
默认取最新 run 时只看规范目录，避免把冒烟目录当成真 run。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[a-z0-9][a-z0-9-]*$")
# 文件名里的版本：`x.json`=1、`x.v7.json`=7（所有脚本共用同一份判定）
VERSIONED_RE = re.compile(r"^(?P<stem>.+?)(?:\.v(?P<v>\d+))?\.[A-Za-z0-9]+$")
VERSION_SUFFIX_RE = re.compile(r"\.v\d+(?=\.[^.]+$)")
# image-map 版本文件：image-map.json / image-map.v6.json
IMAGE_MAP_RE = re.compile(r"^image-map(?:\.v(\d+))?\.json$")
STAGES = [
    "01-product",
    "02-keywords",
    "03-copy",
    "04-assets",
    "05-plan",
    "06-payloads",
    "07-audit",
    "08-execution",
]


def ecwf_home(base: str | None = None) -> Path:
    if base:
        return Path(base).expanduser().resolve()
    env = os.environ.get("ECWF_HOME")
    return Path(env).expanduser().resolve() if env else (PROJECT_ROOT / ".ecwf")


def runs_root(base: str | None = None) -> Path:
    return ecwf_home(base) / "runs"


def resolve_run_dir(
    run: str | None = None,
    run_dir: str | None = None,
    base: str | None = None,
) -> Path:
    """--run-dir 优先；其次 --run；否则取最新一个规范的 run 目录。"""
    if run_dir:
        path = Path(run_dir).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"run 目录不存在：{path}")
        return path

    root = runs_root(base)
    if run:
        cand = root / run
        if not cand.is_dir():
            raise SystemExit(f"run 不存在：{cand}")
        return cand

    if not root.is_dir():
        raise SystemExit(f"找不到 runs 目录：{root}（先跑 scripts/new_run.py）")
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name))
    if not dirs:
        raise SystemExit(f"{root} 下没有规范的 run 目录（<YYYYMMDD-HHMMSS>-<slug>）")
    return dirs[-1]


def file_version(name: str) -> int:
    """文件名里的版本：`x.json`=1、`x.v7.json`=7。"""
    m = VERSIONED_RE.match(name)
    return int(m.group("v")) if (m and m.group("v")) else 1


def strip_version(name: str) -> str:
    """去掉文件名里的 `.vN`（用于白名单比对）。"""
    return VERSION_SUFFIX_RE.sub("", name)


def image_map_version(path: Path) -> int:
    m = IMAGE_MAP_RE.match(path.name)
    if m:
        return int(m.group(1) or 1)
    import json
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("version") or 1)
    except (ValueError, OSError):
        return 1


def pick_latest_image_map(assets_dir: Path) -> Path | None:
    """04-assets 下最新一版 image-map（按 .vN）。"""
    if not assets_dir.is_dir():
        return None
    cands = [p for p in assets_dir.glob("image-map*.json") if IMAGE_MAP_RE.match(p.name)]
    if not cands:
        fallback = assets_dir / "image-map.json"
        return fallback if fallback.is_file() else None
    return max(cands, key=lambda p: (image_map_version(p), p.name))
