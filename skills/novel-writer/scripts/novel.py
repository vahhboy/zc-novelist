#!/usr/bin/env python3
"""novel-writer engineering CLI - outline/timeline/canonical-state ledger for fiction writing.

Zero third-party dependencies. Python 3.8+. Cross-platform.

The point of this tool: make "follow the outline and the timeline" something that can be
CHECKED, not something the author has to remember. Structured ledgers (timeline,
characters, foreshadowing, chapter index) are the source of truth for consistency;
prose stays in Markdown where humans can read it.

Layout created by `init`:

    <root>/
      outline.md          volume / chapter / beat outline   (human written)
      worldbuilding.md    rules, geography, factions, power system
      style.md            POV, tense, voice, taboos, target length
      chapters/ch001.md   prose
      notes/              revision suggestions and decisions
      .novel/             machine state (json ledgers, cache, reports)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

VERSION = "1.0.0"
NOVEL_DIR = ".novel"

# Conflict resolution priority, shared with the novel-review expert team so that
# writing-side and review-side never disagree about which source wins.
PRECEDENCE = [
    "作者明确声明的设定",
    "现行大纲",
    "现行世界观文档",
    "正文最早出现处",
    "正文最常见写法",
]

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

DEFAULT_GLOSSARY_RULE = "同一实体在同一部作品里只用一个主称呼，别名仅在角色对话/特定视角下出现。"


# --------------------------------------------------------------------------- io


def novel_root(root: str) -> str:
    return os.path.join(root, NOVEL_DIR)


def path_of(root: str, name: str) -> str:
    return os.path.join(novel_root(root), name + ".json")


def load(root: str, name: str, default: Any) -> Any:
    p = path_of(root, name)
    if not os.path.exists(p):
        return default
    with open(p, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            sys.exit("error: %s is not valid JSON - fix it or delete it to reset." % p)


def save(root: str, name: str, data: Any) -> None:
    p = path_of(root, name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def require_project(root: str) -> Dict[str, Any]:
    if not os.path.exists(path_of(root, "project")):
        sys.exit("error: no novel project at '%s'. Run `init --root %s` first." % (root, root))
    return load(root, "project", {})


def split_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    parts = re.split(r"[,，;；]", value)
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- outline parsing


def parse_outline(root: str) -> Dict[str, Any]:
    """Parse outline.md into a cache used for beat-coverage checks.

    Recognised shapes:
        ## 卷一 · 标题
        ### 第1章 章节标题
        - [ ] 节拍文本 @b1
        - [x] 已完成节拍 @b2
    Also tolerates `## 第1章` and plain `- 节拍` lines.
    """
    md_path = os.path.join(root, "outline.md")
    if not os.path.exists(md_path):
        return {"volumes": [], "chapters": [], "warnings": ["outline.md not found"]}
    with open(md_path, "r", encoding="utf-8") as f:
        text = f.read()

    warnings: List[str] = []
    volumes: List[Dict[str, Any]] = []
    chapters: List[Dict[str, Any]] = []
    cur_volume = None
    cur_chapter = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## ") and not line.startswith("### "):
            title = line[3:].strip()
            cur_volume = {"title": title, "chapters": []}
            volumes.append(cur_volume)
            # tolerate "## 第1章" as a chapter heading
            m = re.match(r"第\s*(\d+)\s*章", title)
            if m:
                cur_chapter = {"index": int(m.group(1)), "title": title,
                               "volume": title, "beats": []}
                chapters.append(cur_chapter)
            else:
                cur_chapter = None
        elif line.startswith("### "):
            title = line[4:].strip()
            m = re.match(r"第\s*(\d+)\s*章", title)
            idx = int(m.group(1)) if m else (len(chapters) + 1)
            if not m:
                warnings.append("章节标题未标序号，按顺序推断为第 %d 章：%s" % (idx, title))
            cur_chapter = {"index": idx, "title": title,
                           "volume": cur_volume["title"] if cur_volume else "", "beats": []}
            chapters.append(cur_chapter)
            if cur_volume is not None:
                cur_volume["chapters"].append(idx)
        elif re.match(r"^\s*[-*]\s+", line) and cur_chapter is not None:
            body = re.sub(r"^\s*[-*]\s+", "", line)
            checked = False
            mbox = re.match(r"^\[([ xX])\]\s*(.*)$", body)
            if mbox:
                checked = mbox.group(1).lower() == "x"
                body = mbox.group(2)
            beat_id = None
            mid = re.search(r"@([A-Za-z0-9_\-]+)\s*$", body)
            if mid:
                beat_id = mid.group(1)
                body = body[: mid.start()].strip()
            if not beat_id:
                beat_id = "ch%03d-b%d" % (cur_chapter["index"], len(cur_chapter["beats"]) + 1)
            cur_chapter["beats"].append({
                "id": beat_id, "text": body.strip(), "checked": checked,
            })

    return {"volumes": volumes, "chapters": chapters, "warnings": warnings,
            "parsed_at": os.path.getmtime(md_path)}


# --------------------------------------------------------------------------- derived state


def sorted_events(root: str) -> List[Dict[str, Any]]:
    data = load(root, "timeline", {"events": []})
    events = list(data.get("events", []))
    events.sort(key=lambda e: (e.get("seq", 0), e.get("chapter", 0)))
    return events


def state_at(root: str, upto_seq: Optional[float], upto_chapter: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Replay timeline effects to get each character's state at a point in story-time."""
    chars = load(root, "characters", {"characters": []})
    state: Dict[str, Dict[str, Any]] = {}
    for c in chars.get("characters", []):
        state[c["id"]] = {
            "id": c["id"], "name": c.get("name", c["id"]),
            "status": c.get("status", "alive"),
            "location": c.get("location", ""),
            "traits": dict(c.get("traits", {})),
            "knows": set(c.get("knows", [])),
            "first_appearance": c.get("first_appearance"),
        }
    for ev in sorted_events(root):
        seq = ev.get("seq", 0)
        if upto_seq is not None and seq > upto_seq:
            continue
        if upto_seq is None and upto_chapter is not None and ev.get("chapter", 0) > upto_chapter:
            continue
        for eff in ev.get("effects", []):
            # effect syntax: target.field=value
            m = re.match(r"^([^.=]+)\.([^=]+)=(.*)$", eff)
            if not m:
                continue
            cid, field, value = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
            if cid not in state:
                state[cid] = {"id": cid, "name": cid, "status": "alive", "location": "",
                              "traits": {}, "knows": set(), "first_appearance": None}
            if field == "status":
                state[cid]["status"] = value
            elif field == "location":
                state[cid]["location"] = value
            else:
                state[cid]["traits"][field] = value
        for pid in ev.get("participants", []):
            if pid in state and state[pid].get("first_appearance") is None:
                state[pid]["first_appearance"] = ev.get("chapter")
            if pid in state and ev.get("location"):
                # being present at an event updates location
                state[pid]["location"] = ev["location"]
        if upto_seq is None and upto_chapter is not None:
            continue
    for cid in state:
        state[cid]["knows"] = sorted(state[cid]["knows"])
    return state


