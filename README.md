# mybmo-agents

百度电商推广（CampaignShopping）**自动化搭建工作流**——多角色协作方案与执行规范。

## 定位

本项目是**工作流 / skill 层**，负责"把商品素材变成线上可投放计划"的编排、审计与实施。
它**不实现**任何百度接口，所有实际调用都通过 `mybmo-mcp` 提供的 MCP 能力完成。

| 层 | 项目 | 职责 |
|---|---|---|
| 能力层 | [`mybmo-mcp`](../mybmo-mcp) | 封装百度营销 API（读/写能力 + 账户 + 审计） |
| **工作流层** | **`mybmo-agents`（本项目）** | 角色分工、产物规范、审计清单、灰度实施 |
| 执行环境 | DSH / WorkBuddy | 加载本项目，按方案驱动 agent 执行 |

## 核心设计

- **链路**：图片+卖点 → 关键词（词包）→ 创意（文案/图片）→ 计划+定向 → API 参数 → 审计 → 实施
- **层级**：电商线只有「计划」可编排，无单元层；创意是计划级 `material`
- **单一写入口**：所有 `mutating` 能力只由「成员8 实施操作员」执行
- **单一写入口 + 存储契约**：所有产物落 `.ecwf/runs/<runId>/`，`manifest.json` 为状态机
- **灰度实施**：预检 → 图片入库 → 建词包 → 建计划(pause) → 绑包 → 回读开量

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/ec-shopping-workflow-v2.md`](docs/ec-shopping-workflow-v2.md) | 方案 v2 全文：结构修正、链路、接口路由、8 角色分工、产物存储规范、6 层审计清单、铁律 |
| [`docs/dsh-multi-agent-v1.md`](docs/dsh-multi-agent-v1.md) | **DSH 多 Agent 实现方案**：9 个 Agent preset、团长派活机制、单一写入口的权限隔离、安装与验证、排期 |
| `mybmo-mcp:docs/decisions/0011-readonly-endpoint.md` | 上游 ADR：只读 MCP 入口 `/mcp/readonly` 的设计与实现（本项目的需求单已蒸馏进 `dsh-multi-agent-v1.md` §5.2/5.3，原需求文件已删除） |

## 执行环境（DSH）

本仓库同时是一个可安装的 DSH 组合：

| 产物 | 位置 | 作用 |
|---|---|---|
| bundle `@local/dsh-ecwf` | `adapters/dsh/` | 9 个 Agent preset（团长 + 成员1-8） |
| skill `ecwf-protocol` | `.agents/skills/ecwf-protocol/` | 共享协议：目录所有权、交接契约、六步顺序、审计与打回 |

- 安装：`plugin_manager { action: install_bundle, target: <abs>/adapters/dsh }`
- 百度 MCP 由 Host 级 row 共享一个实例：**读能力人人可用**（成员2 要靠 `KRService` 拓词），**写能力只有成员8**——由 persona + `ecwf-protocol` §5 铁律 + `calls.jsonl` 对账三层保证。
- skill 由 `dsh-skill-filesystem` 从项目根自动发现，改文件即生效，无需重启。

## 执行工作区约定（摘要）

```
<session workspace>/.ecwf/runs/<runId>/
├── manifest.json       # 状态机 + 索引
├── 01-product/ … 07-audit/   # 成员1-7 各自编号目录
└── 08-execution/       # 成员8：preflight / before-snapshot / calls.jsonl …
```

- `.ecwf/.gitignore` = `*` + `!.gitignore`（产物不入库）
- token / 密钥不入盘；`call_id` 必落 `calls.jsonl` 以便回连 `mybmo-mcp` 的 `audit_log`

## 自动校验（§5.6 的落地实现）

`scripts/validate_payloads.py`——成员8 第 1 步预检用的只读校验器：

```bash
python3 -m venv .venv && .venv/bin/pip install jsonschema   # 一次性
.venv/bin/python scripts/validate_payloads.py                # 校验最新 run
.venv/bin/python scripts/validate_payloads.py --run <runId>  # 指定 run
.venv/bin/python scripts/validate_payloads.py --allow-missing --json   # 半成品/CI
```

它做三件事：

1. **模板校验**：`06-payloads/{addKeywordPackages,uploadImage,addCampaignShopping}.json`
   逐份对 `schemas/*.schema.json`（draft-07）校验；
2. **占位符 / 编造校验**：出现具体整数 `imageId` 但 `04-assets/image-map.json`
   查不到 → 判定编造，直接打回成员6；`PENDING:<file>` 视为待回填；
3. **业务规则子集**：`pause=true` 铁律、意图词 ≤2000、标题 9-60 字符、
   `material`/`brand` 非空、`03-copy` 禁投词自检结论。

退出码 `0` 通过 / `1` 有 FAIL / `2` 环境错。只读，不改任何产物。

## 状态

设计稿 + 校验器可用。`CampaignShoppingService.addCampaignShopping` 尚未实测，
首单须 1 计划 / 1 词包 / 1 图、`pause=true`、回读通过再放量。
