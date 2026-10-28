#!/usr/bin/env python3
"""Merge-gate helper: read the reviewer's answer, parse its verdict and one_way lines, merge.

    merge_gate.py extract <gate.stream.jsonl> <gate.md>
    merge_gate.py parse <gate.md>
    merge_gate.py merge [--builder-bot NAME] <pr_url> <out_dir>
    merge_gate.py pick <gate-route.json>
    merge_gate.py cursor-result|claude-result|plain-result|muse-result <reviewer output> <gate.md>

The reviewer ends its answer with `one_way: yes` or `one_way: no`, and on yes
one `one_way_at: <file>:<line> <what>` line per step a git revert cannot undo.
Lines inside code fences are quotes and do not count. A line counts only when
its first token is `one_way:` or `one_way_at:`; prose that mentions one_way is
ignored, and a directive with a value other than yes/no is unparseable.

The verdict is a line, outside code fences, that is exactly `approve`,
`request-changes`, or `comment` (lowercase), alone on the line. It may end in
one `.` or `:`, or carry a glued `Findings` / `Findings:` header
(`approveFindings:`), and nothing else. A preamble before it is fine. A name
that starts with the word (`approveRequest`, `commentOn`) is not a verdict. Prose that only mentions approve
is not a verdict. No verdict line, or two different ones, records verdict null
and a verdict_error. review writes the parse to gate-verdict.json.

merge fails closed. It refuses (exit 3, nothing merged) unless gate-verdict.json
records verdict approve and gate.md still parses to that same verdict. It also
refuses when the one_way verdict is missing, conflicting, or malformed, when gate-head.txt is missing, when
the PR has the `one-way` label, when gh cannot read the labels, or when
gate-route.json is missing, unreadable, or records no builder vendor, no
reviewer vendor, or the same vendor for both. Every reason is printed.
Otherwise it runs `gh pr merge <pr> --squash --match-head-commit <reviewed
sha>` and returns gh's exit code. MERGE_GATE_GH overrides the gh binary.

A one_way: yes verdict merges only with Ryan's yes recorded in
<out_dir>/acks/one-way-ack.json, and then the `one-way` label does not
refuse. The ack is JSON with pr_url, head_sha (40 hex), one_way_items (the
{at, what} list copied from gate-verdict.json), ryan_words (verbatim), source
({chat, message_or_card_id}), writer (the bot that wrote it), and written_at
(ISO 8601 with a UTC offset). merge refuses unless the ack is well formed, its
pr_url is this PR, its head_sha is gate-head.txt and the PR's live head, its
items are exactly gate-verdict.json's (which must match gate.md's), and its
writer is not the builder bot. The builder bot comes from builder-bot.txt,
which review writes, or from --builder-bot for an older gate dir; both given
and different, or neither, refuses. After gh merges, the ack moves to
acks/used/<UTC time>-one-way-ack.json, so it cannot authorize again, and the
merge note that quotes it is printed and written to merge-note.md.

pick reads the `seat-usage --route review --json` output and prints one JSON
line, {"model": ..., "runner": [argv...]}. The argv is the top-level `runner`
list, else reviewers[0].runner. When the route asked, named no reviewer, has
no argv list (an older seat-usage), the model or argv is unsafe, or the
recorded builder vendor and reviewer vendor are missing or the same, it prints
one line saying why and exits 3. grok and xai are the same vendor.

cursor-result, claude-result, plain-result, and muse-result write the
reviewer's answer to gate.md from a cursor-agent stream, a claude JSON result,
a plain file, or a `muse exec --json` JSONL stream, and exit 1 when there is
no answer.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from trailer_vendor import normalize_vendor

LABEL = "one-way"
REFUSED = 3
# A directive is a line whose first token is one_way: or one_way_at: (colon
# required). A garbled value after the colon is unparseable; prose is not a directive.
VERDICT = re.compile(r"^(?i:one_way)\s*:(.*)$")
# The value is lowercase yes|no. It may end the line (trailing spaces allowed),
# be followed by . : ) ] or be glued to an uppercase letter, so "noThe ..."
# parses. Whitespace, a comma or a semicolon then more text, or a lowercase
# letter is unparseable: nothing, nope, none, "no longer", "no, actually yes".
VALUE = re.compile(r"\s*(yes|no)(?=\s*$|[.:)\]]|[A-Z])")
# The reviewer's verdict: the word alone on its line, optionally followed by a
# glued "Findings" / "Findings:" header (cursor-agent joins turns with no
# separator) or one "." or ":", then end of line. Anything else after the word
# ("approveRequest", "comment: a note", "approve, mostly") is not a verdict.
VERDICTS = ("approve", "request-changes", "comment")
VERDICT_WORD = re.compile(r"^(approve|request-changes|comment)(?:Findings:?|[.:])?\s*$")
DIRECTIVE_AT = re.compile(r"^one_way_at\s*:", re.I)
ITEM = re.compile(r"^one_way_at\s*:\s*(\S+:\d+)\s*(.*)$", re.I)


def _clean(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^(?:[-*>]\s+)+", "", s)
    return s.replace("`", "").replace("**", "").strip()


def parse(text: str) -> dict:
    """{"verdict", "verdict_error", "one_way", "items", "error"}.

    verdict is approve|request-changes|comment or None with verdict_error set.
    one_way is "yes"|"no" or None with error set.
    """
    verdicts: list[str] = []
    values: list[str] = []
    items: list[dict] = []
    bad: list[str] = []
    fence = False
    for raw in text.splitlines():
        if raw.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        line = _clean(raw)
        w = VERDICT_WORD.match(line)
        if w:
            verdicts.append(w.group(1))
            continue
        m = ITEM.match(line)
        if m:
            items.append({"at": m.group(1), "what": m.group(2).strip()})
            continue
        if DIRECTIVE_AT.match(line):
            bad.append(line)
            continue
        m = VERDICT.match(line)
        if m:
            v = VALUE.match(m.group(1))
            if v:
                values.append(v.group(1))
            else:
                bad.append(line)
        # Anything else that only mentions one_way (prose, a bullet) is ignored.
    out: dict = {"verdict": None, "verdict_error": None,
                 "one_way": None, "items": items, "error": None}
    if not verdicts:
        out["verdict_error"] = "no approve|request-changes|comment line in the gate answer"
    elif len(set(verdicts)) > 1:
        out["verdict_error"] = "conflicting verdict lines: " + ", ".join(dict.fromkeys(verdicts))
    else:
        out["verdict"] = verdicts[0]
    if bad:
        out["error"] = "unparseable one_way line: " + "; ".join(repr(b) for b in bad)
    elif not values:
        out["error"] = "no one_way: yes|no line in the gate answer"
    elif len(set(values)) > 1:
        out["error"] = "conflicting one_way lines: " + ", ".join(values)
    elif values[0] == "no" and items:
        out["error"] = "one_way: no but one_way_at lines are listed"
    else:
        out["one_way"] = values[0]
    return out


def extract(stream: str, md: str) -> int:
    init = res = None
    try:
        lines = Path(stream).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"merge-gate: cannot read {stream}: {e}", file=sys.stderr)
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("event") == "init" and init is None:
            init = e.get("init")
        if e.get("event") == "result":
            res = e.get("result")
    init = init if isinstance(init, dict) else {}
    res = res if isinstance(res, dict) else {}
    r = res.get("response") or ""
    Path(md).write_text(r, encoding="utf-8")
    print("init agent:", init.get("agent"), "| status:", res.get("status"),
          "| denied:", res.get("denied_actions"))
    print("response chars:", len(r))
    if res.get("status") != "SUCCESS" or res.get("denied_actions") or not r.strip():
        print("merge-gate: the reviewer did not return a clean answer", file=sys.stderr)
        return 1
    return 0


def verdict_reasons(path: Path, parsed: dict) -> list[str]:
    """Empty only when gate-verdict.json records approve and gate.md agrees."""
    if not path.is_file():
        return [f"no gate-verdict.json at {path} (run merge-gate.sh review first)"]
    try:
        rec = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return ["gate-verdict.json is unreadable"]
    recorded = rec.get("verdict") if isinstance(rec, dict) else None
    if recorded is None:
        why = rec.get("verdict_error") if isinstance(rec, dict) else None
        return ["gate-verdict.json records no verdict" + (f": {why}" if why else "")]
    if recorded not in VERDICTS:
        return [f"gate-verdict.json records an unknown verdict {recorded!r}"]
    if recorded != parsed["verdict"]:
        return [f"gate-verdict.json says {recorded} but gate.md parses as {parsed['verdict']}"]
    if recorded != "approve":
        return [f"gate verdict is {recorded}, not approve"]
    return []


def pr_labels(gh: str, pr: str) -> tuple[list[str] | None, str]:
    try:
        p = subprocess.run([gh, "pr", "view", pr, "--json", "labels"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh pr view failed: {e}"
    if p.returncode != 0:
        return None, f"gh pr view exited {p.returncode}: {p.stderr.strip()[-300:]}"
    try:
        data = json.loads(p.stdout)
        return [str(l["name"]) for l in data["labels"]], ""
    except (ValueError, KeyError, TypeError) as e:
        return None, f"gh pr view returned no labels list: {e}"


ACK = Path("acks") / "one-way-ack.json"
USED = Path("acks") / "used"
SHA = re.compile(r"[0-9a-f]{40}")


def _text(v: object) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _same_bot(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _step(it: dict) -> str:
    return f"{it['at']} {it['what']}".rstrip()


def builder_bot(out: Path, flag: str | None) -> tuple[str | None, str]:
    """(name, "") from builder-bot.txt or --builder-bot, else (None, why)."""
    f = out / "builder-bot.txt"
    recorded = f.read_text(encoding="utf-8", errors="replace").strip() if f.is_file() else ""
    given = (flag or "").strip()
    if recorded and given and not _same_bot(recorded, given):
        return None, f"--builder-bot {given} does not match builder-bot.txt ({recorded})"
    if recorded or given:
        return recorded or given, ""
    return None, ("the builder bot is unknown: re-run review with the builder bot, "
                  "or pass --builder-bot NAME for this older gate dir")


def read_ack(path: Path) -> tuple[dict | None, list[str]]:
    """(ack, []) when every field is present with the right type, else (None, reasons)."""
    try:
        a = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None, ["one-way ack is malformed: not JSON"]
    if not isinstance(a, dict):
        return None, ["one-way ack is malformed: not a JSON object"]
    bad: list[str] = []
    for k in ("pr_url", "ryan_words", "writer"):
        if not _text(a.get(k)):
            bad.append(f"{k} is missing or not a non-empty string")
    if not (isinstance(a.get("head_sha"), str) and SHA.fullmatch(a["head_sha"])):
        bad.append("head_sha is not a 40-hex sha")
    src = a.get("source")
    for k in ("chat", "message_or_card_id"):
        if not (isinstance(src, dict) and _text(src.get(k))):
            bad.append(f"source.{k} is missing or not a non-empty string")
    items = a.get("one_way_items")
    if not isinstance(items, list) or not items:
        bad.append("one_way_items is missing or not a non-empty list")
    else:
        for i, it in enumerate(items):
            if not (isinstance(it, dict) and set(it) == {"at", "what"}
                    and all(isinstance(it[k], str) for k in it)):
                bad.append(f"one_way_items[{i}] is not {{at, what}} strings")
    try:
        ok = datetime.fromisoformat(a["written_at"]).tzinfo is not None
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        bad.append("written_at is not an ISO timestamp with a UTC offset")
    if bad:
        return None, [f"one-way ack is malformed: {b}" for b in bad]
    return a, []


def ack_reasons(gh: str, pr: str, out: Path, head: str, items: list[dict],
                flag: str | None) -> tuple[dict | None, list[str]]:
    """(ack, []) when acks/one-way-ack.json authorizes this one_way: yes merge."""
    reasons: list[str] = []
    builder, why = builder_bot(out, flag)
    if builder is None:
        reasons.append(why)
    try:
        rec = json.loads((out / "gate-verdict.json").read_text(encoding="utf-8", errors="replace"))
        recorded = rec.get("items") if isinstance(rec, dict) else None
    except (OSError, ValueError):
        recorded = None
    if recorded != items:
        reasons.append("gate-verdict.json one_way items differ from gate.md")
    path = out / ACK
    if not path.is_file():
        used = len(list((out / USED).glob("*-one-way-ack.json"))) if (out / USED).is_dir() else 0
        reasons.append(f"no one-way ack at {path}" + (
            f"; acks/used/ holds {used} used ack{'s' if used != 1 else ''}, "
            "and a used ack cannot authorize a merge again" if used else ""))
        return None, reasons
    ack, bad = read_ack(path)
    if ack is None:
        return None, reasons + bad
    if ack["pr_url"].strip() != pr.strip():
        reasons.append(f"one-way ack is for {ack['pr_url']}, not {pr}")
    if ack["head_sha"] != head:
        reasons.append(f"one-way ack head_sha {ack['head_sha']} is not the reviewed head {head} (gate-head.txt)")
    live, err = pr_head(gh, pr)
    if live is None:
        reasons.append(f"cannot read the PR's live head: {err}")
    elif live != head:
        reasons.append(f"the PR head is now {live}, not the reviewed head {head}")
    want = Counter((it["at"], it["what"]) for it in items)
    got = Counter((it["at"], it["what"]) for it in ack["one_way_items"])
    reasons += [f"one-way step missing from the ack: {at} {what}".rstrip() for at, what in (want - got).elements()]
    reasons += [f"one-way ack lists a step the gate did not: {at} {what}".rstrip() for at, what in (got - want).elements()]
    if builder is not None and _same_bot(ack["writer"], builder):
        reasons.append(f"one-way ack writer '{ack['writer']}' is the builder bot {builder}")
    return (None if reasons else ack), reasons


def pr_head(gh: str, pr: str) -> tuple[str | None, str]:
    try:
        p = subprocess.run([gh, "pr", "view", pr, "--json", "headRefOid", "--jq", ".headRefOid"],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh pr view failed: {e}"
    if p.returncode != 0:
        return None, f"gh pr view exited {p.returncode}: {p.stderr.strip()[-300:]}"
    sha = p.stdout.strip()
    return (sha, "") if SHA.fullmatch(sha) else (None, f"gh pr view returned no head sha: {sha[:80]!r}")


def merge_note(ack: dict, items: list[dict], used: Path) -> str:
    src = ack["source"]
    lines = [f"Ryan's yes: \"{ack['ryan_words']}\"",
             f"Source: {src['chat']} message {src['message_or_card_id']}, "
             f"written by {ack['writer']} at {ack['written_at']}.",
             f"Ack: {used}",
             "One-way steps acknowledged:"]
    return "\n".join(lines + [f"- {_step(it)}" for it in items]) + "\n"


def use_ack(out: Path) -> Path:
    """Move acks/one-way-ack.json to acks/used/<UTC time>-one-way-ack.json."""
    (out / USED).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out / USED / f"{stamp}-one-way-ack.json"
    n = 1
    while dest.exists():
        n += 1
        dest = out / USED / f"{stamp}-{n}-one-way-ack.json"
    (out / ACK).rename(dest)
    return dest


def merge(pr: str, out_dir: str, flag: str | None = None) -> int:
    gh = os.environ.get("MERGE_GATE_GH") or "/usr/local/bin/gh"
    out = Path(out_dir)
    reasons: list[str] = []
    items: list[dict] = []
    one_way = None
    md = out / "gate.md"
    if not md.is_file():
        reasons.append(f"no gate answer at {md} (run merge-gate.sh review first)")
    else:
        v = parse(md.read_text(encoding="utf-8", errors="replace"))
        items = v["items"]
        one_way = v["one_way"]
        reasons.extend(verdict_reasons(out / "gate-verdict.json", v))
        if v["error"]:
            reasons.append(f"gate verdict is missing or unparseable: {v['error']}")
    head = ""
    hf = out / "gate-head.txt"
    if hf.is_file():
        head = hf.read_text(encoding="utf-8").strip()
    if not SHA.fullmatch(head):
        reasons.append(f"no reviewed head sha in {hf}")
    ack = None
    if one_way == "yes":
        ack, why = ack_reasons(gh, pr, out, head, items, flag)
        if why:
            reasons.append("gate verdict is one_way: yes"
                           + ("" if items else " (the reviewer listed no file:line)")
                           + f", and no valid one-way ack authorizes it ({ACK})")
            reasons.extend(why)
    labels, err = pr_labels(gh, pr)
    if labels is None:
        reasons.append(f"cannot read the PR labels, so the one-way label is unknown: {err}")
    elif any(l.lower() == LABEL for l in labels) and ack is None:
        reasons.append(f"the PR has the `{LABEL}` label")
    reasons.extend(route_file_reasons(out / "gate-route.json"))
    if reasons:
        print(f"merge-gate: REFUSED to merge {pr}", file=sys.stderr)
        for r in reasons:
            print(f"reason: {r}", file=sys.stderr)
        for it in items:
            print(f"  one-way step: {_step(it)}", file=sys.stderr)
        print("A step that a git revert cannot undo, or a PR whose one-way status is "
              f"unknown, needs Ryan's explicit yes recorded in {out / ACK} by a bot other "
              "than the builder (see README). Nothing was merged.", file=sys.stderr)
        return REFUSED
    if ack is None:
        print(f"merge-gate: approve, one_way: no, no `{LABEL}` label; squash-merging {pr} at {head}", flush=True)
        return subprocess.run([gh, "pr", "merge", pr, "--squash",
                               "--match-head-commit", head]).returncode
    print(f"merge-gate: approve, one_way: yes with Ryan's ack; squash-merging {pr} at {head}", flush=True)
    rc = subprocess.run([gh, "pr", "merge", pr, "--squash", "--match-head-commit", head]).returncode
    if rc != 0:
        return rc
    try:
        used = use_ack(out)
    except OSError as e:
        print(f"merge-gate: merged, but could not move the ack to {out / USED}: {e}", file=sys.stderr)
        return 1
    note = merge_note(ack, items, used)
    (out / "merge-note.md").write_text(note, encoding="utf-8")
    print(note, end="", flush=True)
    return 0


MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SHELL_META = re.compile(r"[;|&$`<>\n]")


def _argv(v: object) -> list[str] | None:
    if isinstance(v, list) and v and all(isinstance(p, str) for p in v):
        return v
    return None


def pick(route_json: str) -> tuple[dict | None, str]:
    """({"model", "runner"}, "") for the router's reviewer, else (None, why)."""
    try:
        rows = json.loads(Path(route_json).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError) as e:
        return None, f"seat-usage output is not JSON ({type(e).__name__})"
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if row is None or row.get("role") != "review":
        return None, "seat-usage output has no review row"
    reviewers = row.get("reviewers") if isinstance(row.get("reviewers"), list) else []
    if row.get("mode") == "ask" or not reviewers:
        why = " ".join(str(row.get("reason") or "no reviewer").split())
        return None, f"no eligible reviewer: {why}"
    first = reviewers[0] if isinstance(reviewers[0], dict) else {}
    # The argv list is the contract. A string runner (`agy`) or none is an
    # older seat-usage that only names a model.
    runner = _argv(row.get("runner")) or _argv(first.get("runner"))
    if runner is None:
        return None, "seat-usage is too old to return a review runner; update quartermaster"
    model = first.get("model")
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        return None, "seat-usage reviewer has no usable model id"
    if any(SHELL_META.search(p) for p in runner):
        return None, "seat-usage runner is not a launch command"
    # An older route with no argv still reports that it is too old. This check
    # is for a route we would otherwise launch.
    conflict = vendor_conflict_reasons(rows)
    if conflict:
        return None, "; ".join(conflict)
    return {"model": model, "runner": runner}, ""


