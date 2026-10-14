#!/usr/bin/env python3
"""relapse_check.py - machine checks for the "don't repeat this mistake" list.

Zero third-party dependencies. Python 3.8+. Cross-platform. Read-only.

This is the mechanical half of references/relapse-guard.md. Everything that can be
settled from the text alone is settled here; everything that needs judgement
(causality, foreshadowing, POV knowledge, author intent) stays in that file.
Every finding carries its rule id (R-01 ... R-29) so a hit can be read back
against the handbook.

Design rule: report, never rewrite. This tool never edits, moves or deletes
anything; the only file it may write is the report given by --out.

Exit codes follow scripts/novel.py:
    0 = clean
    1 = only P1 / P2
    2 = at least one P0
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.0.0"

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}

EXIT_OK, EXIT_WARN, EXIT_BLOCK = 0, 1, 2


# --------------------------------------------------------------- default config

# Groups are cosmetic: they only shape the report headings.
DEFAULT_BANNED_WORDS: Dict[str, List[str]] = {
    "现代器物与科技": [
        "手机", "电脑", "电话", "电视", "马达", "引擎", "发动机", "电池", "灯泡", "电灯",
        "注射", "疫苗", "抗生素", "医院", "护士", "警察", "公司", "汽油", "柴油", "塑料",
        "照片", "相机", "屏幕", "键盘", "鼠标", "充电", "开机", "关机", "地铁", "公交",
        "短信", "上网", "网络", "电子邮件", "快递", "超市", "记者", "广播", "录音机", "录像",
    ],
    "当代口语与网络语": [
        # 注意：不要把方位词「上头」列进来 —— 它在白话里是固有方位用法，
        # 列了会在长稿里刷出上百条误报。
        "吐槽", "破防", "躺平", "内卷", "社死", "摸鱼", "逆天", "离谱", "裂开", "爆红",
        "靠谱", "搞不定", "搞定", "翻车", "踩雷", "私信", "拉黑", "带节奏", "扎心", "实锤",
    ],
}

# Words that are wrong in period writing but common elsewhere. They are reported
# as P2 (check by hand) so the tool stays useful across very different settings.
# Move any of them into banned_words when the book's style makes them hard errors.
DEFAULT_WATCH_WORDS: Dict[str, List[str]] = {
    "现代行政与抽象词": [
        "意识到", "反应过来", "整个过程", "负责", "沟通", "压力", "状态", "系统", "机制",
        "平台", "方案", "组织", "管理", "提升", "优化", "反馈", "评估", "目标", "情绪",
        "性格", "意识", "安排", "问题", "没关系", "处理", "控制", "确认", "强调", "信号",
        "节奏", "逻辑", "模式", "关系", "表情",
    ],
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "banned_words": DEFAULT_BANNED_WORDS,
    "watch_words": DEFAULT_WATCH_WORDS,
    "punctuation_ban": ["?", "!", "？", "！"],
    "latin_min_len": 2,
    "latin_allow": ["md", "py", "json", "txt", "csv", "yaml", "yml"],
    "chapter_regex": r"^#{1,6}\s+第[0-9零〇一二三四五六七八九十百千万两廿卅]{1,8}\s*章",
    "chapter_regex_fallback": r"^#{1,6}\s+\S",
    "meta_ref_patterns": [
        r"第[0-9零〇一二三四五六七八九十百十]{1,6}\s*章",
        r"本章", r"本回", r"上一章", r"下一章", r"后文", r"前文提到", r"上回说到", r"本卷",
    ],
    "skip_frontmatter": True,
    "skip_tables": True,
    "skip_code": True,
    "target_words": [300, 6000],
    "opening_chars": 120,
    "opening_similarity": 0.55,
    "dup_similarity": 0.86,
    "dup_min_chars": 12,
    "dup_window": 24,
    "claim_window": 40,
    "slot_top": 30,
    "snippet_chars": 60,
    "quantifiers": [
        "道", "回", "趟", "个", "只", "碗", "间", "行", "块", "样", "人", "钱", "毫",
        "层", "张", "枚", "页", "年", "月", "日", "天", "步", "句", "遍", "次", "封",
        "本", "册", "盏", "斤", "根", "扇", "条", "列", "排", "户", "口",
    ],
    "claim_words": ["一共", "总共", "总计", "合计", "共"],
    "claim_context_lines": 4,
    "slot_hard_min": 100,
    # Which counting slots the author declares load-bearing for this book.
    # The machine cannot guess these - only the outline knows which numbers are
    # locked together - so they are declared here and checked strictly.
    "watch_slots": [],
    # Time, money and speech units legitimately take many values anywhere;
    # using them as counting slots produces nothing but noise.
    "object_quantifiers": [
        "道", "回", "趟", "个", "只", "碗", "间", "行", "块", "样", "人", "层",
        "张", "枚", "页", "盏", "根", "扇", "条", "列", "排", "户", "口", "本", "册",
    ],
    "slot_tail_stop": "的了他在她它也就都还又再上下里中前后去来是在和与把被给从向往不有个一二三四五六七八九十这那",
    "levels": {
        "banned_words": "P1",
        "watch_words": "P2",
        "latin_noise": "P1",
        "punctuation": "P1",
        "meta_ref": "P1",
        "dup_sentence": "P2",
        "opening_similarity": "P2",
        "word_count": "P2",
        "claim_vs_list": "P0",
        "split_sum": "P0",
        "slot_conflict": "P2",
    },
}

CHECK_LABELS = {
    "banned_words": ("R-25", "禁用现代词"),
    "watch_words": ("R-25", "边界词（待人工确认）"),
    "latin_noise": ("R-26", "中英混排噪音"),
    "punctuation": ("R-27", "标点体例"),
    "meta_ref": ("R-28", "叙述者章节元引用"),
    "dup_sentence": ("R-24", "同章重复叙述"),
    "opening_similarity": ("R-23", "相邻章开头结构雷同"),
    "word_count": ("R-29", "章字数偏离目标区间"),
    "claim_vs_list": ("R-01", "声明与列举不符"),
    "split_sum": ("R-02", "拆分加总不等于总数"),
    "slot_conflict": ("R-04", "同一计数槽位出现多个值"),
}


# ------------------------------------------------------------------ helpers

CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "两": 2, "廿": 20, "卅": 30,
}
CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

CN_NUM = r"(?:[0-9]+|[零〇一二三四五六七八九十百千万两廿卅]{1,7})"


def cn_to_int(token: str) -> Optional[int]:
    """Turn '13' / '十三' / '一百二十' into an int. None when it is not a number."""
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    total, cur = 0, 0
    for ch in token:
        if ch in CN_DIGITS:
            cur = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            total += (cur if cur else 1) * CN_UNITS[ch]
            cur = 0
        else:
            return None
    return total + cur


def load_file(path: str) -> str:
    if not os.path.isfile(path):
        sys.exit("error: no such file: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def apply_range(text: str, start_at: Optional[str], stop_at: Optional[str]) -> str:
    """Cut the text down to the prose body.

    Most working manuscripts carry appendices, rule lists and revision ledgers
    that must NOT be judged by prose rules - the scenery out there legitimately
    contains dates, numbers and section names. Point start_at / stop_at at the
    heading that opens or closes the body and everything outside is ignored.
    """
    if not start_at and not stop_at:
        return text
    lines = text.split("\n")
    start_pat = re.compile(start_at) if start_at else None
    stop_pat = re.compile(stop_at) if stop_at else None
    begin, end = 0, len(lines)
    for i, line in enumerate(lines):
        if start_pat and start_pat.match(line) and i > begin:
            begin = i
            break
    for i in range(begin, len(lines)):
        if stop_pat and stop_pat.match(lines[i]):
            end = i
            break
    return "\n".join(lines[begin:end])


def load_config(path: Optional[str]) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy without extra imports
    if not path:
        return cfg
    if not os.path.isfile(path):
        sys.exit("error: no such config: %s" % path)
    with open(path, "r", encoding="utf-8") as fh:
        try:
            user = json.load(fh)
        except json.JSONDecodeError as exc:
            sys.exit("error: %s is not valid JSON (%s)" % (path, exc))
    # shallow merge is enough: every top-level key is replaced wholesale
    for key, value in user.items():
        cfg[key] = value
    return cfg


def flatten_groups(groups: Dict[str, List[str]]) -> List[str]:
    out: List[str] = []
    for _group, words in (groups or {}).items():
        for word in words:
            word = word.strip()
            if word:
                out.append(word)
    return out


def snippet(text: str, size: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= size else text[:size] + "…"


def cjk_len(text: str) -> int:
    """Count every non-space character; punctuation included, like word counts do."""
    return sum(1 for ch in text if not ch.isspace())


def ngrams(text: str, n: int) -> set:
    return {text[i:i + n] for i in range(max(0, len(text) - n + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


# ------------------------------------------------------------------ chapters


class Chapter:
    def __init__(self, title: str, start: int, lines: Sequence[str]):
        self.title = title
        self.start = start
        self.lines = list(lines)
        self.opening = ""
        for raw in self.lines:
            body = strip_line(raw, keep_punct=False)
            if body:
                self.opening = body
                break

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def words(self) -> int:
        return cjk_len(strip_line(self.text, keep_punct=False))


def strip_line(line: str, keep_punct: bool = True) -> str:
    """Drop markdown noise (headings, emphasis, tables markers) from a line."""
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^>\s*", "", line)
    line = line.replace("`", "")
    line = re.sub(r"\*\*([^*]*)\*\*", r"\1", line)
    line = re.sub(r"\*([^*]*)\*", r"\1", line)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    if not keep_punct:
        line = re.sub(r"[，。、；：！？,\.\-—…《》「」『』“”‘’（）()《》\[\]]", "", line)
    return line.strip()


def split_chapters(text: str, cfg: Dict[str, Any]) -> List[Chapter]:
    lines = text.split("\n")
    start_idx = 0
    if cfg.get("skip_frontmatter") and lines and lines[0].startswith("---"):
        for i in range(1, min(len(lines), 200)):
            if lines[i].startswith("---"):
                start_idx = i + 1
                break

    primary = re.compile(cfg["chapter_regex"])
    fallback = re.compile(cfg["chapter_regex_fallback"])
    heads = [i for i in range(start_idx, len(lines)) if primary.match(lines[i])]
    if len(heads) < 2:
        heads = [i for i in range(start_idx, len(lines)) if fallback.match(lines[i])]

    chapters: List[Chapter] = []
    if not heads:
        chapters.append(Chapter("(whole file)", 1, lines[start_idx:]))
        return chapters

    if heads[0] > start_idx:
        chapters.append(Chapter("(front matter)", start_idx + 1, lines[start_idx:heads[0]]))
    for idx, head in enumerate(heads):
        end = heads[idx + 1] if idx + 1 < len(heads) else len(lines)
        chapters.append(Chapter(lines[head].strip().lstrip("# ").strip(), head + 1, lines[head + 1:end]))
    return chapters


def scannable_lines(lines: Sequence[str], cfg: Dict[str, Any]) -> Iterable[Tuple[int, str]]:
    """Yield (1-based line number, cleaned line) dropping tables / code fences."""
    in_code = False
    for offset, raw in enumerate(lines):
        line_no = offset + 1
        stripped = raw.strip()
        if stripped.startswith("```"):
            if cfg.get("skip_code", True):
                in_code = not in_code
            continue
        if in_code:
            continue
        if cfg.get("skip_tables", True) and (stripped.startswith("|") or raw.count("|") >= 3):
            continue
        yield line_no, strip_line(raw)


# ------------------------------------------------------------------ findings


class Finding:
    __slots__ = ("level", "check", "chapter", "line", "message", "detail")

    def __init__(self, check: str, level: str, message: str, detail: str = "",
                 chapter: str = "", line: Optional[int] = None):
        self.check = check
        self.level = level
        self.message = message
        self.detail = detail
        self.chapter = chapter
        self.line = line

    def sort_key(self) -> Tuple[int, str, int]:
        return (SEVERITY_ORDER.get(self.level, 9), self.check, self.line or 0)


class Report:
    def __init__(self, source: str):
        self.source = source
        self.findings: List[Finding] = []
        self.tables: Dict[str, str] = {}

    def add(self, check: str, message: str, detail: str = "", chapter: str = "",
            line: Optional[int] = None, levels: Optional[Dict[str, str]] = None) -> None:
        level = (levels or {}).get(check, DEFAULT_CONFIG["levels"].get(check, "P2"))
        self.findings.append(Finding(check, level, message, detail, chapter, line))

    def counts(self) -> Dict[str, int]:
        out = {"P0": 0, "P1": 0, "P2": 0}
        for f in self.findings:
            out[f.level] = out.get(f.level, 0) + 1
        return out

    def exit_code(self) -> int:
        counts = self.counts()
        if counts.get("P0"):
            return EXIT_BLOCK
        if counts.get("P1") or counts.get("P2"):
            return EXIT_WARN
        return EXIT_OK


# ------------------------------------------------------------------ checks


def check_words(text_lines: Sequence[str], cfg: Dict[str, Any], rep: Report) -> None:
    levels = cfg["levels"]
    banned = sorted(set(flatten_groups(cfg.get("banned_words", {}))), key=len, reverse=True)
    watch = sorted(set(flatten_groups(cfg.get("watch_words", {}))), key=len, reverse=True)
    size = int(cfg.get("snippet_chars", 60))
    hits: Dict[str, List[Tuple[int, str]]] = {}

    for line_no, clean in scannable_lines(text_lines, cfg):
        for word in banned:
            pos = 0
            while True:
                idx = clean.find(word, pos)
                if idx < 0:
                    break
                left = max(0, idx - size // 2)
                hits.setdefault(word, []).append((line_no, snippet(clean[left:left + size + len(word)], size)))
                pos = idx + len(word)

    for word, places in sorted(hits.items(), key=lambda kv: -len(kv[1])):
        lines = sorted({p[0] for p in places})
        rep.add("banned_words", "禁用词「%s」出现 %d 次" % (word, len(places)),
                "行号：" + ", ".join(str(n) for n in lines[:20]),
                line=lines[0], levels=levels)

    watch_hits: Dict[str, List[int]] = {}
    for line_no, clean in scannable_lines(text_lines, cfg):
        for word in watch:
            if word in clean:
                watch_hits.setdefault(word, []).append(line_no)
    for word, lines in sorted(watch_hits.items(), key=lambda kv: -len(kv[1])):
        rep.add("watch_words", "边界词「%s」出现 %d 次，请按作品年代确认" % (word, len(lines)),
                "行号：" + ", ".join(str(n) for n in sorted(set(lines))[:20]),
                line=sorted(set(lines))[0], levels=levels)


def check_latin(text_lines: Sequence[str], cfg: Dict[str, Any], rep: Report) -> None:
    min_len = int(cfg.get("latin_min_len", 2))
    allow = {w.lower() for w in cfg.get("latin_allow", [])}
    pattern = re.compile(r"[A-Za-z]{%d,}" % max(1, min_len))
    url = re.compile(r"https?://\S+")
    inline = re.compile(r"`[^`]*`")
    hits: Dict[str, List[int]] = {}
    in_code = False

    for offset, raw in enumerate(text_lines):
        line_no = offset + 1
        stripped = raw.strip()
        if stripped.startswith("```"):
            if cfg.get("skip_code", True):
                in_code = not in_code
            continue
        if in_code:
            continue
        if cfg.get("skip_tables", True) and (stripped.startswith("|") or raw.count("|") >= 3):
            continue
        probe = url.sub(" ", raw)
        probe = inline.sub(" ", probe)
        probe = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", probe)
        for token in pattern.findall(probe):
            if token.lower() in allow:
                continue
            hits.setdefault(token, []).append(line_no)

    for token, lines in sorted(hits.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rep.add("latin_noise", "英文串「%s」出现 %d 次" % (token, len(lines)),
                "行号：" + ", ".join(str(n) for n in sorted(set(lines))[:20]),
                line=sorted(set(lines))[0], levels=cfg["levels"])


def check_punctuation(text_lines: Sequence[str], cfg: Dict[str, Any], rep: Report) -> None:
    banned = [p for p in cfg.get("punctuation_ban", []) if p]
    if not banned:
        return
    size = int(cfg.get("snippet_chars", 60))
    hits: Dict[str, List[Tuple[int, str]]] = {}
    for line_no, clean in scannable_lines(text_lines, cfg):
        for mark in banned:
            if mark in clean:
                idx = clean.find(mark)
                left = max(0, idx - size // 2)
                hits.setdefault(mark, []).append((line_no, snippet(clean[left:left + size], size)))
    for mark, places in hits.items():
        lines = sorted({p[0] for p in places})
        rep.add("punctuation", "禁用标点「%s」出现 %d 次" % (mark, len(places)),
                "行号：" + ", ".join(str(n) for n in lines[:20]),
                line=lines[0], levels=cfg["levels"])


def check_meta_ref(text_lines: Sequence[str], cfg: Dict[str, Any], chapters: Sequence[Chapter],
                   rep: Report) -> None:
    patterns = cfg.get("meta_ref_patterns", [])
    if not patterns:
        return
    combined = re.compile("|".join(patterns))
    heading_lines = {c.start for c in chapters}
    size = int(cfg.get("snippet_chars", 60))
    hits: List[Tuple[str, int, str]] = []
    front_pages = {c.start for c in chapters if c.title.startswith("(")}
    front_body = set()
    for ch in chapters:
        if ch.title.startswith("("):
            front_body.update(range(ch.start, ch.start + len(ch.lines) + 1))
    for line_no, clean in scannable_lines(text_lines, cfg):
        # Meta-reference is normal in front matter, notes and appendices; it is
        # only a leak when the prose narrator winks at the reader.
        if line_no in heading_lines or line_no in front_pages or line_no in front_body:
            continue
        found = combined.search(clean)
        if found:
            left = max(0, found.start() - size // 2)
            hits.append((found.group(0), line_no, snippet(clean[left:left + size], size)))
    by_word: Dict[str, List[Tuple[int, str]]] = {}
    for word, line_no, ctx in hits:
        by_word.setdefault(word, []).append((line_no, ctx))
    for word, places in by_word.items():
        lines = sorted({p[0] for p in places})
        rep.add("meta_ref", "疑似章节元引用「%s」出现 %d 次" % (word, len(places)),
                "行号：" + ", ".join(str(n) for n in lines[:20]),
                line=lines[0], levels=cfg["levels"])


def sentence_split(text: str) -> List[str]:
    parts = re.split(r"[。！？；\n]+", text)
    return [p.strip() for p in parts if p.strip()]


def check_dup_sentence(chapters: Sequence[Chapter], cfg: Dict[str, Any], rep: Report) -> None:
    threshold = float(cfg.get("dup_similarity", 0.86))
    min_chars = int(cfg.get("dup_min_chars", 12))
    window = int(cfg.get("dup_window", 24))
    for ch in chapters:
        sentences = [s for s in sentence_split(ch.text) if cjk_len(s) >= min_chars]
        grams = [ngrams(s, 2) for s in sentences]
        for i in range(len(sentences)):
            for j in range(i + 1, min(i + 1 + window, len(sentences))):
                score = jaccard(grams[i], grams[j])
                if score >= threshold:
                    rep.add("dup_sentence", "相邻处两句高度相似（%.0f%%）" % (score * 100),
                            "甲：%s\n乙：%s" % (snippet(sentences[i], 50), snippet(sentences[j], 50)),
                            chapter=ch.title, line=ch.start, levels=cfg["levels"])
                    return  # one example per chapter is enough to act on


def check_opening_similarity(chapters: Sequence[Chapter], cfg: Dict[str, Any], rep: Report) -> None:
    threshold = float(cfg.get("opening_similarity", 0.55))
    take = int(cfg.get("opening_chars", 120))
    usable = [c for c in chapters if len(c.opening) >= 20]
    for i in range(len(usable) - 1):
        a, b = usable[i], usable[i + 1]
        score = jaccard(ngrams(a.opening[:take], 2), ngrams(b.opening[:take], 2))
        if score >= threshold:
            rep.add("opening_similarity", "两章开头骨架雷同（%.0f%%）" % (score * 100),
                    "甲《%s》：%s\n乙《%s》：%s" % (
                        a.title, snippet(a.opening, take), b.title, snippet(b.opening, take)),
                    chapter=b.title, line=b.start, levels=cfg["levels"])


def check_word_count(chapters: Sequence[Chapter], cfg: Dict[str, Any], rep: Report) -> None:
    lo, hi = int(cfg["target_words"][0]), int(cfg["target_words"][1])
    rows: List[str] = []
    for ch in chapters:
        count = ch.words()
        if not ch.title.startswith("(") and count == 0:
            continue
        rows.append("%s｜%d 字" % (ch.title, count))
        if ch.title.startswith("("):
            continue
        if count < lo:
            rep.add("word_count", "《%s》%d 字，低于下限 %d" % (ch.title, count, lo),
                    "", chapter=ch.title, line=ch.start, levels=cfg["levels"])
        elif count > hi:
            rep.add("word_count", "《%s》%d 字，高于上限 %d" % (ch.title, count, hi),
                    "", chapter=ch.title, line=ch.start, levels=cfg["levels"])
    rep.tables["字数分布"] = "\n".join(rows)


# ------------------------------------------------------------------ counting


def build_number_regex(cfg: Dict[str, Any]) -> re.Pattern:
    quant = "|".join(re.escape(q) for q in cfg.get("quantifiers", []))
    return re.compile(r"(" + CN_NUM + r")\s*(" + quant + r")")


def build_slot_regex(cfg: Dict[str, Any]) -> re.Pattern:
    quant = "|".join(re.escape(q) for q in cfg.get("object_quantifiers", []))
    return re.compile(r"(" + CN_NUM + r")\s*(" + quant + r")")


def check_claim_vs_list(text_lines: Sequence[str], chapters: Sequence[Chapter],
                        cfg: Dict[str, Any], rep: Report) -> None:
    """Claimed total vs. the list that follows it (R-01)."""
    claim_words = "|".join(re.escape(w) for w in cfg.get("claim_words", []))
    quant = "|".join(re.escape(q) for q in cfg.get("quantifiers", []))
    claim_re = re.compile(r"(?:%s)\s*(%s)\s*(%s)" % (claim_words, CN_NUM, quant))
    window = int(cfg.get("claim_window", 40))
    list_re = re.compile(r"^\s*(?:[-*+]\s+|\d+[.、)]|[（(]?\d+[）)]|第" + CN_NUM + r"[行个道回趟只间样块页]?[、，,\s])")

    heads = sorted(((c.start, c.title) for c in chapters), key=lambda pair: pair[0])

    def chapter_of(line_no: int) -> str:
        title = ""
        for start, name in heads:
            if start <= line_no:
                title = name
            else:
                break
        return title

    for offset, raw in enumerate(text_lines):
        line_no = offset + 1
        clean = strip_line(raw)
        if cfg.get("skip_tables", True) and raw.count("|") >= 3:
            continue
        for match in claim_re.finditer(clean):
            value = cn_to_int(match.group(1))
            unit = match.group(2)
            if value is None or value <= 0:
                continue
            listed, blank_run = 0, 0
            for probe in text_lines[line_no: line_no + window]:
                probe_clean = probe.strip()
                if not probe_clean:
                    blank_run += 1
                    if listed or blank_run > 1:
                        break
                    continue
                if listed or blank_run <= 1:
                    if list_re.match(probe):
                        listed += 1
                        blank_run = 0
                        continue
                # The list has to start in the two lines right after the claim.
                # Scanning further risks picking up an unrelated enumeration
                # twenty lines later and calling it a broken breakdown.
                break
            if listed and listed != value:
                rep.add("claim_vs_list",
                        "声明「%s」=%d%s，紧随其后只列出 %d 项" % (match.group(0), value, unit, listed),
                        "位置：%s 第 %d 行" % (chapter_of(line_no) or "(未分章)", line_no),
                        chapter=chapter_of(line_no), line=line_no, levels=cfg["levels"])


def check_split_sum(text_lines: Sequence[str], cfg: Dict[str, Any], rep: Report) -> None:
    """A claimed total that its own breakdown fails to add up to (R-02).

    Two outputs, deliberately split:
      * automatic P0 -- only when the same counting unit appears both in the claim
        and in at least two nearby addends, so the arithmetic is unambiguous;
      * a review table of every claimed sentence, for the eye to settle the rest.
    Cross-unit breakdowns ("three rooms, three tiers each") are arithmetic the
    machine cannot safely guess, so they go to the table, never to P0.
    """
    quant = "|".join(re.escape(q) for q in cfg.get("quantifiers", []))
    claim_words = "|".join(re.escape(w) for w in cfg.get("claim_words", []))
    claim_re = re.compile(r"(?:%s)\s*(%s)\s*(%s)" % (claim_words, CN_NUM, quant))
    context = int(cfg.get("claim_context_lines", 4))
    review: List[str] = []

    for offset, raw in enumerate(text_lines):
        line_no = offset + 1
        clean = strip_line(raw)
        if not clean or (cfg.get("skip_tables", True) and raw.count("|") >= 3):
            continue
        for match in claim_re.finditer(clean):
            total = cn_to_int(match.group(1))
            unit = match.group(2)
            if total is None or total <= 0:
                continue
            context_text = clean
            for probe in text_lines[line_no: line_no + context]:
                probe_clean = strip_line(probe)
                if probe_clean:
                    context_text += " " + probe_clean
                elif len(context_text) > len(clean):
                    break
            review.append("L%d｜声明 %d%s｜%s" % (
                line_no, total, unit, snippet(context_text, int(cfg.get("snippet_chars", 60)) * 2)))

            # same-unit addends only: that is the one case where the arithmetic
            # is unambiguous and a mismatch is worth stopping the author for.
            tail = context_text[match.end():]
            addends = [cn_to_int(m.group(1)) for m in
                       re.finditer(r"(%s)\s*(%s)" % (CN_NUM, re.escape(unit)), tail)]
            addends = [a for a in addends if a is not None]
            body = sum(addends)
            lo = max(int(cfg.get("sum_min_floor", 3)), total // 3)
            hi = total * 3
            # A handful of stray "一个、一个" in the next sentence is not a
            # breakdown of anything; only report when the amounts are on the
            # same order of magnitude as the claim and still do not add up.
            if len(addends) >= 2 and lo <= body <= hi and body != total:
                rep.add("split_sum",
                        "声明 %d%s，同单位分项合计 %d（%s）" % (
                            total, unit, sum(addends), "＋".join(str(a) for a in addends)),
                        "%s" % snippet(context_text, int(cfg.get("snippet_chars", 60)) * 2),
                        line=line_no, levels=cfg["levels"])

    if review:
        rep.tables["待核加总清单（机器不敢判的，请眼过一遍）"] = "\n".join(review)


def check_slot_conflict(text_lines: Sequence[str], chapters: Sequence[Chapter],
                        cfg: Dict[str, Any], rep: Report) -> None:
    """Watch declared counting slots for value drift (R-04).

    Which numbers are locked together is something only the outline knows, so
    the author declares them in `watch_slots` ("道痕", "个碗", "块牌") and those
    are checked strictly across the whole book.

    With no configuration nothing is judged. That is deliberate: the machine
    cannot tell "碗 went from 17 to 16 because two people left" (story) from
    "the author forgot one of the six places that says 16" (bug). Pretending
    otherwise fills the report with confident nonsense and the tool stops being
    trusted. So everything lands in the review table, and only the slots the
    author declared load-bearing get flagged.

    To use it: list them in `watch_slots`, e.g. ["道痕", "个碗", "块牌"].
    Those are then checked across the whole book, and any drift is a P1.
    """
    slot_re = build_slot_regex(cfg)
    heads = sorted(((c.start, c.title) for c in chapters), key=lambda pair: pair[0])
    watch = set(cfg.get("watch_slots", []) or [])
    stop = set(cfg.get("slot_tail_stop", ""))

    def chapter_of(line_no: int) -> str:
        title = ""
        for start, name in heads:
            if start <= line_no:
                title = name
            else:
                break
        return title or "(未分章)"

    slots: Dict[str, Dict[int, List[Tuple[int, str]]]] = {}
    for offset, raw in enumerate(text_lines):
        line_no = offset + 1
        clean = strip_line(raw)
        if cfg.get("skip_tables", True) and raw.count("|") >= 3:
            continue
        for match in slot_re.finditer(clean):
            value = cn_to_int(match.group(1))
            if value is None:
                continue
            noun = "".join(ch for ch in clean[match.end(): match.end() + 2] if "\u4e00" <= ch <= "\u9fff")
            noun = "".join(ch for ch in noun if ch not in stop)
            key = match.group(2) + noun
            slots.setdefault(key, {}).setdefault(value, []).append((line_no, chapter_of(line_no)))

    rows: List[Tuple[int, str, List[Tuple[int, int, str]]]] = []
    for key, values in slots.items():
        summary: List[Tuple[int, int, str]] = sorted(
            (value, places[0][0], places[0][1]) for value, places in values.items())
        occurrences = sum(len(places) for places in values.values())
        rows.append((occurrences, key, summary))
        if len(values) < 2:
            continue

        def render(items: List[Tuple[int, int, str]]) -> str:
            return "、".join("%d（L%d《%s》）" % (v, n, c) for v, n, c in items)

        if key in watch:
            rep.add("slot_conflict",
                    "关注槽位「%s」出现 %d 个取值：%s" % (key, len(values), render(summary)),
                    "这是配置里声明为互锁的计数，改一处必须处处改。",
                    chapter=summary[0][2], line=summary[0][1], levels={"slot_conflict": "P1"})

    rows.sort(key=lambda r: -r[0])
    top = int(cfg.get("slot_top", 40))
    table_lines = []
    for occurrences, key, summary in rows[:top]:
        rendered = "、".join("%d（L%d）" % (v, n) for v, n, _c in summary)
        table_lines.append("%s｜%d 处｜%s" % (key, occurrences, rendered))
    if table_lines:
        rep.tables["计数槽位全表（跨章漂移多为剧情演进，但要能解释；把互锁的写进 watch_slots 才会严格查）"] = "\n".join(table_lines)


# ------------------------------------------------------------------ drivers


def run_scan(text: str, cfg: Dict[str, Any], rep: Report) -> None:
    lines = text.split("\n")
    chapters = split_chapters(text, cfg)
    check_words(lines, cfg, rep)
    check_latin(lines, cfg, rep)
    check_punctuation(lines, cfg, rep)
    check_meta_ref(lines, cfg, chapters, rep)
    check_dup_sentence(chapters, cfg, rep)
    check_opening_similarity(chapters, cfg, rep)
    check_word_count(chapters, cfg, rep)


def run_count(text: str, cfg: Dict[str, Any], rep: Report) -> None:
    lines = text.split("\n")
    chapters = split_chapters(text, cfg)
    check_claim_vs_list(lines, chapters, cfg, rep)
    check_split_sum(lines, cfg, rep)
    check_slot_conflict(lines, chapters, cfg, rep)


def render_md(rep: Report, cfg: Dict[str, Any], chapters: Sequence[Chapter]) -> str:
    counts = rep.counts()
    out: List[str] = []
    out.append("# 防复发机检报告")
    out.append("")
    out.append("- 文件：`%s`" % rep.source)
    out.append("- 章节：%d 段（含前言等非章节段落）" % len(chapters))
    out.append("- 检查工具：relapse_check.py v%s" % VERSION)
    out.append("")
    if not rep.findings:
        out.append("**结论：干净。** 机器项全部通过 —— 但请务必再过一遍 `references/relapse-guard.md` 里标 [人] 的条目，因果层机器查不了。")
        out.append("")
        return "\n".join(out)

    verdict = "发现 %d 项 P0（阻断）／%d 项 P1／%d 项 P2" % (counts["P0"], counts["P1"], counts["P2"])
    out.append("**结论：%s。**" % verdict)
    out.append("")

    for level in ("P0", "P1", "P2"):
        items = sorted([f for f in rep.findings if f.level == level], key=lambda f: f.sort_key())
        if not items:
            continue
        out.append("## %s" % level)
        out.append("")
        current = None
        for f in items:
            if f.check != current:
                current = f.check
                rid, label = CHECK_LABELS.get(f.check, ("", f.check))
                out.append("### %s %s" % (rid, label))
                out.append("")
            location = []
            if f.chapter:
                location.append("《%s》" % f.chapter)
            if f.line:
                location.append("第 %d 行" % f.line)
            suffix = "（%s）" % " ".join(location) if location else ""
            out.append("- %s%s" % (f.message, suffix))
            if f.detail:
                for detail_line in f.detail.split("\n"):
                    out.append("  - %s" % detail_line)
        out.append("")

    if rep.tables:
        out.append("## 附表")
        out.append("")
        for name, body in rep.tables.items():
            out.append("### %s" % name)
            out.append("")
            out.append("```")
            out.append(body)
            out.append("```")
            out.append("")
    return "\n".join(out)


def render_text(rep: Report) -> str:
    counts = rep.counts()
    out = ["P0=%d P1=%d P2=%d" % (counts["P0"], counts["P1"], counts["P2"])]
    for f in sorted(rep.findings, key=lambda f: f.sort_key()):
        rid, label = CHECK_LABELS.get(f.check, ("", f.check))
        out.append("[%s][%s %s] %s%s" % (
            f.level, rid, label, f.message,
            " @%s:%s" % (f.chapter, f.line) if f.line else ""))
    return "\n".join(out)


def emit(content: str, out_path: Optional[str]) -> None:
    if out_path:
        directory = os.path.dirname(os.path.abspath(out_path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        sys.stderr.write("report written to %s\n" % out_path)
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        print(content)


# ------------------------------------------------------------------ cli


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relapse_check.py",
        description="Machine checks for the relapse-guard list (see references/relapse-guard.md).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="exit codes: 0 clean | 1 P1/P2 only | 2 has P0")
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="cmd")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--file", required=True, help="markdown / text file to check")
        sp.add_argument("--config", help="JSON config overriding the defaults")
        sp.add_argument("--start-at", help="regex: heading line where the prose body starts")
        sp.add_argument("--stop-at", help="regex: first appendix heading; everything after it is ignored")
        sp.add_argument("--out", help="write the report here instead of stdout")
        sp.add_argument("--format", choices=["md", "text", "json"], default="md")

    for name, help_text in (
        ("scan", "language + structure checks (default entry point)"),
        ("count", "counting checks: claims, split sums, slot conflicts"),
        ("all", "scan + count"),
    ):
        sp = sub.add_parser(name, help=help_text)
        common(sp)

    sp = sub.add_parser("config", help="print the default config to start from")
    sp.add_argument("--out", help="write it here")

    sub.add_parser("checks", help="list every machine check and its rule id")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return EXIT_OK

    if args.cmd == "config":
        blob = json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2)
        if getattr(args, "out", None):
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(blob + "\n")
            sys.stderr.write("config written to %s\n" % args.out)
        else:
            print(blob)
        return EXIT_OK

    if args.cmd == "checks":
        rows = ["%-20s %-6s %s" % ("check", "rule", "meaning")]
        for check, (rid, label) in CHECK_LABELS.items():
            rows.append("%-20s %-6s %s" % (check, rid, label))
        print("\n".join(rows))
        return EXIT_OK

    cfg = load_config(getattr(args, "config", None))
    text = apply_range(load_file(args.file), getattr(args, "start_at", None),
                       getattr(args, "stop_at", None))
    rep = Report(os.path.basename(args.file))

    if args.cmd in ("scan", "all"):
        run_scan(text, cfg, rep)
    if args.cmd in ("count", "all"):
        run_count(text, cfg, rep)

    if args.format == "json":
        payload = {
            "file": rep.source,
            "counts": rep.counts(),
            "findings": [
                {
                    "level": f.level, "check": f.check,
                    "rule": CHECK_LABELS.get(f.check, ("", ""))[0],
                    "message": f.message, "detail": f.detail,
                    "chapter": f.chapter, "line": f.line,
                }
                for f in sorted(rep.findings, key=lambda f: f.sort_key())
            ],
        }
        emit(json.dumps(payload, ensure_ascii=False, indent=2), args.out)
    elif args.format == "text":
        emit(render_text(rep), args.out)
    else:
        emit(render_md(rep, cfg, split_chapters(text, cfg)), args.out)

    return rep.exit_code()


if __name__ == "__main__":
    sys.exit(main())
