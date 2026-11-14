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
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from _runbook import image_map_version as _image_map_version
from _runbook import pick_latest_image_map as _pick_latest_image_map
from _runbook import resolve_run_dir as _resolve_run_dir

try:
    from _enums import AUDIENCE_FIELD_ENUMS, PICTURES_ALLOWED_SIZES, check_enum
except ImportError:  # pragma: no cover - 直接以脚本路径导入时的兜底
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _enums import AUDIENCE_FIELD_ENUMS, PICTURES_ALLOWED_SIZES, check_enum

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
    """同名文件取最高版本：`x.json` < `x.v2.json` < `x.v3.json`（冻结不改写的产物用 .vN 落盘）。

    支持任意扩展名（`base` 可为 `audit-report.md` / `required-fields.json`）。
    """
    suffix = Path(base).suffix
    stem = base[: -len(suffix)] if suffix else base
    versioned = re.compile(rf"^{re.escape(stem)}(?:\.v(\d+))?{re.escape(suffix)}$")
    best: tuple[int, Path] | None = None
    for p in payload_dir.glob(f"{stem}*{suffix}"):
        m = versioned.match(p.name)
        if m is None:
            continue
        version = int(m.group(1) or 1)
        if best is None or version > best[0]:
            best = (version, p)
    return best[1] if best else None


def resolve_run_dir(args: argparse.Namespace) -> Path:
    return _resolve_run_dir(args.run, args.run_dir)


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


def load_image_map(run_dir: Path, explicit: str | None = None) -> tuple[dict[str, Any], str]:
    """返回 ({file: entry}, 来源说明)。file 为 image-map 里的 assets[].file。"""
    path = Path(explicit).expanduser().resolve() if explicit else _pick_latest_image_map(run_dir / "04-assets")
    if path is None or not path.is_file():
        return {}, "（未找到 image-map）"
    try:
        data = load_json(path)
    except json.JSONDecodeError:
        return {}, f"{path.name}（JSON 解析失败）"
    assets = {a.get("file"): a for a in data.get("assets", []) if isinstance(a, dict)}
    supersedes = data.get("supersedes")
    label = path.name + (f"（supersedes {supersedes}）" if supersedes else "")
    return assets, label


def check_image_ids(
    payloads: dict[str, Any], run_dir: Path, report: Report, explicit_map: str | None = None
) -> None:
    image_map, map_label = load_image_map(run_dir, explicit_map)
    report.ok("image-map 版本", f"以 {map_label} 为溯源基准")
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
            # T2：material.userPortrait 走"复用已过审品牌头像"分支，不当素材图溯源。
            # 品牌头像是图库既有对象（120×120、auditStatus=2），不由 04-assets 产出，
            # 拿它去 image-map 查必然是"无记录"。这里只要求是正整数，并提示证据链要求。
            if "userPortrait" in where:
                if isinstance(value, int) and value >= 1:
                    report.ok(
                        label,
                        f"userPortrait imageId={value} 按『复用已过审品牌头像（120×120）』处理，"
                        "不按素材图溯源；须在 _notes 里给 call_id 证明（getImageList / getCampaignShopping）",
                    )
                else:
                    m = PENDING_RE.match(str(value))
                    if m:
                        report.warn(
                            label,
                            f"{value} 仍是占位符：品牌头像须在 ⑧-1 前确定真实 imageId（复用图库既有头像）",
                        )
                    else:
                        report.fail(label, f"userPortrait.imageId 只接受正整数，实际 {value!r}")
                continue
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


