#!/usr/bin/env bash
# ⑧-1 预检的一键入口：把 5 项只读校验串起来，任一 FAIL 即非 0 退出。
#
#   bash scripts/preflight.sh --run <runId>
#   bash scripts/preflight.sh --run-dir .ecwf/runs/xxx
#   bash scripts/preflight.sh --run <runId> --skip-snapshot
#   （默认包含 gate_check --for execute；--skip-gates 可跳过）
#   --strict-snapshot 非预期字段差异即 FAIL（推荐 ⑧-6 用）
#
# 依次执行：
#   1) validate_payloads.py  —— schema / 占位文本 / imageId 追溯 / 图片尺寸 / 业务枚举 / ⑤↔⑥ / 自述对账 / calls 链与账户 / 审计取证
#   2) manifest.py verify    —— version==文件名 .vN、将发送文件 sha256 与放行记录对账、未闭环人工闸
#   3) manifest.py lint-files—— 文件名封闭清单（§1.1）
#   4) snapshot_diff.py      —— 有 before/after 时才跑（只读比对，差异不阻断；缺文件则跳过）
#   5) gate_check.py --for execute —— 01~07 done + 审计放行件 pass（默认；--skip-gates 跳过）
#
# 退出码：0 全过 / 1 有 FAIL / 2 用法错。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

RUN_ARGS=()
SKIP_SNAPSHOT=0
WITH_GATES=1  # 默认跑 gate_check --for execute
STRICT_SNAPSHOT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --run|--run-dir)
      [ $# -ge 2 ] || { echo "缺少 $1 的值" >&2; exit 2; }
      RUN_ARGS+=("$1" "$2"); shift 2 ;;
    --skip-snapshot) SKIP_SNAPSHOT=1; shift ;;
    --with-gates) WITH_GATES=1; shift ;;
    --skip-gates) WITH_GATES=-1; shift ;;
    --strict-snapshot) STRICT_SNAPSHOT=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

fail=0
run_step() {
  echo
  echo "==================== $1 ===================="
  shift
  "$@" || fail=1
}

run_step "1/5 validate_payloads" "$PY" "$ROOT/scripts/validate_payloads.py" "${RUN_ARGS[@]}"
run_step "2/5 manifest verify" "$PY" "$ROOT/scripts/manifest.py" "${RUN_ARGS[@]}" verify
run_step "3/5 manifest lint-files" "$PY" "$ROOT/scripts/manifest.py" "${RUN_ARGS[@]}" lint-files

if [ "$WITH_GATES" -eq 1 ]; then
  run_step "5/5 gate_check --for execute" "$PY" "$ROOT/scripts/gate_check.py" "${RUN_ARGS[@]}" --for execute
elif [ "$WITH_GATES" -eq -1 ]; then
  echo
  echo "==================== 5/5 gate_check（已跳过）===================="
fi

if [ "$SKIP_SNAPSHOT" -eq 1 ]; then
  echo
  echo "==================== 4/5 snapshot_diff（已跳过）===================="
else
  echo
  echo "==================== 4/5 snapshot_diff ===================="
  if [ "$STRICT_SNAPSHOT" -eq 1 ]; then
    "$PY" "$ROOT/scripts/snapshot_diff.py" "${RUN_ARGS[@]}" --allow-diff --fail-on-unexpected
  else
    "$PY" "$ROOT/scripts/snapshot_diff.py" "${RUN_ARGS[@]}" --allow-diff
  fi
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "[skip] 无 before/after 快照，跳过（⑧-6 之前属正常）"
  elif [ "$rc" -ne 0 ]; then
    fail=1
  fi
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "预检结论：PASS（可进入 ⑧）"
else
  echo "预检结论：FAIL（先按上面的 [FAIL] 修，不要进入 ⑧）" >&2
fi
exit "$fail"
