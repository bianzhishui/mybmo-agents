#!/usr/bin/env python3
"""run 状态机的读写（docs/ec-shopping-workflow-v2.md §5.5）。

子命令：
    show                       打印当前 manifest
    set <stage> <status>       改一个阶段状态
        [--file PATH] [--by WHO] [--reason TEXT] [--retry-to STAGE] [--bump-version]
    next                       打印"下一步该谁"（第一个非 done 的阶段）
    object <key> <value>       写 objects 下的键（campaignShoppingId 等）
    final <status>             写 finalStatus（success / success-paused / failed / abandoned），可带 --reason
                               有未闭环 pendingGates 时拒绝置 success（建议12）
    gate <id> <status>         改一个人工闸（open / closed），可带 --reason
    verify [--record]          校验"版本 ↔ 文件名"绑定，并打印/校验将发送文件的 sha256

状态枚举：pending / running / done / rejected / frozen
finalStatus 枚举：running / success / success-paused / failed / abandoned
硬约束：
  - `rejected` 必须同时给 --reason 与 --retry-to，否则拒绝执行。
  - `version` 必须等于文件名里的 `.vN`（无 `.vN` 视为 1）。这是 P1-5/P2-7 的机器化。
  - `verify` 会把 06-payloads 执行版的 sha256 与 08-execution/execution-plan.json 里
    记录的 sha256 对账，不一致就 FAIL（防止"拿旧 payload 烧掉最后一次重试"）。

退出码：0 ok / 1 校验不过 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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
FINAL_STATUSES = {"running", "success", "success-paused", "failed", "abandoned"}
VERSIONED_RE = re.compile(r"^(?P<stem>.+?)(?:\.v(?P<v>\d+))?\.json$")


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


def file_version(name: str) -> int:
    """文件名里的版本：`x.json`=1、`x.v7.json`=7。"""
    m = VERSIONED_RE.match(name)
    return int(m.group("v")) if (m and m.group("v")) else 1


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(obj, trail=""):
    if isinstance(obj, dict):
        yield trail, obj
        for k, v in obj.items():
            yield from _walk(v, f"{trail}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{trail}[{i}]")


def recorded_shas(run_dir: Path) -> dict[str, str]:
    """从 execution-plan.json 收集 {basename: sha256} 形式的放行记录。"""
    plan_path = run_dir / "08-execution" / "execution-plan.json"
    if not plan_path.is_file():
        return {}
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for _trail, node in _walk(plan):
        path_v, sha_v = node.get("path"), node.get("sha256")
        if isinstance(path_v, str) and isinstance(sha_v, str):
            out[Path(path_v).name] = sha_v
    return out


def verify(run_dir: Path, manifest: dict, record: bool) -> int:
    """版本 ↔ 文件名强绑定 + 将发送文件 sha256 对账。返回 0 通过 / 1 有 FAIL。"""
    fails: list[str] = []
    warns: list[str] = []
    recorded = recorded_shas(run_dir)
    print(f"run: {run_dir}\n")
    for stage in STAGES:
        entry = manifest.get("stages", {}).get(stage, {})
        rel = entry.get("file")
        if not rel:
            continue
        path = run_dir / rel
        declared = entry.get("version")
        if not path.is_file():
            fails.append(f"{stage}: manifest 指向的 {rel} 不存在")
            print(f"[FAIL] {stage:12} 文件缺失 {rel}")
            continue
        fv = file_version(path.name)
        if declared is None:
            if fv != 1:
                fails.append(f"{stage}: 文件 {path.name} 是 v{fv}，但 manifest 没写 version")
                print(f"[FAIL] {stage:12} {path.name} 是 v{fv}，manifest 缺 version")
            else:
                print(f"[ok]   {stage:12} {path.name}（v1，manifest 未写 version，视为 1）")
        elif int(declared) != fv:
            fails.append(
                f"{stage}: version={declared} 与文件名 {path.name} 的 .v{fv} 错位"
            )
            print(f"[FAIL] {stage:12} version={declared} ≠ 文件名 {path.name} 的 .v{fv}（错位）")
        else:
            print(f"[ok]   {stage:12} version={declared} == 文件名 .v{fv}")

        # 06-payloads 等"将发送文件"的 sha256
        digest = sha256_of(path)
        rec = recorded.get(path.name)
        if rec is None:
            warns.append(f"{stage}: {path.name} 未在 execution-plan.json 留下放行 sha256")
            print(f"[warn] {stage:12} sha256={digest[:16]}…  无放行记录可对账")
            if record and stage == "06-payloads":
                entry["sha256"] = digest
                warns.append(f"{stage}: 已把 sha256={digest[:16]}… 写入 manifest（--record）")
        elif rec == digest:
            print(f"[ok]   {stage:12} sha256={digest[:16]}…  与放行记录一致")
        else:
            fails.append(
                f"{stage}: {path.name} sha256={digest} ≠ execution-plan 放行记录 {rec}"
            )
            print(f"[FAIL] {stage:12} sha256 不一致：现场 {digest[:16]}… ≠ 放行 {rec[:16]}…")
    open_gates = [
        gid for gid, g in (manifest.get("pendingGates") or {}).items()
        if (g or {}).get("status", "open") != "closed"
    ]
    if open_gates:
        warns.append(f"人工闸未闭环：{open_gates}（未闭环不得置 finalStatus=success）")
    for w in warns:
        print(f"[WARN] {w}")
    print(f"\n结论：{'FAIL' if fails else 'PASS'}（FAIL={len(fails)} WARN={len(warns)}）")
    return 1 if fails else 0


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

    p_final = sub.add_parser("final", help="写 finalStatus")
    p_final.add_argument("status", choices=sorted(FINAL_STATUSES))
    p_final.add_argument("--reason")

    p_gate = sub.add_parser("gate", help="改人工闸状态（pendingGates）")
    p_gate.add_argument("gate_id")
    p_gate.add_argument("status", choices=["open", "closed"])
    p_gate.add_argument("--reason")

    p_verify = sub.add_parser("verify", help="版本↔文件名绑定 + 将发送文件 sha256 对账")
    p_verify.add_argument("--record", action="store_true", help="把 06-payloads 执行版 sha256 写回 manifest")

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

    if args.cmd == "verify":
        rc = verify(run_dir, manifest, args.record)
        if args.record:
            save(run_dir, manifest)
        return rc

    if args.cmd == "gate":
        gates = manifest.setdefault("pendingGates", {})
        entry = gates.setdefault(args.gate_id, {"desc": "", "status": "open"})
        entry["status"] = args.status
        if args.reason:
            entry["reason"] = args.reason
        save(run_dir, manifest)
        print(f"gate {args.gate_id} -> {args.status}")
        return 0

    if args.cmd == "final":
        open_gates = [
            gid for gid, g in (manifest.get("pendingGates") or {}).items()
            if (g or {}).get("status", "open") != "closed"
        ]
        if args.status == "success" and open_gates:
            print(
                f"拒绝置 success：仍有未闭环人工闸 {open_gates}。"
                f"先 scripts/manifest.py gate <id> closed --reason …，"
                f"或置 success-paused（保持暂停交付）。",
                file=sys.stderr,
            )
            return 1
        manifest["finalStatus"] = args.status
        if args.reason:
            manifest.setdefault("finalStatusNote", {})["reason"] = args.reason
        save(run_dir, manifest)
        print(f"finalStatus -> {args.status}")
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