def chapter_records(root: str) -> List[Dict[str, Any]]:
    data = load(root, "chapter-index", {"chapters": []})
    recs = list(data.get("chapters", []))
    recs.sort(key=lambda c: c.get("index", 0))
    return recs


def find_chapter(root: str, index: int) -> Optional[Dict[str, Any]]:
    for c in chapter_records(root):
        if c.get("index") == index:
            return c
    return None


def chapter_seq_window(root: str, index: int) -> Tuple[float, float]:
    evs = [e for e in sorted_events(root) if e.get("chapter") == index]
    rec = find_chapter(root, index)
    if rec and rec.get("seq_start") is not None:
        return float(rec["seq_start"]), float(rec.get("seq_end", rec["seq_start"]))
    if evs:
        return float(min(e.get("seq", 0) for e in evs)), float(max(e.get("seq", 0) for e in evs))
    return 0.0, 0.0


def latest_chapter(root: str) -> int:
    recs = chapter_records(root)
    return max([c.get("index", 0) for c in recs], default=0)


def issue(sev: str, kind: str, chapter: Optional[int], message: str, fix: str = "") -> Dict[str, Any]:
    return {"severity": sev, "kind": kind, "chapter": chapter,
            "message": message, "fix": fix}


# --------------------------------------------------------------------------- commands: init


def cmd_init(args: argparse.Namespace) -> int:
    root = args.root
    os.makedirs(root, exist_ok=True)
    os.makedirs(os.path.join(root, "chapters"), exist_ok=True)
    os.makedirs(os.path.join(root, "notes"), exist_ok=True)
    os.makedirs(novel_root(root), exist_ok=True)

    templates_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
    copies = {
        "outline.md": "outline.md",
        "worldbuilding.md": "worldbuilding.md",
        "style.md": "style.md",
    }
    for src, dst in copies.items():
        s = os.path.join(templates_dir, src)
        d = os.path.join(root, dst)
        if os.path.exists(s) and not os.path.exists(d):
            with open(s, "r", encoding="utf-8") as f:
                content = f.read()
            content = content.replace("{{TITLE}}", args.title).replace("{{POV}}", args.pov)
            content = content.replace("{{TENSE}}", args.tense).replace("{{GENRE}}", args.genre)
            with open(d, "w", encoding="utf-8") as f:
                f.write(content)

    save(root, "project", {
        "title": args.title, "genre": args.genre,
        "pov": args.pov, "tense": args.tense,
        "target_words_per_chapter": args.target_words,
        "created_at": str(os.path.getmtime(root)),
        "precedence": PRECEDENCE,
        "version": VERSION,
    })
    save(root, "timeline", {"events": []})
    save(root, "characters", {"characters": []})
    save(root, "foreshadowing", {"items": []})
    save(root, "chapter-index", {"chapters": []})
    save(root, "notes", {"items": []})
    save(root, "glossary", {"rule": DEFAULT_GLOSSARY_RULE, "terms": []})
    cmd_sync(argparse.Namespace(root=root))
    emit({"ok": True, "root": os.path.abspath(root), "title": args.title,
          "files": ["outline.md", "worldbuilding.md", "style.md", "chapters/", "notes/", ".novel/"],
          "next": "1) 填 outline.md 的卷/章/节拍  2) novel.py character add / timeline add  3) novel.py context --chapter 1"})
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    require_project(args.root)
    cache = parse_outline(args.root)
    save(args.root, "outline-cache", cache)
    total_beats = sum(len(c.get("beats", [])) for c in cache["chapters"])
    emit({"ok": True, "volumes": len(cache["volumes"]), "chapters": len(cache["chapters"]),
          "beats": total_beats, "warnings": cache["warnings"]})
    return 0


# --------------------------------------------------------------------------- commands: context


