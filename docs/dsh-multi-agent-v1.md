# DSH 多 Agent 实现方案 v1

> 本文是**执行环境侧**的设计：把 `docs/ec-shopping-workflow-v2.md` 定义的 8 角色链路，
> 落成 DeepSeek Harness（DSH）上可安装、可运行、可审计的多 Agent 组合。
> 只考虑 DSH，不考虑其他 runtime。
>
> 依据：本机 `DSH_PROFILE=desktop`、`DSH_INSTALL_DIR=…/app.asar/dsh/node_modules/@deepseek-ai/dsh` 的
> 已安装包 README 与出厂 preset 文件（`presets/standard.patch.yml`、`presets/minimal.patch.yml`）。
> 标注「待实测」的条目没有在线上验证过。
>
> **状态（2026-11-14）：本文是 v1 的"执行环境实现记录"，其中 P4（首个灰度单）已于 2026-11-13 跑通；
> 运行口径（字段约束、闸门、脚本）以 `docs/ec-shopping-workflow-v2.md` 与 skill `ecwf-protocol` 为准。
> v1 之后新增的机制见文末「§十一 2026-11-14 补充」。**

---

## 一、总体结构：三层，各管一件事

```
┌─────────────────────────────────────────────────────────────┐
│ 编排层  bundle @local/dsh-ecwf  → 9 个 Agent preset          │
│         身份 / 工具 / 目录所有权 / 谁能调百度接口             │
├─────────────────────────────────────────────────────────────┤
│ 协议层  skill ecwf-protocol (.agents/skills/)                │
│         交接契约 / manifest 状态机 / 六步顺序 / 审计与打回     │
├─────────────────────────────────────────────────────────────┤
│ 能力层  百度 MCP（Host 级共享：读人人有 / 写只成员8）+ scripts/ │
│         百度接口 / schema 校验 / 产物落盘                     │
└─────────────────────────────────────────────────────────────┘
```

**分工原则**：preset 只说"你是谁、你能用哪些工具、你只能写哪个目录"；skill 只说"怎么交接、什么顺序、怎么算通过"。同一个事实只在一个地方说——因为 preset 是 DSH 私有格式，skill 是纯文件，后者更容易改。

| 层 | 载体 | 变更成本 | 谁能改 |
|---|---|---|---|
| 编排层 | `adapters/dsh/cordis.patch.yml` | 需要重装 bundle + 新开会话 | 开发者 |
| 协议层 | `.agents/skills/ecwf-protocol/SKILL.md` | 改文件即生效（每次 load 重读） | 任何人 |
| 能力层 | MCP 行 + `scripts/` | 改脚本即生效；换 MCP 要改 profile | 开发者 |

---

## 二、9 个 Agent preset

| # | Preset id（`agent_preset` 枚举值） | 行 id | 角色 | 工具 |
|---|---|---|---|---|
| 0 | `ecwf-lead` | `preset-ecwf-lead` | 团长 | fs(rw) + bash + skill + todo + ask-user + **subagent/agent_preset 派活** + workflow |
| 1 | `ecwf-m1-product` | `preset-ecwf-m1` | 商品与卖点分析师 | fs(rw) + bash + skill |
| 2 | `ecwf-m2-keywords` | `preset-ecwf-m2` | 关键词策略师 | fs(rw) + bash + skill |
| 3 | `ecwf-m3-copy` | `preset-ecwf-m3` | 创意设计师 | fs(rw) + bash + skill |
| 4 | `ecwf-m4-assets` | `preset-ecwf-m4` | 素材准备员 | fs(rw) + bash + skill |
| 5 | `ecwf-m5-plan` | `preset-ecwf-m5` | 计划与定向架构师 | fs(rw) + bash + skill |
| 6 | `ecwf-m6-payloads` | `preset-ecwf-m6` | API 参数工程师 | fs(rw) + bash + skill |
| 7 | `ecwf-m7-audit` | `preset-ecwf-m7` | 审计官 | fs(rw) + bash + skill（**不挂任何百度工具**） |
| 8 | `ecwf-m8-exec` | `preset-ecwf-m8` | 实施操作员 | fs(rw) + bash + skill + **MCP `mybmo_exec`** |

