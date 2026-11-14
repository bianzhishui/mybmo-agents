# audit/

**外部锚点**：每次"审计放行"追加一行日志，用于"防偷改"（免密钥）。

- **按 run 分片**：`audit/approvals/<runId>.log`（JSONL，带 `prev` hash 链），**只存本机、不入库**。
  每个 run 一条独立链 → 多流程/多机/多分支并行都不会互相踩，git 也按文件干净合并。
- 旧版全局 `audit/approvals.log` 仅作**迁移期只读兼容**（`verify` 找不到分片时回退读它），不再追加。**本机已移除**（代码仍保留回退逻辑以兼容旧机器）。
- 写入：团长跑 `python3 scripts/manifest.py --run <runId> anchor`
- 核验：`python3 scripts/manifest.py --run <runId> verify --strict-anchor`
- 每行：`{ts, runId, payload, payloadSha256, approvalSha256, prev}`
- 判据：`payloadSha256 == sha256(payload)`、`approvalSha256 == sha256(07-audit/approval.json)`、
  该行 `prev == sha256(上一行原文)`；且只有 `approval.result == "pass"` 才允许锚定。
- 并发：`anchor` 内已有同机文件锁（`fcntl.flock`）；跨机靠"按 run 分片"避免竞争。

## 存储与可移植性（重要）
- **⚠ `git clean -xfd` 会把本机锚点一起删掉**（它被 ignore，属"未跟踪/忽略"文件）。
  定期备份：`tar czf approvals-$(date +%F).tar.gz audit/approvals`；删日志前先备份。
- **只存本机、不入版本库**（`.gitignore` 已忽略 `audit/approvals/` 与 `audit/approvals.log`）。
  因此 `prev` 链只能防"同机随手改"，git 历史**不再**是外部参照。
- **换机器 / 新 clone**：日志不存在 → `verify --strict-anchor` 会 FAIL。两种处理：
  1) 在本机重新 `manifest.py anchor`（需要 run 目录与 approval.json 也在本机）；
  2) 手工把旧机器的 `audit/approvals/<runId>.log` 拷过来（保持原文，勿改行）。
- 需要"跨机取证"时，请把该目录单独备份（`tar czf approvals.tar.gz audit/approvals`），不要依赖仓库。