def cmd_context(args: argparse.Namespace) -> int:
    root = args.root
    proj = require_project(root)
    n = args.chapter
    cache = load(root, "outline-cache", {"chapters": []})
    if not cache.get("chapters"):
        cache = parse_outline(root)
    outline_ch = None
    for c in cache.get("chapters", []):
        if c.get("index") == n:
            outline_ch = c
            break

    prev_start, prev_end = chapter_seq_window(root, n - 1) if n > 1 else (0.0, 0.0)
    state = state_at(root, prev_end if n > 1 else 0.0)
    events = sorted_events(root)
    prior_events = [e for e in events if (e.get("seq", 0) <= prev_end if n > 1 else False)]
    fs = load(root, "foreshadowing", {"items": []})
    open_fs = [f for f in fs.get("items", []) if f.get("status", "open") == "open"]
    overdue = [f for f in open_fs if f.get("due_chapter") and n > int(f["due_chapter"])]

    lines: List[str] = []
    lines.append("# 第 %d 章 · 写前约束包（必须先读完再动笔）" % n)
    lines.append("")
    lines.append("> 作品：%s ｜ 视角：%s ｜ 时态：%s ｜ 目标字数：%s" % (
        proj.get("title"), proj.get("pov"), proj.get("tense"),
        proj.get("target_words_per_chapter")))
    lines.append("")

    if outline_ch:
        lines.append("## 本章大纲节拍（逐条落地，不得增删关键节拍）")
        lines.append("")
        lines.append("**%s**" % outline_ch.get("title"))
        lines.append("")
        for b in outline_ch.get("beats", []):
            mark = "x" if b.get("checked") else " "
            lines.append("- [%s] %s `%s`" % (mark, b.get("text"), b.get("id")))
        lines.append("")
    else:
        lines.append("## ⚠️ 大纲里没有第 %d 章" % n)
        lines.append("")
        lines.append("先补 `outline.md` 再写，或者显式记录为「计划外章节」并说明理由。")
        lines.append("")

    if n > 1:
        lines.append("## 上章结尾（接续点）")
        lines.append("")
        rec = find_chapter(root, n - 1)
        if rec and rec.get("file"):
            fp = os.path.join(root, rec["file"])
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    body = f.read()
                tail = body.strip().splitlines()[-args.tail:]
                lines.append("> " + "\n> ".join(tail))
            else:
                lines.append("(正文文件缺失：%s)" % rec["file"])
        else:
            lines.append("(第 %d 章未登记，无法自动接续 —— 用 `chapter register` 补登记)" % (n - 1))
        lines.append("")

    if prior_events:
        lines.append("## story-time 时间线（最近 %d 条）" % min(args.events, len(prior_events)))
        lines.append("")
        lines.append("| seq | 章 | 时间 | 事件 | 参与者 | 地点 |")
        lines.append("|---|---|---|---|---|---|")
        for e in prior_events[-args.events:]:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                e.get("seq"), e.get("chapter"), e.get("time") or "-", e.get("title"),
                ", ".join(e.get("participants", [])), e.get("location") or "-"))
        lines.append("")
        lines.append("本章 story-time 起点 seq：**%s**（不得小于上一章结尾 %s）" % (
            int(prev_end) + 1, int(prev_end)))
        lines.append("")

    involved = [s for s in state.values() if s.get("first_appearance") is not None or args.all_chars]
    if involved:
        lines.append("## 人物当前状态（截至本章前）")
        lines.append("")
        lines.append("| 角色 | 状态 | 所在地 | 已知 |")
        lines.append("|---|---|---|---|")
        for s in sorted(involved, key=lambda x: x.get("name", "")):
            lines.append("| %s | %s | %s | %s |" % (
                s.get("name"), s.get("status"), s.get("location") or "-",
                ", ".join(s.get("knows", [])) or "-"))
        lines.append("")

    if open_fs:
        lines.append("## 开放伏笔（本章可推进，但不得提前回收未到期的）")
        lines.append("")
        for f in open_fs:
            due = f.get("due_chapter")
            flag = " ⚠️ 已逾期（计划第 %s 章）" % due if f in overdue else "（计划第 %s 章回收）" % due
            lines.append("- `%s` 埋于第 %s 章：%s%s" % (
                f.get("id"), f.get("planted_chapter"), f.get("content"), flag))
        lines.append("")

    notes = load(root, "notes", {"items": []})
    pending = [x for x in notes.get("items", [])
               if x.get("status", "open") == "open" and (x.get("chapter") in (n, None))]
    if pending:
        lines.append("## 待处理的修改建议")
        lines.append("")
        for x in pending:
            lines.append("- `%s`（第 %s 章，来自 %s）：%s" % (
                x.get("id"), x.get("chapter") or "全局", x.get("source", "-"), x.get("text")))
        lines.append("")

    lines.append("## 硬约束（违反即为 P0）")
    lines.append("")
    lines.append("1. 本章只落地上面列出的节拍；新增/删除关键节拍必须先改 `outline.md` 并说明理由")
    lines.append("2. story-time 不得倒流（回忆/插叙须显式标注为闪回）")
    lines.append("3. 不得让已死亡/已离场的角色出场，不得让角色知道尚未发生的事件")
    lines.append("4. 视角与叙述人称必须全章统一：%s / %s" % (proj.get("pov"), proj.get("tense")))
    lines.append("5. 未到期伏笔不得提前回收；到期伏笔不得无声消失")
    lines.append("6. 名词统一：`glossary.json` 里的主称呼为唯一写法")
    lines.append("")
    lines.append("## 冲突时的优先级")
    lines.append("")
    for i, p in enumerate(PRECEDENCE, 1):
        lines.append("%d. %s" % (i, p))

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("context written: %s" % args.out)
    else:
        print(text)
    return 0


# --------------------------------------------------------------------------- commands: check


