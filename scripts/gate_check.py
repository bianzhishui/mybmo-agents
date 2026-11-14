#!/usr/bin/env python3
"""开量 / 删除前的硬闸检查（H2 / L3）。

把"别忘了看 G1~G6"变成一条命令：

    python3 scripts/gate_check.py --run-dir <run> --for execute   # ⑧ 启动前：01~07 done + 放行件 pass + 已锚定
    python3 scripts/gate_check.py --run-dir <run> --for unpause   # 开量前：所有人工闸 closed + 用户书面确认
    python3 scripts/gate_check.py --run-dir <run> --for delete --ids 123  # 删除前：二次确认件

配套文件（必须人工产出，脚本只校验）：
  - 07-audit/approval.json       审计放行件（{result:"pass", payload, sha256, evidence:[call_id]})
  - 08-execution/user-confirmation.json  开量确认（{confirmedBy, at, quote}）
  - 08-execution/delete-confirmation.json 删除确认（{confirmedBy, at, targets:[id], reason}）

退出码：0 可放行 / 1 未就绪（逐条打印缺什么）/ 2 用法错。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _runbook import STAGES, check_approval_payload, resolve_run_dir


def load(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return "INVALID"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="开量/删除前硬闸检查")
    ap.add_argument("--run")
    ap.add_argument("--run-dir")
    ap.add_argument("--for", dest="target", required=True, choices=["execute", "unpause", "delete"])
    ap.add_argument("--ids", nargs="*", default=[], help="删除目标 id（delete 时必须给）")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    manifest = load(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        print(f"缺或坏 manifest.json：{run_dir}", file=sys.stderr)
        return 2

    problems: list[str] = []
    print(f"run: {run_dir}\nfor: {args.target}\n")

    if args.target in ("execute", "unpause"):
        ap_file = run_dir / "07-audit" / "approval.json"
        approval = load(ap_file)
        if approval is None:
            problems.append("缺 07-audit/approval.json（审计放行件）")
        elif approval == "INVALID":
            problems.append("07-audit/approval.json 不是合法 JSON")
        elif approval.get("result") != "pass":
            problems.append(f"审计结论非 pass：{approval.get('result')!r}")
        else:
            stage06 = (manifest.get("stages") or {}).get("06-payloads", {}).get("file")
            payload_rel = str(approval.get("payload") or "")
            if stage06 and payload_rel and Path(stage06).name != Path(payload_rel).name:
                problems.append(f"approval.payload={Path(payload_rel).name} ≠ manifest 06={Path(stage06).name}")
            # 校验"货"本身（缺 sha256 / 文件 / 内容不符 都会被 check_approval_payload 报出）
            probs, _, _ = check_approval_payload(approval, run_dir)
            problems.extend(probs)

    if args.target == "execute":
        stages = manifest.get("stages", {})
        not_done = [s for s in STAGES[:7] if stages.get(s, {}).get("status") != "done"]
        if not_done:
            problems.append(f"01~07 未全部 done：{not_done}")


    if args.target == "unpause":
        gates = manifest.get("pendingGates") or {}
        open_gates = [g for g, v in gates.items() if (v or {}).get("status", "open") != "closed"]
        if open_gates:
            problems.append(f"人工闸未闭环：{open_gates}")
        conf = load(run_dir / "08-execution" / "user-confirmation.json")
        if conf is None:
            problems.append("缺 08-execution/user-confirmation.json（用户书面确认）")
        elif conf == "INVALID" or not (conf.get("confirmedBy") and conf.get("quote")):
            problems.append("user-confirmation.json 需含 confirmedBy 与 quote")

    if args.target == "delete":
        if not args.ids:
            problems.append("delete 检查必须给 --ids")
        conf = load(run_dir / "08-execution" / "delete-confirmation.json")
        if conf is None:
            problems.append("缺 08-execution/delete-confirmation.json（二次确认）")
        elif conf == "INVALID" or not (conf.get("confirmedBy") and conf.get("at")):
            problems.append("delete-confirmation.json 需含 confirmedBy 与 at")
        else:
            missing = [i for i in args.ids if str(i) not in [str(x) for x in conf.get("targets", [])]]
            if missing:
                problems.append(f"确认件未覆盖目标 id：{missing}")

    if problems:
        for p in problems:
            print(f"[FAIL] {p}")
        print(f"\n结论：NOT READY（{len(problems)} 项未就绪）")
        return 1
    print("结论：READY（可放行）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
