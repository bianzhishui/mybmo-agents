#!/usr/bin/env python3
"""run 状态机的读写（docs/ec-shopping-workflow-v2.md §5.5）。

子命令：
    show                       打印当前 manifest
    set <stage> <status>       改一个阶段状态
        [--file PATH] [--by WHO] [--reason TEXT] [--retry-to STAGE] [--bump-version]
    next                       打印"下一步该谁"（第一个非 done 的阶段）
    object <key> <value>       写 objects 下的键（campaignShoppingId 等）
    final <status>             写 finalStatus（success / success-paused / failed / abandoned），可带 --reason
                               置 success 需同时满足：pendingGates 全 closed + 07-audit/approval.json
                               + 08-execution/user-confirmation.json + approval.payload 内容哈希匹配；
                               --force 跳过以上检查
    gate <id> <status>         改一个人工闸（open / closed）；置 closed 必须带 --evidence（用户原话/call_id），--force 可跳过
    retry <stage>              记一次重试；超过 3 次拒绝（除非 --force）
    verify [--record]          校验"版本 ↔ 文件名"绑定，并打印/校验将发送文件的 sha256
    lint-files                 校验 run 目录文件名是否落在协议封闭清单内（§1.1）

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
import json
import re
import sys
from pathlib import Path

from _runbook import (
    STAGES,
    check_approval_payload,
    file_version,
    resolve_run_dir as _resolve_run_dir,
    sha256_of,
    strip_version,
    within,
)

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


def verify(run_dir: Path, manifest: dict, record: bool, require_approval: bool = False) -> int:
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
        path = within(run_dir, rel)
        declared = entry.get("version")
        if path is None:
            fails.append(f"{stage}: 文件路径越界 {rel!r}")
            print(f"[FAIL] {stage:12} 文件路径越界 {rel!r}")
            continue
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

        # H1：审计放行件（07 写、08 只读），把"自证"变成"他证"
        if stage == "06-payloads":
            approval_path = run_dir / "07-audit" / "approval.json"
            if not approval_path.is_file():
                msg = "缺 07-audit/approval.json（审计放行件）"
                if require_approval:
                    fails.append(f"{stage}: {msg}")
                    print(f"[FAIL] {stage:12} {msg}")
                else:
                    warns.append(f"{stage}: {msg}（加 --require-approval 可判 FAIL）")
                    print(f"[warn] {stage:12} {msg}")
            else:
                try:
                    ap = json.loads(approval_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    fails.append(f"{stage}: approval.json 不是合法 JSON")
                    print(f"[FAIL] {stage:12} approval.json 解析失败")
                    ap = {}
                if ap:
                    ap_sha = str(ap.get("sha256") or "")
                    ap_file = Path(str(ap.get("payload") or "")).name
                    if not ap_sha:
                        fails.append(f"{stage}: approval 缺 sha256（无法绑定 payload 内容）")
                        print(f"[FAIL] {stage:12} approval 缺 sha256")
                    if ap.get("result") != "pass":
                        fails.append(f"{stage}: approval.result={ap.get('result')!r}（非 pass）")
                        print(f"[FAIL] {stage:12} 审计结论非 pass：{ap.get('result')!r}")
                    if ap_file and ap_file != path.name:
                        fails.append(f"{stage}: approval.payload={ap_file} ≠ 当前执行版 {path.name}")
                        print(f"[FAIL] {stage:12} 放行件指向 {ap_file}，当前是 {path.name}（拿旧放行件发新 payload）")
                    if ap_sha and ap_sha != digest:
                        fails.append(f"{stage}: sha256≠审计放行 {ap_sha}")
                        print(f"[FAIL] {stage:12} sha256 与审计放行不符：现场 {digest[:16]}… ≠ {ap_sha[:16]}…")
                    elif ap_sha == digest:
                        print(f"[ok]   {stage:12} 与 07-audit/approval.json 一致（审计放行 sha）")
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


# skill §1.1 的目录内允许文件名（不含 .vN 变体与下划线辅助文件，二者另行放行）
ALLOWED_FILES: dict[str, list[str]] = {
    "01-product": [r"analysis\.md", r"required-fields\.json"],
    "02-keywords": [r"packages\.json", r"binding\.md"],
    "03-copy": [r"copy\.json", r"image-prompts\.md"],
    "04-assets": [r"img-.+\.(?:png|jpe?g|webp|gif|bmp)", r"img-.+\.b64", r"image-map\.json"],
    "05-plan": [r"plan-draft\.json"],
    "06-payloads": [r"addKeywordPackages\.json", r"uploadImage\.json", r"addCampaignShopping\.json"],
    "07-audit": [r"audit-report\.md", r"approval\.json"],
    "08-execution": [
        r"preflight\.json",
        r"execution-plan\.json",
        r"before-snapshot\.json",
        r"after-snapshot\.json",
        r"calls\.jsonl",
        r"user-confirmation\.json",
        r"delete-confirmation\.json",
    ],
}



def lint_files(run_dir: Path) -> int:
    """文件名是否都落在协议封闭清单内。返回 0 全合规 / 1 有清单外文件。

    放行三类：① §1.1 列出的基础名；② 其 `.vN` 变体（`xxx.v2.json`）；③ 下划线开头的辅助文件
    （`_notes.json`、`_draft.md`、`_xxx-response.json`）。其余即"协议外命名"。
    """
    problems: list[str] = []
    checked = 0
    print(f"run: {run_dir}\n")
    for stage, patterns in ALLOWED_FILES.items():
        d = run_dir / stage
        if not d.is_dir():
            continue  # 早期 run 可能还没建到这一步；只检查已存在的目录
        compiled = [re.compile("^" + pat + "$") for pat in patterns]
        for f in sorted(d.iterdir()):
            if f.is_dir():
                continue
            checked += 1
            name = f.name
            if name.startswith("_") or name.startswith("."):
                continue  # 辅助文件 / 系统点文件（.DS_Store 等）
            m = re.search(r"\.v(\d+)(?=\.[^.]+$)", name)
            if m and int(m.group(1)) > 99:
                problems.append(f"{stage}/{name}: 版本号 {m.group(1)} 过大（上限 99，防止用高版本号制造'唯一执行版'混淆）")
                continue
            base = strip_version(name)  # 去掉 .vN
            if any(c.match(name) or c.match(base) for c in compiled):
                continue
            problems.append(
                f"{stage}/{name}: 不在协议封闭清单内（允许 {patterns} 或其 .vN 变体、下划线辅助文件）"
            )
    # 未知子目录也算协议外（否则 99-junk/ 这类目录会被完全忽略）
    for d in sorted(run_dir.iterdir()):
        if d.is_dir() and not d.name.startswith(".") and d.name not in ALLOWED_FILES:
            problems.append(f"{d.name}/: 不在协议目录清单内（允许 {list(ALLOWED_FILES)}）")

    # 根目录只允许 manifest.json
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.name != "manifest.json" and not f.name.startswith("."):
            problems.append(f"{f.name}: run 根目录只允许 manifest.json")
    for p_msg in problems:
        print(f"[FAIL] {p_msg}")
    print(f"\n检查 {checked} 个文件，清单外 {len(problems)} 个 → {'FAIL' if problems else 'PASS'}")
    return 1 if problems else 0


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
    p_set.add_argument("--force", action="store_true", help="跳过前置阶段/文件存在检查（会记录原因）")

    p_obj = sub.add_parser("object", help="写 objects 键")
    p_obj.add_argument("key")
    p_obj.add_argument("value")

    p_final = sub.add_parser("final", help="写 finalStatus")
    p_final.add_argument("status", choices=sorted(FINAL_STATUSES))
    p_final.add_argument("--reason")
    p_final.add_argument("--force", action="store_true", help="跳过开量放行检查")

    p_gate = sub.add_parser("gate", help="改人工闸状态（pendingGates）")
    p_gate.add_argument("gate_id")
    p_gate.add_argument("status", choices=["open", "closed"])
    p_gate.add_argument("--reason")
    p_gate.add_argument("--evidence", help="关闭人工闸的证据（用户原话/call_id）；closed 时必填")
    p_gate.add_argument("--force", action="store_true")

    sub.add_parser("lint-files", help="文件名封闭清单校验（skill §1.1）")

    p_retry = sub.add_parser("retry", help="记一次重试（硬上限 3）")
    p_retry.add_argument("stage", choices=STAGES)
    p_retry.add_argument("--force", action="store_true")

    p_verify = sub.add_parser("verify", help="版本↔文件名绑定 + 将发送文件 sha256 对账")
    p_verify.add_argument("--record", action="store_true", help="把 06-payloads 执行版 sha256 写回 manifest")
    p_verify.add_argument("--require-approval", action="store_true", help="缺 07-audit/approval.json 即 FAIL（推荐 ⑧ 前用）")

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

    if args.cmd == "lint-files":
        return lint_files(run_dir)

    if args.cmd == "verify":
        rc = verify(run_dir, manifest, args.record, args.require_approval)
        if args.record:
            save(run_dir, manifest)
        return rc

    if args.cmd == "retry":
        entry = manifest.setdefault("stages", {}).setdefault(args.stage, {"status": "pending"})
        n = int(entry.get("retries", 0)) + 1
        if n > 3 and not args.force:
            print(f"拒绝：{args.stage} 已重试 {n - 1} 次，达硬上限 3 次（转人工后台建计划；--force 才可继续）", file=sys.stderr)
            return 1
        entry["retries"] = n
        save(run_dir, manifest)
        print(f"{args.stage} retries -> {n}")
        return 0

    if args.cmd == "gate":
        if args.status == "closed" and not (args.evidence or args.force):
            print("关闭人工闸必须给 --evidence（用户原话/call_id），否则审计无法复核（--force 可跳过）", file=sys.stderr)
            return 1
        gates = manifest.setdefault("pendingGates", {})
        entry = gates.setdefault(args.gate_id, {"desc": "", "status": "open"})
        entry["status"] = args.status
        if args.reason:
            entry["reason"] = args.reason
        if args.evidence:
            entry["evidence"] = args.evidence
        save(run_dir, manifest)
        print(f"gate {args.gate_id} -> {args.status}")
        return 0

    if args.cmd == "final":
        open_gates = [
            gid for gid, g in (manifest.get("pendingGates") or {}).items()
            if (g or {}).get("status", "open") != "closed"
        ]
        if args.status == "success" and not args.force:
            if open_gates:
                print(
                    f"拒绝置 success：仍有未闭环人工闸 {open_gates}。"
                    f"先 scripts/manifest.py gate <id> closed --evidence …，"
                    f"或置 success-paused（保持暂停交付）。",
                    file=sys.stderr,
                )
                return 1
            if not (run_dir / "07-audit" / "approval.json").is_file():
                print("拒绝置 success：缺 07-audit/approval.json（审计放行件）", file=sys.stderr)
                return 1
            if not (run_dir / "08-execution" / "user-confirmation.json").is_file():
                print("拒绝置 success：缺 08-execution/user-confirmation.json（用户书面确认开量）", file=sys.stderr)
                return 1
            try:
                _ap = json.loads((run_dir / "07-audit" / "approval.json").read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print("拒绝置 success：approval.json 解析失败", file=sys.stderr)
                return 1
            _probs, _, _ = check_approval_payload(_ap, run_dir)
            if _probs:
                print(f"拒绝置 success：{'；'.join(_probs)}", file=sys.stderr)
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

    # 任何情况下 --file 都必须落在 run 目录内（防止把外部路径写进 manifest）
    if args.file and within(run_dir, args.file) is None:
        print(f"拒绝：--file 路径越界 {args.file!r}", file=sys.stderr)
        return 1

    # H4：推进到 done 前，前置阶段必须 done、文件必须存在；08 还需 07 done + 审计放行件
    if args.status == "done" and not args.force:
        idx = STAGES.index(args.stage)
        missing = [s for s in STAGES[:idx] if manifest.get("stages", {}).get(s, {}).get("status") != "done"]
        if missing:
            print(f"拒绝置 done：前置阶段未完成 {missing}（确需越级加 --force）", file=sys.stderr)
            return 1
        recorded_file = args.file or manifest.get("stages", {}).get(args.stage, {}).get("file")
        if not recorded_file:
            print(f"拒绝置 {args.stage} done：未指定产出文件（--file 或 manifest 已有 file）", file=sys.stderr)
            return 1
        if within(run_dir, recorded_file) is None:
            print(f"拒绝置 done：文件路径越界 {recorded_file!r}", file=sys.stderr)
            return 1
        if not (run_dir / recorded_file).is_file():
            print(f"拒绝置 done：文件不存在 {recorded_file}", file=sys.stderr)
            return 1
        if args.stage == "08-execution":
            if manifest.get("stages", {}).get("07-audit", {}).get("status") != "done":
                print("拒绝置 08-execution done：07-audit 未 done", file=sys.stderr)
                return 1
            if not (run_dir / "07-audit" / "approval.json").is_file():
                print("拒绝置 08-execution done：缺 07-audit/approval.json（审计放行件）", file=sys.stderr)
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
    if args.force:
        stage_entry["forceNote"] = "越级/跳过前置检查（--force）"
    if args.bump_version or was_done:
        stage_entry["version"] = int(stage_entry.get("version", 1)) + (1 if (args.bump_version or was_done) else 0)

    save(run_dir, manifest)
    print(f"{args.stage} -> {args.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