def vendor_conflict_reasons(data: object) -> list[str]:
    """Empty when the recorded builder and reviewer vendors differ.

    grok and xai compare equal. A missing side is its own reason, so a route
    that lacks both still lists both.
    """
    if isinstance(data, list):
        row = data[0] if data and isinstance(data[0], dict) else {}
    elif isinstance(data, dict):
        row = data
    else:
        row = {}
    builder = normalize_vendor(row.get("builder_vendor"))
    reviewers = row.get("reviewers") if isinstance(row.get("reviewers"), list) else []
    first = reviewers[0] if reviewers and isinstance(reviewers[0], dict) else {}
    reviewer = normalize_vendor(first.get("vendor"))
    reasons: list[str] = []
    if not builder:
        reasons.append("no recorded builder vendor (gate-route.json)")
    if not reviewer:
        reasons.append("no recorded reviewer vendor (gate-route.json)")
    if not reasons and builder == reviewer:
        reasons.append(
            f"the recorded reviewer vendor {reviewer} is the builder's vendor {builder} (gate-route.json)"
        )
    return reasons


def route_file_reasons(path: Path) -> list[str]:
    if not path.is_file():
        return [f"no gate-route.json at {path}"]
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return ["gate-route.json is unreadable"]
    return vendor_conflict_reasons(data)


