---
name: expert-watchdog
description: >-
  专家 / 专家团进度看门狗。监控单个专家或专家团（Team 型多角色）每个角色节点是否长时间没有反馈或卡住，
  判定"沉默"与"空转"两类停滞，并按 L0-L4 分级策略重新调用该专家或专家团（带 checkpoint 重启、拆分降级、
  换角色接管、跳过上报）。Trigger on: 专家卡住、专家没反馈、专家团没进展、长时间没动静、卡在某个环节、
  重新调用专家、专家超时、watchdog、stall detection、expert stuck。
description_zh: 监控专家/专家团各节点进度，超时自动判定卡住并按分级策略重新调用或降级接管
description_en: Watchdog for expert and expert-team runs - detect stalled role nodes and re-dispatch them with graded recovery
allowed-tools: Bash, Read, Write, Glob, Agent, TaskOutput, TaskStop, SendMessage, TaskCreate, TaskGet, TaskUpdate, AskUserQuestion
argument-hint: <要监控的专家/专家团任务，或 "检查 xxx 卡没卡">
agent_created: false
---

# Expert Watchdog · 专家进度看门狗

专家和专家团在长任务里会卡住：某个角色节点半天没动静、反复输出同样的内容、或者干脆停在等待外部资源上。
这个 skill 给"卡住"一个**可判定的定义**和一条**可执行的恢复路径**，让停滞的节点被自动发现、被带着上下文重新调用，
而不是整条流水线一起干等。

## 何时使用

- 启动一个**专家**或**专家团**（Team 型多角色）执行多阶段任务时 —— 主动挂上看门狗
- 用户说："专家卡住了 / 半天没反馈 / 这一步怎么没动静 / 重新调用一下专家 / 别等了换个办法"
- 派发了后台子代理（`Agent` + `run_in_background`）去跑专家角色，需要判断它是否还活着
- 专家团的某个成员迟迟不交付，需要决定是等、是催、是重启、还是换人

## 核心概念

| 概念 | 含义 |
|---|---|
| **run** | 一次专家/专家团任务的完整执行，一个 run 对应一个状态文件 |
| **node（节点）** | 一个角色 / 一个阶段。专家团里每个成员是一个节点；单专家则每个阶段是一个节点 |
| **beat（心跳）** | 节点每产生一次可见进展就打一次点，记录**绝对时间戳 + 进展描述 + 产物路径** |
| **stall budget（停滞预算）** | 该节点允许沉默的最长时间，按节点类型取值（见下表） |
| **checkpoint** | 节点卡住时已经完成的部分（产物 + 已完成/未完成清单），是重新调用的入场上下文 |

因为心跳存的是**绝对时间戳**，任何时刻回来看都能算出沉默了多久 —— **不需要常驻进程**也能判定卡住。

## 停滞的两种信号

1. **SILENCE（沉默）**：最后一次心跳距今超过停滞预算 —— 彻底没输出了。
2. **SPIN（空转）**：心跳还在打，但进展描述和产物列表一直没变 —— 在原地打转（典型：同一条命令反复失败重试）。

`check` 同时判这两个，并额外标出 `at-risk`（沉默已达预算 70%，快到了）。

## 停滞预算默认值

| 节点类型 kind | 预算 | 典型场景 |
|---|---|---|
| `orchestrate` | 120s | 团队编排、派活、写计划 |
| `research` | 150s | 联网检索、查资料 |
| `review` | 240s | 长文审阅、对照检查 |
| `write` | 300s | 长文生成、写作 |
| `code` | 240s | 改代码、跑命令 |
| `summarize` | 180s | 汇总、出报告 |
| `default` | 180s | 其他 |

可用 `--stall-seconds` 覆盖。经验：宁可略宽松，也不要在正常长任务上误判。

## 恢复阶梯 L0-L4

判定卡住后**不是直接重跑**，而是按已消耗的重试次数逐级升级：

| 级别 | 名称 | 动作 | 何时用 |
|---|---|---|---|
| **L0** | nudge 轻推 | 向在跑的节点发一条追问（"报告当前进度"），再等一个宽限期 | 第一次怀疑，可能只是慢 |
| **L1** | restart-with-checkpoint | 停掉该节点，**带着 checkpoint 重新调用同一个专家角色** | 轻推无反应，从最后的好状态重启 |
| **L2** | degrade-and-split | 缩小契约重派：拆成 2-3 个子任务、降 `max_turns`、换更快模型、去掉非必要工具 | 重启又卡 —— 任务本身太大或太含糊 |
| **L3** | substitute-role | 交给团里**另一个角色**承担，或 team-lead 直接执行，产物标注降级 | 同一角色反复卡 —— 怀疑角色/任务不匹配 |
| **L4** | skip-and-escalate | 标记该节点跳过，流水线继续，缺口交给用户决定 | 预算耗尽，不为单点烧掉整条 run |

每个节点默认 **3 次**重试预算（`--max-attempts`），用完直接 L4 上报。

## 工作流

### 1. 建 run —— 派活之前就建

```bash
python scripts/watchdog.py --state-dir <dir> init \
  --run-id <run-id> --task "<这次要交付什么>" \
  --expert <专家名> --team <角色A> --team <角色B>
```

`<run-id>` 用短标识，如 `review-ch1-5`。`--state-dir` 默认 `./.workbuddy/watchdog`（或环境变量 `WATCHDOG_STATE_DIR`）。

### 2. 注册节点 —— 每个角色/阶段一个

```bash
python scripts/watchdog.py --state-dir <dir> node \
  --run-id <run-id> --node-id n2-canon --role 沈无漏 --kind review \
  --task-id <后台 agent 的 task_id> --note "世界观/设定一致性检查"
```