def run_checks(root: str, only_chapter: Optional[int]) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    proj = load(root, "project", {})
    recs = chapter_records(root)
    events = sorted_events(root)
    chars = load(root, "characters", {"characters": []})
    fs = load(root, "foreshadowing", {"items": []})
    cache = load(root, "outline-cache", {"chapters": []})
    if not cache.get("chapters"):
        cache = parse_outline(root)

    name_of = {c["id"]: c.get("name", c["id"]) for c in chars.get("characters", [])}
    latest = latest_chapter(root)

    # ---- 1. timeline does not flow backwards
    prev_end = None
    prev_idx = None
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            if prev_end is None and idx < only_chapter:
                prev_end = rec.get("seq_end")
                prev_idx = idx
            continue
        if rec.get("seq_start") is None:
            issues.append(issue("P1", "时间线", idx,
                                "本章未登记 story-time 区间（seq_start/seq_end）",
                                "用 `chapter register` 补登记，或先 `timeline add` 再 `sync`"))
            continue
        if prev_end is not None and not rec.get("flashback"):
            if float(rec["seq_start"]) < float(prev_end):
                issues.append(issue("P0", "时间倒流", idx,
                                    "第 %d 章起点 seq=%s 早于第 %d 章结尾 seq=%s" % (
                                        idx, rec["seq_start"], prev_idx, prev_end),
                                    "确认是否闪回（register 时加 --flashback），否则调整事件顺序"))
        prev_end = rec.get("seq_end", rec["seq_start"])
        prev_idx = idx

    # ---- 2. dead / departed characters must not appear
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            continue
        start, _end = chapter_seq_window(root, idx)
        # state just BEFORE this chapter opens: dying inside this chapter is legal,
        # appearing after having died in an earlier chapter is not.
        snap = state_at(root, start - 0.5 if start else 0.0)
        for cid in rec.get("characters", []):
            s = snap.get(cid)
            if s and s.get("status") in ("dead", "departed", "gone"):
                issues.append(issue("P0", "人物状态", idx,
                                    "第 %d 章让 %s 出场，但该角色此时状态为 %s" % (
                                        idx, s.get("name"), s["status"]),
                                    "删除出场，或补一条时间线事件改变其状态"))

    # ---- 3. knowledge boundary
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            continue
        _, end = chapter_seq_window(root, idx)
        ev_seq = {e.get("id"): e.get("seq", 0) for e in events}
        for cid in rec.get("characters", []):
            snap = state_at(root, end)
            knows = set(snap.get(cid, {}).get("knows", []))
            for ev_id in rec.get("reveals", {}).get(cid, []) if isinstance(rec.get("reveals"), dict) else []:
                if ev_id not in knows and ev_seq.get(ev_id, 0) > end:
                    issues.append(issue("P0", "知情越界", idx,
                                        "%s 在第 %d 章表现出知道 %s，但该事件发生在 seq=%s（本章结尾 %s）" % (
                                            name_of.get(cid, cid), idx, ev_id, ev_seq.get(ev_id), end),
                                        "改为不知情，或补一条该角色获知此事的事件"))

    # ---- 4. location conflicts inside one chapter
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            continue
        # moving between locations inside a chapter is normal; being in two places
        # at the same story-time tick is not
        by_seq: Dict[Any, Dict[str, set]] = {}
        for e in events:
            if e.get("chapter") != idx:
                continue
            slot = by_seq.setdefault(e.get("seq", 0), {})
            for p in e.get("participants", []):
                if e.get("location"):
                    slot.setdefault(p, set()).add(e["location"])
        for seq_key, slot in by_seq.items():
            for cid, ls in slot.items():
                if len(ls) > 1:
                    issues.append(issue("P1", "地点冲突", idx,
                                        "第 %d 章 seq=%s 处 %s 同时出现在：%s" % (
                                            idx, seq_key, name_of.get(cid, cid), "、".join(sorted(ls))),
                                        "拆分到不同 seq，或补移动事件"))

    # ---- 5. foreshadowing hygiene
    for f in fs.get("items", []):
        ch = f.get("planted_chapter")
        if only_chapter is not None and ch != only_chapter and f.get("resolved_chapter") != only_chapter:
            continue
        status = f.get("status", "open")
        if f.get("resolved_chapter") is not None:
            if int(f["resolved_chapter"]) <= int(f.get("planted_chapter", 0)):
                issues.append(issue("P0", "伏笔", f.get("resolved_chapter"),
                                    "伏笔 %s 在第 %s 章回收，但不晚于埋设章第 %s 章" % (
                                        f.get("id"), f["resolved_chapter"], f.get("planted_chapter")),
                                    "未埋先收：先补埋设，或改判为设定而非伏笔"))
        if status == "open" and f.get("due_chapter") and latest > int(f["due_chapter"]):
            issues.append(issue("P1", "伏笔逾期", f.get("due_chapter"),
                                "伏笔 %s（第 %s 章埋）计划第 %s 章回收，已写到第 %d 章仍未回收" % (
                                    f.get("id"), f.get("planted_chapter"), f["due_chapter"], latest),
                                "本章回收，或把 due_chapter 后移并说明，或标记 abandoned"))

    # ---- 6. outline beat coverage
    covered = set()
    for rec in recs:
        covered.update(rec.get("beats_covered", []) or [])
        covered.update(rec.get("beats_deviated", []) or [])
    for och in cache.get("chapters", []):
        idx = och.get("index")
        if idx is None or idx > latest:
            continue
        if only_chapter is not None and idx != only_chapter:
            continue
        unchecked = [b for b in och.get("beats", [])
                     if not b.get("checked") and b.get("id") not in covered]
        if unchecked:
            issues.append(issue("P1", "节拍未落地", idx,
                                "第 %d 章有 %d 个节拍在正文中未见落地：%s" % (
                                    idx, len(unchecked), "；".join(b["text"][:30] for b in unchecked[:5])),
                                "补写，或用 `chapter mark-beat --status deviated` 明确记录偏离"))

    # ---- 7. POV / tense consistency
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            continue
        if proj.get("pov") and rec.get("pov") and rec["pov"] != proj["pov"]:
            issues.append(issue("P1", "视角漂移", idx,
                                "第 %d 章视角为 %s，项目设定为 %s" % (idx, rec["pov"], proj["pov"]),
                                "统一视角，或在 style.md 中声明该章为有意为之"))
        if proj.get("tense") and rec.get("tense") and rec["tense"] != proj["tense"]:
            issues.append(issue("P1", "时态漂移", idx,
                                "第 %d 章时态为 %s，项目设定为 %s" % (idx, rec["tense"], proj["tense"]),
                                "统一时态"))

    # ---- 8. prose file presence + length sanity
    target = proj.get("target_words_per_chapter") or 0
    for rec in recs:
        idx = rec.get("index", 0)
        if only_chapter is not None and idx != only_chapter:
            continue
        if not rec.get("file"):
            continue
        fp = os.path.join(root, rec["file"])
        if not os.path.exists(fp):
            issues.append(issue("P0", "正文缺失", idx, "登记的正文文件不存在：%s" % rec["file"], "补写或修正登记"))
            continue
        with open(fp, "r", encoding="utf-8") as fh:
            body = fh.read()
        wc = len(re.findall(r"[\u4e00-\u9fff]", body)) + len(re.findall(r"[A-Za-z]+", body))
        rec_words = rec.get("word_count") or wc
        if target and rec_words and abs(rec_words - target) / float(target) > 0.5:
            issues.append(issue("P2", "字数偏离", idx,
                                "第 %d 章约 %d 字，目标 %d 字，偏离超过 50%%" % (idx, rec_words, target),
                                "确认是否为有意的章节长度变化"))

    # ---- 9. glossary / naming consistency
    gl = load(root, "glossary", {"terms": []})
    for term in gl.get("terms", []):
        main = term.get("main")
        banned = term.get("aliases", [])
        if not main or not banned:
            continue
        for rec in recs:
            idx = rec.get("index", 0)
            if only_chapter is not None and idx != only_chapter:
                continue
            if not rec.get("file"):
                continue
            fp = os.path.join(root, rec["file"])
            if not os.path.exists(fp):
                continue
            with open(fp, "r", encoding="utf-8") as fh:
                body = fh.read()
            hits = [a for a in banned if a in body]
            if hits:
                issues.append(issue("P2", "名词不统一", idx,
                                    "第 %d 章出现了 %s 的非主称呼：%s（主称呼应为「%s」）" % (
                                        idx, main, "、".join(hits), main),
                                    "替换为「%s」，除非是角色对话中的有意用法" % main))

    issues.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i.get("chapter") or 0))
    return issues