- **preset id 必须小写 kebab-case**，`agent_preset` 枚举用这一列的值。
- Loader 行 id（`preset-ecwf-*`）全局唯一，`config.id` 是 preset 身份，两者不要混用。
- 每个成员 preset 都挂 `compaction-basic`（长 run 会爆上下文）与 `agent-instructions`（自动加载 `AGENTS.md`，上限 64KB）。

---

## 三、团长如何派活（这是"多 agent"的核心机制）

团长 preset 的 `delegation` 组里这样配置：

```yaml
- id: tool-subagent
  name: '@deepseek-ai/dsh-tool-subagent'
  config:
    provider: spawn
    toolName: subagent
    agentPreset: ecwf-m1-product        # 默认子 agent
    allowedAgentPresets:                # 暴露成模型的 agent_preset 枚举
      - ecwf-m1-product
      - …
      - ecwf-m8-exec
    maxDepth: 1                         # 子 agent 不能再往下派
```

已核实的行为（`tool-subagent/README.md`）：

| 事实 | 影响 |
|---|---|
| 只有 **in-process `spawn`** 支持 `agentPreset` / `allowedAgentPresets`；`fork`、`acp`、外部后端会拒绝 | 团长只用 `subagent`（spawn），不要用 `subagent_fork` 派角色 |
| 子 agent **拿不到团长上下文**，只看到 prompt | 交接契约必须自包含（skill §2 规定格式） |
| 本地 spawn 子 agent **可续**：`send_message` 追加指令、`interrupt_agent`、结束后可 resume | **审计打回 = 一句话发回同一个子 agent**，不用重开、不丢上下文 |
| 一次调用立即返回 `started subagent <name>`，结果通过完成通知回来 | 团长必须在同一轮里并发拉多个独立任务，然后结束当前轮等通知 |
| `allowedAgentPresets` 必须包含 `agentPreset`，空/重复 id 在加载时失败 | 配置错误会在 roster 上留下诊断，整个 preset 不能组会话 |

### 调度时序

```
团长 ─┬─ subagent(m1, agent_preset=ecwf-m1-product)  ┐
      ├─ subagent(m2, agent_preset=ecwf-m2-keywords) │ 同一轮并发
      ├─ subagent(m3, agent_preset=ecwf-m3-copy)     │
      └─ subagent(m4, agent_preset=ecwf-m4-assets)   ┘
                                    ↓ 四个完成通知到齐
      ── subagent(m5, ecwf-m5-plan)      ← 依赖 01/02/03/04
      ── subagent(m6, ecwf-m6-payloads)  ← 依赖 05
      ── subagent(m7, ecwf-m7-audit)     ← 依赖 06
             ├ 结论「打回」→ send_message 回对应成员 → 等新版本 → 重新审计
             └ 结论「通过」↓
      ── subagent(m8, ecwf-m8-exec)      ← 六步串行，唯一写入口
```

**为什么 ①–④ 能并行、⑤–⑧ 不能**：①–④ 是纯产出，各写各的目录，无共享输入；⑤ 起开始出现真实依赖链（`imageId` → 建计划、`packageId` → 绑包），并行会导致"图片还没入库就建计划"。

---

## 四、为什么不用 `workflow` 工具编排

`standard` preset 里 `dsh-tool-workflow` 是可用的（provider `spawn`），写一段 JS 就能并行拉 8 个 agent，看起来很省事。但它有两个硬伤：

1. **`agent()` 没有 preset 参数** —— 我当前的 workflow 工具 schema 只有 `label / phase / provider / model / reasoning_effort / schema`。也就是说 workflow 里起的子 agent **拿不到角色 preset**（没有 persona、没有工具隔离），角色只能靠 prompt 糊。
2. **角色隔离失效** = 单一写入口失效 —— 如果所有阶段都在同一个"身份"下跑，那"只有成员8 能调写接口"就没有任何技术含义了。

**结论**：确定性编排用 `workflow`（同一身份的多步任务），**角色隔离用 `subagent` + `agent_preset`**。两者不要混用。

---

## 五、权限隔离：单一写入口在 DSH 里怎么落地

这是整套方案唯一有技术杠杆的地方，也是最需要实测的地方。

### 5.1 现状的硬约束（已核实）

