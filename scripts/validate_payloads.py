#!/usr/bin/env python3
"""⑧-1 预检的自动校验（对应 docs/ec-shopping-workflow-v2.md §5.6）。

两件事，缺一不可：

1. **模板校验**：`06-payloads/*.json` 逐份对 `schemas/*.schema.json`（draft-07）校验。
2. **占位符 / 编造校验**：扫 `06-payloads/`，凡出现具体整数 `imageId` 却在
   `04-assets/image-map.json` 里查不到对应记录 → 判定「编造」，直接打回成员6。

顺带做几条 §七 审计清单里可机器判定的项（关键词上限、禁投词结论、material 完整性）。

本项目只读不写：不修改任何产物，只输出结论与退出码。

用法：
    python3 scripts/validate_payloads.py                     # 取 .ecwf/runs/ 下最新 run
    python3 scripts/validate_payloads.py --run 20261113-114432-jewelry-clean
    python3 scripts/validate_payloads.py --run-dir .ecwf/runs/xxx --json
    python3 scripts/validate_payloads.py --allow-missing     # 缺文件只报警告（半成品阶段用）

退出码：0 = 全部通过；1 = 有 FAIL；2 = 用法 / 环境错（找不到 run、缺 schemas 等）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = PROJECT_ROOT / "schemas"

# payload 文件名 → schema 文件；同时容忍带 "06-" 前缀的模板命名
PAYLOAD_SCHEMAS = {
    "addKeywordPackages.json": "addKeywordPackages.schema.json",
    "uploadImage.json": "uploadImage.schema.json",
    "addCampaignShopping.json": "addCampaignShopping.schema.json",
}

PENDING_RE = re.compile(r"^PENDING:([A-Za-z0-9._-]+)$")
MAX_KEYWORDS = 2000  # audience.keywords 上限（单计划）


class Report:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, level: str, check: str, message: str) -> None:
        self.items.append({"level": level, "check": check, "message": message})

    def ok(self, check: str, message: str = "") -> None:
        self.add("PASS", check, message)

    def warn(self, check: str, message: str) -> None:
        self.add("WARN", check, message)

    def fail(self, check: str, message: str) -> None:
        self.add("FAIL", check, message)

    @property
    def failed(self) -> bool:
        return any(i["level"] == "FAIL" for i in self.items)

    def dump(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for i in self.items:
            counts[i["level"]] = counts.get(i["level"], 0) + 1
        return {
            "result": "FAIL" if self.failed else "PASS",
            "counts": counts,
            "items": self.items,
        }


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def disp_width(text: str) -> int:
    """终端显示宽度：东亚宽字符按 2 计。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pick_latest(payload_dir: Path, base: str) -> Path | None:
    """同名文件取最高版本：`x.json` < `x.v2.json` < `x.v3.json`（冻结不改写的产物用 .vN 落盘）。"""
    stem = base[: -len(".json")]
    versioned = re.compile(rf"^{re.escape(stem)}(?:\.v(\d+))?\.json$")
    best: tuple[int, Path] | None = None
    for p in payload_dir.glob(f"{stem}*.json"):
        m = versioned.match(p.name)
        if m is None:
            continue
        version = int(m.group(1) or 1)
        if best is None or version > best[0]:
            best = (version, p)
    return best[1] if best else None


def resolve_run_dir(args: argparse.Namespace) -> Path:
    if args.run_dir:
        return Path(args.run_dir).expanduser().resolve()
    home = Path(os.environ.get("ECWF_HOME", PROJECT_ROOT / ".ecwf"))
    runs = home / "runs"
    if not runs.is_dir():
        raise SystemExit(f"找不到 runs 目录：{runs}（先建 run，或用 --run-dir 指定）")
    candidates = sorted(p for p in runs.iterdir() if p.is_dir())
    if args.run:
        candidate = runs / args.run
        if not candidate.is_dir():
            raise SystemExit(f"run 不存在：{candidate}")
        return candidate
    if not candidates:
        raise SystemExit(f"{runs} 下没有任何 run")
    return candidates[-1]