def cmd_check(args: argparse.Namespace) -> int:
    require_project(args.root)
    only = args.chapter
    issues = run_checks(args.root, only)
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for i in issues:
        counts[i["severity"]] = counts.get(i["severity"], 0) + 1
    payload = {"scope": ("第 %d 章" % only) if only else "全稿",
               "issues": issues, "counts": counts,
               "blocked": counts.get("P0", 0) > 0}
    emit(payload)
    if issues:
        print("\n%s" % ("P0 未清零 —— 修完再继续" if counts.get("P0") else "无 P0，可继续（P1/P2 需记录处理决定）"),
              file=sys.stderr)
        for i in issues:
            print("  [%s] 第%s章 %s：%s" % (i["severity"], i.get("chapter") or "-", i["kind"], i["message"]),
                  file=sys.stderr)
    return 2 if counts.get("P0", 0) else (1 if issues else 0)


# --------------------------------------------------------------------------- commands: ledgers


def cmd_chapter(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "chapter-index", {"chapters": []})
    idx = args.index
    rec = find_chapter(root, idx)
    if rec is None:
        rec = {"index": idx, "beats_covered": [], "beats_deviated": []}
        data["chapters"].append(rec)
    if args.file:
        rec["file"] = args.file
    if args.seq_start is not None:
        rec["seq_start"] = args.seq_start
    if args.seq_end is not None:
        rec["seq_end"] = args.seq_end
    if args.characters:
        rec["characters"] = split_list(args.characters)
    if args.planted:
        rec["planted"] = split_list(args.planted)
    if args.resolved:
        rec["resolved"] = split_list(args.resolved)
    if args.pov:
        rec["pov"] = args.pov
    if args.tense:
        rec["tense"] = args.tense
    if args.flashback:
        rec["flashback"] = True
    if args.summary:
        rec["summary"] = args.summary
    if args.word_count is not None:
        rec["word_count"] = args.word_count
    if rec.get("seq_start") is None or rec.get("seq_end") is None:
        s, e = chapter_seq_window(root, idx)
        if s or e:
            rec.setdefault("seq_start", s)
            rec.setdefault("seq_end", e)
    rec["status"] = args.status or rec.get("status") or "drafted"
    save(root, "chapter-index", data)
    emit({"ok": True, "chapter": rec})
    return 0


def cmd_mark_beat(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "chapter-index", {"chapters": []})
    rec = find_chapter(root, args.chapter)
    if rec is None:
        sys.exit("error: 第 %d 章尚未登记，先 `chapter register`" % args.chapter)
    key = "beats_covered" if args.status == "covered" else "beats_deviated"
    other = "beats_deviated" if args.status == "covered" else "beats_covered"
    lst = rec.setdefault(key, [])
    if args.beat not in lst:
        lst.append(args.beat)
    if args.beat in rec.setdefault(other, []):
        rec[other].remove(args.beat)
    if args.status == "deviated":
        rec.setdefault("drift_log", []).append({
            "beat": args.beat, "nature": args.nature, "note": args.note or "",
        })
    save(root, "chapter-index", data)
    emit({"ok": True, "chapter": args.chapter, "beat": args.beat, "status": args.status,
          "nature": args.nature if args.status == "deviated" else None,
          "hint": "偏离已记账。若 nature=harmful，下一步应写修订方案（见 revision-playbook.md）"})
    return 0


def cmd_timeline(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "timeline", {"events": []})
    if args.tl_action == "add":
        seq = args.seq
        if seq is None:
            seq = max([e.get("seq", 0) for e in data["events"]], default=0) + 1
        ev = {
            "id": args.id or ("ev%03d" % (len(data["events"]) + 1)),
            "seq": float(seq), "chapter": args.chapter,
            "time": args.time or "", "title": args.title,
            "participants": split_list(args.participants),
            "location": args.location or "",
            "effects": args.effect or [],
            "note": args.note or "",
        }
        data["events"].append(ev)
        save(root, "timeline", data)
        emit({"ok": True, "event": ev,
              "hint": "已入账。若该事件改变人物状态，记得 --effect 角色id.字段=值"})
        return 0
    # show
    events = sorted_events(root)
    if args.last:
        events = events[-args.last:]
    if args.chapter:
        events = [e for e in events if e.get("chapter") == args.chapter]
    if args.json:
        emit({"events": events})
        return 0
    print("| seq | 章 | 时间 | 事件 | 参与者 | 地点 | 效果 |")
    print("|---|---|---|---|---|---|---|")
    for e in events:
        print("| %s | %s | %s | %s | %s | %s | %s |" % (
            int(e.get("seq", 0)), e.get("chapter"), e.get("time") or "-", e.get("title"),
            ", ".join(e.get("participants", [])) or "-", e.get("location") or "-",
            "; ".join(e.get("effects", [])) or "-"))
    return 0