| 事实 | 出处 |
|---|---|
| `dsh-mcp-client` **没有工具白名单/黑名单字段** | `dsh-mcp-client/README.md` 配置字段表全部 |
| `dsh-agent-preset` 的 config 只有 `id / plugins / name / description / order`，**没有 `toolFilter`**（老写法会在加载时报迁移错误） | `dsh-agent-preset/README.md`；`tool-subagent/README.md` §Selecting a child preset |
| **preset 贡献的是 scoped tools**（"Preset plugins contribute scoped tools, persona, prompt sections, and policies to those registries"） | `compositions.md` §Choose plugin placement |
| "server 属于单个 preset 而非整个 Host 时，用 plain `mcp-client` 行" | `dsh-mcp-manager/README.md` §When to choose it |

### 5.2 采用方案：两个入口，写边界由结构保证（已落地）

`mybmo-mcp` 已于 **2026-11-13** 上线只读端点（ADR 0011）。本方案据此切成两个入口：

| 入口 | serverName / 前缀 | 挂在哪 | 工具面 |
|---|---|---|---|
| `/mcp/readonly` | `mybmo_read` → `mcp__mybmo_read__*` | **Host 级一行**（所有角色共享，1 个进程） | 7 个只读；无 `bind_account`/`share_account`/`revoke_share` |
| `/mcp` | `mybmo_exec` → `mcp__mybmo_exec__*` | **只挂 `ecwf-m8-exec` 的 preset** | 10 个，含写能力 |

"成员调不了写能力"因此**不再是纪律问题**：他的工具列表里根本没有那些工具。
仍保留的类别纪律（同一人手上也有只读工具，别用错入口）：

| 类别 | 谁可以 | 判据 |
|---|---|---|
| 只读（`mutating: false`） | **所有角色** | `KRService.*`、`PvSearchFunction.getPvSearch`、`getCampaignShopping`、`getImageList`、`getKeywordPackages`、`list_accounts`、`query_audit`、`search_capabilities`… |
| 写（`mutating: true` / `unknown`） | **只有成员8** | `uploadImage` / `addKeywordPackages` / `bindPlanKeywordPackages` / `addCampaignShopping` / `updateCampaignShopping` / `bind_account` / `share_account` / `revoke_share` |

三层保证（结构 → 协议 → 对账）：

1. **结构**：只读端点在服务端直接不注册写工具，`execute_capability` 对 `mutating != "false"` 硬拒，
   并落 `audit_log` `state="denied"`。想越权也调不到。
2. **协议**：skill §5 的类别纪律 + "判定看 `ok` 不看 `isError`"（服务端用 `ok:false` 信封）+ 审计清单第 5 层。
3. **对账**：`08-execution/calls.jsonl` 的每个 `call_id` 必须能回连 `audit_log`，且执行者只有成员8。

> **为什么成员2 必须有读**：它的产出不是"拍脑袋关键词"，而是 `KRService.getKRByQuery` 的实时拓词结果
> （`pv` 月均搜索量 / `recBid` 推荐出价 / `similar` 相似度）。没有读能力，词包质量无从保证。
> 找词只读、不写，所以与"单一写入口"不冲突。

### 5.3 只读端点的已定契约（`mybmo-mcp` 回信）

| 项 | 结论 |
|---|---|
| 上游权威文档 | `mybmo-mcp`: `docs/decisions/0011-readonly-endpoint.md`（ADR 0011）+ `openspec/changes/archive/2026-11-13-readonly-endpoint/`（proposal / design / tasks / reply） |
| 入口 ↔ serverName | `/mcp/readonly` → `mybmo_read`（`mcp__mybmo_read__*`）；`/mcp` → `mybmo_exec`（`mcp__mybmo_exec__*`）；client_id 均为 `709a8e16656a223a1ab7` |
| 形态 | 同端口独立路由 `/mcp/readonly`（第二个 `MCPServer` 实例）；反代不用改 |
| 鉴权 | 同一套 OAuth/tenancy，**不需要新身份**；两个入口**各自授权一次**（`mcp-remote` 缓存键含 URL），同机全员复用 |
| 被拒表现 | 结构化信封 `ok:false` + `error.{message,hint,capability,mutating,endpoint}`，**`isError` 仍为 false** → 判定看 `ok` |
| 审计 | 被拒调用记 `audit_log`，`state="denied"`（复用现成枚举，无迁移） |
| 拒绝时机 | 参数校验**之前**，一个字节都不发到百度 |
| `mutating=unknown` | 一律拒；全表仅 `PreAuditService.materialPreAudit` 一条（sms 线，不在电商范围） |
| `query_audit` | 保持原语义（已按用户隔离），不在只读入口再收窄 |
| 无 `bind_account` | 接受：`needs_reauth: true` 只能人工重绑（走全量入口或后台）——**已写进值班流程** |
| 已知上游缺陷（已修） | ① 只读入口初期"任何令牌都 401"（装配漏搬鉴权 middleware）；② 账户令牌过期一次后**永久失效**（刷新时提前关闭 HTTP 客户端）→ 现已自动刷新自愈，实测 `expires_at` 自动续期约 26.5h |

