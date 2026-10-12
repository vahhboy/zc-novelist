---
name: novel-writer
description: >-
  写小说 / 长篇连载的工程化创作技能。以大纲（卷-章-节拍）与 story-time 时间线为硬约束，
  写前生成约束包、写后强制一致性自检（时间倒流、死人出场、知情越界、伏笔逾期/未埋先收、
  节拍未落地、视角漂移、名词不统一），并支持按修改建议做影响面分析与剧情调整。
  Trigger on: 写小说、小说创作、写下一章、章节写作、大纲一致性、时间线冲突、改剧情、
  修订小说、按建议改稿、伏笔台账、人物设定、novel writing、write chapter、story outline。
description_zh: 严格遵守大纲与时间线的小说创作技能，支持按修改建议做影响面分析与剧情调整
description_en: Outline- and timeline-locked fiction writing with consistency checks and revision impact analysis
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, AskUserQuestion
argument-hint: <书名 / 要写的章节 / 修改建议>
---

# Novel Writer · 大纲锁定的小说创作

写长篇最容易出的问题不是"写不出来"，是**写到第 30 章发现第 5 章埋的线断了、时间账对不上、
人物属性前后打架**。这个 skill 把大纲、时间线、人物状态、伏笔做成**可校验的台账**，
让"遵循大纲"从一句提醒变成一条能跑的检查。

## 核心原则

1. **大纲是契约，不是参考**。章内节拍逐条落地；要改先改 `outline.md`，再改正文。
2. **时间线用 story-time 序号（seq）做主序**。日历写法（"天启三年四月初七"）只作展示，
   真正参与校验的是全局递增的 `seq` —— 这样"三天/三个月"这类相对时标才不会算错。
3. **人物状态由事件推进，不是靠记忆**。谁死了、谁在哪、谁知道什么，都由时间线事件推导。
4. **偏离要记账，不禁止**。写崩了不丢人，丢了不记才致命：`mark-beat --status deviated`
   并标注有害/中性/有益。
5. **改稿先算影响面**。任何剧情调整，先回答"哪些章节、哪些台账要跟着改"，再动笔。
6. **冲突时的优先级**（与审校团共用同一套，写审不打架）：
   作者当场声明 > 现行大纲 > 现行世界观 > 正文最早出现处 > 正文最常见写法。

## 工程结构（`init` 生成）

```
<root>/
  outline.md          卷 / 章 / 节拍（人写，判据第二优先级）
  worldbuilding.md    世界规则、地理势力、历法与耗时、名词表
  style.md            视角、时态、语体、节奏、禁忌
  chapters/ch001.md   正文
  notes/              约束包、修订方案、一致性报告
  .novel/             timeline / characters / foreshadowing / chapter-index / notes / glossary
```

前三个是**人读的判据**，`.novel/` 是**机器校验用的台账**，两者必须同步。

## 写作流程（每章必走，不许跳步）

### 第 1 步 · 写前：读约束包

```bash
python scripts/novel.py --root <root> context --chapter 5 --out notes/ctx-ch5.md
```

它会输出这一章**必须遵守的一切**：本章大纲节拍、上一章结尾原文、截至本章前的 story-time 时间线、
人物当前状态（含生死、所在地、已知信息）、开放伏笔（含逾期警告）、待处理的修改建议、六条硬约束。

**没读过约束包不许动笔。**

### 第 2 步 · 写：只落地本章节拍

- 逐条覆盖本章节拍，不提前兑现后续章节的高潮/揭晓
- 视角人称全章统一；限知视角下不写视角人物看不到的事
- 角色不知道的事，不能"顺手"让他知道

### 第 3 步 · 写后：登记 + 自检

```bash
python scripts/novel.py --root <root> chapter --index 5 --file chapters/ch005.md \
  --seq-start 21 --seq-end 26 --characters c_lin,c_shen --planted f003 --resolved f001
python scripts/novel.py --root <root> mark-beat --chapter 5 --beat ch005-b1 --status covered
python scripts/novel.py --root <root> check --chapter 5
```

`check` 退出码：**2 = 有 P0（阻断）｜1 = 只有 P1/P2｜0 = 干净**。

