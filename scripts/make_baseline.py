#!/usr/bin/env python3
"""从一个"读回文件"生成某账户的参数骨架（baseline）。

为什么要它：`templates/campaign-baseline.json` 是**手工**从一条真实计划抄出来的。
换账户/换商品时不应再手抄——把 `getCampaignShopping` 的返回存成 JSON，跑本脚本即可生成
该账户自己的骨架（audience 键集合 + material 全要素），⑥ 再在骨架上替换取值。

⚠ 本脚本**不调接口**：`--from` 是成员8 用只读能力拿到并保存的返回体
（`getCampaignShopping` 的返回、或 `08-execution/after-snapshot.json` 这种含 `rawReadback` 的文件）。

用法：
    python3 scripts/make_baseline.py --from readback.json --account main \
        --out templates/campaign-baseline.main.json
    python3 scripts/make_baseline.py --from .ecwf/runs/x/08-execution/after-snapshot.json --stdout

退出码：0 成功 / 2 用法或解析错。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TPL = PROJECT_ROOT / "templates" / "campaign-baseline.json"

PLAN_SCALARS = [
    "campaignShoppingName", "targetType", "eshopType", "productId", "productName",
    "campaignType", "budget", "transType", "ocpcBid", "startTime", "endTime",
    "ideaType",
]


def find_campaign(node: Any) -> dict | None:
    """递归找第一个同时含 audience 与 material 的 dict。"""
    if isinstance(node, dict):
        if isinstance(node.get("audience"), dict) and isinstance(node.get("material"), dict):
            return node
        for v in node.values():
            got = find_campaign(v)
            if got:
                return got
    elif isinstance(node, list):
        for v in node:
            got = find_campaign(v)
            if got:
                return got
    return None


def strip_material(m: dict) -> dict:
    """只留写侧字段：brand / userPortrait.imageId / title[].text / description[].text / pictures[].imageId。"""
    out: dict[str, Any] = {"brand": m.get("brand") or "<用户确认的 brand>"}
    up = m.get("userPortrait") or {}
    out["userPortrait"] = {"imageId": up.get("imageId") or "<本账户已过审品牌头像 imageId（120×120）>"}
    for key, idkey in (("title", "text"), ("description", "text")):
        rows = []
        for item in m.get(key) or []:
            if isinstance(item, dict) and item.get(idkey) is not None:
                rows.append({idkey: item[idkey]})
        out[key] = rows
    pics = []
    for item in m.get("pictures") or []:
        if isinstance(item, dict) and item.get("imageId") is not None:
            pics.append({"imageId": item["imageId"]})
    out["pictures"] = pics or [{"imageId": "PENDING:img-001.png"}]
    if m.get("fengchaoTitle"):
        out["fengchaoTitle"] = [{"text": t.get("text", "")} for t in m["fengchaoTitle"] if isinstance(t, dict)]
    out.setdefault("videos", [])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从读回文件生成账户参数骨架（不调接口）")
    ap.add_argument("--from", dest="src", required=True, help="读回 JSON（getCampaignShopping 返回 / after-snapshot）")
    ap.add_argument("--account", default="<account-alias>", help="账户别名（写进 _provenance）")
    ap.add_argument("--out", help="输出路径（默认打印到 stdout）")
    ap.add_argument("--stdout", action="store_true", help="强制打印到 stdout")
    args = ap.parse_args(argv)

    src = Path(args.src).expanduser()
    if not src.is_file():
        print(f"读回文件不存在：{src}", file=sys.stderr)
        return 2
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    campaign = find_campaign(data)
    if campaign is None:
        print("在读回文件里没找到同时含 audience 与 material 的对象", file=sys.stderr)
        return 2

    template = json.loads(DEFAULT_TPL.read_text(encoding="utf-8")) if DEFAULT_TPL.is_file() else {}

    skeleton: dict[str, Any] = {}
    for k in PLAN_SCALARS:
        if k in campaign:
            skeleton[k] = campaign[k]
    skeleton.setdefault("pause", True)
    skeleton["audience"] = {
        k: v for k, v in (campaign.get("audience") or {}).items()
        if not isinstance(v, (dict, list)) and v is not None and k != "fcKeywordWtpids"
    }  # fcKeywordWtpids 属"绑包结果"，不写进骨架：否则⑥照抄会把别的计划的包绑到新计划上
    skeleton["schedule"] = campaign.get("schedule") or [{"weekDay": d, "startHour": 0, "endHour": 24} for d in range(1, 8)]
    skeleton["material"] = strip_material(campaign.get("material") or {})

    out = {
        "_doc": "⑥ 出 payload 的骨架（由 scripts/make_baseline.py 从读回文件生成）。只替换取值，不从零拼。",
        "_provenance": {
            "account": args.account,
            "generatedFrom": str(src),
            "pickedPlan": campaign.get("campaignShoppingId"),
            "note": "结构来自本账户真实读回；audience 键集合与 material 要素齐全。",
        },
        "_perAccountFields": template.get("_perAccountFields", []),
        "_easyErrors": template.get("_easyErrors", []),
        "_serverDefaults": template.get("_serverDefaults", {}),
        "campaignShoppingTypes": [skeleton],
    }
    text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    if args.out and not args.stdout:
        Path(args.out).expanduser().write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}（audience {len(skeleton['audience'])} 键，"
              f"title {len(skeleton['material']['title'])} 条，pictures {len(skeleton['material']['pictures'])} 张）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
