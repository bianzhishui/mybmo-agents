# ECWF 电商推广搭建多 Agent

把百度电商推广（CampaignShopping）的"图片+卖点 → 关键词 → 创意 → 计划 → API 参数 → 审计 → 实施"链路拆成 9 个职责分离的 Agent preset。

## 装了什么

| Preset id | 角色 | 能写哪里 | 能调百度接口 |
|---|---|---|---|
| `ecwf-lead` | 团长：调度与汇总 | `manifest.json` | 否 |
| `ecwf-m1-product` | 成员1 商品与卖点分析师 | `01-product/` | 否 |
| `ecwf-m2-keywords` | 成员2 关键词策略师（词包） | `02-keywords/` | 否 |
| `ecwf-m3-copy` | 成员3 创意设计师 | `03-copy/` | 否 |
| `ecwf-m4-assets` | 成员4 素材准备员 | `04-assets/` | 否 |
| `ecwf-m5-plan` | 成员5 计划与定向架构师 | `05-plan/` | 否 |
| `ecwf-m6-payloads` | 成员6 API 参数工程师 | `06-payloads/` | 否 |
| `ecwf-m7-audit` | 成员7 审计官 | `07-audit/` | 否 |
| `ecwf-m8-exec` | 成员8 实施操作员 | `08-execution/` | **写：仅此角色**（读：全角色） |

## 怎么用

1. 用 `ecwf-lead` 开一个会话，工作目录指向本仓库。
2. 给它商品图片与卖点；它按 `ecwf-protocol` skill 派活给成员，①–④ 并行、⑤–⑧ 串行。
3. 产物落 `.ecwf/runs/<runId>/`，`manifest.json` 是状态机，断点可续跑。
4. 审计通过后，只有成员8 会去调百度写接口，且首单强制 `pause=true`。

## 关键限制

- 百度 MCP 由 Host 级 row 共享：**只读能力所有角色都能用**（成员2 用关键词规划师拓词），**写能力只有成员8**。写边界目前靠 persona + 协议 + `calls.jsonl` 对账保证；结构性隔离要等 `mybmo-mcp` 提供只读端点。
- 写操作全部串行、全部留 `call_id`；未通过回读校验不算"已实施"。
- `CampaignShoppingService.addCampaignShopping` **从未在线上实测**，首单必须 1 计划 / 1 词包 / 1 图灰度。
- 连接器没有文生图能力，图片必须外部出图后 base64 入库。

完整设计见仓库 `docs/dsh-multi-agent-v1.md`；链路方案见 `docs/ec-shopping-workflow-v2.md`。
