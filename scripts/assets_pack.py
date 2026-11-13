#!/usr/bin/env python3
"""成员4 的素材打包：图片 → 纯 base64 + 统一命名 + 映射表（§5.3 `04-assets/`）。

成员4 的硬约束全部在这里机器化，不靠人算：

1. `04-assets/img-001.png…` 原图必须留（不可再生）；
2. `04-assets/img-001.b64…` 是**纯 base64，不带 `data:` 前缀**；
3. `imageName` ≤200 字符（**1 个汉字算 2**）；
4. 单张 ≤10MB、整批 ≤10MB、单次 ≤20 张；
5. `image-map.json` 里 `imageId` 一律留 `null`（真实 ID 由成员8 在 ⑧-2 上传后回填，
   用 `backfill_image_ids.py`）；同图 MD5 重复要提示"可能复用已有图"。

**本脚本不调用任何接口**——它只准备物料。

用法：
    python3 scripts/assets_pack.py --from ./pics --image-name-prefix "珠宝清洗剂"
    python3 scripts/assets_pack.py --from a.png b.jpg --run <runId>
    python3 scripts/assets_pack.py --from ./pics --dry-run

退出码：0 成功 / 1 校验不过 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import sys
import unicodedata
from pathlib import Path

from _runbook import resolve_run_dir

MAX_PER_IMAGE = 10 * 1024 * 1024   # 单张 10MB
MAX_BATCH = 10 * 1024 * 1024       # 整批 10MB
MAX_COUNT = 20                     # 单次 20 张
MAX_NAME_WIDTH = 200               # imageName 上限，1 汉字算 2
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def disp_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def looks_like_image(data: bytes) -> str | None:
    """按魔数判断格式；返回格式名或 None。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:2] == b"BM":
        return "bmp"
    return None


def collect_inputs(sources: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in sources:
        p = Path(raw).expanduser()
        if p.is_dir():
            files.extend(sorted(q for q in p.iterdir() if q.suffix.lower() in IMAGE_SUFFIXES))
        elif p.is_file():
            files.append(p)
        else:
            raise SystemExit(f"输入不存在：{p}")
    return files


def load_map(path: Path) -> dict:
    if not path.is_file():
        return {"assets": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("assets", [])
    return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="素材打包（只准备物料，不调接口）")
    ap.add_argument("--from", dest="sources", nargs="+", required=True, help="图片目录或图片文件")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--image-name-prefix", default="", help="拼成 `<prefix>-主图N`")
    ap.add_argument("--used-in", action="append", default=[], help="对应创意，如 title#1（可重复）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    assets_dir = run_dir / "04-assets"
    files = collect_inputs(args.sources)
    problems: list[str] = []

    if not files:
        print("没找到任何图片", file=sys.stderr)
        return 2
    if len(files) > MAX_COUNT:
        problems.append(f"单次 {len(files)} 张 > 上限 {MAX_COUNT} 张")
    batch = 0
    for f in files:
        size = f.stat().st_size
        batch += size
        if size > MAX_PER_IMAGE:
            problems.append(f"{f.name} {size / 1048576:.1f}MB > 单张上限 10MB")
    if batch > MAX_BATCH:
        problems.append(f"整批 {batch / 1048576:.1f}MB > 上限 10MB")

    existing = load_map(assets_dir / "image-map.json")
    by_md5 = {a.get("md5"): a for a in existing["assets"] if a.get("md5")}
    used_files = {a.get("file") for a in existing["assets"]}
    next_index = 1 + max(
        [int(a["file"].split("-")[1].split(".")[0]) for a in existing["assets"]
         if a.get("file", "").startswith("img-")],
        default=0,
    )

    plan: list[dict] = []
    for f in files:
        raw = f.read_bytes()
        fmt = looks_like_image(raw)
        if fmt is None:
            problems.append(f"{f.name} 不是可识别的图片（魔数不匹配）")
        if f.suffix.lower() == ".png" and fmt not in (None, "png"):
            problems.append(f"{f.name} 后缀是 .png 但实际是 {fmt}")
        md5 = hashlib.md5(raw).hexdigest()
        index = next_index
        next_index += 1
        stem = f"img-{index:03d}"
        image_name = f"{args.image_name_prefix}-主图{index}" if args.image_name_prefix else f.stem
        if disp_width(image_name) > MAX_NAME_WIDTH:
            problems.append(f"imageName {image_name!r} 显示宽度 {disp_width(image_name)} > {MAX_NAME_WIDTH}")
        plan.append(
            {
                "file": f"{stem}{f.suffix.lower()}",
                "base64File": f"{stem}.b64",
                "imageName": image_name,
                "usedIn": list(args.used_in),
                "imageId": None,
                "md5": md5,
                "_src": f,
                "_bytes": raw,
                "_dup": md5 in by_md5,
            }
        )

    if problems:
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        return 1

    for row in plan:
        note = "（⚠ 与已有图同 MD5，可能复用同一 imageId）" if row["_dup"] else ""
        print(f"[ok] {row['_src'].name} -> {row['file']} / {row['base64File']}  imageName={row['imageName']}{note}")

    if args.dry_run:
        print(f"[dry-run] 将写入 {assets_dir}")
        return 0

    assets_dir.mkdir(parents=True, exist_ok=True)
    for row in plan:
        assert row["file"] not in used_files, f"文件名冲突：{row['file']}"
        shutil.copyfile(row["_src"], assets_dir / row["file"])
        (assets_dir / row["base64File"]).write_text(
            base64.b64encode(row["_bytes"]).decode("ascii"), encoding="ascii"
        )
        existing["assets"].append({k: row[k] for k in ("file", "base64File", "imageName", "usedIn", "imageId", "md5")})

    (assets_dir / "image-map.json").write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{assets_dir / 'image-map.json'}  共 {len(existing['assets'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
