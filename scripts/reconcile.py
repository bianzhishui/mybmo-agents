#!/usr/bin/env python3
"""结果未知/失败调用的"后台对账"清单（M3）。

超时/5xx 时**不要直接重发**（可能已生效，重发会建重复计划/重复包）。本脚本把 calls.jsonl 里
state=unknown/failed/pending 的行挑出来，按 capability 给出"该去查什么"，只读、不调接口。

用法：python3 scripts/reconcile.py --run-dir <run> [--json]
退出码：0 有清单或无需对账 / 2 用法错。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _runbook import resolve_run_dir

HOW = {
    "addCampaignShopping": "getCampaignShopping 按计划名/时间窗查是否已创建；已创建则复用其 id，勿重发",
    "addKeywordPackages": "getKeywordPackages 按 packageName 查是否已建；已建则复用 packageId",
    "uploadImage": "getImageList 按 imgmd5/尺寸查是否已入库；已入库则复用 imageId",
    "bindPlanKeywordPackages": "getCampaignShopping 读 audience.fcKeywordWtpids 看是否已绑",
    "updateCampaignShopping": "getCampaignShopping 回读目标字段是否已是目标值",
}
NEEDS = ("unknown", "failed", "pending")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="未知/失败调用的对账清单（只读）")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    calls = run_dir / "08-execution" / "calls.jsonl"
    if not calls.is_file():
        print("无需对账（无 calls.jsonl）")
        return 0
    rows = []
    for line in calls.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("state")) in NEEDS:
            cap = str(row.get("capability") or "")
            rows.append({"call_id": row.get("call_id"), "step": row.get("step"),
                         "capability": cap, "state": row.get("state"),
                         "howToCheck": HOW.get(cap, "人工核对后台状态")})
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        if not rows:
            print("无需对账：没有 unknown/failed/pending 调用")
        for r in rows:
            print(f"- {r['step']} {r['capability']} call_id={r['call_id']} state={r['state']}")
            print(f"    → {r['howToCheck']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