同一 `--node-id` 再注册一次 = 记为一次**重派**（attempts +1，时钟重置），正好用于恢复流程。

### 3. 打心跳 —— 有可见进展就打

```bash
python scripts/watchdog.py --state-dir <dir> beat \
  --run-id <run-id> --node-id n2-canon \
  --note "已比对 3 处战力描述" --artifact out/canon-issues.md
```

**打点纪律**（决定看门狗准不准，务必遵守）：
- 每次 `TaskOutput` 拿回新输出 → beat 一次
- 每写出一个产物文件 → beat 一次并 `--artifact`
- 描述要**每次不同**（写清楚新进展），否则会被判成 SPIN
- 节点进入等待外部资源（网络、锁、人工确认）时 → beat 并注明"等待 X"，让时钟有据可依

### 4. 检查 —— 轮询时顺手跑

```bash
python scripts/watchdog.py --state-dir <dir> check --run-id <run-id>
```

退出码 **1 = 有节点卡住**，stdout 给出每个节点的判定与建议级别，stderr 直接给一行 action。
把它挂在每次轮询后台 agent 的动作后面，成本几乎为零。

需要独立时间基准（例如主 agent 也在等）时，可起一个后台轮询：

```bash
python scripts/watchdog.py --state-dir <dir> daemon --run-id <run-id> --poll 30 --max-runtime 1800
```

它只负责往 `<run-id>/alerts.jsonl` 写告警并打印，**不代替主 agent 做恢复决策**。

### 5. 恢复 —— 按建议级别执行，并登记

```bash
python scripts/watchdog.py --state-dir <dir> attempt \
  --run-id <run-id> --node-id n2-canon --strategy "L1 restart" --reset
```

执行顺序（详见 `references/recovery-playbook.md`）：

1. `check` 拿到建议级别
2. 按级别做动作：L0 用 `SendMessage` 追问 / 再取一次 `TaskOutput`；L1-L3 先 `TaskStop` 停掉旧任务，再重新 `Agent` 派发
3. 立即 `attempt` 登记（带 `--reset` 重置时钟）
4. **重新派发必须带上 checkpoint**（已完成什么、卡在哪、已有哪些产物、下一步只做什么）—— 见手册里的重派指令模板
5. 重派后回到步骤 3 继续打心跳；再卡则升一级

### 6. 收尾

```bash
python scripts/watchdog.py --state-dir <dir> resolve --run-id <run-id> --node-id <id> --outcome done|failed|skipped --note "..."
python scripts/watchdog.py --state-dir <dir> report --run-id <run-id> --out <path>.md
```

`report` 会列出每个节点的沉默时长、重试次数、以及**交付缺口**（被 skipped/failed 的节点），
这份报告要作为最终交付的一部分交给用户 —— 降级交付必须明说，不能含糊过去。

## 与执行工具的映射

| 场景 | 用的工具 | 看门狗动作 |
|---|---|---|
| 派发角色为后台子代理 | `Agent(run_in_background:true, name:"role") → task_id` | `node --task-id <task_id>` |
| 轮询它 | `TaskOutput(task_id, block:false)` | 有新输出就 `beat`；每次轮询后 `check` |
| 轻推（L0） | `SendMessage({to:"role", ...})` 或再取一次 `TaskOutput` | `attempt --strategy "L0 nudge"` |
| 停掉卡死的 | `TaskStop(task_id)` | — |
| 重新调用（L1-L2） | `Agent` 带 checkpoint 重派 | `node` 重注册 + `attempt --reset` |
| 换角色（L3） | 派给另一个 `name` 的 agent | 新 node-id + `attempt --strategy "L3 substitute"` |
| 前序节点未产出 | — | 用 `--depends-on` 记录依赖，前序卡住就不要派后续，避免连环空转 |

## 常用参数

```
--state-dir DIR    状态目录，默认 ./.workbuddy/watchdog 或 $WATCHDOG_STATE_DIR
init   --run-id --task [--expert] [--team] [--max-attempts 3] [--grace-seconds 60]
node   --run-id --node-id --role --kind [--task-id] [--stall-seconds] [--max-attempts] [--depends-on]
beat   --run-id --node-id [--note] [--artifact] [--progress]
check  --run-id [--all]           # exit 1 表示有卡住
attempt--run-id --node-id --strategy [--reset] [--note]
resolve--run-id --node-id --outcome done|failed|skipped [--note] [--artifact]
report --run-id [--out FILE]
daemon --run-id [--poll 30] [--max-runtime 1800]
ladder                            # 打印恢复阶梯与默认阈值
```

## 硬规则

1. **先注册再派活**。没注册的节点无法监控，事后补的心跳不可信。
2. **不许无限重试**。每个节点预算默认 3 次，用完走 L4 上报用户。
3. **重派必带 checkpoint**。不带上下文的重跑，大概率在同一个地方再卡一次。
4. **降级交付要透明**。跳过或换人完成的节点，必须在最终报告和用户回复里写清楚。
5. **不要为单点烧掉整条 run**。一个节点反复卡 3 次以上，就让它带着缺口往下走，把决定权交回用户。
6. **怀疑误判时先 nudge**。L0 成本最低，能区分"慢"和"死"。

## 参考

- `references/recovery-playbook.md` —— 卡住类型诊断表 + 各级别重派指令模板
- `references/integration.md` —— 单专家 / 专家团 / 串并行三种接法的完整示例

脚本：`scripts/watchdog.py`（纯标准库，跨平台，Python 3.8+）。
Windows 用 `python`，macOS/Linux 用 `python3`。
