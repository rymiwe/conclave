#!/usr/bin/env python3
"""A fable row seated on cursor must come back as cursor-fable.

CONCLAVE_SH points at the shell that defines lineup_parse_voters.
An older shell that ignores the seat fails this check.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = os.environ.get("CONCLAVE_SH", str(ROOT / "scripts" / "conclave.sh"))
RAW = json.dumps([{
    "role": "lineup",
    "mode": "default",
    "lineup": {"voters": [
        {"family": "fable", "seat": "cursor", "effort": "high"},
        {"family": "astra", "seat": "chatgpt", "effort": "high"},
        {"family": "grok", "seat": "grok", "effort": "high"},
    ]},
}])


def parsed(script: str, raw: str) -> str:
    proc = subprocess.run(
        ["bash", "-c",
         'export COUNCIL_SOURCE_ONLY=1\n. "$1"\nlineup_parse_voters "$2"\n',
         "bash", script, raw],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stderr)
        raise SystemExit(f"parser exited {proc.returncode}")
    return proc.stdout


def main() -> int:
    out = parsed(SCRIPT, RAW)
    lines = out.splitlines()
    if "cursor-fable" not in lines or lines[:2] != ["ok", "claude,codex,grok"]:
        print("FAIL cursor seat did not select cursor-fable")
        print(out, end="" if out.endswith("\n") else "\n")
        return 1
    sys.path.insert(0, str(ROOT / "scripts"))
    import lineup  # noqa: E402
    leg = lineup.Leg("fable", "cursor")
    if lineup.launch_for(leg) != "cursor-fable":
        print(f"FAIL launch_for returned {lineup.launch_for(leg)!r}")
        return 1
    try:
        lineup.launch_for(lineup.Leg("fable", "chatgpt"))
    except ValueError:
        pass
    else:
        print("FAIL launch_for accepted fable/chatgpt")
        return 1
    print("ok lineup seat")
    print(out, end="" if out.endswith("\n") else "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