def cmd_character(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "characters", {"characters": []})
    if args.ch_action == "add":
        for c in data["characters"]:
            if c["id"] == args.id:
                sys.exit("error: 角色 %s 已存在" % args.id)
        c = {"id": args.id, "name": args.name or args.id,
             "status": args.status, "location": args.location or "",
             "traits": {}, "knows": [], "first_appearance": None,
             "note": args.note or ""}
        data["characters"].append(c)
        save(root, "characters", data)
        emit({"ok": True, "character": c})
        return 0
    if args.ch_action == "set":
        c = None
        for x in data["characters"]:
            if x["id"] == args.id:
                c = x
        if c is None:
            sys.exit("error: 未找到角色 %s" % args.id)
        if args.field in ("status", "location"):
            c[args.field] = args.value
        else:
            c.setdefault("traits", {})[args.field] = args.value
        save(root, "characters", data)
        emit({"ok": True, "character": c,
              "hint": "直接改档案只影响「初始状态」；若该变化发生在故事中途，用 timeline add --effect 更准确"})
        return 0
    if args.ch_action == "knows":
        c = None
        for x in data["characters"]:
            if x["id"] == args.id:
                c = x
        if c is None:
            sys.exit("error: 未找到角色 %s" % args.id)
        c["knows"] = sorted(set(c.get("knows", [])) | set(split_list(args.events)))
        save(root, "characters", data)
        emit({"ok": True, "id": args.id, "knows": c["knows"]})
        return 0
    # list
    if args.json:
        emit(data)
        return 0
    print("| id | 名字 | 状态 | 所在地 | 已知事件 |")
    print("|---|---|---|---|---|")
    for c in data["characters"]:
        print("| %s | %s | %s | %s | %s |" % (
            c["id"], c.get("name"), c.get("status"), c.get("location") or "-",
            ", ".join(c.get("knows", [])) or "-"))
    return 0


def cmd_foreshadow(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "foreshadowing", {"items": []})
    if args.fs_action == "add":
        fid = args.id or ("f%03d" % (len(data["items"]) + 1))
        item = {"id": fid, "planted_chapter": args.chapter, "content": args.content,
                "due_chapter": args.due_chapter, "resolved_chapter": None,
                "status": "open", "note": args.note or ""}
        data["items"].append(item)
        save(root, "foreshadowing", data)
        emit({"ok": True, "item": item, "hint": "记得在对应章节 register 时 --planted %s" % fid})
        return 0
    if args.fs_action == "resolve":
        item = None
        for x in data["items"]:
            if x["id"] == args.id:
                item = x
        if item is None:
            sys.exit("error: 未找到伏笔 %s" % args.id)
        item["resolved_chapter"] = args.chapter
        item["status"] = "resolved"
        if args.note:
            item["note"] = args.note
        save(root, "foreshadowing", data)
        emit({"ok": True, "item": item})
        return 0
    items = [x for x in data["items"]
             if (args.status is None or x.get("status") == args.status)]
    if args.json:
        emit({"items": items})
        return 0
    print("| id | 埋于 | 内容 | 应回收 | 实际回收 | 状态 |")
    print("|---|---|---|---|---|---|")
    for x in items:
        print("| %s | 第%s章 | %s | 第%s章 | %s | %s |" % (
            x["id"], x.get("planted_chapter"), x.get("content")[:40],
            x.get("due_chapter") or "-",
            ("第%s章" % x["resolved_chapter"]) if x.get("resolved_chapter") else "-",
            x.get("status")))
    return 0


def cmd_notes(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "notes", {"items": []})
    if args.nt_action == "add":
        item = {"id": "n%03d" % (len(data["items"]) + 1), "chapter": args.chapter,
                "text": args.text, "source": args.source, "status": "open",
                "created": args.created or ""}
        data["items"].append(item)
        save(root, "notes", data)
        emit({"ok": True, "item": item,
              "next": "跑 `revise plan --chapter %s --change \"%s\"` 看影响面" % (
                  args.chapter or "N", args.text)})
        return 0
    if args.nt_action == "resolve":
        for x in data["items"]:
            if x["id"] == args.id:
                x["status"] = "resolved"
                x["resolution"] = args.note or ""
                save(root, "notes", data)
                emit({"ok": True, "item": x})
                return 0
        sys.exit("error: 未找到建议 %s" % args.id)
    items = [x for x in data["items"] if args.status is None or x.get("status") == args.status]
    emit({"items": items})
    return 0


# --------------------------------------------------------------------------- commands: revision