def cursor_result(stream: str, md: str) -> int:
    """The last cursor-agent `result` event that is not an error and has text.

    cursor-agent joins the assistant turns in `result` with no separator, which
    glues the verdict onto the prose before it. When the assistant events' text
    is exactly that string, rejoin it with newlines.
    """
    r = ""
    chunks: list[str] = []
    try:
        lines = Path(stream).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"merge-gate: cannot read {stream}: {e}", file=sys.stderr)
        lines = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if e.get("type") == "assistant":
            chunks += _assistant_text(e)
        elif (e.get("type") == "result" and e.get("is_error") is False
                and isinstance(e.get("result"), str) and e["result"].strip()):
            r = e["result"]
    if chunks and "".join(chunks) == r:
        r = "\n".join(chunks)
    return _answer(r, md)


def _assistant_text(e: dict) -> list[str]:
    msg = e.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return []
    return [c["text"] for c in content
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]


def claude_result(out: str, md: str) -> int:
    """The `.result` string from `claude -p --output-format json`."""
    try:
        e = json.loads(Path(out).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        e = None
    r = e.get("result") if isinstance(e, dict) and not e.get("is_error") else None
    return _answer(r if isinstance(r, str) else "", md)


def plain_result(src: str, md: str) -> int:
    """A file that holds the answer as is (codex --output-last-message)."""
    try:
        r = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError:
        r = ""
    return _answer(r, md)


def muse_result(src: str, md: str) -> int:
    """The answer inside a `muse exec --json` JSONL stream.

    muse-usage.py keeps the joined run.output.delta text, and the last
    run.terminal.* text when the deltas are empty. Plain stdout stays as is.
    """
    try:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    return _answer(_muse_answer(text), md)


def _muse_answer(text: str) -> str:
    parts: list[str] = []
    terminal = ""
    saw = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            rec = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        kind = rec.get("payload_type")
        payload = rec.get("payload")
        if not isinstance(kind, str) or not isinstance(payload, dict):
            continue
        chunk = payload.get("text")
        if not isinstance(chunk, str):
            continue
        saw = True
        if kind == "run.output.delta":
            parts.append(chunk)
        elif kind.startswith("run.terminal."):
            terminal = chunk
    if not saw:
        return text
    joined = "".join(parts)
    if joined.strip():
        return joined
    return terminal if terminal.strip() else joined


def _answer(r: str, md: str) -> int:
    Path(md).write_text(r, encoding="utf-8")
    print("response chars:", len(r))
    if not r.strip():
        print("merge-gate: the reviewer did not return a clean answer", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "extract":
        return extract(argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "parse":
        print(json.dumps(parse(Path(argv[1]).read_text(encoding="utf-8", errors="replace"))
                         if Path(argv[1]).is_file() else
                         {"verdict": None, "verdict_error": f"no file {argv[1]}",
                          "one_way": None, "items": [], "error": f"no file {argv[1]}"}))
        return 0
    if argv[:1] == ["merge"]:
        rest = argv[1:]
        flag = None
        if rest[:1] == ["--builder-bot"] and len(rest) >= 2:
            flag, rest = rest[1], rest[2:]
        if len(rest) == 2:
            return merge(rest[0], rest[1], flag)
    if len(argv) == 2 and argv[0] == "pick":
        picked, why = pick(argv[1])
        if picked is None:
            print(why)
            return REFUSED
        print(json.dumps(picked))
        return 0
    if len(argv) == 3 and argv[0] in ("cursor-result", "claude-result", "plain-result", "muse-result"):
        return {"cursor-result": cursor_result, "claude-result": claude_result,
                "plain-result": plain_result, "muse-result": muse_result}[argv[0]](argv[1], argv[2])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
