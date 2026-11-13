#!/usr/bin/env python3
"""比对改前/改后快照（§5.4 规则 4 / §七 第 5 层）。

`before-snapshot.json` 是回滚的唯一基准，`after-snapshot.json` 是"是否真按设计生效"的唯一凭据。
本脚本只读两份 JSON、逐字段比对，不调任何接口。

用法：
    python3 scripts/snapshot_diff.py                     # 取最新 run 的 08-execution/{before,after}-snapshot.json
    python3 scripts/snapshot_diff.py --run <runId>
    python3 scripts/snapshot_diff.py --before a.json --after b.json
    python3 scripts/snapshot_diff.py --json

专项检查（命中即 WARN，因为这些是最容易踩的坑）：
  * material 变空 / brand 变空  → 素材整体被清
  * fengchaoTitle 变空         → 典型的"漏读 51 字段"事故
  * pause 被置 false 而 design 仍期望 pause  → 灰度被绕过

退出码：0 无差异或只有 WARN / 1 有差异（如需"必须完全一致"的校验）/ 2 用法或环境错。
加 --allow-diff 则无论差异都返回 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _runbook import resolve_run_dir


def flatten(node: Any, trail: str = "") -> dict[str, Any]:
    """把嵌套结构压成 a/b[0]/c 形式的路径表，便于逐字段比对。"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        if not node and trail:
            out[trail] = {}
            return out
        for k, v in node.items():
            out.update(flatten(v, f"{trail}/{k}" if trail else str(k)))
    elif isinstance(node, list):
        if not node and trail:
            out[trail] = []
            return out
        for i, v in enumerate(node):
            out.update(flatten(v, f"{trail}[{i}]"))
    else:
        out[trail] = node
    return out


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    fb, fa = flatten(before), flatten(after)
    rows: list[dict[str, Any]] = []
    for path in sorted(set(fb) | set(fa)):
        if path not in fa:
            rows.append({"path": path, "kind": "removed", "before": fb[path]})
        elif path not in fb:
            rows.append({"path": path, "kind": "added", "after": fa[path]})
        elif fb[path] != fa[path]:
            rows.append({"path": path, "kind": "changed", "before": fb[path], "after": fa[path]})
    return rows


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, dict)) and not value)


def hazards(before: dict[str, Any], after: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    warns: list[str] = []
    changed = {r["path"] for r in rows if r["kind"] != "added"}
    flat_after = flatten(after)

    for path in changed:
        leaf = path.split("/")[-1].split("[")[0]
        if leaf == "pause":
            continue
        if _is_empty(flat_after.get(path)):
            warns.append(f"{path} 变成空值（改前非空）—— 检查是否漏读 51 字段")
    if any("fengchaoTitle" in p for p in changed):
        warns.append("fengchaoTitle 发生变化 —— 这是 2026-11-11 真实踩过的坑（读不回、漏读即被清空）")
    if any(p == "material/brand" or p.endswith("/brand") for p in changed):
        warns.append("material.brand 发生变化 —— brand 为空会让线上素材整体失效")
    for p in changed:
        if p == "pause" or p.endswith("/pause"):
            if flat_after.get(p) is not True:
                warns.append("pause 变化 —— 确认这是 ⑧-6 的有意开量，而不是误开")
            break
    return warns


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="快照比对")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--before")
    ap.add_argument("--after")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--allow-diff", action="store_true", help="有差异也返回 0")
    args = ap.parse_args(argv)

    if args.before and args.after:
        before_path, after_path = Path(args.before), Path(args.after)
    else:
        run_dir = resolve_run_dir(args.run, args.run_dir)
        before_path = run_dir / "08-execution" / "before-snapshot.json"
        after_path = run_dir / "08-execution" / "after-snapshot.json"

    for p in (before_path, after_path):
        if not p.is_file():
            print(f"缺快照文件：{p}", file=sys.stderr)
            return 2

    before = json.loads(before_path.read_text(encoding="utf-8"))
    after = json.loads(after_path.read_text(encoding="utf-8"))
    rows = diff(before, after)
    warns = hazards(before, after, rows)

    if args.json:
        print(json.dumps(
            {"before": str(before_path), "after": str(after_path),
             "diffCount": len(rows), "diffs": rows, "warnings": warns},
            ensure_ascii=False, indent=2))
    else:
        print(f"before: {before_path}\nafter : {after_path}\n")
        if not rows:
            print("无差异")
        for r in rows:
            if r["kind"] == "changed":
                print(f"[changed] {r['path']}\n    - {r['before']!r}\n    + {r['after']!r}")
            elif r["kind"] == "removed":
                print(f"[removed] {r['path']}  (改前 {r['before']!r})")
            else:
                print(f"[added]   {r['path']}  (改后 {r['after']!r})")
        for w in warns:
            print(f"\n[WARN] {w}")
        print(f"\n差异 {len(rows)} 处，告警 {len(warns)} 条")

    if rows and not args.allow_diff:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