def check_audience_enums(payload: Any, report: Report) -> None:
    """T3：枚举表补 excludeTrans "0"。schema 之外再核一次业务枚举，
    防止"schema 与口径脱钩"（P1-8：schema 曾只认 1/4，把服务端真值 "0" 判非法）。"""
    if not isinstance(payload, dict):
        return
    for idx, c in enumerate(payload.get("campaignShoppingTypes") or []):
        audience = c.get("audience") or {}
        if not isinstance(audience, dict):
            continue
        for field in AUDIENCE_FIELD_ENUMS:
            if field not in audience:
                continue
            err = check_enum(AUDIENCE_FIELD_ENUMS, field, audience[field])
            label = f"plan[{idx}].audience.{field}"
            if err:
                report.fail(label, err)
            else:
                report.ok(label, f"= {audience[field]!r} 在允许枚举内")
        if "interests" not in audience:
            report.fail(
                f"plan[{idx}].audience.interests",
                "缺 interests（键级唯一必带）→ 901216「兴趣错误」；不限请写 \"0\"",
            )
        else:
            report.ok(f"plan[{idx}].audience.interests", "interests 已带")

        # P1-1：region 传非法值不报错、会被静默放大为"不限"。只收地域码。
        if "region" in audience:
            region = str(audience["region"])
            if re.fullmatch(r"\d+(,\d+)*", region):
                report.ok(f"plan[{idx}].audience.region", f"= {region!r}（地域码形态合法）")
            else:
                report.fail(
                    f"plan[{idx}].audience.region",
                    f"= {region!r} 不是地域码（只收数字，逗号分隔；不定向写 9999999）。"
                    "传中文/非法值不会报错，会被静默放大成不限",
                )


def _entry_dims(entry: dict) -> tuple[int | None, int | None]:
    if entry.get("width") and entry.get("height"):
        return entry.get("width"), entry.get("height")
    size = entry.get("size")
    if isinstance(size, dict) and size.get("width") and size.get("height"):
        return size.get("width"), size.get("height")
    return None, None


def check_picture_dimensions(run_dir: Path, payloads: dict[str, Any], report: Report,
                             explicit_map: str | None = None) -> None:
    """P0-2 机检兜底：pictures 的 imageId 必须是**精确宽高白名单**里的尺寸。

    `assets_pack.py` 只在"打包本地图片"时卡尺寸；但**复用图库既有 imageId**（如直接把上游
    `getProduct.logo` 的 800×800 图塞进创意）会绕过它 —— 这正是本 run 第 2 次 901217 的成因。
    这里按 image-map 里声明的尺寸再核一遍。
    """
    image_map, map_label = load_image_map(run_dir, explicit_map)
    by_id = {
        a.get("imageId"): a for a in image_map.values() if isinstance(a.get("imageId"), int)
    }
    allowed = PICTURES_ALLOWED_SIZES
    payload = payloads.get("addCampaignShopping.json")
    if not isinstance(payload, dict):
        return
    for ci, c in enumerate(payload.get("campaignShoppingTypes") or []):
        pics = ((c or {}).get("material") or {}).get("pictures") or []
        if len(pics) > 18:
            report.fail(f"plan[{ci}].material.pictures", f"{len(pics)} 张 > 上限 18（901217 数量超限）")
        for i, item in enumerate(pics):
            v = (item or {}).get("imageId")
            label = f"plan[{ci}].pictures[{i}].尺寸"
            if not isinstance(v, int):
                continue  # 占位符，尺寸等回填后再验
            entry = by_id.get(v)
            if entry is None:
                continue  # 已由 check_image_ids 判编造
            w, h = _entry_dims(entry)
            if not w or not h:
                report.warn(label, f"imageId={v} 在 {map_label} 未声明 width/height，无法验证尺寸")
            elif (w, h) in allowed:
                report.ok(label, f"imageId={v} = {w}×{h} 命中白名单")
            else:
                hint = "（这是白名单尺寸的转置：方向固定，横竖颠倒也会被拒）" if (h, w) in allowed else ""
                report.fail(
                    label,
                    f"imageId={v} = {w}×{h} 不在精确白名单 {{370×245, 1140×640, 750×1000}}{hint}"
                    " —— 上传成功 ≠ 尺寸合规",
                )


