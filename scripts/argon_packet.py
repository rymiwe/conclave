#!/usr/bin/env python3
"""Size the Argon review packet and keep it under the byte cap.

agy stops reading near 190 KB with no error, and the diff is path-ordered, so
an oversized packet silently loses whatever sorts last. This script measures
the packet instead. Priority when it must cut: the brief, then the changed
source diff, then the spec and test diff, then generated files (schema,
migrations, lockfiles). Whole file diffs are kept or cut, and every cut is
named in the packet and in the plan. When the cut would lose files and both
source and tests changed, the packet splits in two (source, then specs and
tests), each under the cap.

  argon_packet.py plan --brief F --diff F --max-bytes N --out-dir D
  argon_packet.py finalize --plan D/plan.json --out F [--report N=FILE ...]

plan writes D/packet-<n>.md and D/plan.json. finalize merges the per-packet
reports with packet provenance into one report.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DIFF_HEADER = re.compile(rb"^diff --git (.*)$")
_NEW_SIDE = (
    re.compile(rb'^\+\+\+ (?:"b/((?:[^"\\]|\\.)*)"|b/(.*?))\t?$'),
    re.compile(rb'^rename to (?:"((?:[^"\\]|\\.)*)"|(.*))$'),
)
_C_ESCAPES = {b"n": b"\n", b"t": b"\t", b'"': b'"', b"\\": b"\\", b"a": b"\a",
              b"b": b"\b", b"f": b"\f", b"r": b"\r", b"v": b"\v"}
GENERATED = re.compile(
    r"(^|/)db/(schema\.rb|structure\.sql|schema\.sql)$"
    r"|(^|/)(package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock|go\.sum)$"
    r"|\.lock$|(^|/)db/migrate/|(^|/)migrations/|(^|/)(generated|dist|vendor)/"
    r"|\.min\.(js|css)$|\.snap$"
)
TESTISH = re.compile(
    r"(^|/)(spec|specs|test|tests|__tests__|fixtures|e2e)/"
    r"|_(spec|test)\.[A-Za-z0-9]+$|\.(spec|test)\.[A-Za-z0-9]+$"
    r"|(^|/)test_[^/]*\.py$"
)
CLASS_ORDER = ("source", "spec", "generated")
CLASS_LABEL = {
    "source": "changed source",
    "spec": "specs and tests",
    "generated": "generated",
}
FENCE_OVERHEAD = len(b"\n## The change under review\n\n```diff\n```\n")
LIST_CAP = 60
MIN_TRUNCATED = 2000


def classify(path: str) -> str:
    if GENERATED.search(path):
        return "generated"
    if TESTISH.search(path):
        return "spec"
    return "source"


@dataclass
class Section:
    index: int
    path: str
    kind: str
    body: bytes
    included: bool = False
    truncated: bool = False
    shown: bytes = b""

    @property
    def size(self) -> int:
        return len(self.body)


def _c_unquote(raw: bytes) -> bytes:
    """Undo git's C-style path quoting (the text between the double quotes)."""
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i : i + 1]
        if ch != b"\\" or i + 1 >= len(raw):
            out += ch
            i += 1
            continue
        nxt = raw[i + 1 : i + 2]
        if nxt in _C_ESCAPES:
            out += _C_ESCAPES[nxt]
            i += 2
        elif re.match(rb"[0-7]{3}", raw[i + 1 : i + 4]):
            out.append(int(raw[i + 1 : i + 4], 8))
            i += 4
        else:
            out += nxt
            i += 2
    return bytes(out)


