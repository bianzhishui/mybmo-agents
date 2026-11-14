#!/usr/bin/env python3
"""脚本回归测试（标准库 unittest，不引入 pytest）。

运行：
    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/python tests/test_scripts.py

设计：每个用例都在临时目录里复制 `tests/fixtures/run_min`（一份最小可过机检的 run），
只改一处再断言——把这几轮手工验证过的正反例固化下来，防止以后改脚本悄悄失效。
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "run_min"
PY = sys.executable
SCRIPTS = ROOT / "scripts"


def run(script: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    import os as _os
    e = _os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [PY, str(SCRIPTS / script), *args], capture_output=True, text=True, env=e
    )


def make_png(path: Path, w: int, h: int) -> None:
    raw = b"".join(b"\x00" + bytes([120, 140, 110]) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


class RunCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ecwf-test-"))
        self.run_dir = self.tmp / "run"
        shutil.copytree(FIXTURE, self.run_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 工具 --------------------------------------------------------------- #
    def load(self, rel: str):
        return json.loads((self.run_dir / rel).read_text(encoding="utf-8"))

    def save(self, rel: str, obj) -> None:
        (self.run_dir / rel).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def write_manifest(self, stages: dict, gates: dict | None = None) -> None:
        m = {"runId": "test", "stages": stages, "finalStatus": "running"}
        if gates is not None:
            m["pendingGates"] = gates
        (self.run_dir / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


class TestValidate(RunCase):
    def test_base_pass(self):
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)

    def test_region_not_a_geo_code(self):
        p = self.load("06-payloads/addCampaignShopping.json")
        p["campaignShoppingTypes"][0]["audience"]["region"] = "全国"
        self.save("06-payloads/addCampaignShopping.json", p)
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("不是地域码", r.stdout)

    def test_trace_key_broken(self):
        p = self.load("05-plan/plan-draft.json")
        p["campaignShoppingTypes"][0]["material"]["pictures"][0]["imageId"] = "PENDING:img-404.png"
        self.save("05-plan/plan-draft.json", p)
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("查不到该素材", r.stdout)

    def test_missing_before_snapshot(self):
        (self.run_dir / "08-execution/before-snapshot.json").unlink()
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("before-snapshot.json", r.stdout)

    def test_dimension_not_in_whitelist(self):
        m = self.load("04-assets/image-map.json")
        m["assets"][0]["width"], m["assets"][0]["height"] = 800, 800
        self.save("04-assets/image-map.json", m)
        p = self.load("06-payloads/addCampaignShopping.json")
        p["campaignShoppingTypes"][0]["material"]["pictures"][0]["imageId"] = 123456
        self.save("06-payloads/addCampaignShopping.json", p)
        q = self.load("05-plan/plan-draft.json")
        q["campaignShoppingTypes"][0]["material"]["pictures"][0]["imageId"] = "PENDING:img-001.png"
        self.save("05-plan/plan-draft.json", q)
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("不在精确白名单", r.stdout)

    def test_self_description_contradiction(self):
        f = self.load("01-product/required-fields.json")
        f["changesFromV2Reason"] = {"changes": ["fields.spec：更正为待核实"]}
        f["userConfirmations"]["unchangedFields"].append("spec")
        self.save("01-product/required-fields.json", f)
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("不该出现在『未变更』清单里", r.stdout)


class TestAssetsPack(RunCase):
    def _attest(self, names, **flags):
        data = {n: {"aiWatermark": False, "beforeAfter": False, "absoluteClaims": False} for n in names}
        data.update(flags)
        p = self.tmp / "attest.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    def test_reject_960x540(self):
        img = self.tmp / "960x540.png"
        make_png(img, 960, 540)
        r = run("assets_pack.py", "--from", str(img), "--run-dir", str(self.run_dir),
                "--content-attest", self._attest(["960x540.png"]), "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不在精确白名单", r.stderr)

    def test_reject_19_images(self):
        d = self.tmp / "many"
        d.mkdir()
        names = []
        for i in range(19):
            n = f"raw-{i:02d}.png"
            make_png(d / n, 750, 1000)
            names.append(n)
        r = run("assets_pack.py", "--from", str(d), "--run-dir", str(self.run_dir),
                "--content-attest", self._attest(names), "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("张数上限 18", r.stderr)

    def test_reject_ai_watermark(self):
        img = self.tmp / "ok.png"
        make_png(img, 750, 1000)
        att = self.tmp / "att.json"
        att.write_text(json.dumps({"ok.png": {"aiWatermark": True, "beforeAfter": False,
                                              "absoluteClaims": False}}), encoding="utf-8")
        r = run("assets_pack.py", "--from", str(img), "--run-dir", str(self.run_dir),
                "--content-attest", str(att), "--dry-run")
        self.assertEqual(r.returncode, 1)
        self.assertIn("内容红线未过", r.stderr)

    def test_accept_750x1000(self):
        img = self.tmp / "ok.png"
        make_png(img, 750, 1000)
        r = run("assets_pack.py", "--from", str(img), "--run-dir", str(self.run_dir),
                "--content-attest", self._attest(["ok.png"]), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("四关全过", r.stdout)


class TestManifest(RunCase):
    def test_version_filename_mismatch(self):
        self.write_manifest({"04-assets": {"status": "done", "file": "04-assets/image-map.json", "version": 7}})
        r = run("manifest.py", "--run-dir", str(self.run_dir), "verify")
        self.assertEqual(r.returncode, 1)
        self.assertIn("错位", r.stdout)

    def test_gate_blocks_success(self):
        self.write_manifest({"08-execution": {"status": "done", "file": "08-execution/before-snapshot.json"}},
                            gates={"G4": {"status": "open"}})
        r1 = run("manifest.py", "--run-dir", str(self.run_dir), "final", "success")
        self.assertEqual(r1.returncode, 1)
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "gate", "G4", "closed",
                 "--evidence", "用户原话：同意开量")
        self.assertEqual(r2.returncode, 0)
        # 置 success 还需 approval + 用户确认
        r2b = run("manifest.py", "--run-dir", str(self.run_dir), "final", "success")
        self.assertEqual(r2b.returncode, 1)
        self.assertIn("user-confirmation", r2b.stderr)

    def test_lint_files_stray(self):
        (self.run_dir / "05-plan" / "notes.json").write_text("{}", encoding="utf-8")
        r = run("manifest.py", "--run-dir", str(self.run_dir), "lint-files")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不在协议封闭清单内", r.stdout)

    def test_lint_files_clean(self):
        r = run("manifest.py", "--run-dir", str(self.run_dir), "lint-files")
        self.assertEqual(r.returncode, 0, r.stdout)


class TestTools(RunCase):
    def test_rollback_plan(self):
        r = run("rollback_plan.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("uploadImage", r.stdout)
        self.assertIn("留档", r.stdout)

    def test_cost_report(self):
        usage = self.tmp / "usage.json"
        usage.write_text(json.dumps({"rows": [
            {"agent": "a", "ts": "2026-11-13T15:00:00+08:00",
             "input_miss": 1000000, "input_hit": 0, "output": 0},
            {"agent": "a", "ts": "2026-11-14T15:00:00+08:00",
             "input_miss": 1000000, "input_hit": 0, "output": 0},
        ]}), encoding="utf-8")
        r = run("cost_report.py", "--usage", str(usage), "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertAlmostEqual(d["total"]["peak_cost"], 2.0, places=4)
        self.assertAlmostEqual(d["total"]["off_cost"], 1.0, places=4)

    def test_make_baseline(self):
        rb = self.tmp / "readback.json"
        rb.write_text(json.dumps({"campaignShoppingTypes": [{
            "campaignShoppingId": 1, "productId": "1", "eshopType": 1, "transType": 14,
            "audience": {"interests": "0", "region": "9999999", "excludeTrans": "0"},
            "material": {"brand": "B", "userPortrait": {"imageId": 9, "astat": 2},
                         "title": [{"text": "标题文案标题文案", "astat": 2}],
                         "description": [{"text": "描述文案描述文案"}],
                         "pictures": [{"imageId": 5, "width": 750, "height": 1000}]},
        }]}), encoding="utf-8")
        r = run("make_baseline.py", "--from", str(rb), "--stdout")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        c = d["campaignShoppingTypes"][0]
        self.assertEqual(c["material"]["userPortrait"], {"imageId": 9})
        self.assertEqual(c["material"]["pictures"], [{"imageId": 5}])
        self.assertNotIn("astat", json.dumps(c))


class TestGatesAndGuards(RunCase):
    def test_set_requires_prereq(self):
        self.write_manifest({})
        r = run("manifest.py", "--run-dir", str(self.run_dir), "set", "06-payloads", "done",
                "--file", "06-payloads/addCampaignShopping.json")
        self.assertEqual(r.returncode, 1)
        self.assertIn("前置阶段未完成", r.stderr)

    def test_set_force_allows(self):
        self.write_manifest({})
        r = run("manifest.py", "--run-dir", str(self.run_dir), "set", "06-payloads", "done",
                "--file", "06-payloads/addCampaignShopping.json", "--force")
        self.assertEqual(r.returncode, 0)

    def test_verify_requires_approval(self):
        (self.run_dir / "07-audit" / "approval.json").unlink()
        self.write_manifest({"06-payloads": {"status": "done", "file": "06-payloads/addCampaignShopping.json",
                                             "version": 1}})
        r = run("manifest.py", "--run-dir", str(self.run_dir), "verify", "--require-approval")
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval.json", r.stdout)

    def test_placeholder_text_fails(self):
        p = self.load("06-payloads/addCampaignShopping.json")
        p["campaignShoppingTypes"][0]["productName"] = "待核实"
        self.save("06-payloads/addCampaignShopping.json", p)
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("残留占位", r.stdout)

    def test_gate_check_unpause(self):
        self.write_manifest({"08-execution": {"status": "done", "file": "08-execution/before-snapshot.json"}},
                            gates={"G4": {"status": "open"}})
        r = run("gate_check.py", "--run-dir", str(self.run_dir), "--for", "unpause")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未闭环", r.stdout)

    def test_retry_cap(self):
        self.write_manifest({})
        for i in range(3):
            r = run("manifest.py", "--run-dir", str(self.run_dir), "retry", "08-execution")
            self.assertEqual(r.returncode, 0, r.stderr)
        r = run("manifest.py", "--run-dir", str(self.run_dir), "retry", "08-execution")
        self.assertEqual(r.returncode, 1)
        self.assertIn("硬上限 3 次", r.stderr)

    def test_lint_version_cap(self):
        (self.run_dir / "05-plan" / "plan-draft.v100.json").write_text("{}", encoding="utf-8")
        r = run("manifest.py", "--run-dir", str(self.run_dir), "lint-files")
        self.assertEqual(r.returncode, 1)
        self.assertIn("版本号", r.stdout)

    def test_calls_chain_mismatch(self):
        calls = self.run_dir / "08-execution" / "calls.jsonl"
        rows = [
            json.dumps({"ts": "t1", "step": "8-2", "capability": "uploadImage",
                        "call_id": "a"*12, "state": "ok", "prev": ""}, ensure_ascii=False),
            json.dumps({"ts": "t2", "step": "8-3", "capability": "addKeywordPackages",
                        "call_id": "b"*12, "state": "ok", "prev": "deadbeef"}, ensure_ascii=False),
        ]
        calls.write_text("\n".join(rows) + "\n", encoding="utf-8")
        r = run("validate_payloads.py", "--run-dir", str(self.run_dir))
        self.assertEqual(r.returncode, 1)
        self.assertIn("prev 不匹配", r.stdout)


class TestHardening(RunCase):
    def _closed_gate(self):
        self.write_manifest({"08-execution": {"status": "done", "file": "08-execution/before-snapshot.json"}},
                            gates={"G4": {"status": "closed", "evidence": "x"}})

    def test_final_success_needs_approval_and_confirmation(self):
        self._closed_gate()
        # fixture 自带 approval.json → 下一步卡 user-confirmation
        r = run("manifest.py", "--run-dir", str(self.run_dir), "final", "success")
        self.assertEqual(r.returncode, 1)
        self.assertIn("user-confirmation", r.stderr)
        (self.run_dir / "08-execution" / "user-confirmation.json").write_text(json.dumps(
            {"confirmedBy": "u", "quote": "同意"}), encoding="utf-8")
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "final", "success")
        self.assertEqual(r2.returncode, 0, r2.stderr)

    def test_final_success_blocks_without_approval(self):
        self._closed_gate()
        (self.run_dir / "07-audit" / "approval.json").unlink()
        (self.run_dir / "08-execution" / "user-confirmation.json").write_text(json.dumps(
            {"confirmedBy": "u", "quote": "同意"}), encoding="utf-8")
        r = run("manifest.py", "--run-dir", str(self.run_dir), "final", "success")
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval.json", r.stderr)

    def test_set_done_needs_file(self):
        self.write_manifest({})
        r = run("manifest.py", "--run-dir", str(self.run_dir), "set", "01-product", "done")
        self.assertEqual(r.returncode, 1)
        self.assertIn("未指定产出文件", r.stderr)

    def test_lint_ignores_dotfiles(self):
        (self.run_dir / ".DS_Store").write_bytes(b"x")
        (self.run_dir / "05-plan" / ".DS_Store").write_bytes(b"x")
        r = run("manifest.py", "--run-dir", str(self.run_dir), "lint-files")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_baseline_has_no_fc_keyword(self):
        base = json.loads((ROOT / "templates" / "campaign-baseline.json").read_text(encoding="utf-8"))
        self.assertNotIn("fcKeywordWtpids", base["campaignShoppingTypes"][0]["audience"])

    def test_make_baseline_drops_fc_keyword(self):
        rb = self.tmp / "rb.json"
        rb.write_text(json.dumps({"campaignShoppingTypes": [{
            "audience": {"interests": "0", "fcKeywordWtpids": "123", "region": "9999999"},
            "material": {"brand": "B", "userPortrait": {"imageId": 9},
                         "title": [{"text": "标题文案标题文案"}], "description": [],
                         "pictures": [{"imageId": 5}]}}]}), encoding="utf-8")
        r = run("make_baseline.py", "--from", str(rb), "--stdout")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)["campaignShoppingTypes"][0]["audience"]
        self.assertNotIn("fcKeywordWtpids", out)


class TestAnchor(RunCase):
    def _anchor(self):
        log = self.tmp / "approvals.log"
        r = run("manifest.py", "--run-dir", str(self.run_dir), "anchor", "--log", str(log))
        return log, r

    def test_anchor_then_strict_verify(self):
        log, r = self._anchor()
        self.assertEqual(r.returncode, 0, r.stderr)
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify",
                 "--strict-anchor", "--log", str(log))
        self.assertEqual(r2.returncode, 0, r2.stdout)
        self.assertIn("三方一致", r2.stdout)

    def test_strict_verify_without_anchor(self):
        r = run("manifest.py", "--run-dir", str(self.run_dir), "verify",
                "--strict-anchor", "--log", str(self.tmp / "none.log"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺锚定", r.stdout)

    def test_payload_swapped_after_anchor(self):
        log, r = self._anchor()
        self.assertEqual(r.returncode, 0, r.stderr)
        p = self.load("06-payloads/addCampaignShopping.json")
        p["campaignShoppingTypes"][0]["campaignShoppingName"] = "被偷换的计划名"
        self.save("06-payloads/addCampaignShopping.json", p)
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify",
                 "--strict-anchor", "--log", str(log))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("锚定", r2.stdout)

    def test_approval_tampered_after_anchor(self):
        log, r = self._anchor()
        self.assertEqual(r.returncode, 0, r.stderr)
        ap = self.load("07-audit/approval.json")
        ap["evidence"] = ["forged"]
        self.save("07-audit/approval.json", ap)
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify",
                 "--strict-anchor", "--log", str(log))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("approval.json 与 manifest 不符", r2.stdout)

    def test_anchor_requires_pass(self):
        ap = self.load("07-audit/approval.json")
        ap["result"] = "fail"
        self.save("07-audit/approval.json", ap)
        r = run("manifest.py", "--run-dir", str(self.run_dir), "anchor",
                "--log", str(self.tmp / "l.log"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("只有 pass 可锚", r.stderr)

    def test_anchor_idempotent(self):
        log = self.tmp / "approvals.log"
        r1 = run("manifest.py", "--run-dir", str(self.run_dir), "anchor", "--log", str(log))
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "anchor", "--log", str(log))
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertEqual(len(log.read_text(encoding="utf-8").strip().splitlines()), 1)

    def test_log_chain_broken(self):
        log, r = self._anchor()
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = log.read_text(encoding="utf-8").splitlines()
        row = json.loads(lines[0]); row["prev"] = "deadbeef"; lines[0] = json.dumps(row, ensure_ascii=False)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify",
                 "--strict-anchor", "--log", str(log))
        self.assertEqual(r2.returncode, 1)
        self.assertIn("日志链断裂", r2.stdout)


class TestAnchorShard(RunCase):
    def test_default_writes_per_run_shard(self):
        anchor_dir = self.tmp / "anchor"
        env = {"ECWF_ANCHOR_DIR": str(anchor_dir)}
        r = run("manifest.py", "--run-dir", str(self.run_dir), "anchor", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        rid = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))["runId"]
        shard = anchor_dir / f"{rid}.log"
        self.assertTrue(shard.is_file(), r.stdout)
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify", "--strict-anchor", env=env)
        self.assertEqual(r2.returncode, 0, r2.stdout)
        self.assertIn("三方一致", r2.stdout)

    def test_unsafe_run_id_does_not_escape_anchor_dir(self):
        m = json.loads((self.run_dir / "manifest.json").read_text(encoding="utf-8"))
        m["runId"] = "../../evil"
        (self.run_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        anchor_dir = self.tmp / "anchor"
        env = {"ECWF_ANCHOR_DIR": str(anchor_dir)}
        r = run("manifest.py", "--run-dir", str(self.run_dir), "anchor", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        files = list(anchor_dir.glob("*.log"))
        self.assertEqual(len(files), 1, r.stdout)
        self.assertNotIn("..", files[0].name)
        # 目录外不能出现 evil.log
        self.assertFalse((self.tmp / "evil.log").exists())
        r2 = run("manifest.py", "--run-dir", str(self.run_dir), "verify", "--strict-anchor", env=env)
        self.assertEqual(r2.returncode, 0, r2.stdout)

    def test_two_runs_parallel_shards(self):
        import shutil as _sh
        a = self.tmp / "a"; b = self.tmp / "b"
        _sh.copytree(self.run_dir, a); _sh.copytree(self.run_dir, b)
        # 两个 run 用不同 runId 才能分片
        for d, rid in ((a, "run-a"), (b, "run-b")):
            m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            m["runId"] = rid
            (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        env = {"ECWF_ANCHOR_DIR": str(self.tmp / "anchor")}
        for d in (a, b):
            r = run("manifest.py", "--run-dir", str(d), "anchor", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
        for d in (a, b):
            r = run("manifest.py", "--run-dir", str(d), "verify", "--strict-anchor", env=env)
            self.assertEqual(r.returncode, 0, r.stdout)


class TestSnapshotEnums(unittest.TestCase):
    def test_exclude_trans_zero_is_legal(self):
        sys.path.insert(0, str(SCRIPTS))
        from snapshot_diff import enum_warnings  # noqa: E402

        self.assertEqual(enum_warnings({"audience": {"excludeTrans": "0"}}), [])
        self.assertTrue(enum_warnings({"audience": {"excludeTrans": "9"}}))


class TestPreflight(RunCase):
    def test_preflight_pass(self):
        # 默认已包含 gate_check --for execute（fixture 是完整 run，含 approval）
        r = subprocess.run(
            ["bash", str(SCRIPTS / "preflight.sh"), "--run-dir", str(self.run_dir), "--skip-snapshot"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("预检结论：PASS", r.stdout)

    def test_preflight_blocks_without_approval(self):
        (self.run_dir / "07-audit" / "approval.json").unlink()
        r = subprocess.run(
            ["bash", str(SCRIPTS / "preflight.sh"), "--run-dir", str(self.run_dir), "--skip-snapshot"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("approval.json", r.stdout)

    def test_preflight_skip_gates(self):
        (self.run_dir / "07-audit" / "approval.json").unlink()
        r = subprocess.run(
            ["bash", str(SCRIPTS / "preflight.sh"), "--run-dir", str(self.run_dir),
             "--skip-snapshot", "--skip-gates"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
