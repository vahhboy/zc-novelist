# 集成指南 · 单专家 / 专家团 / 串并行

下面三种接法覆盖绝大多数场景。`WD` 指看门狗命令前缀：

```bash
# Windows
WD='python C:/Users/<你>/.workbuddy/skills/expert-watchdog/scripts/watchdog.py'
# macOS / Linux
WD='python3 ~/.workbuddy/skills/expert-watchdog/scripts/watchdog.py'
```

状态目录统一放在工作区里，便于事后查：

```bash
export WATCHDOG_STATE_DIR="$PWD/.workbuddy/watchdog"
```

---

## 接法一：单专家，多阶段串行

适合：一个专家按阶段推进（例如写作专家：大纲 → 初稿 → 修订 → 定稿）。

```bash
$WD init --run-id write-ch3 --task "写第 3 章并自检" --expert writing-expert
$WD node --run-id write-ch3 --node-id s1-outline --role 写作专家 --kind write
# ... 派发 s1，拿到输出后：
$WD beat --run-id write-ch3 --node-id s1-outline --note "大纲完成" --artifact ch3/outline.md
$WD resolve --run-id write-ch3 --node-id s1-outline --outcome done

$WD node --run-id write-ch3 --node-id s2-draft --role 写作专家 --kind write
# ... 同上
```

**每完成一个阶段就 `resolve`**，这样 `check` 只盯着真正在跑的节点，不会误报。

---

## 接法二：专家团，成员并行（推荐接法）

适合：Team 型专家团（如六人审校团），成员可并行，team-lead 汇总。

```bash
$WD init --run-id review-ch1-5 --task "审校第 1-5 章" --expert novel-review \
  --team 温定夺 --team 许合辙 --team 沈无漏 --team 陆牵丝 --team 何知味 --team 方可行

# 编排节点（team-lead）
$WD node --run-id review-ch1-5 --node-id n0-lead --role 温定夺 --kind orchestrate

# 各成员节点，声明依赖
$WD node --run-id review-ch1-5 --node-id n1-outline --role 许合辙 --kind review --depends-on n0-lead
$WD node --run-id review-ch1-5 --node-id n2-canon  --role 沈无漏 --kind review --depends-on n0-lead
$WD node --run-id review-ch1-5 --node-id n3-plot   --role 陆牵丝 --kind review --depends-on n0-lead
$WD node --run-id review-ch1-5 --node-id n4-reader --role 何知味 --kind review --depends-on n0-lead
```

然后并行派发（`Agent` + `run_in_background: true`，每个成员一个 `name`），
轮询循环长这样：

```
每轮：
  1. 对每个在跑成员：TaskOutput(block:false)
  2. 有新输出 → beat --note "<进展>" [--artifact ...]
  3. $WD check --run-id review-ch1-5
  4. 退出码 1 → 按 suggested.level 执行恢复（见 recovery-playbook.md）
  5. 成员完成 → resolve --outcome done
```

**关键**：`--depends-on` 记录的前序节点如果卡住，**不要派发后续节点**，
否则下游拿不到输入会集体空转。等前序 resolve 或走 L4 跳过后再派。

---

## 接法三：只做兜底检查（最轻量）

不想全流程打点，只想防"卡死没人管"：在派发后台专家后，
每隔几轮轮询顺手跑一次 `check`，没注册节点也能用 `init + node` 快速补一个。

```bash
$WD init --run-id ad-hoc-1 --task "<任务>"
$WD node --run-id ad-hoc-1 --node-id only --role <专家名> --kind research
# 每次看到新输出：
$WD beat --run-id ad-hoc-1 --node-id only --note "<一句话进展>"
# 每次轮询：
$WD check --run-id ad-hoc-1 && echo "still alive"
```

---

## 长任务的守护模式

主 agent 自己也在等（例如等一个长检索），没有机会周期性调用 `check` 时，
起一个后台轮询进程提供独立时间基准：

```bash
$WD daemon --run-id review-ch1-5 --poll 30 --max-runtime 1800
```

- 只在**节点首次卡住**（同一 attempt 内）告警一次，不会刷屏
- 告警写入 `<state-dir>/<run-id>/alerts.jsonl` 并打印到 stdout
- 全部节点结束后自动退出；`--max-runtime` 到点也退出（默认 1800s）
- 它**不做恢复决策** —— 恢复仍然由主 agent 按 `check` 的建议执行

---

## 与 Task 工具的配合

看门狗是台账，不是替代任务管理。两者分工：

| 工具 | 管什么 |
|---|---|
| `TaskCreate` / `TaskUpdate` | 任务的分解与完成状态（给人看） |
| `watchdog` | 每个节点的**时间维度健康度**（给监控用） |

建议：`node-id` 与 Task 标题保持一致，方便对照。