def header_path(rest: bytes, body: bytes = b"") -> str:
    """The new-side path of a `diff --git` line (what follows `diff --git `).

    Handles git's quoting for paths with spaces or non-ASCII bytes. An unquoted
    path may itself contain ` b/`, so the separator is the one whose a-side and
    b-side agree. A rename has two different sides; its path comes from the
    `+++` or `rename to` line in body. A header that matches no known shape is
    still a section boundary, named by its text.
    """
    quoted = re.search(rb'"b/((?:[^"\\]|\\.)*)"$', rest)
    if quoted:
        return _c_unquote(quoted.group(1)).decode("utf-8", "replace")
    if rest.startswith(b"a/"):
        for m in re.finditer(rb" b/", rest):
            if rest[2 : m.start()] == rest[m.end() :]:
                return rest[m.end() :].decode("utf-8", "replace")
    for line in body.splitlines()[1:]:
        if line.startswith((b"@@", b"Binary files ")):
            break
        for pat in _NEW_SIDE:
            m = pat.match(line)
            if m:
                raw = _c_unquote(m.group(1)) if m.group(1) is not None else m.group(2)
                return raw.decode("utf-8", "replace")
    tail = rest.rsplit(b" b/", 1)
    if len(tail) == 2:
        return tail[1].decode("utf-8", "replace")
    return "(unparsed header: " + rest.decode("utf-8", "replace")[:80] + ")"


def parse_diff(diff: bytes) -> list[Section]:
    sections: list[Section] = []
    start = None
    rest = b""
    pos = 0
    for line in diff.splitlines(keepends=True):
        m = DIFF_HEADER.match(line.rstrip(b"\r\n"))
        if m:
            if start is not None:
                body = diff[start:pos]
                sections.append(_section(len(sections), header_path(rest, body), body))
            start = pos
            rest = m.group(1)
        pos += len(line)
    if start is not None:
        body = diff[start:pos]
        sections.append(_section(len(sections), header_path(rest, body), body))
    elif diff.strip():
        sections.append(_section(0, "(unparsed diff)", diff))
    return sections


def _section(index: int, path: str, body: bytes) -> Section:
    if not body.endswith(b"\n"):
        body += b"\n"
    return Section(index, path, classify(path), body)


def head_at_hunk(body: bytes, room: int) -> bytes:
    """The longest prefix of one file diff within room, cut at a late hunk, else a line."""
    if room <= 0:
        return b""
    window = body[:room]
    cut = window.rfind(b"\n@@ ")
    if cut > room // 2:
        return window[: cut + 1]
    cut = window.rfind(b"\n")
    return window[: cut + 1] if cut > 0 else b""


def fill(order: list[Section], room: int) -> int:
    """Whole files in path order within room, then the head of an oversize file.

    Returns the room left. An oversize file is one that cannot fit in an
    empty packet, so it would otherwise be lost whole.
    """
    capacity = room
    oversize = []
    for sec in order:
        if sec.included:
            continue
        if sec.size <= room:
            sec.included, sec.shown = True, sec.body
            room -= sec.size
        elif sec.size > capacity:
            oversize.append(sec)
    for sec in oversize:
        if sec.kind == "generated" or room < MIN_TRUNCATED:
            continue
        head = head_at_hunk(sec.body, room)
        if len(head) >= MIN_TRUNCATED:
            sec.included, sec.truncated, sec.shown = True, True, head
            room -= len(head)
    return room