### 5.3.1 过渡状态与收尾

- 本 bundle 新增 **Host 级 `mcp-mybmo-read`**（指向 `/mcp/readonly`），所有角色共享；
- 成员8 的 preset 新增 **`mcp-mybmo-exec`**（指向 `/mcp`），全量；
- **旧 Host 级 `mcp-pandabase-mybmo`（全量、人人可见）必须下线**，否则结构隔离仍被绕开——
  这是最后一步，用 `plugin_manager set_plugin include:mcp-pandabase-mybmo enabled:false` 完成，可随时恢复。


### 5.4 实测记录（原"待实测"项，已全部收敛）

- [x] preset 作用域内的 `dsh-mcp-client`（stdio + npx mcp-remote + oauth）能连上：`Proxy established successfully between local STDIO and remote StreamableHTTPClientTransport`。
- [x] 两实例并存可行但**不再需要**：第二个实例能连、能调只读能力（`list_accounts` / `search_capabilities` 均成功）。
- [x] OAuth 令牌是否跨作用域共用 —— **不再适用**：设计已改为共享单实例（§5.2/5.3.1），不存在两个作用域各持一份 token 的问题。

### 5.5 排错记录：首次安装 9 个 preset 全部"加载失败"

v1 首次安装后 9 张卡全显示加载失败。用**等价组合**（工作区内临时 profile：`dsh-base` + `dsh-web-app` + 本 bundle，`DSH_HOME` 指到工作区以绕开 `~/.dsh` 写权限）拿到启动诊断，定位到三个**必填 config 缺省**问题——`@deepseek-ai/*` 这些插件要求显式给必填字段，不给就抛 `ValidationError`：

| 行 | 报错 | 修法 |
|---|---|---|
| `tool-fs-search`（9 个 preset 全有） | `$.sampleOverCapGlobResults missing required value` | 加 `config: { sampleOverCapGlobResults: false }` |
| `tool-todo`（lead / m8） | `$.allowParallelInProgress missing required value` | 加 `config: { allowParallelInProgress: true }` |
| `tool-result-pruner`（lead / m8） | `service "toolResultPruner" has been registered` | 在其 `cordis:group` 的 `isolate` 里补 `toolResultPruner: true`；并补 `thresholdChars / headChars / tailChars` |

教训：**preset 里的每一行都要照抄出厂 preset 的 config**，不能只抄 `name`。出厂 `presets/standard.patch.yml` 里每个带 config 的 row 都是"最小合法配置"的范本，抄全再改。

诊断手法（可复用，不需要重启 Desktop 应用）：

```bash
# 1. 工作区内造一个等价 profile（bundle 用 symlink 挂进 node_modules）
mkdir -p .tmp-dsh/profiles/ecwf-test/node_modules/@local
ln -s <repo>/adapters/dsh .tmp-dsh/profiles/ecwf-test/node_modules/@local/dsh-ecwf
# package.json 的 dsh.profile.bundles = ["@deepseek-ai/dsh-base","@deepseek-ai/dsh-web-app","@local/dsh-ecwf"]
# 2. 用工作区 DSH_HOME 启动（.dsh 在工作区外，沙箱不允许写）
DSH_HOME=<repo>/.tmp-dsh npm_config_cache=<repo>/.tmp-npm \
  "$DSH_NODE_EXECUTABLE" "$DSH_INSTALL_DIR/lib/bin.js" --profile ecwf-test
# 3. 失败会打印 "Plugins waiting for services / Failed plugins"，完整诊断落在 $DSH_HOME/logs/startup-*.log
```


---

## 六、run 生命周期（跨角色唯一事实来源）

