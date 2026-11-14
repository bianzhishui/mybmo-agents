#!/usr/bin/env python3
"""按用量算费用（DeepSeek 官方价目表，元/百万 tokens）。

为什么单独一个脚本：审计报告里的"高峰 ¥5.5437"是手工从会话日志算的，容易算错、不可复算。
把"每次调用的 usage"导出成 JSON，本脚本按时间戳判定高峰/空闲并累加。

用量文件格式（JSON，数组或 {"rows":[...]}），每行：
    {"agent": "m7-audit", "ts": "2026-11-13T15:40:00+08:00",
     "input_miss": 147846, "input_hit": 9709312, "output": 97774}
别名也认：inputMiss / cacheRead / outputTokens。

价格（元/百万 tokens）：
    高峰（周一至周五 09:00–12:00、14:00–18:00，北京时间）：输入未命中 2 / 命中 0.04 / 输出 8
    空闲（其余，含周末节假日）：                             1 / 0.02 / 4
⚠ 节假日未做日历判断（走工作日近似）；如有节假日请把它当周末手工切分。

用法：
    python3 scripts/cost_report.py --usage usage.json
    python3 scripts/cost_report.py --usage usage.json --json
    python3 scripts/cost_report.py --usage usage.json --assume peak   # 缺 ts 时按高峰算

退出码：0 成功 / 2 用法或解析错。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))
PEAK = {"miss": 2.0, "hit": 0.04, "out": 8.0}
OFF = {"miss": 1.0, "hit": 0.02, "out": 4.0}


def parse_ts(value):
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CST)
    return dt.astimezone(CST)


def is_peak(dt: datetime | None, assume: str) -> bool:
    if dt is None:
        return assume == "peak"
    if dt.weekday() >= 5:  # 周六/周日
        return False
    h = dt.hour
    return (9 <= h < 12) or (14 <= h < 18)


def pick(row: dict, *names, default=0):
    for n in names:
        if n in row and row[n] is not None:
            return int(row[n])
    return default


def money(tokens: int, price_per_million: float) -> float:
    return tokens / 1_000_000 * price_per_million


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按用量算费用（只读）")
    ap.add_argument("--usage", required=True, help="用量 JSON 文件")
    ap.add_argument("--assume", choices=["peak", "off"], default="peak", help="缺 ts 时的默认时段")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.usage).expanduser()
    if not path.is_file():
        print(f"用量文件不存在：{path}", file=sys.stderr)
        return 2
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    rows = data.get("rows") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print("用量文件应为数组或 {rows:[...]}", file=sys.stderr)
        return 2

    per_agent: dict[str, dict] = {}
    total = {"miss": 0, "hit": 0, "out": 0, "cost": 0.0, "peak_cost": 0.0, "off_cost": 0.0, "calls": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        agent = str(row.get("agent") or row.get("session") or "-")
        miss = pick(row, "input_miss", "inputMiss", "input_tokens_miss")
        hit = pick(row, "input_hit", "cacheRead", "cache_read", "input_tokens_hit")
        out = pick(row, "output", "outputTokens", "output_tokens")
        dt = parse_ts(row.get("ts") or row.get("timestamp"))
        bucket = PEAK if is_peak(dt, args.assume) else OFF
        cost = money(miss, bucket["miss"]) + money(hit, bucket["hit"]) + money(out, bucket["out"])

        a = per_agent.setdefault(agent, {"miss": 0, "hit": 0, "out": 0, "cost": 0.0, "calls": 0})
        for k, v in (("miss", miss), ("hit", hit), ("out", out)):
            a[k] += v
            total[k] += v
        a["cost"] += cost
        a["calls"] += 1
        total["cost"] += cost
        total["calls"] += 1
        if bucket is PEAK:
            total["peak_cost"] += cost
        else:
            total["off_cost"] += cost

    if args.json:
        print(json.dumps({"perAgent": per_agent, "total": total}, ensure_ascii=False, indent=2))
        return 0

    print(f"用量文件：{path}\n")
    print(f"{'agent':<16}{'calls':>7}{'in_miss':>12}{'in_hit':>13}{'out':>10}{'cost(¥)':>11}")
    for agent, a in sorted(per_agent.items(), key=lambda kv: -kv[1]["cost"]):
        print(f"{agent:<16}{a['calls']:>7}{a['miss']:>12,}{a['hit']:>13,}{a['out']:>10,}{a['cost']:>11.4f}")
    print("-" * 69)
    print(f"{'合计':<16}{total['calls']:>7}{total['miss']:>12,}{total['hit']:>13,}{total['out']:>10,}{total['cost']:>11.4f}")
    print(f"\n高峰费用 ¥{total['peak_cost']:.4f} + 空闲费用 ¥{total['off_cost']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