def short(path: str) -> str:
    """A path as one inert line of the coverage list: a filename cannot add lines."""
    path = (path.replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r"))
    return path if len(path) <= 120 else "..." + path[-117:]


def cut_lines(cut: list[Section]) -> list[str]:
    lines = []
    for sec in cut[:LIST_CAP]:
        if sec.truncated:
            what = f"truncated, {len(sec.shown)} of {sec.size} bytes shown"
        else:
            what = f"not shown, {sec.size} bytes"
        lines.append(f"- {short(sec.path)} ({CLASS_LABEL[sec.kind]}; {what})")
    if len(cut) > LIST_CAP:
        rest = cut[LIST_CAP:]
        lines.append(f"- and {len(rest)} more files, {sum(s.size for s in rest)} bytes")
    return lines


def notice(max_bytes: int, cut: list[Section], other: str, label: str) -> bytes:
    parts = ["## Packet coverage"]
    if label:
        parts.append(f"This is {label}.")
    if other:
        parts.append(other)
    if cut:
        parts.append(
            f"This packet is capped at {max_bytes} bytes and the diff below is PARTIAL. "
            "Files are kept in this priority: source, then specs and tests, then generated "
            "files (schema, migrations, lockfiles). These were cut:"
        )
        parts.append("\n".join(cut_lines(cut)))
        parts.append(
            "Do not draw conclusions about the cut files. Say in your report that your "
            "review did not cover them."
        )
    return ("\n\n".join(parts) + "\n\n").encode()


def assemble(brief: bytes, secs: list[Section], note: bytes) -> bytes:
    shown = b"".join(s.shown for s in sorted(secs, key=lambda s: s.index) if s.included)
    out = brief + b"\n## The change under review\n\n" + note + b"```diff\n" + shown + b"```\n"
    return out


def build_packet(brief, groups, max_bytes, other, label):
    """Fill the groups in order into one packet that fits. Groups are lists of sections."""
    reserve = 0
    for _ in range(6):
        for sec in (s for g in groups for s in g):
            sec.included, sec.truncated, sec.shown = False, False, b""
        room = max_bytes - len(brief) - FENCE_OVERHEAD - reserve
        for group in groups:
            room = fill(group, room)
        cut = [s for g in groups for s in g if not s.included or s.truncated]
        cut.sort(key=lambda s: s.index)
        note = notice(max_bytes, cut, other, label) if (cut or other or label) else b""
        data = assemble(brief, [s for g in groups for s in g], note)
        over = len(data) - max_bytes
        if over <= 0 or max_bytes - len(brief) - FENCE_OVERHEAD <= 0:
            return data, cut
        reserve += over + 64
    return data, cut


def entry(n, name, data, secs, cut, label):
    return {
        "n": n,
        "name": name,
        "path": f"packet-{n}.md",
        "bytes": len(data),
        "label": label,
        "files": [
            {"path": s.path, "class": s.kind, "bytes": len(s.shown), "of": s.size,
             "truncated": s.truncated}
            for s in sorted(secs, key=lambda s: s.index) if s.included
        ],
        "cut": [
            {"path": s.path, "class": s.kind, "bytes": s.size, "shown": len(s.shown),
             "truncated": s.truncated}
            for s in cut
        ],
    }


def plan(brief: bytes, diff: bytes, max_bytes: int, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not brief.endswith(b"\n"):
        brief += b"\n"
    secs = parse_diff(diff)
    by = {k: [s for s in secs if s.kind == k] for k in CLASS_ORDER}
    full = len(brief) + FENCE_OVERHEAD + sum(s.size for s in secs)
    result = {
        "max_bytes": max_bytes,
        "brief_bytes": len(brief),
        "diff_bytes": sum(s.size for s in secs),
        "full_bytes": full,
        "files": len(secs),
        "mode": "whole",
        "packets": [],
        "cut": [],
        "warning": "",
        "brief_exceeds_max": len(brief) + FENCE_OVERHEAD > max_bytes,
    }
    packets = []
    if full <= max_bytes:
        data, _ = build_packet(brief, [secs], max_bytes, "", "")
        packets.append((data, secs, [], ""))
    elif by["source"] and by["spec"]:
        gen = by["generated"]
        a_label = "the changed source files"
        b_label = "the specs and tests"
        a_other = "The specs and tests are in packet 2 and are not shown here."
        b_other = "The changed source files are in packet 1 and are not shown here."
        data_a, cut_a = build_packet(brief, [by["source"], gen], max_bytes, a_other, f"packet 1 of 2, {a_label}")
        data_b, cut_b = build_packet(brief, [by["spec"]], max_bytes, b_other, f"packet 2 of 2, {b_label}")
        packets.append((data_a, by["source"] + gen, cut_a, a_label))
        packets.append((data_b, by["spec"], cut_b, b_label))
        result["mode"] = "split"
        result["cut"] = sorted(cut_a + cut_b, key=lambda s: s.index)
    else:
        data, cut = build_packet(brief, [by["source"], by["spec"], by["generated"]],
                                 max_bytes, "", "")
        packets.append((data, secs, cut, ""))
        result["mode"] = "cut" if cut else "whole"
        result["cut"] = cut
    for n, (data, ss, cut, label) in enumerate(packets, 1):
        (out_dir / f"packet-{n}.md").write_bytes(data)
        result["packets"].append(entry(n, f"packet {n}", data, ss, cut, label))
    cut_final = result["cut"]
    result["cut"] = [
        {"path": s.path, "class": s.kind, "bytes": s.size} for s in cut_final
    ]
    if cut_final or result["brief_exceeds_max"]:
        result["warning"] = warning(result, cut_final)
    result["split_clean"] = result["mode"] == "split" and not cut_final
    (out_dir / "plan.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def warning(result: dict, cut: list[Section]) -> str:
    lines = [
        f"WARNING: argon saw a PARTIAL review packet. The full packet is {result['full_bytes']} "
        f"bytes and the cap is {result['max_bytes']} (CONCLAVE_ARGON_MAX_BYTES); agy silently "
        "stops reading near 190 KB, so Argon's review does not cover what was cut."
    ]
    if result["brief_exceeds_max"]:
        lines.append(
            f"The brief alone is {result['brief_bytes']} bytes, over the cap. No diff fit."
        )
    if result["mode"] == "split":
        sizes = ", ".join(str(p["bytes"]) for p in result["packets"])
        lines.append(f"Split into 2 packets ({sizes} bytes), and files were still cut:")
    elif cut:
        lines.append(f"Sent 1 packet of {result['packets'][0]['bytes']} bytes. Cut, by priority:")
    lines += cut_lines(cut)
    return "\n".join(lines) + "\n"


def finalize(plan_path: Path, out: Path, reports: dict[int, Path | str]) -> None:
    doc = json.loads(plan_path.read_text(encoding="utf-8"))
    parts = []
    if doc["warning"]:
        first = doc["warning"].splitlines()[0]
        parts.append(f"> {first}\n")
    if doc["mode"] == "split":
        parts.append(
            "> Argon reviewed this change in 2 packets. Each finding sits under the packet "
            "that produced it. Findings are not de-duplicated across packets.\n"
        )
    for p in doc["packets"]:
        body = reports.get(p["n"])
        head = f"## Packet {p['n']} of {len(doc['packets'])}: {p['label'] or 'the whole diff'}"
        head += f" ({len(p['files'])} files, {p['bytes']} bytes)\n"
        if doc["mode"] == "split":
            parts.append(head)
        if isinstance(body, Path):
            text = body.read_text(encoding="utf-8", errors="replace")
            parts.append(text if text.endswith("\n") else text + "\n")
        else:
            parts.append(f"NOT REVIEWED: packet {p['n']} failed ({body or 'no report'}).\n")
    out.write_text("\n".join(parts), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--brief", required=True)
    p.add_argument("--diff", required=True)
    p.add_argument("--max-bytes", type=int, required=True)
    p.add_argument("--out-dir", required=True)
    f = sub.add_parser("finalize")
    f.add_argument("--plan", required=True)
    f.add_argument("--out", required=True)
    f.add_argument("--report", action="append", default=[], help="N=FILE or N=!reason")
    args = ap.parse_args(argv)
    if args.cmd == "plan":
        res = plan(Path(args.brief).read_bytes(), Path(args.diff).read_bytes(),
                   args.max_bytes, Path(args.out_dir))
        sys.stdout.write(json.dumps({k: res[k] for k in ("mode", "full_bytes", "max_bytes")}) + "\n")
        return 0
    reports = {}
    for item in args.report:
        n, _, val = item.partition("=")
        reports[int(n)] = val[1:] if val.startswith("!") else Path(val)
    finalize(Path(args.plan), Path(args.out), reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
