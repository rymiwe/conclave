#!/usr/bin/env python3
"""Turn a lineup JSON blob into the lines conclave.sh already reads.

A row is a family and a seat. There is no family-only map.
launch_for((fable, cursor)) is cursor-fable. launch_for((fable, claude))
is claude. Any other pair raises. Unknown rows make the whole parse bad,
which the shell already turns into the fixed default.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Literal

Family = Literal["fable", "astra", "grok", "muse", "wonder-full"]
Seat = Literal["claude", "cursor", "chatgpt", "grok", "muse"]

VOTERS = {
    ("fable", "claude"): "claude",
    ("fable", "cursor"): "claude",
    ("astra", "chatgpt"): "codex",
    ("grok", "grok"): "grok",
    ("muse", "muse"): "muse",
    ("wonder-full", "muse"): "muse",
}


@dataclass(frozen=True)
class Leg:
    family: Family
    seat: Seat


def voter_name(leg: Leg) -> str:
    try:
        return VOTERS[(leg.family, leg.seat)]
    except KeyError as exc:
        raise ValueError(f"no voter for {leg.family}/{leg.seat}") from exc


def launch_for(leg: Leg) -> str:
    """(fable, cursor) -> cursor-fable. (fable, claude) -> claude.

    Any other pair raises. There is no implicit family-only map.
    """
    if (leg.family, leg.seat) == ("fable", "cursor"):
        return "cursor-fable"
    if (leg.family, leg.seat) == ("fable", "claude"):
        return "claude"
    raise ValueError(f"no launch for {leg.family}/{leg.seat}")


def _leg(row: dict) -> Leg:
    family = row.get("family")
    seat = row.get("seat")
    if (family, seat) not in VOTERS:
        raise ValueError(f"no voter for {family}/{seat}")
    return Leg(family, seat)


def parse(raw: str) -> str:
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return "bad\n"
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    if not isinstance(doc, dict):
        return "bad\n"
    if doc.get("mode") == "ask" or not isinstance(doc.get("lineup"), dict):
        return "ask\n" if doc.get("mode") == "ask" else "bad\n"
    down = doc.get("downgrade")
    out: list[str] = []
    cursor = False
    downgraded = ""
    for row in doc["lineup"].get("voters") or []:
        if not isinstance(row, dict):
            continue
        try:
            leg = _leg(row)
            name = voter_name(leg)
        except ValueError:
            return "bad\n"
        try:
            if launch_for(leg) == "cursor-fable":
                cursor = True
        except ValueError:
            if leg.family == "fable":
                return "bad\n"
        if row.get("downgraded") and row.get("effort"):
            downgraded = row["effort"]
        if name not in out:
            out.append(name)
    if isinstance(down, dict) and down.get("reason"):
        if not out:
            return "bad\n"
        return "downgrade\n" + ",".join(out) + "\n" + str(down["reason"]) + "\n"
    if not out:
        return "bad\n"
    lines = ["ok", ",".join(out)]
    if cursor:
        lines.append("cursor-fable")
    if downgraded:
        lines.append("downgraded:" + str(downgraded))
    excl = doc.get("excluded")
    if isinstance(excl, dict) and (excl.get("why") or excl.get("vendor") or excl.get("name")):
        if excl.get("vendor"):
            lines.append(str(excl.get("name") or excl.get("vendor")))
        else:
            lines.append("none")
        lines.append(str(excl.get("why") or ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    raw = argv[0] if argv else sys.stdin.read()
    sys.stdout.write(parse(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
