#!/usr/bin/env python3
"""expert-watchdog - heartbeat / stall detection / recovery bookkeeping for expert & expert-team runs.

Pure standard library, cross-platform (Windows / macOS / Linux).

Design notes
------------
* Progress is recorded as *absolute timestamps*. Because every beat stores wall-clock
  time, a stall can be detected at any later moment without a resident daemon.
* Two stall signals:
    - SILENCE : last beat older than the node's stall budget (no output at all).
    - SPIN    : beats keep arriving but the reported progress does not change
                (same note and same artifact set) for longer than the budget.
* Recovery is graded L0 -> L4. The CLI only *suggests* the next grade based on how
  many attempts a node already consumed; the orchestrating agent decides and reports.

State layout
------------
<state-dir>/<run-id>/state.json     run + node state machine
<state-dir>/<run-id>/alerts.jsonl   append-only alert log
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

VERSION = "1.0.0"

# Default stall budget (seconds) per node kind. Override per node with --stall-seconds.
DEFAULT_STALL: Dict[str, int] = {
    "orchestrate": 120,   # team-lead planning / dispatch
    "research": 150,      # web search / retrieval heavy
    "review": 240,        # reading and critiquing long material
    "write": 300,         # long-form generation
    "code": 240,          # editing / running code
    "summarize": 180,     # condensation / report assembly
    "default": 180,
}

# Recovery ladder. attempts_consumed -> (strategy, action for the orchestrator)
RECOVERY_LADDER: List[Dict[str, str]] = [
    {
        "level": "L0",
        "name": "nudge",
        "action": "Ping the running agent once (SendMessage / one more TaskOutput poll) "
                  "asking for a progress report. Wait one grace window.",
        "when": "First suspicion, node may simply be slow.",
    },
    {
        "level": "L1",
        "name": "restart-with-checkpoint",
        "action": "Stop the node (TaskStop) and re-dispatch the SAME expert role with the "
                  "checkpoint: what is done, what remains, artifacts already produced.",
        "when": "Nudge produced nothing; restart from the last good checkpoint.",
    },
    {
        "level": "L2",
        "name": "degrade-and-split",
        "action": "Re-dispatch with a smaller contract: split the node into 2-3 subtasks, "
                  "lower max_turns, switch to a faster model, drop non-essential tools.",
        "when": "Restart stalls again - the task itself is too big or too vague.",
    },
    {
        "level": "L3",
        "name": "substitute-role",
        "action": "Hand the node to a different member of the team (or let team-lead execute "
                  "it directly) and mark the deliverable as degraded.",
        "when": "Same role stalls repeatedly - suspect the role/task fit, not the runtime.",
    },
    {
        "level": "L4",
        "name": "skip-and-escalate",
        "action": "Mark the node skipped, continue the rest of the pipeline, and surface the "
                  "gap to the user for a decision.",
        "when": "Budget exhausted; do not burn the whole run on one node.",
    },
]


# --------------------------------------------------------------------------- utils


def now() -> float:
    return time.time()


def iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def humanize(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return "%ds" % sec
    m, s = divmod(sec, 60)
    if m < 60:
        return "%dm%02ds" % (m, s)
    h, m = divmod(m, 60)
    return "%dh%02dm" % (h, m)


def default_state_dir() -> str:
    env = os.environ.get("WATCHDOG_STATE_DIR")
    if env:
        return env
    return os.path.join(os.getcwd(), ".workbuddy", "watchdog")


def state_path(state_dir: str, run_id: str) -> str:
    return os.path.join(state_dir, run_id, "state.json")


def alerts_path(state_dir: str, run_id: str) -> str:
    return os.path.join(state_dir, run_id, "alerts.jsonl")


def load_state(state_dir: str, run_id: str) -> Dict[str, Any]:
    p = state_path(state_dir, run_id)
    if not os.path.exists(p):
        sys.exit("error: run '%s' not found (looked at %s). Run `init` first." % (run_id, p))
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_dir: str, state: Dict[str, Any]) -> None:
    p = state_path(state_dir, state["run_id"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def append_alert(state_dir: str, run_id: str, alert: Dict[str, Any]) -> None:
    p = alerts_path(state_dir, run_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(alert, ensure_ascii=False) + "\n")


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    state_dir = args.state_dir
    run_dir = os.path.join(state_dir, args.run_id)
    os.makedirs(run_dir, exist_ok=True)
    state: Dict[str, Any] = {
        "run_id": args.run_id,
        "task": args.task,
        "expert": args.expert,
        "team": args.team or [],
        "created_at": now(),
        "updated_at": now(),
        "status": "running",
        "budget": {
            "max_node_attempts": args.max_attempts,
            "max_run_minutes": args.max_run_minutes,
            "grace_seconds": args.grace_seconds,
        },
        "nodes": {},
    }
    save_state(state_dir, state)
    emit({
        "ok": True,
        "run_id": args.run_id,
        "state_file": state_path(state_dir, args.run_id),
        "hint": "Register each role/stage with `node add`, then `beat` whenever it produces "
                "output, then `check` to find stalls.",
    })
    return 0


def cmd_node_add(args: argparse.Namespace) -> int:
    state = load_state(args.state_dir, args.run_id)
    stall = args.stall_seconds if args.stall_seconds else DEFAULT_STALL.get(args.kind, DEFAULT_STALL["default"])
    if args.node_id in state["nodes"]:
        node = state["nodes"][args.node_id]
        if node["status"] == "running":
            node["attempts"] = node.get("attempts", 0) + 1
            node["started_at"] = now()
            node["last_beat_at"] = now()
            node.setdefault("history", []).append({
                "attempt": node["attempts"],
                "at": now(),
                "event": "redispatch",
                "kind": args.kind,
            })
            emit({"ok": True, "node_id": args.node_id, "event": "redispatch",
                  "attempts": node["attempts"], "message": "Node re-registered (restarted)."})
            state["updated_at"] = now()
            save_state(args.state_dir, state)
            return 0
    node = {
        "node_id": args.node_id,
        "role": args.role,
        "kind": args.kind,
        "task_id": args.task_id,
        "status": "running",
        "started_at": now(),
        "last_beat_at": now(),
        "beats": 0,
        "last_note": "",
        "artifacts": [],
        "depends_on": args.depends_on or [],
        "stall_seconds": stall,
        "grace_seconds": args.grace_seconds,
        "attempts": 0,
        "max_attempts": args.max_attempts,
        "history": [{"attempt": 0, "at": now(), "event": "start", "note": args.note or ""}],
    }
    state["nodes"][args.node_id] = node
    state["updated_at"] = now()
    save_state(args.state_dir, state)
    emit({"ok": True, "node_id": args.node_id, "stall_seconds": stall,
          "message": "Registered. Beat this node whenever it produces output."})
    return 0


def cmd_beat(args: argparse.Namespace) -> int:
    state = load_state(args.state_dir, args.run_id)
    node = state["nodes"].get(args.node_id)
    if node is None:
        sys.exit("error: unknown node '%s' for run '%s'" % (args.node_id, args.run_id))
    ts = now()
    prev_note = node.get("last_note", "")
    prev_artifacts = node.get("artifacts", [])
    node["last_beat_at"] = ts
    node["beats"] = node.get("beats", 0) + 1
    if args.note:
        node["last_note"] = args.note
    if args.artifact:
        for a in args.artifact:
            if a not in node["artifacts"]:
                node["artifacts"].append(a)
    # spin detection: note unchanged AND artifact set unchanged
    if args.note and args.note == prev_note and node["artifacts"] == prev_artifacts:
        node["spin_since"] = node.get("spin_since", ts)
    else:
        node.pop("spin_since", None)
    if args.progress is not None:
        node["progress"] = args.progress
    node.setdefault("history", []).append({
        "at": ts, "event": "beat", "note": args.note or "", "artifact": args.artifact or [],
    })
    state["updated_at"] = ts
    save_state(args.state_dir, state)
    emit({"ok": True, "node_id": args.node_id, "beats": node["beats"], "beat_at": iso(ts)})
    return 0


def evaluate(node: Dict[str, Any], ts: float, grace: int) -> Dict[str, Any]:
    """Return the health verdict for one node."""
    stall = node.get("stall_seconds", DEFAULT_STALL["default"])
    node_grace = node.get("grace_seconds", grace)
    silence = ts - node.get("last_beat_at", node.get("started_at", ts))
    spin_for = ts - node.get("spin_since", ts) if node.get("spin_since") else 0.0
    verdict = "healthy"
    reason = ""
    if silence > stall:
        verdict = "stuck"
        reason = "silence %s > budget %s (no output since %s)" % (
            humanize(silence), humanize(stall), iso(node.get("last_beat_at", ts)))
    elif spin_for > stall:
        verdict = "spinning"
        reason = "no new progress for %s (repeating the same note/artifacts)" % humanize(spin_for)
    elif silence > stall * 0.7:
        verdict = "at-risk"
        reason = "silence %s approaching budget %s" % (humanize(silence), humanize(stall))
    attempts = node.get("attempts", 0)
    max_attempts = node.get("max_attempts", 3)
    idx = min(attempts, len(RECOVERY_LADDER) - 1)
    rung = RECOVERY_LADDER[idx]
    out = {
        "node_id": node["node_id"],
        "role": node.get("role"),
        "kind": node.get("kind"),
        "status": node.get("status"),
        "verdict": verdict,
        "reason": reason,
        "silence": round(silence, 1),
        "silence_h": humanize(silence),
        "stall_budget": stall,
        "beats": node.get("beats", 0),
        "attempts": attempts,
        "max_attempts": max_attempts,
        "budget_left": max(0, max_attempts - attempts),
        "last_note": node.get("last_note", ""),
        "artifacts": node.get("artifacts", []),
        "grace_seconds": node_grace,
    }
    if verdict in ("stuck", "spinning"):
        if attempts >= max_attempts:
            out["suggested"] = RECOVERY_LADDER[-1]
            out["note"] = "Attempt budget exhausted -> escalate to the user."
        else:
            out["suggested"] = rung
    return out


def cmd_check(args: argparse.Namespace) -> int:
    state = load_state(args.state_dir, args.run_id)
    ts = now()
    grace = state.get("budget", {}).get("grace_seconds", 60)
    results = []
    for node in state["nodes"].values():
        if node.get("status") != "running" and not args.all:
            continue
        results.append(evaluate(node, ts, grace))
    alerts = [r for r in results if r["verdict"] in ("stuck", "spinning")
              and r.get("status") == "running"]
    for a in alerts:
        append_alert(args.state_dir, args.run_id, {
            "at": ts, "at_iso": iso(ts), "run_id": args.run_id,
            "node_id": a["node_id"], "role": a["role"], "verdict": a["verdict"],
            "reason": a["reason"], "attempts": a["attempts"],
            "suggested_level": a.get("suggested", {}).get("level"),
        })
    state["updated_at"] = ts
    state["last_check_at"] = ts
    save_state(args.state_dir, state)
    payload: Dict[str, Any] = {
        "run_id": args.run_id,
        "checked_at": iso(ts),
        "running": len(results),
        "stalled": len(alerts),
        "nodes": results,
    }
    emit(payload)
    if alerts:
        print("\nACTION REQUIRED", file=sys.stderr)
        for a in alerts:
            sug = a.get("suggested", {})
            print("  - %s (%s): %s -> %s %s: %s" % (
                a["node_id"], a["role"], a["reason"], sug.get("level", "?"),
                sug.get("name", ""), sug.get("action", a.get("note", ""))), file=sys.stderr)
    return 1 if alerts else 0


def cmd_attempt(args: argparse.Namespace) -> int:
    """Record that a recovery action was taken; returns whether budget remains."""
    state = load_state(args.state_dir, args.run_id)
    node = state["nodes"].get(args.node_id)
    if node is None:
        sys.exit("error: unknown node '%s'" % args.node_id)
    node["attempts"] = node.get("attempts", 0) + 1
    node.setdefault("history", []).append({
        "attempt": node["attempts"], "at": now(), "event": "recovery",
        "strategy": args.strategy, "note": args.note or "",
    })
    if args.reset:
        node["last_beat_at"] = now()
        node.pop("spin_since", None)
        node["status"] = "running"
    state["updated_at"] = now()
    save_state(args.state_dir, state)
    left = max(0, node.get("max_attempts", 3) - node["attempts"])
    nxt = RECOVERY_LADDER[min(node["attempts"], len(RECOVERY_LADDER) - 1)]
    emit({
        "ok": True, "node_id": args.node_id, "attempts": node["attempts"],
        "budget_left": left, "next_strategy": nxt,
        "message": "Budget exhausted - escalate to user." if left == 0 else
                   "Re-dispatch now; if it stalls again use %s." % nxt["level"],
    })
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    state = load_state(args.state_dir, args.run_id)
    node = state["nodes"].get(args.node_id)
    if node is None:
        sys.exit("error: unknown node '%s'" % args.node_id)
    node["status"] = args.outcome
    node["resolved_at"] = now()
    node.setdefault("history", []).append(
        {"at": now(), "event": "resolve", "outcome": args.outcome, "note": args.note or ""})
    if args.artifact:
        for a in args.artifact:
            if a not in node["artifacts"]:
                node["artifacts"].append(a)
    state["updated_at"] = now()
    pending = [n for n in state["nodes"].values() if n.get("status") == "running"]
    if not pending:
        state["status"] = "completed" if all(
            n.get("status") == "done" for n in state["nodes"].values()) else "finished-with-gaps"
    save_state(args.state_dir, state)
    emit({"ok": True, "node_id": args.node_id, "outcome": args.outcome,
          "pending_nodes": [n["node_id"] for n in pending]})
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    state = load_state(args.state_dir, args.run_id)
    ts = now()
    grace = state.get("budget", {}).get("grace_seconds", 60)
    lines: List[str] = []
    lines.append("# Expert run watchdog report")
    lines.append("")
    lines.append("- run_id: `%s`" % state["run_id"])
    lines.append("- task: %s" % state.get("task", ""))
    lines.append("- expert: %s" % (state.get("expert") or "-"))
    if state.get("team"):
        lines.append("- team: %s" % ", ".join(state["team"]))
    lines.append("- generated: %s" % iso(ts))
    lines.append("- run status: **%s**" % state.get("status"))
    lines.append("")
    lines.append("| node | role | kind | status | silence | beats | attempts | verdict |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for node in state["nodes"].values():
        r = evaluate(node, ts, grace)
        lines.append("| %s | %s | %s | %s | %s | %s | %d/%d | %s |" % (
            node["node_id"], node.get("role", ""), node.get("kind", ""), node.get("status"),
            r["silence_h"], node.get("beats", 0), node.get("attempts", 0),
            node.get("max_attempts", 3), r["verdict"]))
    lines.append("")
    stalled = [n for n in state["nodes"].values()
               if n.get("status") == "running"
               and evaluate(n, ts, grace)["verdict"] in ("stuck", "spinning")]
    if stalled:
        lines.append("## Stalled nodes")
        lines.append("")
        for n in stalled:
            r = evaluate(n, ts, grace)
            sug = r.get("suggested", {})
            lines.append("### %s (%s)" % (n["node_id"], n.get("role", "")))
            lines.append("")
            lines.append("- reason: %s" % r["reason"])
            lines.append("- last note: %s" % (n.get("last_note") or "(none)"))
            lines.append("- artifacts so far: %s" % (", ".join(n.get("artifacts", [])) or "(none)"))
            lines.append("- attempts: %d/%d" % (n.get("attempts", 0), n.get("max_attempts", 3)))
            lines.append("- next action: **%s %s** - %s" % (
                sug.get("level", "?"), sug.get("name", ""), sug.get("action", "")))
            lines.append("")
    gaps = [n for n in state["nodes"].values() if n.get("status") in ("skipped", "failed")]
    if gaps:
        lines.append("## Delivered with gaps")
        lines.append("")
        for n in gaps:
            lines.append("- %s (%s): %s" % (n["node_id"], n.get("role", ""),
                                            n.get("history", [{}])[-1].get("note", "")))
        lines.append("")
    artifacts: List[str] = []
    for n in state["nodes"].values():
        artifacts.extend(n.get("artifacts", []))
    if artifacts:
        lines.append("## Artifacts")
        lines.append("")
        for a in artifacts:
            lines.append("- %s" % a)
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("report written: %s" % args.out)
    else:
        print(text)
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Poll loop: prints and logs an alert the first time each node stalls at a given attempt."""
    deadline = now() + args.max_runtime
    seen: Dict[str, str] = {}
    print("watchdog daemon: run=%s poll=%ss max_runtime=%ss" % (
        args.run_id, args.poll, args.max_runtime), flush=True)
    while now() < deadline:
        state = load_state(args.state_dir, args.run_id)
        ts = now()
        grace = state.get("budget", {}).get("grace_seconds", 60)
        any_alert = False
        for node in state["nodes"].values():
            if node.get("status") != "running":
                continue
            r = evaluate(node, ts, grace)
            if r["verdict"] not in ("stuck", "spinning"):
                continue
            key = "%s@%d" % (node["node_id"], node.get("attempts", 0))
            if seen.get(node["node_id"]) == key:
                continue
            seen[node["node_id"]] = key
            sug = r.get("suggested", {})
            msg = "[%s] %s (%s) %s -> %s %s" % (
                iso(ts), node["node_id"], node.get("role", ""), r["reason"],
                sug.get("level", "?"), sug.get("name", ""))
            print("ALERT " + msg, flush=True)
            append_alert(args.state_dir, args.run_id, {
                "at": ts, "at_iso": iso(ts), "run_id": args.run_id,
                "node_id": node["node_id"], "role": node.get("role"),
                "verdict": r["verdict"], "reason": r["reason"],
                "attempts": node.get("attempts", 0),
                "suggested_level": sug.get("level"), "source": "daemon",
            })
            any_alert = True
        if not any_alert and args.heartbeat:
            print("[%s] ok - no stalls" % iso(ts), flush=True)
        if all(n.get("status") != "running" for n in state["nodes"].values()):
            print("all nodes finished - daemon exiting", flush=True)
            break
        time.sleep(args.poll)
    print("daemon stopped", flush=True)
    return 0


