#!/usr/bin/env python3
"""从 `08-execution/calls.jsonl` 反推回滚清单（出事后照单执行）。

只读，不调接口。按"写入的逆序"给出每一步**建议动作**，并标注哪些有接口、哪些只能人工：
- `addCampaignShopping`(ok) → `deleteCampaignShopping {campaignShoppingIds:[id]}`（不可逆，需二次确认；**不传 optFrom**）
- `uploadImage` → 图库无删除入口，只能留档；未被任何计划引用则无副作用
- `addKeywordPackages` → 包无删除接口；若已绑包，用 `updateCampaignShopping` 改 `audience`（合并语义，只能改值）
- `bindPlanKeywordPackages` → 回写 `audience.fcKeywordWtpids` 为绑定前值（取绑前回读/before-snapshot）
- `updateCampaignShopping` → 按 `before-snapshot.json` 原值回写（`material` 需读全 + 带 `ideaType`）

用法：
    python3 scripts/rollback_plan.py --run-dir .ecwf/runs/x
    python3 scripts/rollback_plan.py --run-dir .ecwf/runs/x --out rollback.md

退出码：0 生成成功 / 2 用法或文件缺失。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from _runbook import resolve_run_dir

ID_RE = re.compile(r"(campaignShoppingId|packageId|imageId)\s*[=:]\s*(\d+)")


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"capability": "<unparsable>", "state": "unknown", "raw": line})
    return rows


def advise(row: dict, objects: dict) -> tuple[str, str]:
    """返回 (动作, 风险/说明)。"""
    cap = str(row.get("capability") or "")
    state = str(row.get("state") or "")
    note = str(row.get("note") or "")
    ids = dict(ID_RE.findall(note))

    if state in ("failed", "denied"):
        return ("无需回滚", "该调用未生效（failed/denied），无副作用；仍建议核对后台确认")
    if "addCampaignShopping" in cap:
        cid = ids.get("campaignShoppingId") or objects.get("campaignShoppingId")
        return (f'deleteCampaignShopping {{"campaignShoppingIds":[{cid}]}}（不传 optFrom）',
                "不可逆，需二次确认；删前先确认该计划未开量/无在投消耗")
    if "uploadImage" in cap:
        return ("（无删除接口）留档", "图库图片无删除入口；若未被计划引用则无副作用")
    if "addKeywordPackages" in cap:
        return ("（包无删除接口）", "若已绑定计划，用 updateCampaignShopping 改 audience.fcKeywordWtpids（合并语义，只能改值）")
    if "bindPlanKeywordPackages" in cap:
        return ("回写 audience.fcKeywordWtpids 为绑定前值", "取绑前回读或 before-snapshot；update 的 audience 是合并语义")
    if "updateCampaignShopping" in cap:
        return ("按 before-snapshot.json 原值回写", "material 需读全 51 字段并带 plan 级 ideaType；audience 合并语义")
    return ("人工判断", f"未知写能力：{cap}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从 calls.jsonl 生成回滚清单（只读）")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--out", help="写入文件（默认打印到 stdout）")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    calls = run_dir / "08-execution" / "calls.jsonl"
    if not calls.is_file():
        print(f"缺 {calls}（尚未执行任何写调用）", file=sys.stderr)
        return 2
    manifest = {}
    if (run_dir / "manifest.json").is_file():
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    objects = manifest.get("objects") or {}

    rows = load_jsonl(calls)
    lines = [f"# 回滚清单 · {run_dir.name}", "",
             "> 由 scripts/rollback_plan.py 从 calls.jsonl 逆序生成；执行前逐条人工确认。", ""]
    n = 0
    for row in reversed(rows):
        n += 1
        action, risk = advise(row, objects)
        lines.append(f"{n}. **{row.get('step', '?')}** `{row.get('capability', '?')}` "
                     f"call_id=`{row.get('call_id', '?')}` state={row.get('state', '?')}")
        lines.append(f"   - 动作：{action}")
        lines.append(f"   - 说明：{risk}")
    if not rows:
        lines.append("（calls.jsonl 为空）")
    text = "\n".join(lines) + "\n"
    if args.out:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}（{len(rows)} 条写调用逆序）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
