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

### 1.2 图片链路：编号是唯一的追溯键

```mermaid
成员3 03-copy/image-prompts.md  ──img-00N──▶  外部出图（人工/外部工具）
                                              ↓ 必须按 img-00N.<ext> 命名
                                      04-assets/img-00N.png
                                              ↓ assets_pack.py（纯 base64 + MD5 + 体积 + imageName）
                                      04-assets/{img-00N.b64, image-map.json}
                                              ↓ 成员8 uploadImage(addImage=true)
                                      imageId → backfill_image_ids.py 回填
```

- **编号一致**：提示词块 `img-001` = 文件名 `img-001.png` = `image-map.json` 的 `file` 字段。三者必须对得上。名字对不上的图（`微信截图.png`）先重命名，**禁止猜对应关系**。
- **提示词必须自包含**：7 个字段（用途/比例与最小边/画面要素/是否允许文字入图/负面词/合规/完整提示词）。连接器没有文生图能力，别人拿着这段就能出图。
- 谁出图都行（人、外部 API、将来某天接入的文生图 MCP），**只要守住命名与字段契约，下游一步都不用改**。
- **编号失效必须当场声明**：某张图的提示词块最终没有对应产出（如改用了官方商品主图），要在**同一个
  `image-prompts.md` 里追加"作废声明"**，写清 编号→无产出、最终改用哪张；保证"编号-文件-映射"三方仍可对账。

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

- 状态：`pending / running / done / rejected / frozen`；`finalStatus`：`running / success / success-paused / failed / abandoned`
- `rejected` 必须带 `reason` + `retryTo`
- **`version` 必须等于 `file` 文件名里的 `.vN`**（无 `.vN` 视为 1）：`scripts/manifest.py verify` 会检出错位
- 断点续跑：重跑先读 manifest，`done` 跳过，只补 `pending` / `rejected`
- `objects` 记 `imageIds / packageIds / campaignShoppingId`

## 4. 调度拓扑

```
①商品 → ②词包 → ③文案 → ④素材 → ⑤计划 → ⑥参数 → ⑦审计 → ⑧实施
```

- **①–④ 可并行**：彼此无依赖，都是纯产出，各写各的目录。
- **⑤–⑧ 必须串行**：⑤ 依赖 ①-④，⑥ 依赖 ⑤，⑦ 依赖 ⑥，⑧ 依赖 ⑦ 结论为"通过"。
- 进入 ⑧ 后顺序也不可换：`imageId`（⑧-2）先于建计划（⑧-4）；`packageId`（⑧-3）先于绑包（⑧-5）。
- **等待熔断**：重试预算**硬上限 3 次**，第 3 次失败转人工后台建计划；第 1 次失败就把下一轮的**全部改动一次性批量下发**（图 + payload + 审计要点），禁止逐个往返。

## 5. 读写分离：读人人有，写只有一个入口

百度 MCP 有**两个入口**，写边界由结构保证（`mybmo-mcp` 2026-11-13 上线 `/mcp/readonly`）：

| 入口 | 工具前缀 | 谁有 | 工具面 |
|---|---|---|---|
| 只读端点 `/mcp/readonly` | `mcp__mybmo_read__*` | **所有角色**（Host 级一个实例） | 7 个；无 `bind_account`/`share_account`/`revoke_share`；`execute_capability` 对 `mutating != "false"` 一律服务端拒绝 |
| 全量端点 `/mcp` | `mcp__mybmo_exec__*` | **只有成员8**（只挂在他的 preset 里） | 10 个，含写 |

所以"成员调不了写能力"不再是纪律问题——**他的工具列表里根本没有那些工具**。
仍要遵守下面的类别纪律（同一个人手上也有只读工具，别用错入口）：

| 类别 | 谁可以 | 怎么判 |
|---|---|---|
| **只读**（`mutating: false`） | **所有角色**（含团长、审计官） | `search_capabilities` 返回里 `mutating` 为 `false`；典型：`KRService.*`、`PvSearchFunction.getPvSearch`、`getCampaignShopping`、`getImageList`、`getKeywordPackages`、`list_accounts`、`query_audit` |
| **写**（`mutating: true` / `unknown`） | **只有成员8** | `uploadImage` / `addKeywordPackages` / `bindPlanKeywordPackages` / `addCampaignShopping` / `updateCampaignShopping`，以及账户类 `bind_account` / `share_account` / `revoke_share` |

调用纪律：

0. **判定结果看 `ok` 字段，不要看 `isError`**。服务端被拒时返回结构化信封：
   `{ok:false, error:{code, message, hint, capability, mutating, endpoint}}`，`isError` 仍是 `false`。
   被拒调用会落 `audit_log`（`state="denied"`，`request_bytes` 极小、`response_bytes=0`——
   即一个字节都没发到百度），这是"有人试图越权"的唯一证据。
   `mutating=unknown` 的能力在只读入口一律被拒，全表只剩 `PreAuditService.materialPreAudit`（sms 线，
   不在电商范围）：**电商线不要用它**；万一真需要，找 `mybmo-mcp` 把它标成 `false`，不要在入口开洞。
1. 调之前先看 `mutating`。不确定按**写**处理（`unknown` 默认当写）。
2. 成员1-7 命中写能力 = **违规**，团长必须判该产物无效并重派。
3. 只有成员8 的写调用要留 `call_id`（`08-execution/calls.jsonl`）——这是回滚与事后还原的唯一线索；只读调用不必落流水，但要能把"数据从哪来"说清（如成员2 记在 `02-keywords/_notes.json`）。
4. 改前必须落 `08-execution/before-snapshot.json`；`material` 是整体替换，`fengchaoTitle` 读不回。
5. **未通过 ⑧-6 回读校验，不得声称"已实施"。**

