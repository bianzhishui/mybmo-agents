#!/usr/bin/env python3
"""把一次写调用的 call_id 追加到 calls.jsonl（§5.4 规则 5 / §5.6）。

每行固定六个字段：
    {"ts": "...", "runId": "...", "step": "⑧-2", "capability": "ImageManageService.uploadImage",
     "call_id": "...", "state": "ok"}

`call_id` 用来回连 mybmo-mcp 的 `audit_log`（那张表没有 run_id），所以**这一行是回滚与事后还原的唯一线索**。

用法：
    python3 scripts/record_call.py --step ⑧-2 --capability uploadImage --call-id abc123 --state ok
    python3 scripts/record_call.py --step ⑧-4 --capability addCampaignShopping --call-id abc124 \
        --state unknown --note "超时，结果待查"

退出码：0 成功 / 1 参数校验不过 / 2 环境错。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from _runbook import resolve_run_dir

REQUIRED = ("ts", "runId", "step", "capability", "call_id", "state")
# 追加字段：account（账户一致性核对）、prev（上一行的 sha256，防事后篡改）


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="追加 calls.jsonl")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--step", required=True, help="如 ⑧-2")
    ap.add_argument("--capability", required=True, help="如 uploadImage / addCampaignShopping")
    ap.add_argument("--call-id", required=True)
    ap.add_argument("--state", required=True, help="ok / failed / unknown")
    ap.add_argument("--note", default=None)
    ap.add_argument("--ts", default=None, help="默认当前本地时间")
    args = ap.parse_args(argv)

    if not args.call_id.strip():
        print("--call-id 不能为空：没有 call_id 就无法回连 audit_log", file=sys.stderr)
        return 1
    import re as _re
    if not _re.fullmatch(r"[0-9a-fA-F]{8,64}", args.call_id.strip()):
        print(f"--call-id 形如 12 位十六进制（收到 {args.call_id!r}）：无法回连 audit_log", file=sys.stderr)
        return 1
    if args.state not in {"ok", "failed", "unknown"}:
        print(f"--state 只能是 ok / failed / unknown，收到 {args.state!r}", file=sys.stderr)
        return 1

    run_dir = resolve_run_dir(args.run, args.run_dir)
    manifest_path = run_dir / "manifest.json"
    run_id = None
    if manifest_path.is_file():
        try:
            run_id = json.loads(manifest_path.read_text(encoding="utf-8")).get("runId")
        except json.JSONDecodeError:
            run_id = None
    run_id = run_id or run_dir.name
    account = None
    if manifest_path.is_file():
        try:
            account = json.loads(manifest_path.read_text(encoding="utf-8")).get("account")
        except json.JSONDecodeError:
            account = None

    record = {
        "ts": args.ts or datetime.now().astimezone().isoformat(timespec="seconds"),
        "runId": run_id,
        "step": args.step,
        "capability": args.capability,
        "call_id": args.call_id,
        "state": args.state,
    }
    if args.note:
        record["note"] = args.note
    if account:
        record["account"] = account

    out = run_dir / "08-execution" / "calls.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = ""
    if out.is_file():
        lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            prev = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
    record["prev"] = prev
    ordered = {k: record[k] for k in REQUIRED + tuple(k for k in record if k not in REQUIRED)}
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")

    print(f"{out} += {record['step']} {record['capability']} {record['call_id']} ({record['state']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