文件契约与方案 §五 完全一致，这里只补 DSH 侧的操作方式：

| 阶段 | 谁做 | 用什么 |
|---|---|---|
| 建 run | 团长 | `python scripts/new_run.py --slug <slug>` → 建 8 个目录 + `.ecwf/.gitignore` + `manifest.json` |
| 改状态 | 团长 | `python scripts/manifest.py set 03-copy done --file 03-copy/copy.json` |
| 交付 | 各成员 | 只写自己编号目录，回复里给路径 + 完成判据 |
| 校验 | 成员6 / 成员8 | `python scripts/validate_payloads.py --run <runId>` |
| 记流水 | 成员8 | `python scripts/record_call.py --step ⑧-2 --capability uploadImage --call-id <id> --state ok` |
| 快照比对 | 成员8 | `python scripts/snapshot_diff.py --run <runId>` |
| 断点续跑 | 团长 | `python scripts/manifest.py next` → 打印下一个该动的 stage 与负责人 |

**注意**：`.ecwf/` 在工作区内，`workspace-write` 沙箱允许写。若把 `ECWF_HOME` 指到工作区外，子 agent 会撞沙箱策略。

---

## 七、安装与验证

### 7.1 安装

```
plugin_manager { action: install_bundle, target: "/Users/…/mybmo-agents/adapters/dsh" }
```

需要 Full access 或一次性批准。安装会执行包安装与 bundle 选择，**不要**手工改 profile 的 `package.json` / `cordis.patch.yml`（第 5.3 步的注释除外，那是显式的一次编辑）。

### 7.2 验证清单

状态标记：✅ 已实测通过 / ⏳ 需新会话 / ⬜ 未做

| # | 检查 | 期望 | 状态 |
|---|---|---|---|
| 1 | `plugin_manager list_bundles` | 出现 `@local/dsh-ecwf` | ✅ v1.0.0 已装，`application: applied`，9 行全在 |
| 2 | `plugin_manager list_plugins` | 出现 9 行 `preset-ecwf-*`，`enabled: true`，无诊断 | ✅ 9 行全部 `fiberPhase: active`，无 diagnostic |
| 3 | 新建会话选 `ECWF 团长` | 系统提示里出现团长 persona | ✅ 已实测（卡片可选中） |
| 4 | 团长会话里派一个 m2 | `agent_preset` 枚举可用；子 agent 以 `ecwf-m2-keywords` 身份落盘 | ✅ 已实测：子会话日志 `agentPreset: "ecwf-m2-keywords"`，产出 `02-keywords/{packages.json,binding.md,notes.json}`，`packages.json` 过 schema 校验（3 组 / 32 词），manifest 自动置 `02-keywords=done` |
| 5 | 非成员8 会话的工具面 | 只有 `mcp__mybmo_read__*`（7 个只读），**没有** `mcp__mybmo_exec__*` | ✅ 实测：`mcp__mybmo_exec__list_accounts` → `unknown tool` |
| 6 | 成员8 会话里搜百度工具 | **看得到** `mcp__mybmo_exec__*`，且工具真能调通 | ✅ 已实测：子会话 `agentPreset: "ecwf-m8-exec"`，看到 10 个 `mcp__mybmo_exec__*` 工具，`list_accounts` 返回成功（身份 test01 / 账户 `baidu-YF电商-玛卡快消01`）。**`auth_status` 亦然**（当日曾在服务端抛错，服务端修好后 DSH 侧仍报 outputSchema 不匹配——那是 MCP 客户端缓存了旧工具定义，**重启 Desktop 即恢复**；复核实测返回 `usable: true / needs_reauth: false`） |
| 7 | 成员8 跑 `validate_payloads.py` | 与本地一致（0/1/2 退出码） | ✅ 已实测：输出与本地一致（空的 smoke run → `FAIL 3 / WARN 2`，`EXIT_CODE=1`） |
| 8 | 下线旧 Host 级 `mcp-pandabase-mybmo` | 下线后结构隔离成立 | ✅ 已下线：`enabled:false`；`mcp__pandabase_mybmo__list_accounts` → `unknown tool` |
| 9 | 只读端点工具面 | 恰好 **7** 个：`list_accounts`/`auth_status`/`list_shares`/`search_capabilities`/`get_capability_schema`/`execute_capability`/`query_audit` | ✅ 2026-11-13 直连实测 |
| 10 | 只读端点拒写 | `execute_capability("ImageManageService.uploadImage")` → `ok:false` + `readonly endpoint…mutating=true…` | ✅ 实测 |
| 11 | 只读端点放行读 | `execute_capability("KRService.getKRByQuery",{query:"咖啡机"})` → `ok:true`，pcPV 2291 / mobilePV 8407 | ✅ 实测 |
| 12 | 被拒调用落审计 | `audit_log` 出现 `state="denied"`、`request_bytes=2`、`response_bytes=0` | ✅ 实测：`call_id=06e7313929ec`（本次自测产生），确认一个字节都没发到百度 |

