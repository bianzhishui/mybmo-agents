#!/usr/bin/env python3
"""成员4 的素材打包：图片 → 纯 base64 + 统一命名 + 映射表（§5.3 `04-assets/`）。

成员4 的硬约束全部在这里机器化，不靠人算：

1. `04-assets/img-001.png…` 原图必须留（不可再生）；
2. `04-assets/img-001.b64…` 是**纯 base64，不带 `data:` 前缀**；
3. `imageName` ≤200 字符（**1 个汉字算 2**）；
4. 单张 ≤10MB、整批 ≤10MB、单次 ≤20 张；
5. `image-map.json` 里 `imageId` 一律留 `null`（真实 ID 由成员8 在 ⑧-2 上传后回填，
   用 `backfill_image_ids.py`）；同图 MD5 重复要提示"可能复用已有图"。

**图片四关硬闸（2026-11-14 按 mybmo-mcp 0.3.2 口径补齐，实证 call_id 可复核）**

上传前必须先过下面四关，任一不过就**直接不发上传调用**（本脚本失败退出，调用方不得继续）：

| 关 | 判定 | 口径来源 |
|---|---|---|
| ① 尺寸 | **精确宽高白名单、方向固定**：商品推广只有 `370×245 / 1140×640 / 750×1000`（自定义直播推广只有 `1140×640`）。**禁止按"宽高比 / 最小尺寸"推导** —— 375×248、960×540（同为 16:9）、1000×750、640×1140 实测全被 `901217` 拒；750×1000 过、1000×750 不过 | 平台硬闸（服务端拒） |
| ② 张数 | `pictures` ≤ **18** 张；19 张 → `901217「创意设置图片数量超过上限」`。schema 没有 `maxItems`，参数校验不拦，**拦在服务端** | 平台硬闸（服务端拒） |
| ③ 内容红线 | 无 AI 生成水印、无"使用前/使用后"对比、无绝对化/保证性用语（如"不伤表面""污渍自动溶解"） | **⚠ 我方审计口径，不是平台硬闸**：平台不会因此拒单（img-003 带水印+前后对比仍挂在在投计划里、`astat=0`、计划 `status=2` 非 21）。这条由审计官裁定，本脚本只负责让成员4 显式声明并留痕 |
| ④ MD5 | 与 `04-assets/image-map.json` 已有记录一致（同 `file` 不同 md5 = 冲突；同 md5 不同 file = 提示"可能复用同一 imageId"） | 本地一致性 |

本脚本**不调用任何接口**——它只准备物料，四关都在本地做完；真正 `uploadImage` 由成员8 在 ⑧-2 执行。

用法：
    python3 scripts/assets_pack.py --from ./pics --image-name-prefix "珠宝清洗剂" \
        --content-attest ./content-attest.json
    python3 scripts/assets_pack.py --from a.png b.jpg --run <runId> --content-attest ./content-attest.json
    python3 scripts/assets_pack.py --from ./pics --dry-run            # 只跑四关，不落盘

`--content-attest` 是成员4 对每张图的内容红线自查声明（JSON）：
    {"img-001.png": {"aiWatermark": false, "beforeAfter": false, "absoluteClaims": false}}
（也接受外层包一层 `{"assets": {...}}`）。缺该文件、缺某张图的条目、或任一值为 true → 拒绝打包。

退出码：0 成功 / 1 校验不过 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import struct
import sys
import unicodedata
from pathlib import Path

from _runbook import resolve_run_dir

MAX_PER_IMAGE = 10 * 1024 * 1024   # 单张 10MB
MAX_BATCH = 10 * 1024 * 1024       # 整批 10MB
MAX_COUNT = 20                     # uploadImage 单次 20 张（接口上限）
MAX_PICTURES = 18                  # 建创意时 pictures 张数上限（服务端拦）
MAX_NAME_WIDTH = 200               # imageName 上限，1 汉字算 2
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# ① 精确宽高白名单、方向固定（不是宽高比，也不是最小尺寸）
PICTURES_ALLOWED_SIZES = {
    (370, 245),    # 横幅
    (1140, 640),   # 横幅（商品推广 + 自定义直播推广共用）
    (750, 1000),   # 竖版 3:4
}
PICTURES_ALLOWED_SIZES_LIVE_ONLY = {(1140, 640)}  # 自定义直播推广 only

# ③ 内容红线（我方审计口径，平台不拦）
CONTENT_RED_LINES = [
    "AI 生成水印（如『豆包AI生成』『AI 生成』等第三方工具水印）",
    "使用前 / 使用后 前后对比图",
    "绝对化 / 保证性用语（如『不伤表面』『污渍自动溶解』『彻底清除』『永久』）",
]


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


def _png_size(data: bytes) -> tuple[int, int]:
    # IHDR 紧跟在 8 字节签名 + 4 字节长度 + 4 字节类型之后
    w, h = struct.unpack(">II", data[16:24])
    return int(w), int(h)


def _gif_size(data: bytes) -> tuple[int, int]:
    w, h = struct.unpack("<HH", data[6:10])
    return int(w), int(h)


def _bmp_size(data: bytes) -> tuple[int, int]:
    w, h = struct.unpack("<ii", data[18:26])
    return abs(int(w)), abs(int(h))


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(data)
    while i + 9 < n:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        # SOF0..SOF15，排除 DHT(C4)/JPG(C8)/DAC(CC)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", data[i + 5:i + 9])
            return int(w), int(h)
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg_len
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    fmt = data[12:16]
    if fmt == b"VP8X":
        w = int.from_bytes(data[24:27], "little") + 1
        h = int.from_bytes(data[27:30], "little") + 1
        return w, h
    if fmt == b"VP8 ":
        w, h = struct.unpack("<HH", data[26:30])
        return w & 0x3FFF, h & 0x3FFF
    if fmt == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def image_size(path: Path) -> tuple[int, int] | None:
    """用标准库读图片宽高（不引入 Pillow）。无法识别返回 None。"""
    data = path.read_bytes()
    fmt = looks_like_image(data)
    try:
        if fmt == "png":
            return _png_size(data)
        if fmt == "gif":
            return _gif_size(data)
        if fmt == "bmp":
            return _bmp_size(data)
        if fmt == "jpeg":
            return _jpeg_size(data)
        if fmt == "webp":
            return _webp_size(data)
    except (struct.error, IndexError):
        return None
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


def load_attest(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit(f"--content-attest 文件不存在：{p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("assets", data) if isinstance(data, dict) else {}


def print_red_lines() -> None:
    print("[内容红线自查清单]（⚠ 我方审计口径，平台不会因此拒单）", file=sys.stderr)
    for i, line in enumerate(CONTENT_RED_LINES, 1):
        print(f"  {i}. {line}", file=sys.stderr)


def size_problem(size: tuple[int, int] | None) -> str | None:
    if size is None:
        return "读不出宽高（无法确认是否命中白名单）"
    if size in PICTURES_ALLOWED_SIZES:
        return None
    allowed = " / ".join(f"{w}×{h}" for w, h in sorted(PICTURES_ALLOWED_SIZES))
    if size in {(h, w) for w, h in PICTURES_ALLOWED_SIZES}:
        return (
            f"{size[0]}×{size[1]} 是白名单尺寸的【转置】——方向固定，横竖颠倒会被 901217 拒；"
            f"允许：{allowed}"
        )
    return f"{size[0]}×{size[1]} 不在精确白名单内；允许：{allowed}（按精确宽高匹配，不按宽高比/最小尺寸）"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="素材打包（只准备物料，不调接口）")
    ap.add_argument("--from", dest="sources", nargs="+", required=True, help="图片目录或图片文件")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--image-name-prefix", default="", help="拼成 `<prefix>-主图N`")
    ap.add_argument("--used-in", action="append", default=[], help="对应创意，如 title#1（可重复）")
    ap.add_argument("--content-attest", default=None, help="内容红线自查声明 JSON（每张图一条）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    assets_dir = run_dir / "04-assets"
    files = collect_inputs(args.sources)
    problems: list[str] = []

    if not files:
        print("没找到任何图片", file=sys.stderr)
        return 2

    # ② 张数上限
    if len(files) > MAX_COUNT:
        problems.append(f"单次 {len(files)} 张 > uploadImage 单次上限 {MAX_COUNT} 张")
    if len(files) > MAX_PICTURES:
        problems.append(
            f"单次 {len(files)} 张 > pictures 张数上限 {MAX_PICTURES} 张"
            "（19 张会 901217「创意设置图片数量超过上限」；schema 无 maxItems，拦在服务端）"
        )

    # ③ 内容红线：必须逐张声明，缺则不打包
    attest = load_attest(args.content_attest)
    if not attest:
        problems.append(
            "缺 --content-attest（内容红线自查声明）：上传前必须对每张图声明"
            "『无水印 / 无前后对比 / 无绝对化用语』，见下方清单"
        )

    batch = 0
    for f in files:
        size = f.stat().st_size
        batch += size
        if size > MAX_PER_IMAGE:
            problems.append(f"{f.name} {size / 1048576:.1f}MB > 单张上限 10MB")
        # ① 精确尺寸白名单
        wh = image_size(f)
        sp = size_problem(wh)
        if sp:
            problems.append(f"{f.name} 尺寸不合规：{sp}")
        else:
            print(f"[size ok] {f.name} {wh[0]}×{wh[1]} 命中白名单")
        # ③ 逐张自查
        entry = attest.get(f.name) if isinstance(attest, dict) else None
        if not entry:
            problems.append(f"{f.name} 缺内容红线自查条目（--content-attest 里没有该文件名）")
        else:
            for key, label in (
                ("aiWatermark", "AI 生成水印"),
                ("beforeAfter", "使用前/后对比"),
                ("absoluteClaims", "绝对化/保证性用语"),
            ):
                if entry.get(key) is True:
                    problems.append(f"{f.name} 内容红线未过：{label}（我方审计口径，不带此图）")
    if batch > MAX_BATCH:
        problems.append(f"整批 {batch / 1048576:.1f}MB > 上限 10MB")

    existing = load_map(assets_dir / "image-map.json")
    by_md5 = {a.get("md5"): a for a in existing["assets"] if a.get("md5")}
    by_file = {a.get("file"): a for a in existing["assets"] if a.get("file")}
    used_files = set(by_file)
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
                "_size": image_size(f),
                "_dup": md5 in by_md5,
                "_md5": md5,
            }
        )

    # ④ md5 与 04-assets 映射一致：同 file 不同 md5 = 冲突
    for row in plan:
        prior = by_file.get(row["file"])
        if prior and prior.get("md5") and prior["md5"] != row["_md5"]:
            problems.append(
                f"{row['file']} 已存在于 image-map.json 且 md5={prior['md5']} "
                f"≠ 本次 {row['_md5']}（同 file 不同内容，会破坏溯源）"
            )

    if problems:
        print_red_lines()
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        return 1

    for row in plan:
        wh = row["_size"]
        note = "（⚠ 与已有图同 MD5，可能复用同一 imageId）" if row["_dup"] else ""
        print(f"[ok] {row['_src'].name} -> {row['file']} / {row['base64File']}  "
              f"{wh[0]}×{wh[1]}  imageName={row['imageName']}{note}")

    if args.dry_run:
        print(f"[dry-run] 四关全过，将写入 {assets_dir}")
        return 0

    assets_dir.mkdir(parents=True, exist_ok=True)
    for row in plan:
        assert row["file"] not in used_files, f"文件名冲突：{row['file']}"
        shutil.copyfile(row["_src"], assets_dir / row["file"])
        (assets_dir / row["base64File"]).write_text(
            base64.b64encode(row["_bytes"]).decode("ascii"), encoding="ascii"
        )
        existing["assets"].append(
            {
                k: row[k]
                for k in ("file", "base64File", "imageName", "usedIn", "imageId", "md5")
            }
            | {
                "width": row["_size"][0],
                "height": row["_size"][1],
                "dimensionGate": "exact-whitelist",
            }
        )

    (assets_dir / "image-map.json").write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n{assets_dir / 'image-map.json'}  共 {len(existing['assets'])} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