# --------------------------------------------------------------------------- #
# 1. Schema 校验
# --------------------------------------------------------------------------- #
def schema_errors(payload: Any, schema: Any) -> list[str]:
    try:
        from jsonschema import Draft7Validator
    except ImportError:  # pragma: no cover
        raise SystemExit(
            "需要 jsonschema：python3 -m venv .venv && .venv/bin/pip install jsonschema"
        )
    validator = Draft7Validator(schema)
    out = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(x) for x in err.absolute_path) or "<root>"
        out.append(f"{where}: {err.message}")
    return out


def validate_one_payload(name: str, path: Path, report: Report) -> Any | None:
    schema_name = PAYLOAD_SCHEMAS[name]
    schema_path = SCHEMA_DIR / schema_name
    if not schema_path.is_file():
        report.fail(f"schema/{schema_name}", "schema 文件缺失，无法校验")
        return None
    try:
        payload = load_json(path)
    except json.JSONDecodeError as exc:
        report.fail(f"payload/{name}", f"不是合法 JSON：{exc}")
        return None
    except OSError as exc:
        report.fail(f"payload/{name}", f"读取失败：{exc}")
        return None

    errors = schema_errors(payload, load_json(schema_path))
    if errors:
        for e in errors:
            report.fail(f"payload/{name}", e)
        return payload
    report.ok(f"payload/{name}", f"符合 {schema_name}")
    return payload


# --------------------------------------------------------------------------- #
# 2. 占位符 / 编造校验
# --------------------------------------------------------------------------- #
def iter_image_ids(node: Any, trail: str = "") -> list[tuple[str, Any]]:
    """递归找出所有 key == imageId 的值。"""
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{trail}/{k}" if trail else k
            if k == "imageId":
                found.append((here, v))
            else:
                found.extend(iter_image_ids(v, here))
    elif isinstance(node, list):
        for idx, v in enumerate(node):
            found.extend(iter_image_ids(v, f"{trail}[{idx}]"))
    return found


def load_image_map(run_dir: Path) -> dict[str, Any]:
    """返回 {file: entry}，file 为 04-assets/image-map.json 里的 assets[].file。"""
    path = run_dir / "04-assets" / "image-map.json"
    if not path.is_file():
        return {}
    try:
        data = load_json(path)
    except json.JSONDecodeError:
        return {}
    return {a.get("file"): a for a in data.get("assets", []) if isinstance(a, dict)}


def check_image_ids(payloads: dict[str, Any], run_dir: Path, report: Report) -> None:
    image_map = load_image_map(run_dir)
    known_ids = {
        a.get("imageId")
        for a in image_map.values()
        if isinstance(a.get("imageId"), int)
    }
    any_ref = False
    for name, payload in payloads.items():
        if payload is None:
            continue
        for where, value in iter_image_ids(payload):
            any_ref = True
            label = f"imageId@{name}:{where}"
            if isinstance(value, int):
                if not image_map:
                    report.fail(
                        label,
                        f"出现具体 imageId={value}，但 04-assets/image-map.json 不存在或为空 → 判定编造",
                    )
                elif value not in known_ids:
                    report.fail(
                        label,
                        f"imageId={value} 在 image-map.json 中无对应记录 → 判定编造，打回成员6",
                    )
                else:
                    report.ok(label, f"imageId={value} 可追溯到 image-map")
            else:
                m = PENDING_RE.match(str(value))
                if not m:
                    report.fail(label, f"既不是整数也不是 PENDING:<file> 占位符：{value!r}")
                elif m.group(1) not in image_map:
                    report.fail(
                        label, f"{value} 指向的素材 {m.group(1)!r} 不在 image-map.json 里"
                    )
                else:
                    report.warn(
                        label, f"{value} 仍是占位符，须由成员8 在 ⑧-2 上传后回填真实 imageId"
                    )
    if not any_ref:
        report.warn("imageId", "payload 中没有任何图片引用（若计划不带图，请人工确认）")


