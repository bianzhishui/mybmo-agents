#!/usr/bin/env python3
"""⑧-2 之后的回填：把 `uploadImage` 返回的真实 `imageId` 写回映射表与 payload。

两步，缺一不可：

1. `04-assets/image-map.vN.json` 里对应素材的 `imageId` 从 `null` 填成真实 ID；
2. `06-payloads/addCampaignShopping*.json` 里的 `PENDING:<file>` 占位符换成该整数。

**2026-11-14 修复（P1-9 / T1）**
- 读映射表不再硬编码 `image-map.json`（v1）：默认按 `.vN` 取最新一版，或 `--image-map` 指定；
  输出一律落**新版本** `image-map.v(N+1).json` 并写 `supersedes`/`version`（遵守"冻结不改写"），
  `--in-place` 才原地改。
- 精确回填：用返回体的 `width`/`height`/`imageName`/`signature`（= md5）多路匹配，
  **不再要求响应必须带 imageName**（`uploadImage` 实际返回里就没有 imageName，
  这正是旧版在 ⑧-2 自动回填失败、被迫手工的原因）。

⚠️ 本项目**不调用任何接口**：`--response` 是成员8 从 MCP 拿到的 uploadImage 返回值
（自己存成 JSON 文件），本脚本只做纯文本回填。

用法：
    python3 scripts/backfill_image_ids.py --run <runId> --response upload.json
    python3 scripts/backfill_image_ids.py --response upload.json --image-map 04-assets/image-map.v6.json
    python3 scripts/backfill_image_ids.py --response upload.json --in-place

退出码：0 成功 / 1 回填后校验不过 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from _runbook import image_map_version, pick_latest_image_map, resolve_run_dir

PENDING_RE = re.compile(r"^PENDING:(.+)$")
def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_pairs(node: Any) -> list[dict[str, Any]]:
    """从 uploadImage 的任意返回结构里捞出回填所需的字段集合。

    返回 [{imageName?, imageId, width?, height?, signature?}]。imageName 可能缺席
    （实测 uploadImage 返回就无 imageName），所以其它字段用于精确匹配。
    """
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        image_id = node.get("imageId")
        if isinstance(image_id, int):
            found.append(
                {
                    "imageName": node.get("imageName") or node.get("name"),
                    "imageId": image_id,
                    "width": node.get("width"),
                    "height": node.get("height"),
                    "signature": node.get("signature") or node.get("md5"),
                }
            )
        for v in node.values():
            found.extend(find_pairs(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(find_pairs(v))
    return found


def entry_dimensions(entry: dict) -> tuple[int | None, int | None]:
    if "width" in entry and "height" in entry:
        return entry.get("width"), entry.get("height")
    size = entry.get("size") or {}
    if isinstance(size, dict):
        return size.get("width"), size.get("height")
    return None, None


def match_entry(pairs: list[dict], assets: list[dict]) -> tuple[list[tuple[dict, dict]], list[str]]:
    """把返回项与映射表条目精确匹配：imageName → signature/md5 → width×height（唯一）。"""
    matched: list[tuple[dict, dict]] = []
    unmatched: list[str] = []
    used: set[int] = set()
    for pair in pairs:
        chosen = None
        name = pair.get("imageName")
        if name:
            for a in assets:
                if a.get("imageName") == name:
                    chosen = a
                    break
        if chosen is None and pair.get("signature"):
            for a in assets:
                if a.get("md5") and a["md5"] == pair["signature"]:
                    chosen = a
                    break
        if chosen is None and pair.get("width") and pair.get("height"):
            dims = (pair["width"], pair["height"])
            cands = [a for a in assets if entry_dimensions(a) == dims]
            if len(cands) == 1:
                chosen = cands[0]
        if chosen is None:
            unmatched.append(f"imageId={pair['imageId']} imageName={name} "
                             f"size={pair.get('width')}×{pair.get('height')} signature={pair.get('signature')}")
            continue
        used.add(id(chosen))
        matched.append((pair, chosen))
    return matched, unmatched


def replace_pending(node: Any, mapping: dict[str, int], hits: list[str]) -> Any:
    if isinstance(node, dict):
        return {k: replace_pending(v, mapping, hits) for k, v in node.items()}
    if isinstance(node, list):
        return [replace_pending(v, mapping, hits) for v in node]
    if isinstance(node, str):
        m = PENDING_RE.match(node)
        if m and m.group(1) in mapping:
            hits.append(f"{node} -> {mapping[m.group(1)]}")
            return mapping[m.group(1)]
    return node


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="回填 imageId（不调接口）")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--image-map", help="显式指定 image-map（默认取 04-assets 下最新一版）")
    ap.add_argument("--response", required=True, help="uploadImage 返回值的 JSON 文件")
    ap.add_argument("--in-place", action="store_true", help="原地改写映射表（默认落 image-map.v(N+1).json）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    response_path = Path(args.response).expanduser()
    if not response_path.is_file():
        print(f"响应文件不存在：{response_path}", file=sys.stderr)
        return 2
    response = load_json(response_path)

    pairs = find_pairs(response)
    if not pairs:
        print("响应里没找到任何 imageId——检查是不是 uploadImage 的返回值", file=sys.stderr)
        return 2

    map_path = Path(args.image_map).expanduser() if args.image_map else pick_latest_image_map(run_dir / "04-assets")
    if map_path is None:
        raise SystemExit(f"缺 04-assets/image-map*.json（先跑 scripts/assets_pack.py）")
    if not map_path.is_file():
        print(f"缺映射表：{map_path}", file=sys.stderr)
        return 2
    image_map = load_json(map_path)
    assets = [a for a in image_map.get("assets", []) if isinstance(a, dict)]

    matched, unmatched = match_entry(pairs, assets)
    if unmatched:
        print(f"[warn] 响应里有 {len(unmatched)} 项无法在 {map_path.name} 精确匹配：", file=sys.stderr)
        for u in unmatched:
            print(f"       {u}", file=sys.stderr)

    mapping: dict[str, int] = {}
    for pair, entry in matched:
        # 用返回的 width/height 做一次一致性断言，防止张冠李戴
        rw, rh = pair.get("width"), pair.get("height")
        ew, eh = entry_dimensions(entry)
        if rw and rh and ew and eh and (rw, rh) != (ew, eh):
            print(f"[FAIL] {entry.get('file')} 映射维度 {ew}×{eh} ≠ 返回维度 {rw}×{rh}", file=sys.stderr)
            return 1
        entry["imageId"] = pair["imageId"]
        mapping[entry["file"]] = pair["imageId"]
        if rw and rh and not (ew and eh):
            entry["width"], entry["height"] = rw, rh
        print(f"[map] {entry.get('file')} ({entry.get('imageName')}) -> imageId {pair['imageId']} "
              f"[{rw}×{rh}]")

    if not mapping:
        print("没有任何回填，检查 --response 与 run/--image-map 是否对应", file=sys.stderr)
        return 1

    changed: list[Path] = []
    hits: list[str] = []
    # 只回填"当前应执行版"（addCampaignShopping 的最高 .vN），历史版本是冻结的，
    # 不回头改（P2/K：冻结 payload 一律不原地重写）。
    payloads = sorted(
        (run_dir / "06-payloads").glob("addCampaignShopping*.json"),
        key=lambda q: int(re.match(r"^.*?(?:\.v(\d+))?\.json$", q.name).group(1) or 1),
    )
    for p in payloads[-1:]:
        payload = load_json(p)
        local_hits: list[str] = []
        updated = replace_pending(payload, mapping, local_hits)
        if not local_hits:
            print(f"[skip] {p.name} 没有可替换的 PENDING 占位符（可能已回填）")
            continue
        if args.in_place:
            target = p
        else:
            m = re.match(r"^(.*?)(?:\.v(\d+))?\.json$", p.name)
            stem = m.group(1)
            nxt = int(m.group(2) or 1) + 1
            target = p.with_name(f"{stem}.v{nxt}.json")
            while target.exists():  # 绝不覆盖已存在的版本文件
                nxt += 1
                target = p.with_name(f"{stem}.v{nxt}.json")
        hits.extend(f"{p.name}: {h}" for h in local_hits)
        changed.append(target)
        if not args.dry_run:
            target.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[payload] {p.name} -> {target.name}  替换 {len(local_hits)} 处")

    if not args.dry_run:
        if args.in_place:
            out_map = map_path
        else:
            nxt = image_map_version(map_path) + 1
            out_map = map_path.with_name(f"image-map.v{nxt}.json")
            image_map["supersedes"] = map_path.name
            image_map["version"] = nxt
        out_map.write_text(json.dumps(image_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[map] {out_map} 已更新 {len(mapping)} 条 imageId")

    if args.dry_run:
        print("[dry-run] 未写盘")
        return 0

    # 回填后立刻做一次 schema 校验，确认没把结构改坏
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        print("[warn] 没装 jsonschema，跳过回填后校验（.venv/bin/pip install jsonschema）", file=sys.stderr)
        return 0
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "addCampaignShopping.schema.json"
    schema = load_json(schema_path)
    bad = 0
    for target in changed:
        if not target.is_file():
            continue
        errs = list(Draft7Validator(schema).iter_errors(load_json(target)))
        for e in errs:
            bad += 1
            print(f"[FAIL] {target.name}: {'/'.join(map(str, e.absolute_path))} {e.message}", file=sys.stderr)
    if bad:
        return 1
    print("回填后 schema 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
