# zc-novelist · 配套 Skills

两个配套 skill 的可分发副本，用于**小说创作**与**长流程监控**。
原始安装位置：`%USERPROFILE%\.workbuddy\skills\`（用户级，全项目生效；Windows 下即 `C:\Users\<用户名>\.workbuddy\skills\`）。

| Skill | 解决什么 | 入口 |
|---|---|---|
| `novel-writer` | 写小说时严格遵守大纲与 story-time 时间线，支持按修改建议做影响面分析与剧情调整 | `scripts/novel.py` |
| `expert-watchdog` | 专家 / 专家团长时间没反馈或卡住时，判定停滞并分级重新调用 | `scripts/watchdog.py` |

---

## 一、安装

复制两个目录到 WorkBuddy 的用户级 skill 目录即可（重启会话或刷新后出现在 skill 列表）：

```bash
cp -r skills/novel-writer   ~/.workbuddy/skills/
cp -r skills/expert-watchdog ~/.workbuddy/skills/
```

Windows 下 `~/.workbuddy/skills/` 即 `C:\Users\<用户名>\.workbuddy\skills\`。

---

## 二、novel-writer · 大纲锁定的小说创作

### 建工程

```bash
python novel-writer/scripts/novel.py --root <小说目录> init \
  --title "书名" --genre 仙侠 --pov third-limited --tense past --target-words 3000
```

生成：`outline.md`（卷/章/节拍）、`worldbuilding.md`、`style.md`、`chapters/`、`.novel/`（机器台账）。

### 每章四步

```bash
# 1 写前：读约束包（本章节拍 / 上章结尾 / 时间线 / 人物状态 / 开放伏笔 / 硬约束）
python novel-writer/scripts/novel.py --root <dir> context --chapter 5

# 2 写完：登记（seq 区间、出场人物、埋/收伏笔）
python novel-writer/scripts/novel.py --root <dir> chapter --index 5 --file chapters/ch005.md \
  --seq-start 21 --seq-end 26 --characters c_lin --planted f003 --resolved f001

# 3 自检：P0 清零才能写下一章
python novel-writer/scripts/novel.py --root <dir> check --chapter 5
#   退出码 2 = 有 P0（阻断）｜1 = 只有 P1/P2｜0 = 干净

# 4 改稿：先算影响面，再动笔
python novel-writer/scripts/novel.py --root <dir> revise plan --chapter 8 --change "沈青不死，改为重伤退场"
```

### 校验项

| 级别 | 查什么 |
|---|---|
| P0 | 时间倒流、死人出场、知情越界、伏笔未埋先收、正文缺失 |
| P1 | 伏笔逾期、节拍未落地、视角/时态漂移、同一 seq 分身两地 |
| P2 | 名词不统一、字数大幅偏离 |

关键约定：story-time 用**全局递增 seq** 做主序（日历写法只作展示）；
人物状态由时间线事件 `--effect 角色.字段=值` 推进；偏离大纲不禁但必须
`mark-beat --status deviated --nature harmful|neutral|beneficial` 记账。

---

## 三、expert-watchdog · 专家进度看门狗

长流程（一次写多章、跑整卷、多角色会诊）时给每个节点打心跳，卡住自动判定并分级恢复。

```bash
python expert-watchdog/scripts/watchdog.py --state-dir <dir> init --run-id <id> --task "..." --team 角色A --team 角色B
python expert-watchdog/scripts/watchdog.py --state-dir <dir> node --run-id <id> --node-id n1 --role 许合辙 --kind review
python expert-watchdog/scripts/watchdog.py --state-dir <dir> beat --run-id <id> --node-id n1 --note "已比对 3 处"
python expert-watchdog/scripts/watchdog.py --state-dir <dir> check --run-id <id>     # exit 1 = 有节点卡住
```

恢复阶梯：L0 轻推 → L1 带 checkpoint 重启 → L2 拆分降级 → L3 换角色 → L4 跳过上报。
每节点默认 3 次重试预算。心跳存**绝对时间戳**，无需常驻进程即可判定沉默时长。

本仓库 `.workbuddy/watchdog/juedi-vol1-20261012/` 就是一次真实运行留下的状态。

---

## 四、三者怎么配合

```
novel-writer  写 → check（P0 清零）→ 交给 novel-review（阅稿堂）审校
                                        ↓ 意见落成 notes → revise plan → 改稿 → 再 check
expert-watchdog  在上述任一长流程上挂心跳，节点卡住时按 L0-L4 恢复
```

判定优先级（写作侧与审校侧共用，避免打架）：
**作者当场声明 > 现行大纲 > 现行世界观 > 正文最早出现处 > 正文最常见写法**。

---

## 五、环境要求

- Python 3.8+，**无第三方依赖**（纯标准库）
- Windows 用 `python`，macOS / Linux 用 `python3`
- 脚本可在任意目录运行，用 `--root` / `--state-dir` 指定目标路径
