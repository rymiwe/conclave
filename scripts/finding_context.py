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
    re.compile(r"^\s*(?:async\s+)?def\s+(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*\.)?([A-Za-z_]\w*[?!=]?)"),
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
# A line that opens one of these statements is never a definition, whatever a
# pattern above makes of it (`async with session() as db:` is not `session`).
_STATEMENT = re.compile(r"^\s*(?:async\s+)?(?:with|for|while|if|elif|else|return|await|switch|catch)\b")
# A captured word that is a keyword is not a name: `function () {` is anonymous.
_NOT_NAMES = {"function", "async", "await", "with", "for", "while", "if", "else", "return",
              "switch", "catch", "new", "do", "try"}
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
    """The name a line defines, or None when it defines nothing we can name."""
    if _STATEMENT.match(line):
        return None
    for pat in _DEF_PATTERNS:
        m = pat.match(line)
        if m:
            return None if m.group(1) in _NOT_NAMES else m.group(1)
    return None


def _def_end(lines: list[str], start: int) -> int:
    """Index of the last line of the definition that opens at `start`."""
    base = _indent(lines[start])
    depth = lines[start].count("(") - lines[start].count(")")
    sig = start
    # A multi-line signature: the body starts after the parens close. Parens
    # inside strings are counted too, so stop at the next definition, and at a
    # line back at the def's indent that does not close the signature.
    while depth > 0 and sig + 1 < len(lines) and sig - start < 40:
        nxt = lines[sig + 1]
        if _def_name(nxt) is not None:
            break
        if nxt.strip() and _indent(nxt) <= base and not nxt.strip().startswith((")", "]", "}")):
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


# Outside a string, a one-line '...' or "..." literal is skipped whole, so a
# quote token inside it (as in this pattern) does not open a string.
_OPENER = re.compile(r'"""|\'\'\'|`|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_HEREDOC = re.compile(r"<<[-~]?(?:'([A-Za-z_]\w*)'|\"([A-Za-z_]\w*)\"|([A-Z_][A-Z0-9_]*))")


def _in_string(lines: list[str]) -> list[bool]:
    """For each line, whether it starts inside a triple-quoted string, a template
    string, or a heredoc. A definition on such a line is text, not code."""
    inside, state = [], None
    for line in lines:
        inside.append(state is not None)
        if isinstance(state, tuple):
            if line.strip() == state[0]:
                state = None
            continue
        text, pos = re.sub(r"\\.", "", line), 0
        while pos < len(text):
            if state is None:
                m = _OPENER.search(text, pos)
                if not m:
                    break
                if m.group(0) in ('"""', "'''", "`"):
                    state = m.group(0)
                pos = m.end()
            else:
                close = text.find(state, pos)
                if close < 0:
                    break
                state, pos = None, close + len(state)
        if state is None and "<<<" not in line:
            h = _HEREDOC.search(line)
            if h:
                state = (next(g for g in h.groups() if g),)
    return inside


def enclosing_def(lines: list[str], line_no: int) -> tuple[int, int, str] | None:
    """(start, end, name), zero based and inclusive, or None at top level."""
    at = min(max(line_no - 1, 0), len(lines) - 1)
    quoted = _in_string(lines)
    for i in range(at, -1, -1):
        if quoted[i]:
            continue
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


def _around(lines: list[str], lo: int, hi: int, at: int, radius: int) -> str:
    """Numbered lo..hi narrowed to radius lines each side of the marked line."""
    wlo, whi = max(lo, at - radius), min(hi, at + radius)
    parts = []
    if wlo > lo:
        parts.append(f"  ... {wlo - lo} lines not shown ...")
    parts.append(_numbered(lines, wlo, whi, at))
    if whi < hi:
        parts.append(f"  ... {hi - whi} lines not shown ...")
    return "\n".join(parts)


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


def _calls(name: str, text: str) -> bool:
    """Whether text uses name itself, not a longer name such as save! for save.

    A ?, ! or = right after the name makes another Ruby method, unless the
    name ends that way. A ?. optional call is still a use.
    """
    for m in re.finditer(rf"(?<![A-Za-z0-9_$]){re.escape(name)}(?![A-Za-z0-9_$])", text):
        after = text[m.end():]
        if after[:1] in ("?", "!", "=") and not after.startswith("?."):
            continue
        return True
    return False


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
        if not _calls(name, text):
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
    caller = ""
    caller_note = ""
    caller_cut_note = ""
    if found is None:
        lo, hi = max(0, at - MIN_LINES // 2), min(len(lines) - 1, at + MIN_LINES // 2)
        title = f"{path}:{lo + 1}-{hi + 1} (function: unknown; lines around the cited line, at head {head[:8]})"
        body = _numbered(lines, lo, hi, at)
    else:
        start, end, name = found
        body, lo, hi = _function_window(lines, start, end, at)
        title = f"{path}:{lo + 1}-{hi + 1} (enclosing `{name}`, at head {head[:8]})"
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
                caller = f"caller of `{name}`: {cpath}:{clo + 1}-{chi + 1}\n" + _numbered(clines, clo, chi, c_at)
                kind = FUNCTION_AND_CALLER
                caller_cut_note = f"caller: `{name}` at {cpath}:{cline} left out at the size limit"

    quote = quoted.strip()

    def render(body: str, caller: str, note: str) -> str:
        text = "\n\n".join(b for b in (f"{title}\n{body}", caller, note) if b)
        if quote:
            text = f"quoted by the chair:\n{quote}\n\nread from the repo:\n{text}"
        return text

    # Over the cap, cut in pieces so the text still matches kind and keeps the
    # marked line: the caller goes first, then the window narrows around that
    # line, then the chair quote is shortened.
    text = render(body, caller, caller_note)
    if len(text) > MAX_CHARS and caller:
        caller_note, kind = caller_cut_note, FUNCTION_ONLY
        text = render(body, "", caller_note)
    for radius in (30, 20, 12, 8, 4, 2, 1, 0):
        if len(text) <= MAX_CHARS:
            break
        body = _around(lines, lo, hi, at, radius)
        text = render(body, "", caller_note)
    if len(text) > MAX_CHARS and quote:
        tail = " ... quote cut at the size limit"
        quote = quote[: max(0, len(quote) - (len(text) - MAX_CHARS) - len(tail))] + tail
        text = render(body, "", caller_note)
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
