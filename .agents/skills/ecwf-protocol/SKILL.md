---
name: ecwf-protocol
description: ECWF 电商推广（CampaignShopping）自动化搭建链路的共享协议：成员目录所有权、交接契约、manifest 状态机、六步实施顺序、单一写入口与审计规则。任何 ECWF 角色（团长 / 成员1-8）开始工作前必须加载。
whenToUse: 当一个任务是电商推广计划搭建链路的一环——商品分析、词包、文案、素材、计划草案、接口参数、审计或实施——并且工作区里有 `.ecwf/` 或 `docs/ec-shopping-workflow-v2.md` 时加载。做与百度营销无关的任务不要加载。
---

# ECWF 共享协议

方案的全文在 `docs/ec-shopping-workflow-v2.md`；本文件只讲**跨角色必须一致的那几条**，8 个角色的身份与专属约束在各自的 Agent preset 里，不重复。

## 1. 工作区与目录所有权

一次投放 = 一个 run：

```
<项目根>/.ecwf/runs/<YYYYMMDD-HHMMSS>-<slug>/
├── manifest.json     # 唯一状态机（团长维护）
├── 01-product/       # 成员1 写
├── 02-keywords/      # 成员2 写
├── 03-copy/          # 成员3 写
├── 04-assets/        # 成员4 写；成员8 回填 imageId
├── 05-plan/          # 成员5 写
├── 06-payloads/      # 成员6 写；成员8 回填 imageId
├── 07-audit/         # 成员7 写
└── 08-execution/     # 成员8 写
```

| 规则 | 内容 |
|---|---|
| 单向写入 | 成员 N 只写 `0N-*/`，只读 `01..0N-1`；团长写 `manifest.json`；成员8 额外可回填 `04-assets/image-map.json` 与 `06-payloads/*.json` |
| 冻结不改写 | 上游产物被下游消费后即冻结。要改落 `xxx.v2.json` 并更新 manifest 的 `version`，**禁止原地覆盖** |
| 文件名封闭 | 每个目录**只允许**本协议列出的文件名（见下表）+ **下划线开头**的辅助文件（`_notes.json`、`_draft.md`）。**禁止新增同级约定名**（如 `notes.json`、`summary.md`）——审计官与 `/5.6` 自动校验只认固定名字，多出来的文件会让核对多一个不确定项 |
| 不可再生必留 | `04-assets/*.png` 必留；`.b64` 可删（能从 png 重编码）；线上对象只存 ID |
| 敏感信息不入盘 | token / 授权码 / 密钥绝不写进 run 目录；`productId` / `imageId` 属业务 ID，可落盘 |

### 1.1 目录内允许的文件名（封闭清单）

| 目录 | 允许的文件 |
|---|---|
| `01-product/` | `analysis.md`、`required-fields.json` |
| `02-keywords/` | `packages.json`、`binding.md` |
| `03-copy/` | `copy.json`、`image-prompts.md` |
| `04-assets/` | `img-*.png`、`img-*.b64`、`image-map.json` |
| `05-plan/` | `plan-draft.json` |
| `06-payloads/` | `addKeywordPackages.json`、`uploadImage.json`、`addCampaignShopping.json` |
| `07-audit/` | `audit-report.md` |
| `08-execution/` | `preflight.json`、`execution-plan.json`、`before-snapshot.json`、`after-snapshot.json`、`calls.jsonl` |

要改一版就落 `xxx.v2.json`（同名 + `.v2`），要写辅助说明就落 `_notes.json` / `_draft.md`。**除这两类，不要新增文件名。**

## 2. 交接契约（团长派活时必须给全）

子 agent 看不到团长的上下文，prompt 必须自包含：

```
runId: <20261113-114432-jewelry-clean>
runDir: .ecwf/runs/<runId>
输入: <具体文件路径列表>
输出: <具体文件路径，且必须在该成员自己的编号目录内>
完成判据: <可检查的条件，例如"通过 schemas/xxx.schema.json"/"每条标题 9-60 字符">
```