# --------------------------------------------------------------------------- #
# 3. 可机器判定的业务规则（§七 清单子集）
# --------------------------------------------------------------------------- #
def check_plan_rules(payload: Any, report: Report) -> None:
    if not isinstance(payload, dict):
        return
    campaigns = payload.get("campaignShoppingTypes") or []
    for idx, c in enumerate(campaigns):
        tag = f"plan[{idx}]"
        if c.get("pause") is not True:
            report.fail(f"{tag}.pause", "新建必须 pause=true（灰度铁律），开量走 ⑧-6 update")
        else:
            report.ok(f"{tag}.pause", "pause=true")

        audience = c.get("audience") or {}
        keywords = (audience.get("keywords") or "").strip()
        if keywords:
            count = len([w for w in re.split(r"[,，]", keywords) if w.strip()])
            if count > MAX_KEYWORDS:
                report.fail(
                    f"{tag}.audience.keywords", f"意图词 {count} 个，超过上限 {MAX_KEYWORDS}"
                )
            else:
                report.ok(f"{tag}.audience.keywords", f"意图词 {count} 个 ≤ {MAX_KEYWORDS}")
        else:
            report.warn(f"{tag}.audience.keywords", "未填意图词，仅靠词包绑定，请人工确认")

        if not str(audience.get("fcKeywordWtpids") or "").strip():
            report.warn(f"{tag}.audience.fcKeywordWtpids", "未绑定词包 ID")

        material = c.get("material") or {}
        if not material:
            report.fail(f"{tag}.material", "material 为空 → 线上素材会是全空（brand 必填）")
            continue
        titles = material.get("title") or []
        bad = [
            t.get("text", "")
            for t in titles
            if not (9 <= len(t.get("text", "")) <= 60)
        ]
        if bad:
            report.fail(f"{tag}.material.title", f"{len(bad)} 条标题不在 9-60 字符")
        else:
            report.ok(f"{tag}.material.title", f"{len(titles)} 条标题长度合规")
        if not (material.get("description") or []):
            report.warn(
                f"{tag}.material.description", "描述为空；商品推广（targetType=1）必须传描述"
            )


def check_copy_forbidden(run_dir: Path, report: Report) -> None:
    path = run_dir / "03-copy" / "copy.json"
    if not path.is_file():
        report.warn("03-copy", "copy.json 不存在，跳过禁投词结论检查")
        return
    try:
        copy = load_json(path)
    except json.JSONDecodeError:
        report.fail("03-copy", "copy.json 不是合法 JSON")
        return
    check = copy.get("forbiddenCheck") or {}
    if check.get("passed") is True:
        report.ok("forbiddenCheck", "成员3 自检标记通过")
    else:
        hits = check.get("hitWords") or []
        report.fail("forbiddenCheck", f"禁投词自检未通过，命中：{hits}")


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验 06-payloads 与素材映射（只读）")
    ap.add_argument("--run", help="runId（目录名），默认取最新")
    ap.add_argument("--run-dir", help="直接指定 run 目录")
    ap.add_argument("--allow-missing", action="store_true", help="payload 缺文件只报警告")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args)
    report = Report()
    payload_dir = run_dir / "06-payloads"
    if not payload_dir.is_dir():
        print(f"FAIL 找不到 {payload_dir}", file=sys.stderr)
        return 1

    payloads: dict[str, Any] = {}
    for base, _schema in PAYLOAD_SCHEMAS.items():
        path = pick_latest(payload_dir, base)
        if path is None:
            (report.warn if args.allow_missing else report.fail)(
                f"payload/{base}", f"{payload_dir} 下缺该文件"
            )
            payloads[base] = None
            continue
        payloads[base] = validate_one_payload(base, path, report)

    check_image_ids(payloads, run_dir, report)
    check_plan_rules(payloads.get("addCampaignShopping.json"), report)
    check_copy_forbidden(run_dir, report)

    if args.json:
        print(json.dumps({"runDir": str(run_dir), **report.dump()}, ensure_ascii=False, indent=2))
    else:
        print(f"run: {run_dir}\n")
        width = max(disp_width(i["check"]) for i in report.items)
        for i in report.items:
            pad = " " * (width - disp_width(i["check"]))
            print(f"[{i['level']:4}] {i['check']}{pad}  {i['message']}")
        counts = report.dump()["counts"]
        print(
            "\n结论：{}  (PASS={} WARN={} FAIL={})".format(
                "FAIL" if report.failed else "PASS",
                counts.get("PASS", 0),
                counts.get("WARN", 0),
                counts.get("FAIL", 0),
            )
        )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
