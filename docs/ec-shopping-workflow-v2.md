# 电商推广自动化搭建链路（多角色方案 v2）

> **本文件属于 `mybmo-agents` 项目**（工作流 / skill 层）。
> 它调用的百度营销能力（MCP 工具）在 `mybmo-mcp` 仓库，本方案不实现接口，只编排调用。
>
> 本文描述"**图片+卖点 → 关键词 → 创意 → 计划 → API 参数 → 审计 → 实施**"这条自动化链路
> 在**电商推广线（CampaignShopping）**上的落地设计。
>
> 依据：全部字段与接口约束来自实测与后台抓包（**实测样本账户** `baidu-YF电商-玛卡快消01`；本方案是通用能力，不绑定该账户——换账户只需重读该账户自己的基线，见 §六）
> 不是文档推断。字段口径详见 `mybmo-mcp:docs/ec-shopping-official.md`，
> 计划读写速查见 `mybmo-mcp:docs/campaign-shopping.md`，
> 词包见 `mybmo-mcp:docs/keyword-packages.md`，图片见 `mybmo-mcp:docs/image-management.md`。
>
> 状态：设计稿。`addCampaignShopping` 尚未实测，见 [第九节](#九已知未知与前置条件)。

---

## 一、核心认知修正：电商线没有"单元"这一层

这是本方案与"搜索推广通用版"最大的分歧，必须先说清，否则后面全部错位。

| 通用方案假设的层级 | 电商线实际 | 处理方式 |
|---|---|---|
| Campaign（计划） | ✅ `CampaignShopping` 是唯一可编排层级 | 保留 |
| Adgroup（单元） | ⚠️ 有 `adgroupShoppingId` 字段，但**只读、系统自动生成、不可独立增删** | **降级为"计划内定向组"**，一个计划 = 一个定向组 |
| Keyword（关键词） | ❌ 不是单词挂单元，而是**关键词包**（`KeyWordShoppingService`），包独立存在后再绑定计划 | 改为"词包先行、再绑定" |
| Creative（创意） | ❌ 不是独立创意对象，而是**计划级 `material`**（标题/描述/图片/视频整体一个对象） | 改为"素材装配"，随计划一体写入 |

**结论**：不存在"计划与单元架构师"的"单元"职责，改为"计划与定向架构师"，
编排的是 **计划 + `audience` + 词包绑定关系**。

---

## 二、链路总览

```
图片+卖点
   ↓  ①商品与卖点分析（含电商必填项核实）
关键词
   ↓  ②词包策略（产出 keywordPackageTypes）
创意（标题/描述/图片）
   ↓  ③文案撰写 + ④素材准备（base64/命名/映射，不调接口）
计划（计划 + 定向组 + 词包绑定 + 素材装配）
   ↓  ⑤计划与定向架构
API 参数
   ↓  ⑥输出三接口 JSON
审计
   ↓  ⑦六层核对 + 可逆性检查
实施
   ↓  ⑧成员8 六步执行（预检 → 图片入库 → 建词包 → 建计划 → 绑包 → 回读开量）
```

**比"通用版"多一步**：素材准备（④）必须独立成步，因为创意里的图片**只能引用图库 `imageId`**，
而图库只能通过 base64 上传获得（本项目连接器**没有文生图能力**）。
注意：④ 只是**准备物料**（成员4），真正的 `uploadImage` 写操作在 ⑧ 由成员8 执行（见[单一写入口](#单一写入口本方案的核心约束)）。

---

## 三、接口路由表

| 步骤（⑧ 内编号，见[成员8 执行清单](#成员8--实施操作员唯一写入口一次执行清单不跳步)） | 能力 | 读写 | 实测状态 | 执行者 | 关键约束 |
|---|---|---|---|---|---|
| ⑧-2 图片入库 | `ImageManageService.uploadImage` | 写 | ✅ 写实测 | **成员8** | `content` 纯 base64 不带 `data:` 前缀；`addImage=true` 才产 `imageId`；≤20 张/次、≤10MB；`productLine`/`needMola` 不能传 |
| ⑧-3 建词包 | `KeyWordShoppingService.addKeywordPackages` | 写 | ✅ 写实测 | **成员8** | 字段名是 `keywordPackageTypes`；新建 `{packageName, keywordTypes:[{keyword,id:0}]}` |
| ⑧-5 绑词包 | `KeyWordShoppingService.bindPlanKeywordPackages` | 写 | ✅ 写实测 | **成员8** | **双向语义**：包→计划是**替换**（绑 P2 后 P1 的包被清空）、计划→包是**累加**（同计划绑 A、B → `fcKeywordWtpids="A,B"`）；解绑只能 `updateCampaignShopping` 改 `audience` |
| ⑧-4 建计划 | `CampaignShoppingService.addCampaignShopping` | 写 | ⚠️ **未实测** | **成员8** | 9 个必填字段，必须先小范围试 |
| ⑧-1/⑧-6 读计划 | `CampaignShoppingService.getCampaignShopping` | 读 | ✅ 读实测 | 成员8 | 扁平字段名。**按需取字段**：只读核对时只请求需要的字段；**只有要写回 `material`/`audience` 时才必须按 51 项读全**（单次可达 107 KB） |
| ⑧-6 开量 | `CampaignShoppingService.updateCampaignShopping` | 写 | ✅ 写实测 | **成员8** | 两种语义**相反**：**`material` 整体替换**（必须读全再写）；**`audience` 是合并 patch**（只改传入键、未传键保留，**删键没有入口**）。带 `material` 时必须同时带 plan 级 `ideaType`，否则 `901202`「创意类型错误」 |
| ⑧-2 查图 | `ImageManageService.getImageList` | 读 | ✅ 读实测 | 成员8 | 改名后查询有最终一致延迟；**请求里别传 `fields`**（会过滤掉 `auditStatus`/`reason`）——不传才默认返回 |

### 单一写入口（本方案的核心约束）

**所有 `mutating: true` 的能力，只由成员8 一人执行。** 其余成员（1-7）只产出"待执行参数"，
不调用任何写接口。读接口可由成员8 在预检/回读时调用，审计官可读不可写。

理由：
- **审计可追溯**：每次 `execute_capability` 写都落一条审计（带 `call_id`），单一执行者 = 一条顺序链，事后能完整还原"这次投放是怎么建起来的"。
- **回滚单一责任人**：出问题时只有一个人知道"哪些写已发生、按什么顺序回退"。
- **依赖顺序可控**：`imageId` 是建计划的输入依赖，`fcKeywordWtpids` 是绑定的输入依赖——只有同一执行者能保证"图片/词包先于计划"不被并发打乱。

**唯一未实测的是 `addCampaignShopping`**——本方案最大的未知，必须靠"建 1 个 pause 计划"验证掉。

**参数级注意**：`deleteCampaignShopping` **不要传 `optFrom`**（官方文档有、SDK schema 没有，传了会被参数校验拒），只传 `campaignShoppingIds`。

**明确不用的接口**（搜索推广线，本账户上不可用/层级不存在）：
`CampaignService.addCampaign`、`AdgroupService.addAdgroup`、`KeywordService.addWord`、`CreativeService.addCreative`。

---

## 四、角色分工（8 成员 + 团长）

> 每个成员的输出物一律写入执行工作区的 `0N-*/` 目录（N = 成员序号），
> 落盘位置、命名与状态机见[第五节](#五产物存储规范执行工作区)。

### 团长
只编排、只汇总，不代写。调度顺序：①商品分析 → ②词包 → ③文案 → ④素材准备 → ⑤计划架构
→ ⑥API 参数 → ⑦审计 → ⑧实施（成员8 唯一写入口）。审计打回时按"打回给谁"转派。

**等待与重试纪律（P1-6 熔断）**：① 重试预算**硬上限 3 次**，第 3 次仍失败就转人工后台建计划；
② 第 1 次失败时**一次性批量下发**下一轮所需的全部改动（图 + payload + 审计要点），禁止逐个往返——
上一轮 50 次 `wait_agent` / 42 分钟里大量是串行空转。

### 成员1 · 商品与卖点分析师
- **输入**：商品图片 + 卖点素材
- **输出**：商品理解（3 句）+ 卖点清单（3-5 条，每条 ≤15 字）+ **电商必填项核实表**
- **必填项核实表**（通用方案完全漏掉的一项）：`productId`、`productName`、
  `eshopType`（1 度小店 / 4 健康商城）、`transType`（度小店 [14,90]、健康商城 [90]、直播间 [80,81,82,83,84]）
- **硬约束**：不知道的写"待核实"，**禁止编造 productId**

### 成员2 · 关键词策略师（产出"词包"而非"词表"）
- **输入**：成员1 卖点
- **输出**：3 个词包草案
  - 品牌词包：`packageName = "品牌词-XXX"`
  - 品类词包：`"品类词-XXX"`
  - 场景词包：`"场景词-XXX"`（**必须含具体人群或场景**）
- **格式**：`{packageName, keywordTypes:[{keyword, id:0}]}`
- **附加**：标注每个词包"建议绑定到哪个计划"（供成员5）
- **硬约束**：单计划 `audience.keywords` 上限 2000，词包总量不得超

### 成员3 · 创意设计师（文案 + 图片提示词）
- **输出**：
  - **标题 1-12 条，每条 9-60 字符**（电商线是 60 字符，不是搜索线的 50 字节）
  - **描述 0-6 条，每条 9-80 字符**（商品推广**必须**传描述）
  - **图片生成提示词**（连接器无文生图能力，产出提示词交外部出图；出好的图交成员4 整理成 base64 素材包，最终由成员8 上传）
- **硬约束**：不得含"最/第一/根治/百分百"等违禁或绝对化表述；不得承诺疗效（医药/兽药类商品尤其）
- **图片尺寸口径（2026-11-14 定稿）**：`material.pictures` 是**精确宽高白名单、方向固定**——
  商品推广只有 `370×245 / 1140×640 / 750×1000`，自定义直播推广只有 `1140×640`。
  **不是"宽高比"也不是"最小尺寸"**：375×248、960×540（同为 16:9）、1000×750、640×1140 实测全被 `901217` 拒。
  提示词里要直接写死目标宽高，不要写"3:4 / 最小边 ≥750"这类可被推导成非法尺寸的说法。
  张数上限 **18**（19 张 → `901217`「创意设置图片数量超过上限」）。
- **内容红线**（AI 生成水印 / 使用前后对比 / 绝对化用语如"不伤表面""污渍自动溶解"）是
  **我方审计口径，不是平台拒审条件**——平台不会因此拒单（镜像：img-003 带水印+前后对比仍挂在在投计划里、`astat=0`、计划 `status=2` 非 21），
  由审计官裁定、由 `scripts/assets_pack.py` 要求逐张自查声明。

### 成员4 · 素材准备员【v2 新增，不调接口】
- **输入**：成员3 的图片文件
- **输出**：可直接供 `uploadImage` 使用的**素材包**——
  1. 每张图的**纯 base64**（不含 `data:` 前缀，单独落文件）
  2. 统一 `imageName`（≤200 字符，1 汉字算 2）
  3. `图片文件名 → 用途/对应创意` 的映射表
- **硬约束**：
  - **本角色不调用任何接口**，包括 `uploadImage`——上传由成员8 执行
  - base64 必须逐张校验可解码、体积合规（单张 + 整批 ≤10MB）
  - 同名/同图需提示：同图 MD5 去重会返回同一 `imageId`，映射表里要标注"可能复用已有图"
  - 映射表是成员6 写 `material.pictures[]` 的唯一依据，`imageId` 由成员8 上传后回填
  - **上传成功 ≠ 尺寸合规 ≠ 内容合规**（三关各踩过一次真实事故：img-001 内容 / img-002 尺寸 / img-003 内容）。
    `scripts/assets_pack.py` 现已把四关机器化：① 尺寸精确白名单（370×245/1140×640/750×1000，方向固定）
    ② 张数 ≤18 ③ 内容红线逐张自查声明（我方口径，平台不拦）④ md5 与 image-map 一致。
    任一不过**直接不发上传调用**，并给出可读错误。

### 成员5 · 计划与定向架构师（原"计划与单元架构师"改造）
- **输入**：成员1 必填项 + 成员2 词包 + 成员3 文案 + 成员4 素材映射表（`imageId` 由成员8 上传后回填，此阶段写占位符）
- **输出**：每个计划一份结构草案：

```
计划名
├─ targetType   (1商品 / 2直播)
├─ eshopType    (1度小店 / 4健康商城)
├─ productId / productName       商品推广必填
├─ transType    (与 eshopType 配套)
├─ campaignType (1控成本 / 4放量)
├─ budget       (0=不限；放量模式必须>0；范围 [50, 9999999.99])
├─ ocpcBid      ([0.2, 99999]，两位小数)
├─ schedule     (星期 + 起止小时)
├─ audience     (定向组：region / keywords / 词包ID / sex / age / interests …)
└─ material     (brand + userPortrait + title[] + description[] + pictures[imageId])
```

- **硬约束**：
  1. 一个计划对应**一个定向组**，同一计划下素材与词包语义必须一致
  2. `brand` **必填**——不带 `brand`，接口返回的 `material` 整个为空；
     `material.userPortrait`（品牌头像 120×120）也**必带**（缺 → `901217`「创意设置品牌头像错误」），
     直接复用图库/既有计划里已过审的头像 imageId
  2b. `audience` 键级必带**只有 `interests`**（缺 → `901216`「兴趣错误」；空对象 `{}` → `901214`「定向信息为空」）；
     其余键可省、服务端补默认值。写 12 键、读回 13 键（+`excludeTrans`）、绑包后 14 键属正常，**对账以读回为准**
  2c. `pictures[].imageId` 占位符统一 `PENDING:<img-00N>.<ext>`（如 `PENDING:img-001.png`），
     ⑤ 草案与 ⑥ payload **必须引用同一个追溯键**
  3. `transType` 必须与 `eshopType` / `targetType` 配套，不能混搭
  4. **只产出结构草案，不调任何接口**（执行权在成员8）

### 成员6 · API 参数工程师（三接口分列输出）
- **出 payload 以基线为骨架**：以 `templates/campaign-baseline.json`（同账户真实计划的读回值：
  `audience` 全 14 键 + `material` 全要素 brand/userPortrait/title/description/pictures）为骨架，
  **只替换取值，不从 schema 从零拼**。三个易错点（userPortrait 必带、interests 必带、picture 尺寸/张数）
  在基线的 `_easyErrors` 里逐条写明。从零拼 = 每次都在重复踩 P0-1/P0-3。
- **输出**三份可直接执行的 JSON，**每个字段标注来源或"待核实"**：
  1. `addKeywordPackages` 请求体（词包）
  2. `uploadImage` 请求体（图片）
  3. `addCampaignShopping` 请求体（计划 + `audience` + `material`）
- **硬约束**：
  - **只产出参数，不执行任何写接口**（执行权在成员8）
  - 参数名用**驼峰**（如 `campaignShoppingName`）
  - `productId` 未提供就写"待核实"，**禁止编造**
  - 明确标注 `addCampaignShopping` 为"未实测，需 pause 建单验证"
- 素材溯源用 `getImageList` 时**不要传 `fields`**（传了会过滤掉 `auditStatus`/`reason`，就"复现不了机审通过"）；断言必须带 `call_id`

### 成员7 · 审计官（六层核对 + 可逆性）
不生成内容，逐层核对（清单见[第七节](#七审计清单v2)）。
结论只能是：**"通过"** 或 **"打回给成员X，具体问题：……"**

### 成员8 · 实施操作员（唯一写入口，一次执行清单，不跳步）
**本方案所有 `mutating: true` 的能力都由本角色执行**，其余成员不写。
仅在审计通过后启动，按固定顺序执行下面 6 步，每步成功才进下一步：

| # | 动作 | 能力 | 成功判据 |
|---|---|---|---|
| 1 | **预检** | 授权有效性检查 + `getCampaignShopping` 读全 51 字段 + **`bash scripts/preflight.sh --run <runId>` 一把跑完 4 项只读校验** | 授权正常、字段无缺、预检 PASS |
| 2 | **图片入库** | `uploadImage`（`addImage=true`，members4 提供的 base64） | 返回 `imageId`，回填映射表 |
| 3 | **建词包** | `addKeywordPackages`（成员2 的 `keywordPackageTypes`） | 返回 `packageId`，`getKeywordPackages` 回读存在 |
| 4 | **建计划** | `addCampaignShopping`（`pause=true`，`material` 引用步骤2 的 `imageId`） | 返回 `campaignShoppingId`，`succ=oprs` |
| 4b | **立即补暂停（固定收尾，不是保险）** | `updateCampaignShopping` 最小请求体 `{campaignShoppingTypes:[{campaignShoppingId, pause:true}]}` | 回读 `pause=true`。**`addCampaignShopping` 不认 `pause`**：回显 `true`、落库 `false/status=20`，且与绑包无关；不补 = "以为暂停、其实已进审核"。最小请求体**不要全量读-改-写**（带 `material` 必须同时带 plan 级 `ideaType`，否则 `901202`） |
| 5 | **绑词包** | `bindPlanKeywordPackages`（步骤3 的 `packageId` → 步骤4 的计划） | 回读 `audience.fcKeywordWtpids` 含该包 |
| 6 | **回读 → 开量** | `getCampaignShopping` 逐字段比对 → `updateCampaignShopping` 置 `pause=false` | `material`/`audience` 与设计一致，状态 `pause=false` |

**硬约束**：
- **单一写入口**：只有成员8 调写接口；成员4 的 base64、成员6 的 JSON 都是"待执行物料"，不是已执行结果
- **顺序不可乱**：`imageId`（步骤2）必须先于建计划（步骤4）；`packageId`（步骤3）必须先于绑定（步骤5）
- `addCampaignShopping` 未实测，**首次只建 1 个计划、1 个词包、1 张图**（步骤2-5 各一次），验证通过再批量
- **禁止声称"已实施"除非步骤6 回读校验成功**；任何一步失败立即停手并报告 `call_id`
- **⑧-1 的机检一律用 `bash scripts/preflight.sh --run <runId>`**（内部串 `validate_payloads` + `manifest verify` + `manifest lint-files` + `snapshot_diff`，任一 FAIL 即非 0 退出）
- **发写调用前先跑 `scripts/manifest.py verify`**：校验 `version == 文件名 .vN`，并打印"将发送文件"的 sha256，
  与审计放行记录（`execution-plan.json`）一致才发；不一致 = 拿旧 payload 烧最后一次重试额度
- **重试预算硬上限 3 次**：第 1 次失败就要把下一轮所需的**全部改动一次性批量下发**（图 + payload + 审计要点），
  禁止逐个往返；第 3 次再失败彻底停手、转人工后台建计划
- **⑧-4b 的 pause 收尾属 ⑧ 的既定授权范围**（是建单语义缺陷的必备补救，不是额外写操作），
  不需要为"补一次 `pause=true`"另开授权；但 `pause=false` 开量仍须 G4 闭环 + 用户书面确认
- 所有产物写入 `08-execution/`（`preflight` / `execution-plan` / `before-snapshot` / `after-snapshot` / `calls.jsonl`），见[第五节](#五产物存储规范执行工作区)
- 所有写调用的 `call_id` 必须记录成执行日志（`calls.jsonl`），作为回滚依据

---

## 五、产物存储规范（执行工作区）

> 本节是**执行时的落盘契约**。v2 将独立成项目、由 DSH / WorkBuddy 加载执行，
> 所以规范只管"执行工作区"，不写进 mybmo-mcp 的 `data/`。

### 5.1 三个存储层，别混

| 存储层 | 存什么 | 归属 | 生命周期 |
|---|---|---|---|
| **执行工作区** | 成员1-8 的全部产物、图片、payload、快照、`call_id` 流水 | **本工作流项目**（本节定义） | 随 run 存续，可归档/清理 |
| **MCP 审计库** | 每次调用的 `call_id`、入参、返回、结果 | mybmo-mcp（Postgres `audit_log`） | 长期，与 run 无关 |
| **百度服务端** | 计划、词包、图片 `imageId` | 百度 | 真实线上状态，本地**不持有副本** |

工作区规范只管第一层，但必须留出**关联键**打通后两层。

### 5.2 根目录与命名

```
<session workspace>/.ecwf/runs/<runId>/
```

| 项 | 约定 |
|---|---|
| 根目录 | `.ecwf/`（本项目工作区根，隐藏目录，不污染用户项目）；可用环境变量 `ECWF_HOME` 覆盖 |
| `runId` | `<YYYYMMDD-HHMMSS>-<slug>`，如 `20261113-114432-jewelry-clean`（可排序、可读） |
| 防污染 | `.ecwf/.gitignore` 内容固定为 `*` + `!.gitignore` → **不入版本库、不在 `git status` 里碍眼** |
| 溯源 | `manifest.json` 记 `dshSessionId`（DSH 提供 `$DSH_SESSION_ID`） |
| 与审计关联 | `audit_log` 表**没有** `run_id` 字段 → 靠工作区 `calls.jsonl` 记 `call_id` 反查 |

### 5.3 目录结构：一个成员 = 一个编号目录

**核心原则：成员只写自己编号的目录，文件名固定，不互相覆盖。**

```
.ecwf/runs/<runId>/
├── manifest.json              # 唯一状态机 + 索引（团长维护）
├── 01-product/                # 成员1
│   ├── analysis.md            #   商品理解 + 卖点
│   └── required-fields.json   #   productId / eshopType / transType 核实表
├── 02-keywords/               # 成员2
│   └── packages.json          #   3 个词包草案
├── 03-copy/                   # 成员3
│   ├── copy.json              #   标题 / 描述
│   └── image-prompts.md       #   出图提示词
├── 04-assets/                 # 成员4
│   ├── img-001.png            #   原图（不可再生，必留）
│   ├── img-001.b64            #   base64（可再生，可缓存）
│   ├── image-map.json         #   文件名 → imageName → 创意 → 回填 imageId
│   └── image-map.v2.json      #   改版一律 .vN，禁止原地覆盖（v3…v6 同理）
├── 05-plan/                   # 成员5
│   └── plan-draft.json        #   计划结构草案（imageId 为占位符）
├── 06-payloads/               # 成员6
│   ├── addKeywordPackages.json
│   ├── uploadImage.json
│   ├── addCampaignShopping.json
│   ├── addCampaignShopping.v2.json   # 改版 .vN；_notes.json 记改因与溯源
│   └── _notes.json                   # 下划线前缀＝辅助文件（_draft.md / _*-response.json 同）
├── 07-audit/                  # 成员7
│   └── audit-report.md        #   六层清单 + 结论
└── 08-execution/              # 成员8
    ├── preflight.json         #   授权 + 51 字段完整性
    ├── execution-plan.json    #   六步清单（dry-run）
    ├── before-snapshot.json   #   改前 51 字段原文（回滚唯一基准）
    ├── after-snapshot.json    #   改后回读
    └── calls.jsonl            #   每次写的 call_id 流水
```

编号 = 阶段序 = 成员序，一眼看出"卡在第几步、谁还没交"。

### 5.4 六条硬规则

1. **单向写入**：成员 N 只能写 `0N-*/`，只读 `01..0N-1`；成员7 只读全部 + 写 `07-audit/`；成员8 只读 `01..07` + 写 `08-execution/`。
2. **冻结不改写**：上游产物被下游消费后即冻结。要改必须落 `xxx.vN.json` 并更新 `manifest` 版本号，**禁止原地覆盖**——否则审计结论与最终 payload 对不上。
   - **`version` 必须等于文件名里的 `.vN`**（无 `.vN` 视为 1）；`scripts/manifest.py verify` 会检出错位（真实案例：`04-assets.version=7` 而文件是 `image-map.v6.json`）。
   - **已冻结的 payload 一律不原地重写**：改法＝出新文件 `xxx.v(N+1).json` + `_notes.revisionReason` 写明改因与 diff。
3. **文件名落在封闭清单内**（§5.3 目录契约）：清单外文件名用 `scripts/manifest.py lint-files` 检出即 FAIL；改版用 `.vN` 变体、辅助说明用下划线前缀。
4. **只强制留不可再生的**：`04-assets/*.png` 必留（丢了无法重建）；`.b64` 可删（可从 png 重编码）；线上对象只存 ID 不存副本。
5. **`before-snapshot.json` 是回滚唯一基准**：`material` 整体替换 + `fengchaoDescription` 读不回，没有它就无法回滚。
6. **`call_id` 必落盘**：`calls.jsonl` 每行 `{ts, runId, step, capability, call_id, state}`，用 `call_id` 回连 MCP `audit_log`。
7. **敏感信息不入盘**：token / 授权码 / 密钥绝不写进 run 目录；`productId`、`imageId` 属业务 ID，可落盘。

### 5.5 `manifest.json`：状态机 + 断点续跑

```json
{
  "runId": "20261113-114432-jewelry-clean",
  "dshSessionId": "session-xxxx",
  "account": "<account-alias>",
  "createdAt": "2026-11-13T11:44:32+08:00",
  "stages": {
    "01-product":  {"status": "done",     "by": "成员1", "file": "01-product/required-fields.json", "version": 1},
    "02-keywords": {"status": "done",     "by": "成员2", "file": "02-keywords/packages.json", "version": 1},
    "03-copy":     {"status": "rejected", "by": "成员3", "reason": "标题3超60字符", "retryTo": "03-copy"},
    "04-assets":   {"status": "pending"},
    "05-plan":     {"status": "pending"},
    "06-payloads": {"status": "pending"},
    "07-audit":    {"status": "pending"},
    "08-execution":{"status": "pending"}
  },
  "objects": {"imageIds": [], "packageIds": [], "campaignShoppingId": null},
  "finalStatus": "running"
}
```

- 状态枚举：`pending / running / done / rejected / frozen`
- **断点续跑**：重跑时先读 `manifest`，`done` 的步骤跳过，只补 `pending` / `rejected`
- `rejected` 必须带 `reason` + `retryTo`，正好对应"打回给成员X"机制

### 5.6 执行前自动校验（成员8 第 1 步就做）

1. **模板校验**：`06-payloads/*.json` 逐份对项目内置 `schemas/` 的 JSON Schema 校验（驼峰、必填齐、`imageId` 是否为占位符）
2. **占位符校验**：扫 `06-payloads/`，若出现**具体数字 `imageId` 但最新 `04-assets/image-map.vN.json` 无对应记录** → 判定编造，直接打回成员6
3. **⑤↔⑥ 追溯键校验（P2-1 / 建议10）**：读 `05-plan/plan-draft*.json` 最新版，把 ⑤ 的 `PENDING:<file>`
   经 image-map 解析出 imageId，再与 ⑥ 的取值对齐；⑤ 的编号在 image-map 里查不到＝链断，直接 FAIL
   （不再依赖 `_notes` 里的"偏离声明"兜底）
4. **上游自述对账（建议9）**：`01-product/required-fields*.json` 最新版里，`unchangedFields` 不得包含被更正字段，
   `complianceNotesRetained` 的规格数字必须与 `fields.spec` 一致；不符即 FAIL
5. **图片尺寸白名单（P0-2 兜底）**：`pictures[].imageId` 若已是整数，按 image-map 声明的 `width/height`
   必须是 `370×245 / 1140×640 / 750×1000` 之一、且 ≤18 张；**复用图库既有图也照查**（"上传成功 ≠ 尺寸合规"）
6. **执行产物完整性（第5层）**：`08-execution/` 一旦开工，`before-snapshot.json` 与 `calls.jsonl` 必须存在，
   且 `calls.jsonl` 每行都有 `call_id`

> 注（6.5 #2）：本地 `schemas/` 的 `required` **只在本地机检生效**——上游 SDK / `capabilities.json` 表达不了
> `required`，所以"必填"的权威来源始终是服务端报错 + 本仓机检，不要把本地 schema 当成接口约束。

把"人检查"变成"机器检查"，是这层规范最大的价值。

### 5.7 保留与清理

| 项 | 策略 |
|---|---|
| 默认保留 | 最近 30 天 |
| 归档 | 超期移到 `.ecwf/archive/<runId>.tar.gz`（保留 `manifest` + `08-execution/`，丢 `.b64`） |
| 删除 | 只允许删 `pending` / `rejected` 的 run；`finalStatus=success` 的 run 不自动删 |

---

## 六、产出物模板

### 词包 JSON

```json
{
  "keywordPackageTypes": [
    {
      "packageName": "场景词-珠宝清洗",
      "keywordTypes": [
        {"keyword": "首饰发黄怎么洗", "id": 0},
        {"keyword": "在家清洗珠宝", "id": 0}
      ]
    }
  ]
}
```

### 图片上传 JSON

```json
{
  "addImage": true,
  "items": [
    {"content": "<纯base64，无data:前缀>", "imageName": "珠宝清洗剂-主图1"}
  ]
}
```

### 生成工具（把"手拼"机器化）

| 脚本 | 作用 |
|---|---|
| `scripts/make_baseline.py --from <读回.json>` | 从真实读回生成**该账户**的参数骨架（不再手抄 baseline） |
| `scripts/scaffold_plan.py --run-dir <run>` | 从 baseline + ①②③④ 生成 ⑤ `plan-draft` 与 ⑥ 三份 payload 初稿（占位符/溯源就位，只改差异） |
| `scripts/rollback_plan.py --run-dir <run>` | 从 `calls.jsonl` 逆序出回滚清单（标注哪些无接口、需人工） |
| `scripts/cost_report.py --usage <用量.json>` | 按峰谷价目算 token 费用（可复算，不用手算） |

> 生成器只负责"初稿像样"，**不替代审计**：产出后仍必须 `scripts/preflight.sh` 全绿才能进 ⑧。

### 计划 JSON（核心）

> **⑥ 出 payload 以基线为骨架替换取值，不从零拼。** 骨架文件：`templates/campaign-baseline.json`
> （同账户一条真实计划的读回值，`audience` 全 14 键 + `material` 全要素，含 userPortrait / interests / picture 尺寸三处易错点注释）。
> `fcKeywordWtpids` 不写占位符（schema pattern 只收数字），走 ⑧-5 绑包。

```json
{
  "campaignShoppingTypes": [
    {
      "campaignShoppingName": "珠宝清洗剂-场景拉新-20261113",
      "pause": true,
      "targetType": 1,
      "eshopType": "<1 度小店 / 4 健康商城>",
      "productId": "待核实",
      "productName": "<getProduct 实测 productName>",
      "campaignType": "<1 控成本 / 4 放量>",
      "budget": "<预算：0=不限，否则 [50, 9999999.99]>",
      "transType": "<度小店[14,90] / 健康商城[90] / 直播[80-84]>",
      "ocpcBid": "<出价：[0.2,99999]，两位小数>",
      "startTime": "2026-11-14",
      "endTime": "9999-01-01",
      "schedule": [{"weekDay": 1, "startHour": 0, "endHour": 24}],
      "audience": {
        "regionType": "0",
        "region": "9999999",
        "sex": "0",
        "age": "0",
        "customAge": "0",
        "interests": "0",
        "crowd": "0",
        "etradecrowds": "0",
        "excludeCrowd": "",
        "excludeTrans": "0",
        "keywords": "首饰发黄怎么洗,在家清洗珠宝",
        "keywordsExtend": "1",
        "intelligentWeight": "1"
      },
      "ideaType": 1,
      "material": {
        "brand": "<用户确认的 brand>",
        "userPortrait": {"imageId": "<本账户已过审品牌头像 imageId（120×120）>"},
        "title": [{"text": "首饰发黄发乌别扔！一瓶在家洗得光亮如新"}],
        "description": [{"text": "深层去污不伤材质，金银玉石都能用"}],
        "pictures": [{"imageId": "PENDING:img-001.png"}]
      }
    }
  ]
}
```

> 说明：`region` 只传地域码（`9999999`=不限）；传非法值（如中文「全国」）**不报错但被静默放大成不限**，
> 写完必须回读 `region` 校验。`pictures[].imageId` 的占位符统一 `PENDING:<img-00N>.<ext>`。
>
> **通用性**：`eshopType / transType / productId / brand / userPortrait.imageId / budget / ocpcBid`
> 都是**按账户与商品取值**，示例里一律留尖括号占位。可跨账户沿用的只有平台常量
> （`region=9999999`、`sex/age/interests="0"`、`excludeTrans="0"`、`schedule/endTime` 形态）。

---

## 七、审计清单 v2

### 第 0 层 · 前置（通用方案没有，但最容易翻车）

- [ ] 授权有效（token 未过期；`auth_status` 正常）
- [ ] `productId` / `transType` / `eshopType` 是否已核实，而非"待核实"
- [ ] `transType` 与 `eshopType` / `targetType` 是否配套

### 第 1 层 · 词包

- [ ] 三组词包齐全（品牌/品类/场景），场景词含具体人群/场景
- [ ] `packageName` 不与现有包重名
- [ ] 单计划 `audience.keywords` ≤ 2000

### 第 2 层 · 素材

- [ ] 标题 1-12 条，**每条 9-60 字符**
- [ ] 描述 0-6 条，**每条 9-80 字符**，商品推广必须非空
- [ ] `brand` 已填（不填 → `material` 整个空）
- [ ] `material.userPortrait` 存在且为 120×120 品牌头像（缺 → `901217`「创意设置品牌头像错误」）
- [ ] `pictures` 尺寸 ∈ **精确宽高白名单** `370×245 / 1140×640 / 750×1000`（方向固定；不接受按宽高比/最小尺寸推导）
- [ ] `pictures` 张数 **≤18**（19 张 → `901217`「创意设置图片数量超过上限」）
- [ ] 所有 `pictures` 的 `imageId` 均由 `uploadImage(addImage=true)` 真实返回，非编造
- [ ] 文案无违禁词、无绝对化用语、无虚假疗效承诺
- [ ] **内容红线（我方审计口径，非平台拒审）**：无 AI 水印、无使用前后对比、无绝对化/保证性用语；`assets_pack.py` 有逐张自查声明
- [ ] **取证纪律（建议11）**：凡"机审通过""用户确认"这类断言，必须给可复现的 `call_id` 或用户原话，否则视为不合格
- [ ] 视频若用：≥6 秒、≤500MB、有封面

### 第 3 层 · 计划与定向

- [ ] `budget` 在 [50, 9999999.99] 或为 0；`campaignType=4`（放量）时 budget 必须 > 0
- [ ] `ocpcBid` 在 [0.2, 99999]、两位小数
- [ ] `audience.interests` 存在（键级唯一必带；缺 → `901216`「兴趣错误」）；空对象 → `901214`
- [ ] `audience.region` 是地域码或 `9999999`，且**写完回读校验**（非法值被静默放大为不限，`regionType=0/region=9999999` 才是"不限"）
- [ ] `audience.excludeTrans` 允许 `"0"/"1"/"4"`（`"0"`=不限，服务端默认值）
- [ ] 素材与词包语义一致（同一卖点方向）
- [ ] 计划名可识别（含商品 + 策略 + 日期）

### 第 4 层 · 接口参数

- [ ] 字段名驼峰、与官方字段表一致
- [ ] 三份 JSON 分属正确接口，**未混用搜索线接口**（严禁出现 `addWord` / `addCreative` / `addAdgroup`）
- [ ] 无 URL 类字段被编造

### 第 5 层 · 可逆性（通用方案缺失，本方案必加）

- [ ] 新建一律 `pause=true`，禁止直接开量
- [ ] **建单/复制后立刻补暂停（必查项）**：`addCampaignShopping` 不认 `pause`，必须紧跟一次
      `updateCampaignShopping {campaignShoppingId, pause:true}`（最小请求体）并回读 `pause=true`
- [ ] `version == 文件名 .vN`（`scripts/manifest.py verify`）；将发送文件的 sha256 与审计放行记录一致
- [ ] 若涉及 `updateCampaignShopping`：改 `material` 时是否已**读全 51 字段**
      （漏读 `fengchaoTitle` 会被清空——2026-11-11 真实踩过的坑），且**同时带了 plan 级 `ideaType`**（否则 `901202`）
- [ ] 若涉及 `updateCampaignShopping` 改 `audience`：按**合并语义**理解（只改传入键、未传键保留、**删键无入口**，要"清空"只能改值）
- [ ] `deleteCampaignShopping` 未传 `optFrom`（SDK 无该字段，会被参数校验拒）
- [ ] **G4 已闭环**：开量前用户书面确认（表述与实物一致、赠品属实、budget/ocpcBid 终确认）；未闭环只能 `success-paused`
- [ ] 是否有回读比对方案
- [ ] 不含 `deleteCampaignShopping` 等不可逆操作（如有，必须单独二次确认）
- [ ] 产物已按[第五节](#五产物存储规范执行工作区)落盘：`manifest` 状态正确、`before-snapshot` 存在、`06-payloads` 通过自动校验

---

## 八、团长指令的"电商线铁律"

> 1. **层级铁律**：电商线只有"计划"一层可编排；"单元"= 计划内定向组（1:1）。
>    任何成员不得产出 Adgroup / Keyword / Creative 独立层级。
> 2. **接口铁律**：只用 `KeyWordShoppingService` / `ImageManageService` / `CampaignShoppingService`
>    三个族的接口，禁止调用搜索线写接口。
> 3. **单一写入口铁律**：**所有 `mutating: true` 的能力只由成员8 执行**。
>    成员1-7 只产出"待执行参数"，不得调任何写接口——包括 `uploadImage`。
> 4. **词包铁律**：关键词先成包再绑计划。`bindPlanKeywordPackages` 是**双向语义**——
>    **包→计划是替换**（绑 P2 后 P1 的包被清空），**计划→包是累加**（同计划绑 A、B → `"A,B"`）；
>    没有"解绑"入口，解绑只能 `updateCampaignShopping` 改 `audience`。
> 5. **素材铁律**：`material` 是整体替换，读-改-写必须读全；`brand` 为空则素材全空；
>    `userPortrait`（120×120）必带；`pictures` 尺寸走精确白名单、≤18 张；`audience` 只有 `interests` 键级必带，
>    出 payload 以 `templates/campaign-baseline.json` 为骨架替换取值。
> 6. **图片铁律**：图片必须先 `uploadImage(addImage=true)` 拿真实 `imageId`（由成员8 执行）；
>    连接器无生成能力，不得假设"AI 出图直接进创意"。
> 7. **顺序铁律**：`imageId` 先于建计划、`packageId` 先于绑包；顺序由成员8 一人保证。
> 8. **实施铁律**：`addCampaignShopping` 未实测，首单必须 1 计划/1 词包/1 图、`pause=true`、回读通过再放量；
>    **建单/复制后立刻 `updateCampaignShopping` 补 `pause=true`**（建单不认 pause）；重试预算硬上限 3 次、首败即批量下发。
> 9. **不编造铁律**：`productId`、`imageId`、落地页一律不得编造，缺就写"待核实"。
> 10. **存储铁律**：所有产物落在 `.ecwf/runs/<runId>/` 的编号目录；上游产物冻结不改写；
>     `call_id` 必须落 `calls.jsonl`；token/密钥不得入盘。

---

## 九、已知未知与前置条件

| 项 | 状态 | 处理 |
|---|---|---|
| `CampaignShoppingService.addCampaignShopping` | **从未真实调用**（`verified: null`） | 首单建 1 个 `pause=true` 计划验证；这是整个方案唯一的未知 |
| 授权 token | 有到期时间（`expires_at`），`auth_status` 曾报错 | 实施前先刷新授权并确认 `auth_status` 正常 |
| 文生图能力 | **本项目连接器不提供** | 图片由外部生成后 base64 上传，不得假设内置出图 |
| `fengchaoDescription` | 读接口取不到，只能写 | 若涉及风巢素材，改 `material` 前需从后台保存的完整 payload 取，否则会被清空 |

---

## 十、v2 相对 v1 的实质变化

| 维度 | v1（通用版） | v2（电商线） |
|---|---|---|
| 层级 | 计划 → 单元 → 关键词/创意 | 计划 → 定向组 / 词包 / 素材（无单元） |
| 关键词 | 单词 + 出价 + 匹配方式 | **关键词包** + 绑定计划 |
| 创意 | 独立创意对象 + 搜索字数限制 | **计划级 `material`**，标题 9-60 字符、描述 9-80 |
| 图片 | "调用生成能力出图" | **外部出图 → base64 入库 → imageId** |
| 成员数 | 7 | 8（新增"素材准备员"，不调接口） |
| 字数约束 | 标题 50 字节 / 描述 80 字节 | **标题 60 字符 / 描述 80 字符**（已修正） |
| 审计 | 4 层内容核对 | 6 层（含前置、可逆性） |
| 写操作归属 | 分散（谁做谁写） | **单一写入口：全部由成员8 执行** |
| 产物存储 | 未定义（只活在上下文） | **`.ecwf/runs/<runId>/` 按成员编号落盘 + `manifest` 状态机** |
| 实施 | 直接执行 | **六步执行清单 + pause 灰度** |
