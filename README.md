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

## 执行工作区约定（摘要）

```
<session workspace>/.ecwf/runs/<runId>/
├── manifest.json       # 状态机 + 索引
├── 01-product/ … 07-audit/   # 成员1-7 各自编号目录
└── 08-execution/       # 成员8：preflight / before-snapshot / calls.jsonl …
```

- `.ecwf/.gitignore` = `*` + `!.gitignore`（产物不入库）
- token / 密钥不入盘；`call_id` 必落 `calls.jsonl` 以便回连 `mybmo-mcp` 的 `audit_log`

## 状态

设计稿。`CampaignShoppingService.addCampaignShopping` 尚未实测，首单须以 `pause=true` 小范围验证。
