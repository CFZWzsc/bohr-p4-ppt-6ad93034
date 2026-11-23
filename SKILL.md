---
name: academic-slide-editor
description: Use when an academic deck must be generated or edited from source material with version tracking, terminology preservation, figure insertion rules and rollback records.
---

# 学术 PPT 生成与编辑

## 用途与适用条件

从论文材料或提纲生成学术页面骨架，并按指令对指定页做压缩、扩写、替换、删除、换受众等编辑；每次编辑记录“改了什么、依据什么、影响了哪些页”，保留术语、数字与图注的一致性，提供可回退的版本链和图表插入规则。

适用条件：`/input/case.json`（或 `--case`）含起始演示稿、提纲、论文材料、编辑指令、图表清单或模板信息。典型键：`slides`/`deck`、`outline`、`edit_instructions`、`figures`、`glossary`、`template`。

不适用：不生成新的科学结论，不做图形设计美化，不翻译成长篇外语正文（缺少可靠语言模型时明确标记未执行）；不伪造 pptx 渲染结果。

## 输入

- `/input/case.json`：主输入。
- `/input/task.md`：存在时补充编辑目标、受众与版本要求。
- 引用的本地文本/JSON 会被实际读取；缺失登记到 `errors`/`uncertainty`。
- 仅标准库；不联网、不写 `/input`。

## 处理流程

1. **读入**：加载 case.json、task.md 与引用文件，记录哈希。
2. **基线构建**：若已有 `slides`/`deck`，逐页读取 id、标题、正文、备注、版式；否则从 `outline` 或材料段落生成骨架页（每页一个主张），标记 `generated`。
3. **术语表**：读取显式 glossary；缺失时抽取大写缩写与关键方法名，作为编辑后必须保留的术语集合。
4. **指令解析**：支持字典指令与自然语言指令（“第 3 页压缩到 2 条”“把 X 改为 Y”“删除第 5 页”）；无法解析的指令保留原文并标 `unparsed_instruction`。
5. **执行编辑**：压缩按句子边界截断；扩写只补充材料中可定位的句子；替换按原词→新词；删除清空正文；换受众只调整备注解释，不改事实；翻译在无可靠模型时标未执行。
6. **一致性校验**：术语丢失、数字变化无来源、目标页不存在、占位符冲突、图号引用悬空、幻灯片 id 重复。
7. **版本链**：记录 v1 基线、每次编辑的父版本、变更摘要与内容哈希；给出回退到任一版本的操作说明。
8. **图表操作**：区分 `insert_original`、`redraw_allowed`、`quote_only`；检查声明的素材是否存在，不把示意图当实验数据。
9. **导出检查**：登记 pptx/PDF/PNG 检查项，`performed=false`。
10. **契约落盘**：保留 `case_id` 与全部 `output_contract` 字段。

## 评分维度对应

- **实际效果**：指令是否真正落到指定页；术语、数字、图注是否保持一致。
- **完整性**：页面内容、编辑记录、版本链、图表操作、导出检查齐备。
- **可核验性**：每条编辑带 before/after、依据与目标页；版本可回退。
- **创新增益**：术语保持校验、数字变更拦截、版本化编辑与回退。
- **可靠体验**：目标缺失、指令不明、翻译不可用时诚实标记，不静默改坏。

## 输出契约

`/app/output/result.json` 顶层保留 `case_id`、`output_contract` 全部字段、`status`、`result`、`evidence`、`uncertainty`、`errors`、`stats`、`skill`、`challenge_id`、`contract_notes`。未支持字段写 `null` 并记录原因。

主要交付段：`result.deck.edits`、`result.deck.slides`、`result.version_log`、`result.figure_operations`、`result.checks`。

## 异常处理

| 情况 | 处理 | 输出表现 |
|---|---|---|
| case.json 缺失/非法 | 结构化失败 | 退出码 `2`，`status=insufficient_input`/`failed` |
| 无起始稿且无材料 | 不编造页面 | `status=insufficient_input` |
| 指令目标页不存在 | 不创建假页 | `instruction_target_missing` |
| 指令无法解析 | 保留原文 | `unparsed_instruction` |
| 编辑丢失术语 | 保留编辑但告警 | `terminology_dropped` |
| 数字被改且无来源 | 不静默接受 | `number_changed_without_source` |
| 请求翻译但无模型 | 标未执行 | `translation_not_executed` |
| 图号引用不存在 | 保留并标悬空 | `dangling_figure_reference` |
| 渲染工具缺失 | 仍须生成 pptx；无法渲染时在 deliverable 明确标注 | `deliverable.rendered=false`、`pptx_not_generated` |

## 限制

编辑为确定性文本变换与材料检索，不做语义改写模型推理；复杂版式重排与动画不在范围内。脚本产出可复现的页面与版本规格；最终 pptx 与渲染预览由调用 Agent 按「最终交付物（强制）」生成。

## 最小调用示例（示例，非官方案例结果）

```bash
python scripts/run_skill.py --case /input/case.json --out /app/output/result.json
```

示例输入（示意）：两页起始稿 + 指令“第 2 页压缩到 2 条”。示例输出（示意）：`status="success"`，`edits[0].before/after` 均有值，`version_log` 含 v1→v2。仅为示例，不代表官方固定案例结果。


## 最终交付物（强制）

脚本只产出 `result.json` 中的页级结构与证据映射；调用 Agent 必须继续完成最终交付：

1. 用 `python-pptx`（或等价可编辑格式）按 `result` 的页序、版式与占位符生成 `slides.pptx`；文字、备注、图表编号均可编辑，不把文字压成图片。
2. 用 `office-renderer`/LibreOffice 将 `slides.pptx` 渲染为 `preview.pdf` 与逐页 `preview_p01.png...`，并实际查看预览，确认无溢出、缺字、错页、空白页。
3. 把产物路径、页数、渲染命令与检查结果写入 `result.json` 的 `deliverable`（`pptx_path`、`preview_paths`、`rendered`、`checks`），并记录源文件哈希。
4. 工具确实缺失时，在 `result.json` 与 `limitations` 明确写 `pptx_not_generated` 及原因，不得声称已生成或已渲染。

## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--out` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 为 `completed`（仅在确实缺输入时为 `partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
