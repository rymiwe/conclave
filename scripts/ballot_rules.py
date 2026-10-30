#!/usr/bin/env python3
"""One sentence for how kind review seats its voters.

The condition is apply_kind_voters in scripts/conclave.sh.
  python3 scripts/ballot_rules.py --write    # refresh the four copies
  python3 scripts/ballot_rules.py --check [ROOT]
"""
from __future__ import annotations

import sys
from pathlib import Path

_OPUS = "Opus"
SENTENCE = (
    "Review seats two voters from seat-usage --route lineup --kind review, "
    "on two vendor families, excluding a named author or a caller-passed builder or --builder-vendor and nobody when none of those is set, and falls back to Fable and Astra "
    "when that route is missing or fails, and stops with an ask for Ryan when it asks."
)
START = "<!-- ballot-rule:start -->"
END = "<!-- ballot-rule:end -->"
BLOCK = START + SENTENCE + END

# Phrase pieces stay split so this file does not itself match the lint.
PATTERNS = (
    "adds " + _OPUS + " native",
    "Review adds " + _OPUS,
    "adds the " + _OPUS + " coverage",
)
EXCLUSION = "unless Anthropic built the diff"

TARGETS = (
    ".cursor/skills/council/SKILL.md",
    ".agents/skills/council/SKILL.md",
    "docs/review.md",
    "docs/routing.md",
)

# Hand-written fragments --write replaces when the block is not there yet.
OLD = (
    "Review adds " + _OPUS + " native.",
    "Review adds the " + _OPUS + " coverage leg.",
)

PORT = "docs/review-kind-port.md"
PORT_BANNED = "never by default"

SKIP_DIRS = {".git", "__pycache__", "tmp", ".claude"}


def repo_root(argv: list[str]) -> Path:
    if len(argv) >= 1:
        return Path(argv[0]).resolve()
    return Path(__file__).resolve().parent.parent


def write_copies(root: Path) -> None:
    for rel in TARGETS:
        path = root / rel
        text = path.read_text(encoding="utf-8")
        if START in text and END in text:
            pre, rest = text.split(START, 1)
            _, post = rest.split(END, 1)
            text = pre + BLOCK + post
        else:
            replaced = False
            for old in OLD:
                if old in text:
                    text = text.replace(old, BLOCK, 1)
                    replaced = True
                    break
            if not replaced:
                raise SystemExit(f"{rel}: no ballot sentence to replace. The generator is scripts/ballot_rules.py.")
        path.write_text(text, encoding="utf-8")


def _mask(text: str) -> str:
    out = []
    while START in text:
        pre, rest = text.split(START, 1)
        if END not in rest:
            out.append(pre)
            out.append(rest)
            return "".join(out)
        _inside, post = rest.split(END, 1)
        out.append(pre)
        text = post
    out.append(text)
    return "".join(out)


def problems(root: Path) -> list[str]:
    found: list[str] = []
    blocks: list[str] = []
    for rel in TARGETS:
        path = root / rel
        if not path.is_file():
            found.append(f"{rel}: missing generated sentence. Run scripts/ballot_rules.py --write. The condition is apply_kind_voters in scripts/conclave.sh.")
            continue
        text = path.read_text(encoding="utf-8")
        count = text.count(BLOCK)
        if count != 1:
            found.append(
                f"{rel}: expected one generated sentence from scripts/ballot_rules.py, found {count}. "
                "The condition is apply_kind_voters in scripts/conclave.sh."
            )
        else:
            blocks.append(text)
    if len(blocks) == len(TARGETS) and len({b[b.index(BLOCK):b.index(BLOCK) + len(BLOCK)] for b in blocks}) != 1:
        found.append("generated sentences differ. Run scripts/ballot_rules.py --write. The condition is apply_kind_voters in scripts/conclave.sh.")

    port = root / PORT
    if port.is_file() and PORT_BANNED in port.read_text(encoding="utf-8"):
        found.append(
            f"{PORT}: still says {PORT_BANNED!r}. Delete that decision list. "
            "The condition is apply_kind_voters in scripts/conclave.sh."
        )

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.name == "ballot_rules.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        masked = _mask(text)
        rel = path.relative_to(root)
        for i, line in enumerate(masked.splitlines(), 1):
            if any(pat in line for pat in PATTERNS) and EXCLUSION not in line:
                found.append(
                    f"{rel}:{i}: this sentence adds the coverage seat and omits the exclusion. "
                    "Use the sentence from scripts/ballot_rules.py (--write). "
                    "The condition is apply_kind_voters in scripts/conclave.sh."
                )
    return found


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("--check", "--write"):
        raise SystemExit("usage: ballot_rules.py --write | --check [ROOT]")
    root = repo_root(argv[1:])
    if argv[0] == "--write":
        write_copies(root)
    found = problems(root)
    if found:
        print(f"ballot rules: {len(found)} problem(s)")
        for line in found:
            print(line)
        return 1
    print(f"ballot rules: ok ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