### 第 4 步 · 判定

| 级别 | 含义 | 处置 |
|---|---|---|
| **P0** | 时间倒流、死人出场、知情越界、未埋先收、正文缺失 | **必须修完**才能写下一章 |
| **P1** | 伏笔逾期、节拍未落地、视角/时态漂移、同刻两地 | 本章内修掉，或显式记录"已知并接受" |
| **P2** | 名词不统一、字数大幅偏离 | 记录处理决定即可 |

**P0 没清零就往下写 = 债务滚雪球**，这一步不许通融。

## 修订流程（按修改建议调整剧情）

用户给出修改建议后：

```bash
python scripts/novel.py --root <root> notes add --chapter 8 --text "把沈青之死改为重伤退场" --source user
python scripts/novel.py --root <root> revise plan --chapter 8 --change "沈青不死，改为重伤退场" --out notes/revise-ch8.md
```

`revise plan` 输出影响面：受影响的既有章节、时间线事件、伏笔、人物、大纲里需要同步的节拍，
外加**必答四问**和**两套方案**（甲·最小改动 / 乙·彻底重排）。

然后：

1. **先给用户两套方案选**（`AskUserQuestion`），不擅自改剧情走向
2. 改完正文 → 同步台账：时间线事件、`character set` / `knows`、伏笔 `resolve`、大纲 `outline.md`
3. 若改动偏离大纲 → `mark-beat --status deviated --nature harmful|neutral|beneficial`
4. 跑 `check --all`，P0 清零才算改完
5. 大纲偏离是有益的 → 回头把它写进 `outline.md`，让它变成新的契约

详细动作见 `references/revision-playbook.md`。

## 命令速查

```
init       --title --genre --pov --tense --target-words       # 建工程
sync                                                          # 解析 outline.md
context    --chapter N [--out FILE]                           # 写前约束包
check      [--chapter N]                                      # 一致性校验
chapter    --index N --file --seq-start --seq-end --characters --planted --resolved [--flashback]
mark-beat  --chapter N --beat ID --status covered|deviated [--nature harmful|neutral|beneficial]
timeline   add  --chapter N --title [--time] [--seq] [--participants] [--location] [--effect 角色.字段=值]
timeline   show [--chapter N] [--last N]
character  add --id --name --status --location ｜ set --id --field --value ｜ knows --id --events
foreshadow add --chapter N --content --due-chapter M ｜ resolve --id --chapter N ｜ list
notes      add --chapter N --text ｜ resolve --id ｜ list
revise     plan --chapter N --change "..."
glossary   add --main 主称呼 --aliases 别名1,别名2
report     [--out FILE]                                       # 一致性报告
```

Windows 用 `python`，macOS/Linux 用 `python3`。`--root` 默认当前目录。

## 硬规则

1. 写前必读 `context`，写后必跑 `check`，P0 未清零不写下一章。
2. 大纲改动与正文改动必须成对出现 —— 只改正文不改大纲 = 埋雷。
3. 时间线上的任何状态变化都用事件 + `--effect` 表达，不要直接改人物档案（那是"初始状态"）。
4. 伏笔必须成对：埋了就要有 `due_chapter`，收了就要 `resolve`。
5. 涉及剧情走向的调整，**先出方案让用户选**，再动笔。
6. 作废的大纲/设定版本必须标注，不得作为判据。

## 与相关能力的衔接

- **审校**：写完若干章可交给 `novel-review` 专家团（阅稿堂）—— 本 skill 的台账与清单
  刻意对齐了它的清单 A（大纲对照）和清单 D（时间逻辑），它的结论可以直接落到 `notes` 里变成修订任务。
- **长流程监控**：一次写多章 / 跑整卷时，可用 `expert-watchdog` 给每个章节节点打心跳，
  卡住时按 L0-L4 恢复，避免整条流水线干等。

## 参考

- `references/writing-checklist.md` —— 写前 / 写中 / 写后三张清单
- `references/revision-playbook.md` —— 修改建议的分类、影响面、双方案与连锁同步
- `templates/` —— `outline.md` / `worldbuilding.md` / `style.md` / 章节模板
