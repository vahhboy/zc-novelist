---
name: novel-review-kit
description: "Scoring rubric, reader personas, consistency checklists and report templates for reviewing novels and web fiction: dual-track scoring (commercial appeal + literary craft), simulated multi-reader personas with genre-adaptive weights, and structured audit report formats. Use when performing novel chapter review, consistency audit, simulated reader scoring, or revision planning."
---

# 阅稿堂审校工具箱

本工具箱为小说审校提供统一的度量基准与方法清单，确保**同一部作品在不同时间、不同成员手中得到可比较的结论**。

## 使用原则

1. **先锁量表版本**：任何评审开始前选定本轮使用的量表版本（默认 v1.0），并在报告中标注
2. **同一作品的纵向评审必须用同一版量表**，更换版本需说明并重算关键节点，否则分数不可比
3. **所有分数必须有文本证据**：没有引用支撑的分数不予采纳
4. 打分之外要说清楚"好在哪里"——不知道优点在哪，作者改稿时会把优点一起改没

## 资源索引

| 文件 | 用途 | 主要使用者 |
|------|------|-----------|
| `references/scoring-rubric.md` | 双轨评分量表 v1.0：10 个维度、分数锚点、题材权重表、评级标准 | 模拟读者评审官、主理人 |
| `references/reader-personas.md` | 5 类固定读者画像、口味偏好、维度权重与题材自适应规则 | 模拟读者评审官 |
| `references/consistency-checklist.md` | 大纲 / 设定 / 剧情 / 时间与逻辑四张一致性检查清单 | 见下方「清单归属」 |
| `references/prose-rhythm-checklist.md` | **清单 E：文字与节奏**（E1 开篇钩子 – E6 取证定级，独立文件） | 文字与节奏分析师 |
| `templates/review-report.md` | 审校最终报告模板（六节式：总判 / 评分 / 台账 / 专项 / 修改方案 / 分歧） | 主理人 |
| `templates/timeline-ledger.md` | **跨章时间台账**标准格式：累计时间轴、状态量快照、冲突与待拍板事项 | 时序与逻辑稽查、主理人 |
| `templates/handoff-brief.md` | **阶段间传递简报**格式：结论 + 证据三件套 + 待执行位置，含传递前自检清单 | 全体成员、主理人 |

### 清单归属（`references/consistency-checklist.md` 内）

| 清单 | 标题 | 归属席位 |
|---|---|---|
| 清单 A | 大纲对照（A1–A6） | `novel-outline-checker` 许合辙 |
| 清单 B | 设定考据（B1–B6） | `novel-canon-keeper` 沈无漏 |
| 清单 C | 剧情连贯（C1–C6） | `novel-plot-tracker` 陆牵丝 |
| 清单 D | 时间与逻辑（D1–D7） | `novel-logic-auditor` 钟不紊 |
| 清单 E | 文字与节奏（E1–E6，见 `references/prose-rhythm-checklist.md`） | `novel-prose-analyst` 薛斟句 |

> 各席**开工前必读自己那张清单**（详见各自成员定义文件的「前置：加载你的专用判据」）。若主理人派单时忘了指名，主动去读这一份。

## 标准作业顺序

1. 确定判定范围与目标（单章 / 连续若干章 / 全稿 / 仅大纲）
2. 锁定素材：现行大纲、世界观/设定文档、待检正文（**标注作废的版本不得作为判据**）
   - **若工作区已有按 `templates/timeline-ledger.md` 落盘的跨章时间台账**（默认 `07-时间轴台账.md`，与正文同目录），**务必一并交给 `novel-logic-auditor` 承接更新**，不要让它从零重建
3. 依次执行：大纲对照 → 设定考据 → 剧情连贯 → **时间与逻辑** → **文字与节奏** → 模拟读者打分 → 修订建议
   - 时间与逻辑（清单 D）为**前后冲突的最后一道闸**：时间账必须出表，缺表即视为未查
   - 文字与节奏（清单 E）的「节奏一览表」是模拟读者 C1/C2/C3 维度打分的取证依据
   - 若本轮只做"写后自检"，可只跑时间与逻辑一项，跳过打分与修订
4. **跨阶段传递一律先按 `templates/handoff-brief.md` 压缩**，再转交下游——证据原文全留，过程描述全砍
5. 本轮产出的时间轴增量由主理人追加进跨章台账落盘，供下一轮续用
6. 按 `templates/review-report.md` 汇编最终报告
7. 每条建议必须给出位置 + 证据 + 可执行改法

## 一致性判定通则

- 评分争议的裁定以本量表的分数锚点为准，不以个人感觉为准
- 冲突解决优先级：作者明确声明的设定 > 现行大纲 > 现行世界观文档 > 正文最早出现处 > 正文最常见写法
- 作废版本的唯一用途：回溯作者当初的设计意图，理解"当初为什么这么定"