def check_execution_artifacts(run_dir: Path, report: Report) -> None:
    """第5层可逆性：⑧ 一旦开工，回滚基准与 call_id 流水必须在。"""
    d = run_dir / "08-execution"
    if not d.is_dir() or not any(d.iterdir()):
        return
    for name in ("before-snapshot.json", "calls.jsonl"):
        if not (d / name).is_file():
            report.fail(f"08-execution/{name}", "缺失（回滚/审计依据）")
    calls = d / "calls.jsonl"
    if calls.is_file():
        bad = 0
        for ln, line in enumerate(calls.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if not row.get("call_id"):
                bad += 1
        if bad:
            report.fail("08-execution/calls.jsonl", f"{bad} 行缺 call_id 或不是合法 JSON")
        else:
            report.ok("08-execution/calls.jsonl", "每行都有 call_id")


CALL_ID_RE = re.compile(r"\b[0-9a-f]{12}\b")
EVIDENCE_RE = re.compile(r"机审通过|用户确认|已通过机审|审核通过")


PLACEHOLDER_TEXT_RE = re.compile(r"待核实|<\s*[^<>]{1,40}\s*>|\{\{|\}\}|TODO|FIXME|TBD")


def check_placeholders(payloads: dict[str, Any], report: Report) -> None:
    """H3：payload 里不得残留占位文本（待核实 / <...> / {{...}} / TODO）。

    schema 的 minLength 拦不住 `"productId":"待核实"`——必须单独扫。
    """
    BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]{64,}$")

    def walk(node: Any, trail: str):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "content":  # uploadImage 的 base64 正文，不参与占位扫描
                    continue
                yield from walk(v, f"{trail}/{k}" if trail else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{trail}[{i}]")
        elif isinstance(node, str):
            if PENDING_RE.match(node) or BASE64ISH_RE.match(node):
                return
            yield trail, node

    hits = 0
    for name, payload in payloads.items():
        if payload is None:
            continue
        for trail, text in walk(payload, ""):
            m = PLACEHOLDER_TEXT_RE.search(text)
            if m:
                hits += 1
                report.fail(f"占位文本@{name}:{trail}",
                            f"残留占位 {m.group(0)!r}：接口不认占位，必须先替换（{text[:60]!r}…）")
    if not hits:
        report.ok("占位文本", "payload 无待核实/<...> 残留")


def check_calls_integrity(run_dir: Path, report: Report) -> None:
    """M1 + M2：calls.jsonl 的 hash 链（防事后篡改）与 account 一致性。"""
    path = run_dir / "08-execution" / "calls.jsonl"
    if not path.is_file():
        return
    manifest = {}
    if (run_dir / "manifest.json").is_file():
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest = {}
    manifest_account = manifest.get("account")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    broken = nochain = 0
    account_bad: list[str] = []
    prev_raw = ""
    for ln, raw in enumerate(lines, 1):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            broken += 1
            continue
        if "prev" in row:
            expected = hashlib.sha256(prev_raw.encode("utf-8")).hexdigest() if prev_raw else ""
            if str(row.get("prev")) != expected:
                broken += 1
        else:
            nochain += 1
        if manifest_account and row.get("account") and row["account"] != manifest_account:
            account_bad.append(f"第{ln}行 account={row['account']!r}≠manifest {manifest_account!r}")
        prev_raw = raw
    if broken:
        report.fail("calls.jsonl 链", f"{broken} 行 prev 不匹配（可能被事后改动）")
    elif nochain:
        report.warn("calls.jsonl 链", f"{nochain} 行为旧记录（无 prev 字段），无法校验完整性（新记录会带链）")
    else:
        report.ok("calls.jsonl 链", "hash 链完整")
    if account_bad:
        for m in account_bad:
            report.fail("calls.jsonl 账户", m)
    elif manifest_account:
        report.ok("calls.jsonl 账户", f"与 manifest.account={manifest_account!r} 一致")


def check_audit_evidence(run_dir: Path, report: Report) -> None:
    """建议11：审计报告里"机审通过/用户确认"这类断言必须给可复现 call_id 或原话。

    只做 WARN（审计是散文，硬 FAIL 会误伤）；列出缺证据的行，供审计官自查。
    """
    path = pick_latest(run_dir / "07-audit", "audit-report.md")
    if path is None:
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        report.warn("07-audit", f"读取失败：{exc}")
        return
    misses: list[str] = []
    for i, line in enumerate(lines):
        if not EVIDENCE_RE.search(line):
            continue
        ctx = line + " " + (lines[i - 1] if i > 0 else "")
        if not CALL_ID_RE.search(ctx):
            misses.append(f"第{i + 1}行：{line.strip()[:60]}")
    if misses:
        report.warn(
            "07-audit/取证",
            f"{len(misses)} 处'机审通过/用户确认'类断言附近无 call_id（示例：{misses[0]}）",
        )
    else:
        report.ok("07-audit/取证", "取证断言均带 call_id")


def check_plan_payload_consistency(
    payload: Any, run_dir: Path, report: Report, explicit_map: str | None = None
) -> None:
    """⑤ 计划草案与 ⑥ payload 的图片追溯键必须一致（P2-1 / 建议10）。

    规则（按"⑤ 占位符 → image-map 查 imageId → 对 ⑥ 的取值"判定，而不是简单字符串比对）：
      - ⑤ `PENDING:<file>` + ⑥ 整数  → 该整数必须等于 image-map 里 <file> 的 imageId
      - ⑤ `PENDING:<file>` + ⑥ 同一占位符 → PASS（都待回填）
      - 两边都是整数 → 必须相等
      - ⑤ 的占位符在 image-map 里查不到 → FAIL（编号失效/链断）
    """
    if not isinstance(payload, dict):
        return
    plan_path = pick_latest(run_dir / "05-plan", "plan-draft.json")
    if plan_path is None:
        report.warn("05-plan", "找不到 plan-draft*.json，跳过 ⑤↔⑥ 追溯键一致性检查")
        return
    try:
        plan = load_json(plan_path)
    except (json.JSONDecodeError, OSError) as exc:
        report.fail("05-plan", f"{plan_path.name} 读取失败：{exc}")
        return
    image_map, map_label = load_image_map(run_dir, explicit_map)

    plans = plan.get("campaignShoppingTypes") or []
    payloads = payload.get("campaignShoppingTypes") or []
    if not plans or not payloads:
        report.warn("05-plan", "⑤ 或 ⑥ 没有 campaignShoppingTypes，跳过一致性检查")
        return
    if len(plans) != len(payloads):
        report.fail(
            "05-plan↔06-payload",
            f"计划条目数不一致：⑤ {len(plans)} / ⑥ {len(payloads)}（{plan_path.name}）",
        )
        return

    def norm_pics(c: Any) -> list:
        return (((c or {}).get("material") or {}).get("pictures")) or []

    for ci, (plan_c, pay_c) in enumerate(zip(plans, payloads)):
        p_pics = norm_pics(plan_c)
        y_pics = norm_pics(pay_c)
        if len(p_pics) != len(y_pics):
            report.fail(
                f"05-plan↔06-payload[{ci}]",
                f"pictures 张数不一致：⑤ {len(p_pics)} / ⑥ {len(y_pics)}（{plan_path.name}）",
            )
            continue
        if not p_pics:
            report.warn(f"05-plan↔06-payload[{ci}]", "pictures 为空，跳过追溯键检查")
            continue
        for i, (pp, yp) in enumerate(zip(p_pics, y_pics)):
            label = f"traceKey[{ci}][{i}]"
            pv = (pp or {}).get("imageId")
            yv = (yp or {}).get("imageId")
            pm = PENDING_RE.match(str(pv)) if not isinstance(pv, int) else None
            expected = None
            if pm:
                entry = image_map.get(pm.group(1))
                if entry is None:
                    report.fail(
                        label,
                        f"⑤ {plan_path.name} 的 {pv} 在 {map_label} 里查不到该素材 "
                        "（编号失效/链断，见 P2-1/P2-4）",
                    )
                    continue
                expected = entry.get("imageId")
            elif isinstance(pv, int):
                expected = pv
            else:
                report.fail(label, f"⑤ 的 imageId 既非整数也非 PENDING 占位符：{pv!r}")
                continue

            if isinstance(yv, int):
                if isinstance(expected, int) and yv == expected:
                    report.ok(label, f"⑤ {pv} ↔ ⑥ {yv}（经 {map_label} 对齐）")
                elif not isinstance(expected, int):
                    report.warn(
                        label,
                        f"⑤ {pv} 对应素材尚未回填 imageId，⑥ 已是整数 {yv}（可能已手工回填，人工确认）",
                    )
                else:
                    report.fail(label, f"⑤ {pv} 对应 imageId={expected}，但 ⑥ 用的是 {yv} —— 追溯键不一致")
            else:
                ym = PENDING_RE.match(str(yv))
                if ym and pm and ym.group(1) == pm.group(1):
                    report.ok(label, f"⑤⑥ 同为 {yv}（均待回填）")
                else:
                    report.fail(label, f"⑤ 是 {pv!r} 而 ⑥ 是 {yv!r} —— 追溯键不一致")


VOLUME_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mL")


def check_required_fields_consistency(run_dir: Path, report: Report) -> None:
    """建议9 / P2-2：上游产物"自述 vs 事实"对账。"""
    path = pick_latest(run_dir / "01-product", "required-fields.json")
    if path is None:
        report.warn("01-product", "找不到 required-fields*.json，跳过自述/事实对账")
        return
    try:
        data = load_json(path)
    except (json.JSONDecodeError, OSError) as exc:
        report.fail("01-product", f"{path.name} 读取失败：{exc}")
        return
    fields = data.get("fields") or {}
    uc = data.get("userConfirmations") or {}
    problems: list[str] = []

    changed: set[str] = set()
    for key, val in data.items():
        if not key.startswith("changesFrom"):
            continue
        for item in (val or {}).get("changes", []) if isinstance(val, dict) else []:
            changed.update(re.findall(r"fields\.([A-Za-z_]\w*)", str(item)))
    for name, f in fields.items():
        if isinstance(f, dict) and "内容错误" in str(f.get("source") or ""):
            changed.add(name)

    for name in uc.get("unchangedFields") or []:
        if name in changed:
            problems.append(
                f"unchangedFields 含 {name!r}，但变更日志/字段 source 显示它被更正过 "
                "—— 它不该出现在『未变更』清单里（自述 vs 事实矛盾）"
            )

    spec_value = str((fields.get("spec") or {}).get("value") or "")
    for note in uc.get("complianceNotesRetained") or []:
        for vol in VOLUME_RE.findall(str(note)):
            if vol not in spec_value:
                problems.append(
                    f"complianceNotesRetained 写了 {vol}mL，但 fields.spec.value={spec_value!r} 与它不一致"
                )

    if problems:
        for p_msg in problems:
            report.fail(f"01-product/{path.name}", p_msg)
    else:
        report.ok(
            f"01-product/{path.name}",
            "自述与事实一致（unchangedFields 无被更正字段、备注规格与 spec 一致）",
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
    ap.add_argument("--image-map", help="显式指定 image-map 文件（默认按 .vN 取 04-assets 下最新一版）")
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

    check_image_ids(payloads, run_dir, report, args.image_map)
    check_plan_rules(payloads.get("addCampaignShopping.json"), report)
    check_audience_enums(payloads.get("addCampaignShopping.json"), report)
    check_plan_payload_consistency(
        payloads.get("addCampaignShopping.json"), run_dir, report, args.image_map
    )
    check_audit_evidence(run_dir, report)
    check_copy_forbidden(run_dir, report)
    check_required_fields_consistency(run_dir, report)
    check_picture_dimensions(run_dir, payloads, report, args.image_map)
    check_execution_artifacts(run_dir, report)
    check_placeholders(payloads, report)
    check_calls_integrity(run_dir, report)

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