def cmd_revise_plan(args: argparse.Namespace) -> int:
    """Impact analysis for a proposed change: which chapters and ledgers are touched."""
    root = args.root
    require_project(root)
    change = args.change
    chars = load(root, "characters", {"characters": []})
    events = sorted_events(root)
    fs = load(root, "foreshadowing", {"items": []})
    recs = chapter_records(root)
    cache = load(root, "outline-cache", {"chapters": []})
    if not cache.get("chapters"):
        cache = parse_outline(root)

    # entities mentioned in the change text
    hits_chars = [c for c in chars.get("characters", [])
                  if c.get("name") and c["name"] in change]
    hit_ids = {c["id"] for c in hits_chars}
    hit_events = [e for e in events
                  if (e.get("title") and e["title"] in change)
                  or (set(e.get("participants", [])) & hit_ids)
                  or (e.get("location") and e["location"] in change)]
    hit_fs = [f for f in fs.get("items", []) if f.get("content", "")[:12] in change
              or (f.get("id") in change)]
    hit_chapters = sorted({e.get("chapter") for e in hit_events if e.get("chapter") is not None})
    for f in hit_fs:
        hit_chapters.append(f.get("planted_chapter"))
        if f.get("due_chapter"):
            hit_chapters.append(int(f["due_chapter"]))
        if f.get("resolved_chapter"):
            hit_chapters.append(int(f["resolved_chapter"]))
    hit_chapters = sorted({c for c in hit_chapters if c is not None})

    downstream = [r for r in recs if r.get("index", 0) >= (args.chapter or 0)]
    affected_downstream = [r["index"] for r in downstream
                           if (set(r.get("characters", [])) & hit_ids)
                           or r.get("index") in hit_chapters]

    outline_beats: List[Dict[str, Any]] = []
    for och in cache.get("chapters", []):
        for b in och.get("beats", []):
            text = b.get("text", "")
            if any(k and k in text for k in [c["name"] for c in hits_chars]) or \
               any(e.get("title") and e["title"] in text for e in hit_events):
                outline_beats.append({"chapter": och.get("index"), "beat": b.get("id"),
                                      "text": text})

    lines = []
    lines.append("# 修订影响面分析")
    lines.append("")
    lines.append("**拟改动**：%s" % change)
    lines.append("")
    lines.append("**基准章**：第 %s 章" % (args.chapter if args.chapter else "-"))
    lines.append("")
    lines.append("## 一、受影响的既有文本")
    lines.append("")
    if hit_chapters:
        lines.append("- 直接涉及的章节：%s" % "、".join("第%d章" % c for c in hit_chapters))
    if affected_downstream:
        lines.append("- 基准章之后的连带章节：%s" % "、".join("第%d章" % c for c in affected_downstream))
    if not hit_chapters and not affected_downstream:
        lines.append("- 未匹配到既有章节（关键词没对上人物/事件/地点名，建议改用更具体的实体名重试）")
    lines.append("")
    lines.append("## 二、受影响的台账")
    lines.append("")
    lines.append("- 时间线事件：%s" % ("、".join("%s(seq%s)" % (e.get("id"), int(e.get("seq", 0)))
                                                for e in hit_events) or "无"))
    lines.append("- 伏笔：%s" % ("、".join(f.get("id") for f in hit_fs) or "无"))
    lines.append("- 人物：%s" % ("、".join(c.get("name") for c in hits_chars) or "无"))
    lines.append("")
    lines.append("## 三、大纲中需要同步的节拍")
    lines.append("")
    if outline_beats:
        for b in outline_beats:
            lines.append("- 第%s章 `%s`：%s" % (b["chapter"], b["beat"], b["text"]))
    else:
        lines.append("- 未在现有大纲中匹配到节拍；若改动涉及剧情走向，应新增/修改大纲节拍而不是只改正文")
    lines.append("")
    lines.append("## 四、必答的四问（答完才能动笔）")
    lines.append("")
    lines.append("1. 改动后，**时间线**是否需要重排？哪些事件的 seq 要变？")
    lines.append("2. 改动后，**人物状态/知情边界**是否需要在更早的章节补前置？")
    lines.append("3. 改动后，**伏笔**的埋设与回收是否仍然成立？有没有变成「未埋先收」？")
    lines.append("4. 改动属于**大纲内调整**还是**大纲偏离**？后者须 `chapter mark-beat --status deviated` 记账")
    lines.append("")
    lines.append("## 五、两套方案（都要写出来给用户选）")
    lines.append("")
    lines.append("- **方案甲 · 最小改动**：只改本章与直接冲突处，其余靠补叙/追认弥合。成本低，可能留疤。")
    lines.append("- **方案乙 · 彻底重排**：从第 %s 章起重排时间线与因果链，改写范围大但自洽。" % (
        args.chapter or "N"))
    lines.append("")
    lines.append("> 改完必须跑 `check --all`，P0 清零才算完成；大纲偏离要同步回 `outline.md`。")

    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("revision plan written: %s" % args.out)
    else:
        print(text)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = args.root
    proj = require_project(root)
    issues = run_checks(root, None)
    recs = chapter_records(root)
    events = sorted_events(root)
    fs = load(root, "foreshadowing", {"items": []})
    cache = load(root, "outline-cache", {"chapters": []})
    notes = load(root, "notes", {"items": []})

    counts = {"P0": 0, "P1": 0, "P2": 0}
    for i in issues:
        counts[i["severity"]] = counts.get(i["severity"], 0) + 1

    lines = []
    lines.append("# 《%s》一致性报告" % proj.get("title", ""))
    lines.append("")
    lines.append("- 已写章节：%d ｜ 时间线事件：%d ｜ 伏笔：%d（开放 %d）" % (
        len(recs), len(events), len(fs.get("items", [])),
        len([f for f in fs.get("items", []) if f.get("status") == "open"])))
    lines.append("- 问题：P0 %d ｜ P1 %d ｜ P2 %d" % (counts["P0"], counts["P1"], counts["P2"]))
    lines.append("- 结论：%s" % ("**P0 未清零，不可继续往下写**" if counts["P0"] else "无阻断性问题"))
    lines.append("")
    if issues:
        lines.append("## 问题清单")
        lines.append("")
        lines.append("| 级别 | 章 | 类型 | 问题 | 建议 |")
        lines.append("|---|---|---|---|---|")
        for i in issues:
            lines.append("| %s | %s | %s | %s | %s |" % (
                i["severity"], i.get("chapter") or "-", i["kind"],
                i["message"].replace("|", "/"), i["fix"].replace("|", "/")))
        lines.append("")
    if recs:
        lines.append("## 章节台账")
        lines.append("")
        lines.append("| 章 | story-time | 出场 | 埋 | 收 | 状态 |")
        lines.append("|---|---|---|---|---|---|")
        for r in recs:
            lines.append("| %s | %s–%s | %s | %s | %s | %s |" % (
                r.get("index"), r.get("seq_start", "-"), r.get("seq_end", "-"),
                ", ".join(r.get("characters", [])) or "-",
                ", ".join(r.get("planted", [])) or "-",
                ", ".join(r.get("resolved", [])) or "-",
                r.get("status", "-")))
        lines.append("")
    open_notes = [n for n in notes.get("items", []) if n.get("status") == "open"]
    if open_notes:
        lines.append("## 未处理的修改建议")
        lines.append("")
        for n in open_notes:
            lines.append("- `%s` 第%s章（%s）：%s" % (
                n.get("id"), n.get("chapter") or "全局", n.get("source", "-"), n.get("text")))
        lines.append("")

    text = "\n".join(lines)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("report written: %s" % args.out)
    else:
        print(text)
    return 0


