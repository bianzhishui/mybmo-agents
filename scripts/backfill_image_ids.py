#!/usr/bin/env python3
"""⑧-2 之后的回填：把 `uploadImage` 返回的真实 `imageId` 写回映射表与 payload。

两步，缺一不可：

1. `04-assets/image-map.json` 里对应素材的 `imageId` 从 `null` 填成真实 ID；
2. `06-payloads/addCampaignShopping*.json` 里的 `PENDING:<file>` 占位符换成该整数。

写入策略遵守 §5.4「冻结不改写」：默认**另存 `xxx.v2.json`**，不覆盖上游产物；
`--in-place` 才原地改（只在确认上游尚未被下游消费时用）。

⚠️ 本项目**不调用任何接口**：`--response` 是成员8 从 MCP 拿到的 uploadImage 返回值
（自己存成 JSON 文件），本脚本只做纯文本回填。

用法：
    python3 scripts/backfill_image_ids.py --run <runId> --response upload.json
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

from _runbook import resolve_run_dir

PENDING_RE = re.compile(r"^PENDING:(.+)$")


def find_pairs(node: Any, trail: str = "") -> list[tuple[str, int]]:
    """从 uploadImage 的任意返回结构里捞出 (imageName, imageId) 对。"""
    found: list[tuple[str, int]] = []
    if isinstance(node, dict):
        name = node.get("imageName") or node.get("name")
        image_id = node.get("imageId") or node.get("id")
        if isinstance(name, str) and isinstance(image_id, int):
            found.append((name, image_id))
        for v in node.values():
            found.extend(find_pairs(v, trail))
    elif isinstance(node, list):
        for v in node:
            found.extend(find_pairs(v, trail))
    return found


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
    ap.add_argument("--response", required=True, help="uploadImage 返回值的 JSON 文件")
    ap.add_argument("--in-place", action="store_true", help="原地改写（默认另存 .v2.json）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    response_path = Path(args.response).expanduser()
    if not response_path.is_file():
        print(f"响应文件不存在：{response_path}", file=sys.stderr)
        return 2
    response = json.loads(response_path.read_text(encoding="utf-8"))

    pairs = find_pairs(response)
    if not pairs:
        print("响应里没找到 (imageName, imageId) 对——检查是不是 uploadImage 的返回值", file=sys.stderr)
        return 2

    map_path = run_dir / "04-assets" / "image-map.json"
    if not map_path.is_file():
        print(f"缺 {map_path}（先跑 scripts/assets_pack.py）", file=sys.stderr)
        return 2
    image_map = json.loads(map_path.read_text(encoding="utf-8"))
    by_name = {a.get("imageName"): a for a in image_map.get("assets", [])}

    mapping: dict[str, int] = {}
    unmatched: list[str] = []
    for name, image_id in pairs:
        entry = by_name.get(name)
        if entry is None:
            unmatched.append(name)
            continue
        entry["imageId"] = image_id
        mapping[entry["file"]] = image_id
        print(f"[map] {entry['file']} ({name}) -> imageId {image_id}")
    if unmatched:
        print(f"[warn] 响应里有 {len(unmatched)} 个 imageName 在映射表中找不到：{unmatched}", file=sys.stderr)

    changed: list[Path] = []
    hits: list[str] = []
    for p in sorted((run_dir / "06-payloads").glob("addCampaignShopping*.json")):
        if p.name.endswith(".v2.json"):
            continue
        payload = json.loads(p.read_text(encoding="utf-8"))
        local_hits: list[str] = []
        updated = replace_pending(payload, mapping, local_hits)
        if not local_hits:
            print(f"[skip] {p.name} 没有可替换的 PENDING 占位符")
            continue
        target = p if args.in_place else p.with_name(p.stem + ".v2.json")
        hits.extend(f"{p.name}: {h}" for h in local_hits)
        changed.append(target)
        if not args.dry_run:
            target.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[payload] {p.name} -> {target.name}  替换 {len(local_hits)} 处")

    if not args.dry_run:
        map_path.write_text(json.dumps(image_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[map] {map_path} 已更新 {len(mapping)} 条 imageId")

    if not hits and not mapping:
        print("没有任何回填，检查 --response 与 run 是否对应", file=sys.stderr)
        return 1

    # 回填后立刻做一次 schema 校验，确认没把结构改坏
    try:
        from jsonschema import Draft7Validator
    except ImportError:
        print("[warn] 没装 jsonschema，跳过回填后校验（.venv/bin/pip install jsonschema）", file=sys.stderr)
        return 0
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "addCampaignShopping.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    bad = 0
    for target in changed:
        if not target.is_file():
            continue
        errs = list(Draft7Validator(schema).iter_errors(json.loads(target.read_text(encoding="utf-8"))))
        for e in errs:
            bad += 1
            print(f"[FAIL] {target.name}: {'/'.join(map(str, e.absolute_path))} {e.message}", file=sys.stderr)
    if bad:
        return 1
    print("回填后 schema 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