> 值班必读：只读入口**没有 `bind_account`**。若 `auth_status` 报某个账户 `needs_reauth: true`，
> 任何成员都修不了——必须**人工**走全量入口或 pandabase 后台重新绑定，然后才继续。
> 另外：两个入口**各自授权一次**（`mcp-remote` 的令牌缓存键含 URL），同机全员复用，不需要每人授权。

## 5.1 成员2 的找词（只读，必走）

```
search_capabilities("关键词规划师")  →  get_capability_schema("KRService.getKRByQuery")
execute_capability("KRService.getKRByQuery", {query:…, seedFilter:{…}})   # pv / recBid / similar
KRService.getKRCustom      # 账户历史推词
PvSearchFunction.getPvSearch  # 精确/短语流量
```
结论写进 `02-keywords/packages.json`，**查询入参与依据写进 `02-keywords/_notes.json`**，让审计官能核对"词从哪来"。

## 6. 审计与打回

- 审计结论只有两种：`通过` / `打回给成员X，具体问题：…`
- **取证纪律**：凡"机审通过""用户确认"这类断言，必须给可复现的 `call_id` / 用户原话；
  `getImageList` 取证**不要传 `fields`**（传了会过滤掉 `auditStatus`/`reason`）。
- 打回由团长用 `send_message` 发回同一个子 agent（本地子 agent 可续），要求出 `v2` 文件。
- 有任何一层不过就是打回，不存在"基本通过"。

## 7. 本项目的脚本（都用 Python 标准库 + `.venv`）

| 脚本 | 用途 |
|---|---|
| `scripts/new_run.py` | 建 run 目录、`.ecwf/.gitignore`、`manifest.json` |
| `scripts/manifest.py` | 读/改 stages 状态；`verify` 校验「version↔文件名 .vN」+ 将发送文件 sha256 对账；`final` 写 finalStatus（含 `abandoned`） |
| `scripts/record_call.py` | 追加 `calls.jsonl` 一行 |
| `scripts/snapshot_diff.py` | before/after 快照比对（51 字段） |
| `scripts/assets_pack.py` | 成员4：纯 base64 / imageName≤200 / MD5 / 体积 / image-map；**图片四关硬闸（精确尺寸白名单 / ≤18 张 / 内容红线自查 / md5 一致）** |
| `scripts/backfill_image_ids.py` | ⑧-2 后用 uploadImage 返回值回填 imageId（默认落 `.v2.json`） |
| `scripts/validate_payloads.py` | §5.6 预检：schema 校验 + imageId 编造检测 + 业务规则（默认按 `.vN` 取最新 image-map；`userPortrait` 走品牌头像分支） |
| `scripts/_enums.py` | `validate_payloads` / `snapshot_diff` 共用的字段枚举表（`excludeTrans` 含 `"0"`） |

八个脚本都已实测可用；`validate_payloads.py` / `backfill_image_ids.py` 需要 `jsonschema`（`.venv`）。

## 7.1 预检的授权判据（`auth_status` 返回的是账户数组）

```
调 auth_status → data 是数组，逐账户判：
  usable === true 且 needs_reauth === false    → 可实施
  任一账户 usable === false 或 needs_reauth === true → 停手，请用户重新授权
调不通 auth_status 时退化用 list_accounts（字段等价：status/usable/expires_at）
```

不要只看 `expires_at` 是否已过——服务端会自动续期，以 `needs_reauth` 为准。

## 8. 十条铁律（摘要，全文见方案 §八）

1. 层级：电商线只有「计划」一层；"单元" = 计划内定向组（1:1）。
2. 接口：只用 `KeyWordShoppingService` / `ImageManageService` / `CampaignShoppingService`。
3. 读写分离：只读端点人人可用，**写能力只在成员8 的工具列表里**（结构保证）。
4. 词包：先成包再绑计划。`bindPlanKeywordPackages` **双向**——包→计划替换、计划→包累加；解绑只能 update 改 audience。
5. 素材：`material` 整体替换，读-改-写必须读全；`brand` 为空则素材全空。**`audience` 在 update 里是合并语义**
   （只改传入键、未传键保留、删键无入口），与 `material` 相反。`userPortrait`（120×120）必带；
   `pictures` 尺寸走**精确宽高白名单**（370×245/1140×640/750×1000）、≤18 张；`audience` 只有 `interests` 键级必带；
   ⑥ 出 payload 以 `templates/campaign-baseline.json` 为骨架替换取值，不从零拼。
6. 图片：必须先 `uploadImage(addImage=true)` 拿真实 `imageId`（由成员8 执行）；连接器无生成能力。
7. 顺序：`imageId` 先于建计划、`packageId` 先于绑包。
8. 实施：`addCampaignShopping` 未实测，首单 1 计划 / 1 词包 / 1 图、`pause=true`；**建单/复制后立刻
   `updateCampaignShopping {campaignShoppingId, pause:true}`（最小请求体）补暂停**（建单不认 pause，回显 true 落库 false）；
   重试预算硬上限 3 次、首败即批量下发。
9. 不编造：`productId` / `imageId` / 落地页一律不得编造，缺就写"待核实"。
10. 存储：产物落 `.ecwf/runs/<runId>/`，上游冻结，`call_id` 必落盘，token 不入盘。