def cmd_glossary(args: argparse.Namespace) -> int:
    root = args.root
    require_project(root)
    data = load(root, "glossary", {"rule": DEFAULT_GLOSSARY_RULE, "terms": []})
    if args.gl_action == "add":
        data["terms"] = [t for t in data["terms"] if t.get("main") != args.main]
        data["terms"].append({"main": args.main, "aliases": split_list(args.aliases)})
        save(root, "glossary", data)
        emit({"ok": True, "terms": data["terms"]})
        return 0
    emit(data)
    return 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="novel.py", description="Novel writing ledger & consistency CLI")
    p.add_argument("--root", default=".", help="novel project root (default: current dir)")
    p.add_argument("--version", action="version", version="novel-writer %s" % VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="scaffold a new novel project")
    s.add_argument("--title", required=True)
    s.add_argument("--genre", default="未指定")
    s.add_argument("--pov", default="third-limited", help="third-limited / first / omniscient")
    s.add_argument("--tense", default="past")
    s.add_argument("--target-words", type=int, default=3000)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="parse outline.md into the cache")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("context", help="print the pre-writing constraint bundle for a chapter")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--events", type=int, default=12)
    s.add_argument("--tail", type=int, default=8, help="how many tail lines of the previous chapter to show")
    s.add_argument("--all-chars", action="store_true", help="list every character, not just appeared ones")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("check", help="consistency check (exit 2 on P0, 1 on P1/P2)")
    s.add_argument("--chapter", type=int, default=None)
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("chapter", help="register/update a chapter record")
    s.add_argument("--index", type=int, required=True)
    s.add_argument("--file", default=None)
    s.add_argument("--seq-start", type=float, default=None)
    s.add_argument("--seq-end", type=float, default=None)
    s.add_argument("--characters", default=None)
    s.add_argument("--planted", default=None)
    s.add_argument("--resolved", default=None)
    s.add_argument("--pov", default=None)
    s.add_argument("--tense", default=None)
    s.add_argument("--flashback", action="store_true")
    s.add_argument("--summary", default=None)
    s.add_argument("--word-count", type=int, default=None)
    s.add_argument("--status", default=None)
    s.set_defaults(func=cmd_chapter)

    s = sub.add_parser("mark-beat", help="mark an outline beat as covered or deviated")
    s.add_argument("--chapter", type=int, required=True)
    s.add_argument("--beat", required=True)
    s.add_argument("--status", choices=["covered", "deviated"], required=True)
    s.add_argument("--nature", choices=["harmful", "neutral", "beneficial"], default="neutral")
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_mark_beat)

    s = sub.add_parser("timeline", help="timeline ledger")
    s.add_argument("tl_action", choices=["add", "show"])
    s.add_argument("--chapter", type=int, default=None)
    s.add_argument("--seq", type=float, default=None)
    s.add_argument("--time", default=None)
    s.add_argument("--title", default=None)
    s.add_argument("--participants", default=None)
    s.add_argument("--location", default=None)
    s.add_argument("--effect", action="append", default=None, help="角色id.字段=值")
    s.add_argument("--id", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--last", type=int, default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_timeline)

    s = sub.add_parser("character", help="character ledger")
    s.add_argument("ch_action", choices=["add", "set", "knows", "list"])
    s.add_argument("--id", default=None)
    s.add_argument("--name", default=None)
    s.add_argument("--status", default="alive")
    s.add_argument("--location", default=None)
    s.add_argument("--field", default=None)
    s.add_argument("--value", default=None)
    s.add_argument("--events", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_character)

    s = sub.add_parser("foreshadow", help="foreshadowing ledger")
    s.add_argument("fs_action", choices=["add", "resolve", "list"])
    s.add_argument("--chapter", type=int, default=None)
    s.add_argument("--content", default=None)
    s.add_argument("--due-chapter", type=int, default=None)
    s.add_argument("--id", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--status", default=None)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_foreshadow)

    s = sub.add_parser("notes", help="revision suggestion ledger")
    s.add_argument("nt_action", choices=["add", "resolve", "list"])
    s.add_argument("--chapter", type=int, default=None)
    s.add_argument("--text", default=None)
    s.add_argument("--source", default="user")
    s.add_argument("--created", default=None)
    s.add_argument("--id", default=None)
    s.add_argument("--note", default=None)
    s.add_argument("--status", default=None)
    s.set_defaults(func=cmd_notes)

    s = sub.add_parser("revise", help="impact analysis for a proposed change")
    s.add_argument("rv_action", choices=["plan"])
    s.add_argument("--chapter", type=int, default=None)
    s.add_argument("--change", required=True)
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_revise_plan)

    s = sub.add_parser("glossary", help="naming / terminology ledger")
    s.add_argument("gl_action", choices=["add", "list"])
    s.add_argument("--main", default=None)
    s.add_argument("--aliases", default=None)
    s.set_defaults(func=cmd_glossary)

    s = sub.add_parser("report", help="write the consistency report")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_report)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
