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

Family = Literal["fable", "astra", "grok", "muse", "wonder-full", "wonder", "argon"]
Seat = Literal["claude", "cursor", "chatgpt", "grok", "muse", "google"]

VOTERS = {
    ("fable", "claude"): "claude",
    ("fable", "cursor"): "claude",
    ("astra", "chatgpt"): "codex",
    ("grok", "grok"): "grok",
    ("muse", "muse"): "muse",
    ("wonder-full", "muse"): "muse",
    ("wonder", "muse"): "muse",
    ("argon", "google"): "argon",
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


# Used when a route row omits vendor. Fable on either seat is still Anthropic.
_FAMILY_VENDOR = {
    "fable": "anthropic",
    "astra": "openai",
    "grok": "xai",
    "muse": "meta",
    "wonder": "meta",
    "wonder-full": "meta",
    "argon": "google",
}


def pick_replacement(raw: str, failed: str, builder: str, other: str, avoid_names: str = "") -> str:
    """One replacement voter, or none.

    `failed` is the conclave voter that just failed. `builder` and `other`
    are vendor ids (`other` may be empty). `avoid_names` is a comma-separated
    list of voter names that already failed or are already seated. The
    replacement's vendor is neither the builder nor the other working voter.
    A named voter in `avoid_names`, including the one that just failed, is
    not returned. Output is `ok`, the voter name, and `cursor-fable` when
    that is how Fable launches; or `none` and a reason.
    """
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return "none\nseat-usage returned unreadable json\n"
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    if not isinstance(doc, dict):
        return "none\nseat-usage returned unreadable json\n"
    codes = []
    for item in doc.get("skipped") or []:
        if isinstance(item, dict) and item.get("code"):
            codes.append(f"{item.get('family') or '-'}:{item['code']}")
    code_text = ", ".join(codes)
    if doc.get("mode") == "ask" or not isinstance(doc.get("lineup"), dict):
        why = doc.get("reason") or doc.get("why") or "route asked"
        extra = f" ({code_text})" if code_text else ""
        return f"none\nno eligible replacement: {why}{extra}\n"
    avoid_vendors = {v for v in (builder, other) if v}
    avoid = {name.strip() for name in (avoid_names or "").split(",") if name.strip()}
    if failed:
        avoid.add(failed)
    for row in doc["lineup"].get("voters") or []:
        if not isinstance(row, dict):
            continue
        try:
            leg = _leg(row)
            name = voter_name(leg)
        except ValueError:
            continue
        if name in avoid:
            continue
        vendor = row.get("vendor") or _FAMILY_VENDOR.get(leg.family) or ""
        if vendor in avoid_vendors:
            continue
        if not vendor and avoid_vendors:
            continue
        lines = ["ok", name]
        try:
            if launch_for(leg) == "cursor-fable":
                lines.append("cursor-fable")
        except ValueError:
            pass
        return "\n".join(lines) + "\n"
    why = doc.get("why") or doc.get("reason") or "route returned no other seat"
    extra = f" ({code_text})" if code_text else ""
    return (
        "none\nno eligible replacement: vendor must differ from the builder "
        f"and the other voter; {why}{extra}\n"
    )


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--replacement":
        # --replacement <failed> <builder-vendor> <other-vendor> <json>
        # or <failed> <builder> <other> <avoid-names> <json>. "-" is empty.
        if len(argv) < 5:
            sys.stdout.write("none\nreplacement needs the failed voter, both vendors, and json\n")
            return 0
        failed, builder, other = argv[1], argv[2], argv[3]
        if len(argv) >= 6:
            avoid, raw = argv[4], argv[5]
        else:
            avoid, raw = "", argv[4]
        if builder == "-":
            builder = ""
        if other == "-":
            other = ""
        if avoid == "-":
            avoid = ""
        sys.stdout.write(pick_replacement(raw, failed, builder, other, avoid))
        return 0
    raw = argv[0] if argv else sys.stdin.read()
    sys.stdout.write(parse(raw))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
