#!/usr/bin/env python3
"""A public comment that uses an internal word must fail the draft check.

CHECK_LINKS overrides the checker. Point it at an older copy to show that
copy still accepts the words.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECKER = os.environ.get("CHECK_LINKS", str(ROOT / "check-comment-links.py"))
BAD = "I ran a chair repro. The conclave ballot leg was blinded.\n"
CLEAN = "I reproduced this locally against a scripted upstream.\n"


def run(text: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "comments.md"
        path.write_text(text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, CHECKER, str(path)],
            capture_output=True,
            text=True,
        )


def main() -> int:
    bad = run(BAD)
    if bad.returncode == 0:
        print(bad.stdout)
        print(bad.stderr)
        print("FAIL public comment was accepted")
        return 1
    out = bad.stdout + bad.stderr
    if 'contains "chair repro"' not in out or "I reproduced this locally against a scripted upstream" not in out:
        print(out)
        print("FAIL checker rejected the comment without naming the replacement")
        return 1
    clean = run(CLEAN)
    if clean.returncode != 0:
        print(clean.stdout)
        print(clean.stderr)
        print("FAIL a plain comment was rejected")
        return 1
    print("ok comment words")
    print(bad.stdout, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