**已有会话和它们的子 agent 会保留启动时的插件版本** —— 装完必须新开会话验证，旧会话不会热更新。

### 7.2.0 复核（重启应用后）

- `auth_status` 已恢复：`{"ok":true,"data":[{...,"usable":true,"status":"active","needs_reauth":false}]}`。
  教训：**MCP 工具定义在 Host 启动时缓存**，服务端改了 schema/实现后必须 `Cmd+Q` 重启应用才能生效；刷新窗口不够。
- 9 个 preset 在等价组合下重新挂载：**0 条 `agent preset …` 告警**，全部 healthy。

### 7.2.1 安装记录（2026-11-13）

```
install_bundle  target=/Users/biandc/Desktop/mybmo-agents/adapters/dsh
  → stage: enable, enabled: true, application: applied, warnings: []
  → pnpm exitCode 0，@local/dsh-ecwf 1.0.0 以 link 方式装入 desktop profile
  → profile package.json 的 dsh.profile.bundles 末尾已追加 @local/dsh-ecwf
```

副作用：`adapters/dsh` 是 **link 安装**，改 YAML 后需要重新 `install_bundle`（或禁用再启用）才会重新组会话，不是 HMR。


### 7.3 显示元数据

`locale/en.json`、`locale/zh.json` 已写 `meta.title` / `meta.description` / `meta.detail`，详情页读 `docs/DETAIL.md`。
**图标未提供**（走面板默认图），这是有意的取舍：本 bundle 是纯配置层，没有 UI 元素；如需要品牌图标，后续补 `icon.svg`（≤256KiB）。

---

## 八、排期（按依赖顺序）

| 阶段 | 内容 | 依赖 | 产出 |
|---|---|---|---|
| P0 | 方案文档 + bundle YAML + 协议 skill **（本次已落）** | — | `adapters/dsh/`、`.agents/skills/ecwf-protocol/`、本文 |
| P1 | 装 bundle + 验证清单 1–5 | P0 | roster 里 9 个 preset 可用 |
| P2 | 验证 6–8（preset 内 MCP）+ Host 级 MCP 下线 | P1 | 写入口真正唯一 |
| P3 | 脚本补齐：`new_run` / `manifest` / `record_call` / `snapshot_diff` / `assets_pack` / `backfill_image_ids` | P1 | ✅ 已完成，全部实测（含超限/同 MD5/rejected 守卫/快照告警）；**后续已扩到 16 个脚本 + 66 例回归，见 §十一** |
| P4 | 首个灰度单：1 计划 / 1 词包 / 1 图、`pause=true` | P2+P3 | ✅ **2026-11-13 已跑通**：`addCampaignShopping` 建单成功，`campaignShoppingId=2387079078`（第 4 次尝试；前 3 次分别暴露 userPortrait/图片尺寸/audience 必带键问题，见审计报告） |
| P5 | 批量与归档（`archive_runs.py`） | P4 成功 | 可重复生产 |

~~**P4 之前不要批量**：`addCampaignShopping` 至今 `verified: null`，这是整个方案唯一的真实未知。~~
**（2026-11-13 更新）** 灰度单已完成，`addCampaignShopping` 不再是未知；批量前仍应遵守"1 计划/1 词包/1 图"的收敛原则。

---

## 九、已知风险

