#!/usr/bin/env python3
"""⑤⑥ 脚手架：从 baseline + ①产品 ②词包 ③文案 ④素材，生成 plan-draft 与 addCampaignShopping 初稿。

目的：把"⑥ 从零拼 7 版 payload"变成"生成初稿 → 只改差异"。**不调接口**，纯读文件+拼装。

它不是银弹：
- 商品/账户相关字段取自 `01-product/required-fields*.json`（缺就写"待核实"，绝不编造）；
- `userPortrait` 必须显式给（`--user-portrait`），否则留占位并告警；
- 产出后仍需 `scripts/validate_payloads.py` 机检 + 审计。

用法：
    python3 scripts/scaffold_plan.py --run-dir .ecwf/runs/x \
        --name "某商品-场景-20260101" --budget 100 --bid 1.24 \
        --start 2026-01-01 --user-portrait 15505964077 [--force] [--dry-run]

退出码：0 成功 / 1 数据不足或多处待核实（仍写盘但标红） / 2 用法错。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

from _runbook import resolve_run_dir
from validate_payloads import pick_latest


def load_latest(run_dir: Path, sub: str, base: str) -> dict | None:
    p = pick_latest(run_dir / sub, base)
    return json.loads(p.read_text(encoding="utf-8")) if p else None


def field_value(rf: dict | None, name: str):
    if not rf:
        return None
    f = (rf.get("fields") or {}).get(name)
    if isinstance(f, dict):
        return f.get("value")
    return None


def confirmed_brand(rf: dict | None):
    if not rf:
        return None
    for item in (rf.get("userConfirmations") or {}).get("items") or []:
        if item.get("field") == "brand":
            return item.get("value")
    return field_value(rf, "brand")


def pick_package(pkgs: dict | None, wanted: str | None) -> tuple[str | None, list[str]]:
    if not pkgs:
        return None, []
    groups = pkgs.get("keywordPackageTypes") or []
    chosen = None
    if wanted:
        for g in groups:
            if g.get("packageName") == wanted:
                chosen = g
                break
    if chosen is None:
        pref = (pkgs.get("firstOrderSelection") or {}).get("packageName")
        for g in groups:
            if pref and g.get("packageName") == pref:
                chosen = g
                break
    if chosen is None and groups:
        chosen = groups[0]
    if not chosen:
        return None, []
    words = [k.get("keyword") for k in chosen.get("keywordTypes") or [] if k.get("keyword")]
    return chosen.get("packageName"), words


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成 ⑤⑥ 初稿（不调接口）")
    ap.add_argument("--run", help="runId")
    ap.add_argument("--run-dir", help="run 目录")
    ap.add_argument("--baseline", default=str(Path(__file__).resolve().parent.parent / "templates" / "campaign-baseline.json"))
    ap.add_argument("--name", help="campaignShoppingName")
    ap.add_argument("--budget", type=float, default=0.0)
    ap.add_argument("--bid", type=float, default=0.2)
    ap.add_argument("--start", default=None, help="startTime YYYY-MM-DD（默认今天）")
    ap.add_argument("--user-portrait", type=int, default=None, help="已过审品牌头像 imageId（120×120）")
    ap.add_argument("--package", help="指定词包 packageName（默认取首单包）")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的 05/06 文件")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run, args.run_dir)
    rf = load_latest(run_dir, "01-product", "required-fields.json")
    pkgs = load_latest(run_dir, "02-keywords", "packages.json")
    copy = load_latest(run_dir, "03-copy", "copy.json")
    imap = load_latest(run_dir, "04-assets", "image-map.json")
    baseline = json.loads(Path(args.baseline).expanduser().read_text(encoding="utf-8"))

    problems: list[str] = []
    base_c = (baseline.get("campaignShoppingTypes") or [{}])[0]

    audience = dict(base_c.get("audience") or {})
    pkg_name, words = pick_package(pkgs, args.package)
    if words:
        audience["keywords"] = ",".join(words)
    else:
        problems.append("02-keywords 没有可用词包 → audience.keywords 留空")
    audience.pop("fcKeywordWtpids", None)  # 建单不携带，走 ⑧-5 绑包

    product_id = field_value(rf, "productId")
    product_name = field_value(rf, "productName")
    eshop = field_value(rf, "eshopType")
    trans = field_value(rf, "transType")
    target = field_value(rf, "targetType")
    brand = confirmed_brand(rf)
    for label, val in (("productId", product_id), ("productName", product_name),
                       ("eshopType", eshop), ("transType", trans), ("brand", brand)):
        if val in (None, "", "待核实"):
            problems.append(f"01-product 的 {label} 仍是待核实")

    titles = [{"text": t.get("text")} for t in (copy or {}).get("titles") or [] if t.get("text")][:12]
    descs = [{"text": t.get("text")} for t in (copy or {}).get("descriptions") or [] if t.get("text")][:6]
    if not titles:
        problems.append("03-copy 没有标题")

    assets = [a for a in (imap or {}).get("assets") or [] if a.get("file")]
    pictures = [{"imageId": f"PENDING:{a['file']}"} for a in assets[:18]]
    if not pictures:
        problems.append("04-assets 没有素材")
    if len(assets) > 18:
        problems.append(f"04-assets 有 {len(assets)} 张 > 18，已截断")

    if args.user_portrait:
        user_portrait = {"imageId": args.user_portrait}
    else:
        user_portrait = {"imageId": "<本账户已过审品牌头像 imageId（120×120）>"}
        problems.append("未给 --user-portrait（userPortrait 必带，缺会 901217）")

    name = args.name or f"{product_name or '<商品>'}-{date.today():%Y%m%d}"

    campaign = {
        "campaignShoppingName": name,
        "pause": True,
        "targetType": target if isinstance(target, int) else 1,
        "eshopType": eshop if isinstance(eshop, int) else "<1 度小店 / 4 健康商城>",
        "productId": str(product_id) if product_id not in (None, "") else "待核实",
        "productName": product_name or "待核实",
        "campaignType": 1,
        "budget": args.budget,
        "ocpcBid": args.bid,
        "transType": trans if isinstance(trans, int) else "<度小店[14,90]/健康商城[90]/直播[80-84]>",
        "ideaType": 1,
        "startTime": args.start or date.today().isoformat(),
        "endTime": "9999-01-01",
        "schedule": base_c.get("schedule") or [{"weekDay": d, "startHour": 0, "endHour": 24} for d in range(1, 8)],
        "audience": audience,
        "material": {
            "brand": brand or "待核实",
            "userPortrait": user_portrait,
            "title": titles or [{"text": "待核实"}],
            "description": descs,
            "pictures": pictures,
        },
    }

    plan_draft = {"_note": "脚手架生成（scripts/scaffold_plan.py）；⑥ 以基线为骨架替换取值。",
                  "campaignShoppingTypes": [campaign], "by": "成员5"}
    payload = {"campaignShoppingTypes": [campaign]}  # schema additionalProperties=false，不能带 _note

    if args.dry_run:
        print(json.dumps({"plan": plan_draft, "problems": problems}, ensure_ascii=False, indent=2)[:2000])
        return 1 if problems else 0

    targets = {"05-plan/plan-draft.json": plan_draft,
               "06-payloads/addCampaignShopping.json": payload}

    # 词包 payload（有词包就生成）
    if words and pkg_name:
        targets["06-payloads/addKeywordPackages.json"] = {
            "keywordPackageTypes": [{
                "packageName": pkg_name,
                "keywordTypes": [{"keyword": w, "id": 0} for w in words],
            }]
        }

    # 图片 payload（只在 base64 文件在位时生成；content 纯 base64 无 data: 前缀）
    items = []
    for a in assets:
        b64 = run_dir / "04-assets" / str(a.get("base64File") or "")
        if a.get("base64File") and b64.is_file():
            content = b64.read_text(encoding="ascii").strip()
            md5 = hashlib.md5(base64.b64decode(content)).hexdigest()
            items.append({"content": content,
                          "imageName": a.get("imageName") or Path(str(a["file"])).stem,
                          "imgmd5": md5})
    if items:
        targets["06-payloads/uploadImage.json"] = {"addImage": True, "items": items}
    for rel, obj in targets.items():
        p = run_dir / rel
        if p.exists() and not args.force:
            print(f"拒绝覆盖已存在的 {rel}（确需覆盖加 --force，或按协议出 .vN）", file=sys.stderr)
            return 2
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[write] {rel}")

    try:
        from jsonschema import Draft7Validator
        schema = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "addCampaignShopping.schema.json").read_text(encoding="utf-8"))
        errs = [e.message for e in Draft7Validator(schema).iter_errors(payload)]
        if errs:
            print("[schema] 初稿未通过（属正常：待核实字段需先补）", file=sys.stderr)
            for e in errs[:5]:
                print("  -", e, file=sys.stderr)
    except ImportError:
        pass

    if problems:
        print("\n[待补齐]")
        for x in problems:
            print("  -", x)
        return 1
    print("\n初稿就绪；下一步：按基线替换取值 + 跑 scripts/validate_payloads.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