def cmd_ladder(args: argparse.Namespace) -> int:
    emit({"ladder": RECOVERY_LADDER, "default_stall_seconds": DEFAULT_STALL})
    return 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watchdog.py",
        description="Heartbeat, stall detection and recovery bookkeeping for expert / expert-team runs.")
    p.add_argument("--state-dir", default=default_state_dir(),
                   help="where run state is stored (default: $WATCHDOG_STATE_DIR or ./.workbuddy/watchdog)")
    p.add_argument("--version", action="version", version="expert-watchdog %s" % VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a run")
    s.add_argument("--run-id", required=True)
    s.add_argument("--task", required=True, help="what this expert run is supposed to deliver")
    s.add_argument("--expert", default=None, help="expert or expert-team name")
    s.add_argument("--team", action="append", default=None, help="team member role (repeatable)")
    s.add_argument("--max-attempts", type=int, default=3)
    s.add_argument("--max-run-minutes", type=int, default=60)
    s.add_argument("--grace-seconds", type=int, default=60,
                   help="wait window after a nudge before judging it failed")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("node", help="register or re-register a node (role/stage)")
    s.add_argument("action", choices=["add"], nargs="?", default="add")
    s.add_argument("--run-id", required=True)
    s.add_argument("--node-id", required=True)
    s.add_argument("--role", required=True)
    s.add_argument("--kind", default="default", choices=sorted(DEFAULT_STALL.keys()))
    s.add_argument("--task-id", default=None, help="background agent/task id if dispatched as subagent")
    s.add_argument("--depends-on", action="append", default=None)
    s.add_argument("--stall-seconds", type=int, default=None)
    s.add_argument("--grace-seconds", type=int, default=60)
    s.add_argument("--max-attempts", type=int, default=3)
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_node_add)

    s = sub.add_parser("beat", help="record progress for a node")
    s.add_argument("--run-id", required=True)
    s.add_argument("--node-id", required=True)
    s.add_argument("--note", default=None, help="short description of what just happened")
    s.add_argument("--artifact", action="append", default=None, help="file produced (repeatable)")
    s.add_argument("--progress", type=float, default=None, help="0-100 if you can estimate it")
    s.set_defaults(func=cmd_beat)

    s = sub.add_parser("check", help="find stalled nodes; exit code 1 when any is stalled")
    s.add_argument("--run-id", required=True)
    s.add_argument("--all", action="store_true", help="include finished nodes")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("attempt", help="record a recovery action taken on a node")
    s.add_argument("--run-id", required=True)
    s.add_argument("--node-id", required=True)
    s.add_argument("--strategy", required=True, help="L0 nudge | L1 restart | L2 degrade | L3 substitute | L4 skip")
    s.add_argument("--note", default=None)
    s.add_argument("--reset", action="store_true", help="reset the stall clock after re-dispatch")
    s.set_defaults(func=cmd_attempt)

    s = sub.add_parser("resolve", help="close a node")
    s.add_argument("--run-id", required=True)
    s.add_argument("--node-id", required=True)
    s.add_argument("--outcome", required=True, choices=["done", "failed", "skipped"])
    s.add_argument("--note", default=None)
    s.add_argument("--artifact", action="append", default=None)
    s.set_defaults(func=cmd_resolve)

    s = sub.add_parser("report", help="markdown summary of the run")
    s.add_argument("--run-id", required=True)
    s.add_argument("--out", default=None, help="write to file instead of stdout")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("daemon", help="poll loop that logs alerts (optional)")
    s.add_argument("--run-id", required=True)
    s.add_argument("--poll", type=int, default=30)
    s.add_argument("--max-runtime", type=int, default=1800)
    s.add_argument("--heartbeat", action="store_true", help="print an ok line every poll")
    s.set_defaults(func=cmd_daemon)

    s = sub.add_parser("ladder", help="print the recovery ladder and default thresholds")
    s.set_defaults(func=cmd_ladder)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
