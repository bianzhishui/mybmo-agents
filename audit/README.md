# audit/

**外部锚点**：`approvals.log` 是每次"审计放行"的追加日志（JSONL，带 `prev` hash 链），**入 git 版本库**
（与 `.ecwf/` 相反——锚点必须可追溯、能跨机器核对）。

- 写入：团长跑 `python3 scripts/manifest.py --run <runId> anchor`
- 核验：`python3 scripts/manifest.py --run <runId> verify --strict-anchor`
- 每行：`{ts, runId, payload, payloadSha256, approvalSha256, prev}`
- 判据：`payloadSha256 == sha256(payload)`、`approvalSha256 == sha256(07-audit/approval.json)`、
  该行 `prev == sha256(上一行原文)`。三者任一不符即被 `verify` 判 FAIL。
