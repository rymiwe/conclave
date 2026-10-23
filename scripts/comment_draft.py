#!/usr/bin/env python3
"""Draft helpers for a review comment: repro block and comment labels.

A throwaway script that verified a finding lives in the ballot directory as
repro/<finding-id>.<ext> (repro/f1.rb). The comment for that finding gets one
collapsed details block. A finding with no file there gets no block.

Labels (**Requested:**, **Advisory:**, **Informational:**, and the legacy
**Question:**) belong on a new inline comment, and on a review-body item, only
when the pull request is someone else's. A reply never keeps one. This module
strips labels on a reply and requires one on a new inline comment in someone
else's pull request. It does not enforce the absence of labels on your own
pull request.

GitHub rejects a review comment body longer than 65536 characters. A script
that does not fit is truncated inside the fence, with a note after the fence.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

# GitHub's review-comment body limit, in characters.
GITHUB_COMMENT_LIMIT = 65536

# Info string GitHub highlights, keyed by the filename extension.
_LANG = {
    "py": "python",
    "rb": "ruby",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "tsx",
    "jsx": "jsx",
    "rs": "rust",
    "go": "go",
    "yml": "yaml",
    "yaml": "yaml",
    "json": "json",
    "md": "markdown",
}

# Question is no longer drafted. It still counts, so an old draft is labeled
# and a reply that still carries it is stripped.
LABEL_RE = re.compile(r"\*\*(?:Requested|Advisory|Informational|Question):\*\*[ \t]*")
FINDING_ID = re.compile(r"f\d+")
EXT = re.compile(r"\.[A-Za-z0-9]+")


class ReproError(ValueError):
    """The repro directory does not match repro/<finding-id>.<ext>."""


def _links():
    path = Path(__file__).resolve().parent / "check-comment-links.py"
    spec = importlib.util.spec_from_file_location("check_comment_links", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LINKS = None


def links():
    global _LINKS
    if _LINKS is None:
        _LINKS = _links()
    return _LINKS


def language_for(filename: str) -> str:
    """Fence info string for a filename. Unknown extensions stay as written."""
    ext = Path(filename).suffix.lower().lstrip(".")
    return _LANG.get(ext, ext)


def fence_for(script: str, lang: str) -> str:
    """A code fence the script cannot close early.

    The closer is one backtick longer than any run inside the script, and it
    sits on its own line. The script bytes are unchanged apart from a single
    trailing newline when the script did not already end with one, so the
    closer is a fence and not another line of the script.
    """
    longest = max((len(run) for run in re.findall(r"`+", script)), default=0)
    ticks = "`" * max(3, longest + 1)
    inner = script if script.endswith("\n") or script == "" else script + "\n"
    return f"{ticks}{lang}\n{inner}{ticks}\n"


def _note(filename: str, kept: str, total: str) -> str:
    return (
        f"Truncated to {kept} of {total} bytes. "
        f"The full script is `repro/{filename}`."
    )


def render_repro_block(
    filename: str,
    script: str,
    *,
    budget: int | None = None,
    marker: str | None = None,
) -> str:
    """Collapsed details block. Truncates the script when budget is too small.

    budget is the maximum length of the block itself. None means no limit.
    An empty string means even the truncation note does not fit.
    marker, when set, is an HTML comment on the line above the details tag.
    The summary stays the filename. The marker tells two ballots with the
    same filename apart.
    """
    lang = language_for(filename)
    head = f"{marker}\n" if marker else ""

    def block_for(prefix: str, note: str | None) -> str:
        note_part = f"\n{note}\n" if note else ""
        return (
            f"{head}<details><summary>{filename}</summary>\n\n"
            f"{fence_for(prefix, lang)}"
            f"{note_part}</details>\n"
        )

    full = block_for(script, None)
    if budget is None or len(full) <= budget:
        return full

    def truncated(prefix: str) -> str:
        kept = len(prefix.encode("utf-8"))
        total = len(script.encode("utf-8"))
        return block_for(prefix, _note(filename, str(kept), str(total)))

    if len(truncated("")) > budget:
        return ""
    lo = 0
    hi = len(script)
    best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if len(truncated(script[:mid])) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return truncated(script[:best])


def join_comment(body: str, block: str) -> str:
    if not block:
        return body
    if not body:
        return block
    if body.endswith("\n\n"):
        return body + block
    if body.endswith("\n"):
        return body + "\n" + block
    return body + "\n\n" + block


def attach_repro(
    body: str,
    script_path: Path,
    *,
    limit: int = GITHUB_COMMENT_LIMIT,
    key: str | None = None,
) -> str:
    """Append the repro block when the comment does not already carry it.

    key identifies this file. It defaults to the filename. Two ballots can
    both attach f1.rb because each key is different. A hand-written summary
    with no marker is left alone. A script that does not fit under limit is
    truncated with a note. When the comment prose already fills the limit,
    the block is omitted.
    """
    filename = script_path.name
    marker = f"<!-- repro:{key or filename} -->"
    if marker in body:
        return body
    if f"<summary>{filename}</summary>" in body and "<!-- repro:" not in body:
        return body
    script = script_path.read_text(encoding="utf-8")
    if not body:
        budget = limit
        prefix = ""
    elif body.endswith("\n\n"):
        prefix = body
        budget = limit - len(prefix)
    elif body.endswith("\n"):
        prefix = body + "\n"
        budget = limit - len(prefix)
    else:
        prefix = body + "\n\n"
        budget = limit - len(prefix)
    if budget <= 0:
        return body
    block = render_repro_block(filename, script, budget=budget, marker=marker)
    if not block:
        return body
    return prefix + block if prefix else block


def find_repro(ballot_dir: Path, finding_id: str) -> Path | None:
    """The one file repro/<finding-id>.<ext>, or None when the directory is absent.

    Two files for one finding is an error. The id is f<digits>, so f1 never
    matches f10.
    """
    if not FINDING_ID.fullmatch(finding_id):
        raise ReproError(f"bad finding id {finding_id!r}")
    directory = ballot_dir / "repro"
    if not directory.is_dir():
        return None
    matches = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.stem != finding_id:
            continue
        if not EXT.fullmatch(path.suffix):
            continue
        matches.append(path)
    if not matches:
        return None
    if len(matches) > 1:
        names = ", ".join(sorted(path.name for path in matches))
        raise ReproError(f"more than one repro for {finding_id}: {names}")
    return matches[0]


def _outside_fences(text: str, start: int, end: int) -> bool:
    mod = links()
    return not mod.covered((start, end), mod.fence_spans(text))


def label_spans(text: str) -> list[tuple[int, int]]:
    """Label matches that sit in the prose, not inside a fenced script."""
    return [
        match.span()
        for match in LABEL_RE.finditer(text)
        if _outside_fences(text, match.start(), match.end())
    ]


def has_label(text: str) -> bool:
    return bool(label_spans(text))


def strip_labels(text: str) -> str:
    """Remove prose labels. A label inside a fenced script stays."""
    spans = label_spans(text)
    if not spans:
        return text
    parts: list[str] = []
    last = 0
    for start, end in spans:
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def draft_comment(
    body: str,
    *,
    kind: str,
    others_pr: bool = False,
    repro: Path | None = None,
    limit: int = GITHUB_COMMENT_LIMIT,
) -> str:
    """kind is inline, reply, or body.

    A reply loses its labels. A new inline comment on someone else's pull
    request must already have one. A repro file is attached to an inline
    comment or a review-body item, and not to a reply.
    """
    if kind not in ("inline", "reply", "body"):
        raise ValueError(f"bad comment kind {kind!r}")
    if kind == "reply":
        body = strip_labels(body)
    elif kind == "inline" and others_pr and not has_label(body):
        raise ValueError(
            "new inline comment on someone else's PR needs "
            "**Requested:**, **Advisory:**, or **Informational:**"
        )
    if repro is not None and kind != "reply":
        body = attach_repro(body, repro, limit=limit)
    return body
