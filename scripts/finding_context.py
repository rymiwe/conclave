#!/usr/bin/env python3
"""Cited-code expansion for the Jev findings gate.

A chair quotes about one line. Jev then reads 80 characters and "no run was
made", and holds. This module reads the file at the reviewed head and returns
the enclosing function plus one caller, at least MIN_LINES long and under
MAX_CHARS. It reads git objects only. It never checks anything out.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

MIN_LINES = 20
MAX_FUNCTION_LINES = 80
CALLER_RADIUS = 6
MAX_CHARS = 6000
MAX_FILE_BYTES = 400_000

_DEF_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)"),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
    re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
        r"(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
    ),
    re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),
    re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*\{"),
    re.compile(
        r"^\s*(?:(?:public|private|protected|internal|static|final|async|override|virtual|abstract)\s+)+"
        r"[\w<>\[\],.?\s]+?\s+([A-Za-z_]\w*)\s*\("
    ),
]
_CLOSERS = {"end", "}", "};", ")", "end)", "});", "]"}
_TEST_PATH = re.compile(
    r"(^|/)(spec|specs|test|tests|__tests__)/|_spec\.|_test\.|\.test\.|\.spec\.|(^|/)test-[^/]*$"
    r"|(^|/)test_[^/]*\.py$"
)
# Plain, unquoted git output whatever the user's config says.
_PLAIN = ("-c", "color.ui=never", "-c", "core.quotePath=false")

# What cited_code holds after expansion, so the Jev text can say only that.
FUNCTION_AND_CALLER = "function_and_caller"
FUNCTION_ONLY = "function_only"
WINDOW = "window"
CHAIR_QUOTE_KEPT = "chair_quote_kept"
CHAIR_QUOTE_ONLY = "chair_quote_only"


def _git(repo: str, *args: str) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _def_name(line: str) -> str | None:
    for pat in _DEF_PATTERNS:
        m = pat.match(line)
        if m:
            return m.group(1)
    return None


def _def_end(lines: list[str], start: int) -> int:
    """Index of the last line of the definition that opens at `start`."""
    base = _indent(lines[start])
    depth = lines[start].count("(") - lines[start].count(")")
    sig = start
    # A multi-line signature: the body starts after the parens close. Parens
    # inside strings are counted too, so never walk into the next definition.
    while depth > 0 and sig + 1 < len(lines) and sig - start < 40:
        if _def_name(lines[sig + 1]) is not None:
            break
        sig += 1
        depth += lines[sig].count("(") - lines[sig].count(")")
    end = sig
    for i in range(sig + 1, len(lines)):
        text = lines[i]
        if not text.strip():
            continue
        if _indent(text) <= base:
            if text.strip() in _CLOSERS:
                return i
            return end
        end = i
    return end


def enclosing_def(lines: list[str], line_no: int) -> tuple[int, int, str] | None:
    """(start, end, name), zero based and inclusive, or None at top level."""
    at = min(max(line_no - 1, 0), len(lines) - 1)
    for i in range(at, -1, -1):
        name = _def_name(lines[i])
        if name is None:
            continue
        end = _def_end(lines, i)
        if i <= at <= end:
            return i, end, name
    return None


def _numbered(lines: list[str], lo: int, hi: int, mark: int | None) -> str:
    width = len(str(hi + 1))
    out = []
    for i in range(lo, hi + 1):
        flag = ">" if mark is not None and i == mark else " "
        out.append(f"{flag}{i + 1:>{width}}| {lines[i]}")
    return "\n".join(out)


def _function_window(lines: list[str], start: int, end: int, at: int) -> tuple[str, int, int]:
    """Numbered text for the function, at least MIN_LINES, at most MAX_FUNCTION_LINES."""
    lo, hi = start, end
    if hi - lo + 1 < MIN_LINES:
        need = MIN_LINES - (hi - lo + 1)
        lo = max(0, start - need // 2)
        hi = min(len(lines) - 1, end + (need - (start - lo)))
        if hi - lo + 1 < MIN_LINES:
            lo = max(0, hi - MIN_LINES + 1)
    if hi - lo + 1 <= MAX_FUNCTION_LINES:
        return _numbered(lines, lo, hi, at), lo, hi
    head = _numbered(lines, lo, lo + 2, at)
    wlo = max(lo + 3, at - (MAX_FUNCTION_LINES - 4) // 2)
    whi = min(hi, wlo + MAX_FUNCTION_LINES - 4)
    wlo = max(lo + 3, whi - (MAX_FUNCTION_LINES - 4))
    gap = wlo - (lo + 3)
    parts = [head]
    if gap > 0:
        parts.append(f"  ... {gap} lines not shown ...")
    parts.append(_numbered(lines, wlo, whi, at))
    if hi > whi:
        parts.append(f"  ... {hi - whi} lines not shown ...")
    return "\n".join(parts), lo, hi


def _read_at(repo: str, head: str, path: str) -> list[str] | None:
    blob = _git(repo, *_PLAIN, "show", f"{head}:{path}")
    if blob is None or len(blob) > MAX_FILE_BYTES or "\0" in blob:
        return None
    return blob.splitlines()


def _find_caller(repo: str, head: str, name: str, path: str, def_line: int) -> tuple[str, int] | None:
    pat = rf"(^|[^A-Za-z0-9_$]){re.escape(name)}([^A-Za-z0-9_$]|$)"
    # --no-color too: a color.grep=always setting outranks color.ui=never.
    out = _git(repo, *_PLAIN, "grep", "--no-color", "-n", "-I", "-E", "-e", pat, head, "--")
    if not out:
        return None
    best: tuple[int, str, int] | None = None
    for raw in out.splitlines():
        m = re.match(r"^[0-9a-fA-F]{40}:(.+?):(\d+):(.*)$", raw)
        if not m:
            continue
        p, n, text = m.group(1), int(m.group(2)), m.group(3)
        if _def_name(text) == name:
            continue
        if p == path and n == def_line:
            continue
        if text.lstrip().startswith(("#", "//", "*", "--")):
            continue
        rank = 1 if _TEST_PATH.search(p) else 0
        if best is None or (rank, p, n) < (best[0], best[1], best[2]):
            best = (rank, p, n)
    return (best[1], best[2]) if best else None


def _cap(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    tail = "\n  ... cut at the size limit ..."
    return text[: MAX_CHARS - len(tail)].rstrip() + tail


def expand(repo: str, head: str, path: str, line: int, quoted: str) -> tuple[str, str, str]:
    """Return (cited_code, status, kind).

    status is "expanded" or the reason it was not. kind is one of the
    FUNCTION_AND_CALLER .. CHAIR_QUOTE_ONLY constants: what cited_code holds.
    """
    if not repo or not head:
        return quoted, "no repo or head", CHAIR_QUOTE_ONLY
    if _git(repo, "cat-file", "-e", f"{head}^{{commit}}") is None:
        return quoted, f"head {head[:8]} not in repo", CHAIR_QUOTE_ONLY
    lines = _read_at(repo, head, path)
    if lines is None:
        return quoted, f"{path} not readable at {head[:8]}", CHAIR_QUOTE_ONLY
    if not lines:
        return quoted, f"{path} is empty", CHAIR_QUOTE_ONLY
    if line < 1 or line > len(lines):
        return quoted, f"line {line} is outside the file ({len(lines)} lines)", CHAIR_QUOTE_ONLY
    at = line - 1
    found = enclosing_def(lines, line)
    kind = WINDOW
    blocks = []
    caller_note = ""
    if found is None:
        lo, hi = max(0, at - MIN_LINES // 2), min(len(lines) - 1, at + MIN_LINES // 2)
        blocks.append(
            f"{path}:{lo + 1}-{hi + 1} (no enclosing function found; lines around the cited line, at head {head[:8]})\n"
            + _numbered(lines, lo, hi, at)
        )
    else:
        start, end, name = found
        body, lo, hi = _function_window(lines, start, end, at)
        blocks.append(f"{path}:{lo + 1}-{hi + 1} (enclosing `{name}`, at head {head[:8]})\n{body}")
        kind = FUNCTION_ONLY
        hit = _find_caller(repo, head, name, path, start + 1)
        if hit is None:
            caller_note = f"caller: none found for `{name}` in the repo at head {head[:8]}"
        else:
            cpath, cline = hit
            clines = cpath == path and lines or _read_at(repo, head, cpath)
            if clines is None:
                caller_note = f"caller: {cpath}:{cline} is not readable at head {head[:8]}"
            else:
                c_at = min(max(cline - 1, 0), len(clines) - 1)
                clo, chi = max(0, c_at - CALLER_RADIUS), min(len(clines) - 1, c_at + CALLER_RADIUS)
                blocks.append(
                    f"caller of `{name}`: {cpath}:{clo + 1}-{chi + 1}\n" + _numbered(clines, clo, chi, c_at)
                )
                kind = FUNCTION_AND_CALLER
    if caller_note:
        blocks.append(caller_note)
    text = "\n\n".join(blocks)
    if quoted.strip():
        text = f"quoted by the chair:\n{quoted.strip()}\n\nread from the repo:\n{text}"
    return _cap(text), "expanded", kind


def expand_findings(doc: dict, repo: str, head: str) -> list[str]:
    """Replace each short cited_code in place. Returns one status line per finding.

    Sets f["cited_context"] to what cited_code now holds (see expand).
    """
    notes = []
    for f in doc["findings"]:
        quoted = str(f["cited_code"])
        f["cited_context"] = CHAIR_QUOTE_ONLY
        if len(quoted.splitlines()) >= MIN_LINES:
            f["cited_code"] = _cap(quoted)
            f["cited_context"] = CHAIR_QUOTE_KEPT
            notes.append(f"{f['id']}: kept (chair quote has {len(quoted.splitlines())} lines)")
            continue
        try:
            line = int(f["line"])
        except (TypeError, ValueError):
            notes.append(f"{f['id']}: not expanded (line is not a number)")
            continue
        text, status, kind = expand(repo, head, str(f["file"]), line, quoted)
        f["cited_context"] = kind
        if status == "expanded":
            f["cited_code"] = text
            notes.append(f"{f['id']}: expanded to {len(text.splitlines())} lines")
        else:
            notes.append(f"{f['id']}: not expanded ({status})")
    return notes