| 风险 | 影响 | 处置 |
|---|---|---|
| ~~`addCampaignShopping` 从未实测~~ **已实测（2026-11-13）** | 其真实风险转为：`pause` 参数不生效、`pictures` 精确尺寸/≤18 张、`audience` 只有 `interests` 必带 | 已在 `workflow-v2` 与机检中固化（建单后必须补 `pause`；尺寸/张数/必带键均有闸） |
| 旧 Host 级全量行未下线时，隔离被绕开 | 成员仍能调写工具 | 下线 `mcp-pandabase-mybmo`（§5.3.1），可随时恢复 |
| 子 agent 权限继承会话沙箱 | 写不进 `.ecwf/` 或写到工作区外 | 工作目录固定在仓库内，`ECWF_HOME` 不设外路径 |
| `material` 整体替换 + `fengchaoTitle` 读不回 | 回滚不了 | `before-snapshot.json` 必须在改前落盘 |
| 9 个 preset 任一 YAML 写错 | 该行激活失败、roster 带诊断 | 装前用 YAML 解析 + 结构断言校验（本次已做） |
| 长 run 上下文膨胀 | 成员中途丢上下文 | 每个 preset 挂 compaction；成员只读自己需要的上游文件 |

---

## 十、文件清单

```
mybmo-agents/
├── .agents/skills/ecwf-protocol/SKILL.md   # 协议层（已自动被发现，本会话可见）
├── adapters/dsh/
│   ├── package.json                        # bundle 清单
│   ├── cordis.patch.yml                    # 9 个 preset 声明
│   ├── locale/{en,zh}.json                 # 显示元数据
│   └── docs/DETAIL.md                      # 插件详情页
├── docs/
│   ├── ec-shopping-workflow-v2.md          # 链路方案（业务）
│   └── dsh-multi-agent-v1.md               # 本文（执行环境）
├── schemas/  templates/                    # 校验基准与产物模板
└── scripts/
    ├── _runbook.py                # 公共层：ECWF_HOME、STAGES、版本号、image-map 选版、resolve_run_dir
    ├── _enums.py                  # 共用枚举（excludeTrans 含 "0" 等）
    ├── preflight.sh               # ⑧-1 一键预检（5 项）
    ├── new_run.py                 # 建 run（8 目录 + .gitignore + manifest + 同 slug 检查）
    ├── manifest.py                # 状态机 + verify / gate / retry / lint-files / final
    ├── gate_check.py              # 启动/开量/删除硬闸
    ├── record_call.py             # 追加 calls.jsonl（prev 链 + account）
    ├── snapshot_diff.py           # before/after 比对（可 --fail-on-unexpected）
    ├── assets_pack.py             # 成员4：四关（尺寸/张数/内容自查/md5）+ base64/命名/映射
    ├── backfill_image_ids.py      # ⑧-2 回填 imageId（精确匹配，默认落 .vN+1）
    ├── validate_payloads.py       # §5.6 预检（schema/占位/尺寸/枚举/追溯/自述/链）
    ├── scaffold_plan.py           # ⑤⑥ 初稿生成
    ├── make_baseline.py           # 从读回文件生成账户骨架
    ├── rollback_plan.py           # calls.jsonl → 回滚清单
    ├── reconcile.py               # 未知结果对账清单
    ├── cost_report.py             # 用量 → 峰谷费用
    └── archive_runs.py            # 归档超期 run
```

---

## 十一、2026-11-14 补充：v1 之后新增的机制（口径以 workflow-v2 / skill 为准）

| 机制 | 说明 |
|---|---|
| **审计放行件** | 成员7 通过时落 `07-audit/approval.json`（payload + sha256 + pass + evidence）；08 未拿到不得置 done |
| **人工闸 G1~G6** | `manifest.pendingGates`；开量前 `gate_check --for unpause` 要求全 closed + `user-confirmation.json` |
| **启动/删除闸** | `gate_check --for execute/delete`（01~07 done + approval pass / 删除二次确认） |
| **一键预检** | `scripts/preflight.sh`（validate + verify + lint-files + snapshot_diff + gate_check） |
| **机检加固** | 占位文本、图片尺寸白名单、`region` 形制、⑤↔⑥ 追溯键（多计划）、calls hash 链、账户一致性、未知文件/目录 |
| **状态机** | `version == 文件名 .vN` 强绑定；未闭环闸不许 `final success`；`retry` 硬上限 3 次 |
| **回归与 CI** | `tests/` 66 例；`.github/workflows/ci.yml` |

> 单一写入口的结论不变：写能力只在成员8 的 preset 里；新增的闸门是"让越权/伪造**可发现**"，不是替代权限隔离。
