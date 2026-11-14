# audit/

**外部锚点**：每次"审计放行"追加一行日志，用于"防偷改"（免密钥）。

- **按 run 分片**：`audit/approvals/<runId>.log`（JSONL，带 `prev` hash 链），**入 git**。
  每个 run 一条独立链 → 多流程/多机/多分支并行都不会互相踩，git 也按文件干净合并。
- 旧版全局 `audit/approvals.log` 仅作**迁移期只读兼容**（`verify` 找不到分片时回退读它），不再追加。
- 写入：团长跑 `python3 scripts/manifest.py --run <runId> anchor`
- 核验：`python3 scripts/manifest.py --run <runId> verify --strict-anchor`
- 每行：`{ts, runId, payload, payloadSha256, approvalSha256, prev}`
- 判据：`payloadSha256 == sha256(payload)`、`approvalSha256 == sha256(07-audit/approval.json)`、
  该行 `prev == sha256(上一行原文)`；且只有 `approval.result == "pass"` 才允许锚定。
- 并发：`anchor` 内已有同机文件锁（`fcntl.flock`）；跨机靠"按 run 分片"避免竞争。