交接靠**文件**，不靠对话。任何"我口头上告诉你了"都不算交付。

## 3. manifest 状态机

```json
{"stages": {"01-product": {"status": "done", "by": "成员1", "file": "01-product/required-fields.json", "version": 1}}}
```

- 状态：`pending / running / done / rejected / frozen`
- `rejected` 必须带 `reason` + `retryTo`
- 断点续跑：重跑先读 manifest，`done` 跳过，只补 `pending` / `rejected`
- `objects` 记 `imageIds / packageIds / campaignShoppingId`

## 4. 调度拓扑

```
①商品 → ②词包 → ③文案 → ④素材 → ⑤计划 → ⑥参数 → ⑦审计 → ⑧实施
```

- **①–④ 可并行**：彼此无依赖，都是纯产出，各写各的目录。
- **⑤–⑧ 必须串行**：⑤ 依赖 ①-④，⑥ 依赖 ⑤，⑦ 依赖 ⑥，⑧ 依赖 ⑦ 结论为"通过"。
- 进入 ⑧ 后顺序也不可换：`imageId`（⑧-2）先于建计划（⑧-4）；`packageId`（⑧-3）先于绑包（⑧-5）。

## 5. 单一写入口

**只有成员8 能调用任何 `mutating` 的百度能力。** 成员1-7 只产出"待执行参数"，自己不执行，也看不到写工具。

- 每次写调用必须留 `call_id`（`08-execution/calls.jsonl`），这是回滚与事后还原的唯一线索。
- 改前必须落 `08-execution/before-snapshot.json`；`material` 是整体替换，`fengchaoTitle` 读不回。
- **未通过 ⑧-6 回读校验，不得声称"已实施"。**

## 6. 审计与打回

- 审计结论只有两种：`通过` / `打回给成员X，具体问题：…`
- 打回由团长用 `send_message` 发回同一个子 agent（本地子 agent 可续），要求出 `v2` 文件。
- 有任何一层不过就是打回，不存在"基本通过"。

## 7. 本项目的脚本（都用 Python 标准库 + `.venv`）

| 脚本 | 用途 |
|---|---|
| `scripts/new_run.py` | 建 run 目录、`.ecwf/.gitignore`、`manifest.json` |
| `scripts/manifest.py` | 读/改 stages 状态，查"下一步该谁" |
| `scripts/record_call.py` | 追加 `calls.jsonl` 一行 |
| `scripts/snapshot_diff.py` | before/after 快照比对（51 字段） |
| `scripts/assets_pack.py` | 成员4：纯 base64 / imageName≤200 / MD5 / 体积 / image-map |
| `scripts/backfill_image_ids.py` | ⑧-2 后用 uploadImage 返回值回填 imageId（默认落 `.v2.json`） |
| `scripts/validate_payloads.py` | §5.6 预检：schema 校验 + imageId 编造检测 + 业务规则 |

八个脚本都已实测可用；`validate_payloads.py` / `backfill_image_ids.py` 需要 `jsonschema`（`.venv`）。

## 8. 十条铁律（摘要，全文见方案 §八）

1. 层级：电商线只有「计划」一层；"单元" = 计划内定向组（1:1）。
2. 接口：只用 `KeyWordShoppingService` / `ImageManageService` / `CampaignShoppingService`。
3. 单一写入口：写能力只有成员8。
4. 词包：先成包再绑计划，绑定是**替换式**。
5. 素材：`material` 整体替换，读-改-写必须读全；`brand` 为空则素材全空。
6. 图片：必须先 `uploadImage(addImage=true)` 拿真实 `imageId`；连接器无生成能力。
7. 顺序：`imageId` 先于建计划、`packageId` 先于绑包。
8. 实施：`addCampaignShopping` 未实测，首单 1 计划 / 1 词包 / 1 图、`pause=true`。
9. 不编造：`productId` / `imageId` / 落地页一律不得编造，缺就写"待核实"。
10. 存储：产物落 `.ecwf/runs/<runId>/`，上游冻结，`call_id` 必落盘，token 不入盘。
