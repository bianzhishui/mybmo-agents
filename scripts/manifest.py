#!/usr/bin/env python3
"""run 状态机的读写（docs/ec-shopping-workflow-v2.md §5.5）。

子命令：
    show                       打印当前 manifest
    set <stage> <status>       改一个阶段状态
        [--file PATH] [--by WHO] [--reason TEXT] [--retry-to STAGE] [--bump-version]
    next                       打印"下一步该谁"（第一个非 done 的阶段）
    object <key> <value>       写 objects 下的键（campaignShoppingId 等）

状态枚举：pending / running / done / rejected / frozen
硬约束：`rejected` 必须同时给 --reason 与 --retry-to，否则拒绝执行。

退出码：0 ok / 1 校验不过 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _runbook import resolve_run_dir as _resolve_run_dir

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
OWNER = {
    "01-product": "成员1",
    "02-keywords": "成员2",
    "03-copy": "成员3",
    "04-assets": "成员4",
    "05-plan": "成员5",
    "06-payloads": "成员6",
    "07-audit": "成员7",
    "08-execution": "成员8",
}
STATUSES = {"pending", "running", "done", "rejected", "frozen"}


resolve_run = _resolve_run_dir


def load(run_dir: Path) -> dict:
    path = run_dir / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"缺 manifest.json：{run_dir}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save(run_dir: Path, manifest: dict) -> None:
    with (run_dir / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ECWF run 状态机")
    ap.add_argument("--run", help="runId（目录名），默认取最新")
    ap.add_argument("--run-dir", help="直接指定 run 目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("show", help="打印 manifest")
    sub.add_parser("next", help="下一步该谁")

    p_set = sub.add_parser("set", help="改阶段状态")
    p_set.add_argument("stage", choices=STAGES)
    p_set.add_argument("status", choices=sorted(STATUSES))
    p_set.add_argument("--file")
    p_set.add_argument("--by")
    p_set.add_argument("--reason")
    p_set.add_argument("--retry-to", choices=STAGES)
    p_set.add_argument("--bump-version", action="store_true", help="冻结后改写时递增 version")

    p_obj = sub.add_parser("object", help="写 objects 键")
    p_obj.add_argument("key")
    p_obj.add_argument("value")

    args = ap.parse_args(argv)
    run_dir = resolve_run(args.run, args.run_dir)
    manifest = load(run_dir)

    if args.cmd == "show":
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "next":
        stages = manifest.get("stages", {})
        for stage in STAGES:
            st = stages.get(stage, {}).get("status", "pending")
            if st != "done":
                extra = ""
                if st == "rejected":
                    extra = f"（打回原因：{stages[stage].get('reason', '?')}，retryTo={stages[stage].get('retryTo', '?')}）"
                print(f"{stage}  {st}  负责人：{OWNER[stage]}{extra}")
                return 0
        print("全部 done，可归档")
        return 0

    if args.cmd == "object":
        try:
            value = json.loads(args.value)
        except json.JSONDecodeError:
            value = args.value
        manifest.setdefault("objects", {})[args.key] = value
        save(run_dir, manifest)
        print(f"objects.{args.key} = {value!r}")
        return 0

    # set
    if args.status == "rejected" and not (args.reason and args.retry_to):
        print("status=rejected 必须同时给 --reason 与 --retry-to", file=sys.stderr)
        return 1

    stage_entry = manifest.setdefault("stages", {}).setdefault(args.stage, {"status": "pending"})
    was_done = stage_entry.get("status") == "done"
    stage_entry["status"] = args.status
    stage_entry.setdefault("by", OWNER[args.stage])
    if args.by:
        stage_entry["by"] = args.by
    if args.file:
        stage_entry["file"] = args.file
    if args.status == "rejected":
        stage_entry["reason"] = args.reason
        stage_entry["retryTo"] = args.retry_to
    else:
        stage_entry.pop("reason", None)
        stage_entry.pop("retryTo", None)
    if args.bump_version or was_done:
        stage_entry["version"] = int(stage_entry.get("version", 1)) + (1 if (args.bump_version or was_done) else 0)

    save(run_dir, manifest)
    print(f"{args.stage} -> {args.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
