# DSH 多 Agent 实现方案 v1

> 本文是**执行环境侧**的设计：把 `docs/ec-shopping-workflow-v2.md` 定义的 8 角色链路，
> 落成 DeepSeek Harness（DSH）上可安装、可运行、可审计的多 Agent 组合。
> 只考虑 DSH，不考虑其他 runtime。
>
> 依据：本机 `DSH_PROFILE=desktop`、`DSH_INSTALL_DIR=…/app.asar/dsh/node_modules/@deepseek-ai/dsh` 的
> 已安装包 README 与出厂 preset 文件（`presets/standard.patch.yml`、`presets/minimal.patch.yml`）。
> 标注「待实测」的条目没有在线上验证过。

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
│ 能力层  MCP mybmo_exec（只挂成员8）+ 项目 scripts/            │
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

### 5.2 采用方案：MCP 只挂成员8

不在团长/成员1-7 的 plugin list 里出现 `dsh-mcp-client`，只在 `ecwf-m8-exec` 里挂一行：

```yaml
- id: mcp-mybmo-exec
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: mybmo_exec      # 与 Host 级 pandabase_mybmo 不同名，避免工具名冲突
    transport: stdio
    command: npx
    args: ['-y','mcp-remote@latest','https://baiduads.pandabase.top/mcp',
           '--transport','http-only','--disable-resource-parameter',
           '--static-oauth-client-info','{"client_id":"709a8e16656a223a1ab7"}']
    toolCallTimeoutMs: 120000
    startupTimeoutMs: 120000
```

于是：成员8 看到 `mcp__mybmo_exec__*`；其他 8 个角色**一个百度工具都看不到**。"单一写入口"从纪律变成结构。

**代价**：团长与审计官也读不到百度接口。这是可接受的——审计官本来就"只读产物、不调接口"（方案 §四 成员7）；团长需要的读能力（授权状态、现有计划）由成员8 在执行前回读并落 `08-execution/preflight.json`。

### 5.3 灰度切换（可分两步，随时回滚）

| 步 | 动作 | 回滚 |
|---|---|---|
| 1 | 装 bundle（此时 Host 级 `pandabase_mybmo` 仍在，1-7 暂时还能看到写工具，属过渡态） | `remove_bundle` |
| 2 | 验证成员8 能看到 `mybmo_exec` 且团长看不到 → 把 profile 里 `mcp-pandabase-mybmo` 那行注释掉 | 取消注释即可 |

**不要跳过第 2 步**，否则隔离只是"多了一个入口"，不是"只剩一个入口"。

### 5.4 待实测

- [x] **preset 作用域内的 `dsh-mcp-client`（stdio + npx mcp-remote + oauth）能连上** —— 已用等效组合实测：`Proxy established successfully between local STDIO and remote StreamableHTTPClientTransport`，`tools/list` 返回正常。
- [ ] oauth 令牌缓存在 preset 作用域下是否与 Host 级共用（若共用，`--static-oauth-client-info` 的登录态可复用；若隔离，成员8 需要单独走一次授权）。
- [ ] 两个 `dsh-mcp-client` 同时存在但 `serverName` 不同（`pandabase_mybmo` vs `mybmo_exec`）时，工具名是否会撞（预期不会，前缀不同）。

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
| 5 | 团长会话里搜百度工具 | **看不到** `mcp__mybmo_exec__*` | ⏳ 需新会话；**注意**：`Host` 级 `pandabase_mybmo` 此时仍挂着，所以团长暂时**看得到**它——过渡态，见 5.3 步 2 |
| 6 | 成员8 会话里搜百度工具 | **看得到** `mcp__mybmo_exec__*`，`auth_status` 可调 | ⏳ 需新会话（preset 作用内的 MCP 首连未验） |
| 7 | 成员8 跑 `validate_payloads.py` | 与本地一致（0/1/2 退出码） | ⏳ 需新会话 |
| 8 | 关闭 profile 里 Host 级 `mcp-pandabase-mybmo` | 团长仍看不到，成员8 仍看得到 | ⬜ 等 6 通过再做 |

**已有会话和它们的子 agent 会保留启动时的插件版本** —— 装完必须新开会话验证，旧会话不会热更新。

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
| P3 | 脚本补齐：`new_run` / `manifest` / `record_call` / `snapshot_diff` / `assets_pack` / `backfill_image_ids` | P1 | ✅ 已完成，全部实测（含超限/同 MD5/rejected 守卫/快照告警） |
| P4 | 首个灰度单：1 计划 / 1 词包 / 1 图、`pause=true` | P2+P3 | `addCampaignShopping` 实测结论（唯一未知） |
| P5 | 批量与归档（`archive.py` 可选） | P4 成功 | 可重复生产 |

**P4 之前不要批量**：`addCampaignShopping` 至今 `verified: null`，这是整个方案唯一的真实未知。

---

## 九、已知风险

| 风险 | 影响 | 处置 |
|---|---|---|
| `addCampaignShopping` 从未实测 | 建计划可能直接失败或字段被静默忽略 | P4 灰度单验证，`pause=true`，先 1 条 |
| preset 内挂 MCP 未实测 | 隔离方案可能落不了地 | P2 验证，失败则退回"Host 级 + 协议约束 + `calls.jsonl` 对账" |
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
    ├── _runbook.py                # 公共层：ECWF_HOME、规范 runId、resolve_run_dir
    ├── new_run.py                 # 建 run（8 目录 + .gitignore + manifest）
    ├── manifest.py                # 状态机 show/set/next/object
    ├── record_call.py             # 追加 calls.jsonl
    ├── snapshot_diff.py           # before/after 比对 + 3 项专项告警
    ├── assets_pack.py             # 成员4：base64/命名/MD5/体积/映射表
    ├── backfill_image_ids.py      # ⑧-2 回填 imageId（默认落 .v2.json）
    └── validate_payloads.py       # §5.6 预检（schema + 编造检测 + 业务规则）
```
