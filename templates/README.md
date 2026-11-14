# templates/

| 文件 | 用途 |
|---|---|
| `campaign-baseline.json` | **⑥ 出 payload 的骨架**（真实读回结构）：audience 全键 + material 全要素；只替换取值 |
| `manifest.json` | `scripts/new_run.py` 生成 run 的 manifest 模板 |
| `01-required-fields.json` | 成员1 电商必填项核实表 |
| `03-copy.json` / `03-image-prompts.md` | 成员3 文案 / 出图提示词 |
| `04-image-map.json` | 成员4 素材映射表（imageId 留 null） |
| `05-plan-draft.json` | 成员5 计划结构草案 |
| `06-addKeywordPackages.json` | 成员2/6 词包请求体结构 |

## 关于 addCampaignShopping / uploadImage 的请求体
**不再提供静态 JSON 模板**（占位符会让模板本身过不了 schema，容易被误当成品）。
请用 `scripts/scaffold_plan.py` 生成 ⑥ 初稿，或按 `campaign-baseline.json` 骨架替换取值：
```
python3 scripts/scaffold_plan.py --run-dir <run> --name … --budget … --bid … --user-portrait …
```
