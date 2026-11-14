#!/usr/bin/env python3
"""建一个 run 工作区（docs/ec-shopping-workflow-v2.md §5.2 / §5.3 / §5.5）。

做的事：
1. 建 `.ecwf/runs/<YYYYMMDD-HHMMSS>-<slug>/` 下 8 个成员编号目录；
2. 确保 `.ecwf/.gitignore` 内容为 `*` + `!.gitignore`（产物不入库、不在 git status 碍眼）；
3. 按 `templates/manifest.json` 生成 `manifest.json`（状态机 + 索引）。

同 slug 未完结检查（P1-12 / 建议8）：默认拒绝在与某个 finalStatus 仍为 running 的
同 slug run 并存时新建，避免"上一轮中断后直接另起一炉"，留下归属不明的孤儿 run
（真实案例：20261113-152452-jewelry-clean）。确认要并行时显式加 `--allow-parallel`。

用法：
    python3 scripts/new_run.py --slug jewelry-clean
    python3 scripts/new_run.py --slug my-product --account <account-alias> --base /tmp/ecwf
    python3 scripts/new_run.py --slug jewelry-clean --allow-parallel
    python3 scripts/new_run.py --slug x --dry-run

stdout 打印一行 runDir 绝对路径，便于 `RUN=$(...)` 取值。
退出码：0 成功 / 2 用法或环境错。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from _runbook import STAGES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
GITIGNORE_BODY = "*\n!.gitignore\n"


def load_manifest_template() -> dict:
    path = PROJECT_ROOT / "templates" / "manifest.json"
    with path.open(encoding="utf-8") as fh:
        tpl = json.load(fh)
    # 模板里的说明键只用于人读，不写进真实 manifest
    return {k: v for k, v in tpl.items() if not k.startswith("_")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="建 ECWF run 工作区")
    ap.add_argument("--slug", required=True, help="短横线小写 slug，如 jewelry-clean")
    ap.add_argument("--account", default=None, help="mybmo 账户别名，可后补")
    ap.add_argument("--base", default=None, help="覆盖工作区根（默认 $ECWF_HOME 或 <项目>/.ecwf）")
    ap.add_argument("--dsh-session-id", default=None, help="默认读 $DSH_SESSION_ID")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-parallel", action="store_true",
                    help="允许与同 slug 的未完结 run 并存（默认拒绝）")
    args = ap.parse_args(argv)

    if not SLUG_RE.match(args.slug):
        print(f"--slug 不合法：{args.slug!r}（小写字母数字与短横线，首字符非短横线）", file=sys.stderr)
        return 2

    home = Path(args.base).expanduser().resolve() if args.base else Path(
        os.environ.get("ECWF_HOME") or (PROJECT_ROOT / ".ecwf")
    ).resolve()
    now = datetime.now().astimezone()
    run_id = f"{now:%Y%m%d-%H%M%S}-{args.slug}"
    run_dir = home / "runs" / run_id

    if run_dir.exists():
        print(f"run 已存在：{run_dir}", file=sys.stderr)
        return 2

    # 同 slug 未完结 run 检查：防止孤儿 run（P1-12）
    runs_root = home / "runs"
    parallel_override = None
    if runs_root.is_dir() and not args.allow_parallel:
        open_same_slug = []
        for d in sorted(runs_root.iterdir()):
            if not d.is_dir() or not d.name.endswith(f"-{args.slug}"):
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                status = json.loads(mf.read_text(encoding="utf-8")).get("finalStatus", "running")
            except json.JSONDecodeError:
                status = "running"
            if status == "running":
                open_same_slug.append(d.name)
        if open_same_slug:
            print(
                f"拒绝新建：同 slug（{args.slug}）已有未完结 run：{', '.join(open_same_slug)}\n"
                f"  先收尾（scripts/manifest.py --run <runId> final abandoned|success）"
                f"或确认要并行时加 --allow-parallel",
                file=sys.stderr,
            )
            return 2
    elif runs_root.is_dir() and args.allow_parallel:
        same = []
        for d in sorted(runs_root.iterdir()):
            if d.is_dir() and d.name.endswith(f"-{args.slug}"):
                mf = d / "manifest.json"
                if mf.is_file():
                    try:
                        if json.loads(mf.read_text(encoding="utf-8")).get("finalStatus") == "running":
                            same.append(d.name)
                    except json.JSONDecodeError:
                        pass
        if same:
            parallel_override = {"slug": args.slug, "conflictedWith": same,
                                 "note": "显式 --allow-parallel：与未完结 run 并行"}
            print(f"[WARN] --allow-parallel：与未完结 run 并行 {same}（已记入 manifest.parallelOverride）",
                  file=sys.stderr)

    # L4：同账户仍有 running run 时提醒（并行可能互相踩 bind 替换语义）
    if runs_root.is_dir() and args.account:
        busy = []
        for d in sorted(runs_root.iterdir()):
            if not d.is_dir():
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if m.get("finalStatus") == "running" and m.get("account") == args.account:
                busy.append(d.name)
        if busy:
            print(f"[WARN] 账户 {args.account} 仍有未完结 run：{busy}（bind 是替换语义，并行易互相踩）",
                  file=sys.stderr)

    manifest = load_manifest_template()
    manifest.update(
        {
            "runId": run_id,
            "dshSessionId": args.dsh_session_id or os.environ.get("DSH_SESSION_ID") or None,
            "account": args.account,
            "createdAt": now.isoformat(timespec="seconds"),
            "finalStatus": "running",
        }
    )
    if parallel_override:
        manifest["parallelOverride"] = parallel_override
    for stage in manifest.get("stages", {}):
        manifest["stages"][stage] = {"status": "pending"}
    manifest.setdefault("objects", {"imageIds": [], "packageIds": [], "campaignShoppingId": None})

    if args.dry_run:
        print(f"[dry-run] 将创建 {run_dir}")
        print(f"[dry-run] 目录：{', '.join(STAGES)}")
        print(f"[dry-run] manifest：{json.dumps(manifest, ensure_ascii=False, indent=2)}")
        return 0

    for d in STAGES:
        (run_dir / d).mkdir(parents=True, exist_ok=True)

    gitignore = home / ".gitignore"
    if not gitignore.exists() or gitignore.read_text(encoding="utf-8") != GITIGNORE_BODY:
        home.mkdir(parents=True, exist_ok=True)
        gitignore.write_text(GITIGNORE_BODY, encoding="utf-8")

    with (run_dir / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
