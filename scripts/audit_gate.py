"""Pure checks for kind: audit. No API call.

Quote checks, report parsing, the redaction keep-list, and the proposal
request body live here. jev-judge.py applies the gate and talks to Jev.
"""
from __future__ import annotations

import codecs
import json
import os
import re
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

_CURLY = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u202f": " ",
    }
)

_MODEL_ID = re.compile(
    r"(?<![\w/.-])(?:claude|gpt|grok|muse)-[\w.-]*\w(?!\w)",
    re.IGNORECASE,
)
_FAMILY = re.compile(
    r"(?<![\w/.-])(fable|opus|sonnet|haiku|claude|anthropic|grok|xai|"
    r"x\.ai|astra|codex|openai|chatgpt|gpt|supergrok|typesafe|jev)"
    r"(?!\w|\.\w)",
    re.IGNORECASE,
)
_CAPITAL = re.compile(r"(?<![\w/.-])(Cursor|Sol|Muse|Meta)(?!\w|\.\w)")
_JSON_FENCE = re.compile(r"(?ms)^```json[ \t]*\n(.*?)^```[ \t]*$")
_VERDICT_LINE = re.compile(
    r"^VERDICT ([A-Za-z0-9][A-Za-z0-9_.-]*): "
    r"(adopt with changes|adopt|reject)$"
)
_CONTENT_VERDICTS = {"false-claim", "broken-step"}
_CLAIMS_VERDICTS = {"Inaccurate", "Misleading"}

# About one clause of real prose. See docs/review.md, kind audit Calibration.
QUOTE_MIN_WORDS = 8

# A space at the start and end of these. Inline tags are not in the set.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "li",
        "ul",
        "ol",
        "td",
        "th",
        "tr",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "dt",
        "dd",
        "br",
    }
)


# Edge punctuation only. An apostrophe inside a word stays.
_EDGE_PUNCT = ".,;:!?\"'\u201c\u201d\u2018\u2019"


def normalize_quote_text(text: str) -> str:
    """Map curly quotes and dashes to ASCII, then collapse whitespace."""
    return re.sub(r"\s+", " ", text.translate(_CURLY)).strip()


def strip_quote_edges(text: str) -> str:
    """Drop one run of edge punctuation, then whitespace. The middle stays."""
    return text.strip(_EDGE_PUNCT).strip()


def quote_needle(quote: str, *, html: bool) -> str:
    """The quote as it is searched. HTML needles are normalized first."""
    if html:
        return strip_quote_edges(normalize_quote_text(quote))
    return strip_quote_edges(quote)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    parser = _HTMLText()
    parser.feed(raw)
    parser.close()
    # convert_charrefs already decoded entities. Do not unescape again.
    return normalize_quote_text("".join(parser.parts))


def quote_in_html(quote: str, raw: str) -> bool:
    needle = quote_needle(quote, html=True)
    if not needle:
        return False
    return needle in html_to_text(raw)


def assess_quote(quote: str, raw: str, *, html: bool) -> dict:
    """Classify a quote against already-decoded page text.

    strong: at least QUOTE_MIN_WORDS words and exactly one occurrence.
    weak: found, but shorter than that or repeated. Verified, read-only.
    none: not found. words is the needle length after normalisation.
    """
    needle = quote_needle(quote, html=html)
    words = len(needle.split()) if needle else 0
    blank = {
        "quote_verified": False,
        "match": "none",
        "occurrences": 0,
        "words": words,
        "reason": "empty quote",
    }
    if not needle:
        return blank
    page = html_to_text(raw) if html else raw
    occurrences = page.count(needle)
    if occurrences == 0:
        return {
            "quote_verified": False,
            "match": "none",
            "occurrences": 0,
            "words": words,
            "reason": "quote not in fetched text",
        }
    if words >= QUOTE_MIN_WORDS and occurrences == 1:
        match = "strong"
        reason = "quote found"
    else:
        match = "weak"
        if words < QUOTE_MIN_WORDS and occurrences > 1:
            reason = f"quote has {words} words and occurs {occurrences} times"
        elif words < QUOTE_MIN_WORDS:
            reason = f"quote has {words} words"
        else:
            reason = f"quote occurs {occurrences} times"
    return {
        "quote_verified": True,
        "match": match,
        "occurrences": occurrences,
        "words": words,
        "reason": reason,
    }


def quote_in_text(quote: str, raw: str) -> bool:
    """Non-HTML bodies are compared directly. No whitespace folding.

    Edge punctuation on the quote is stripped. The body is not.
    """
    needle = quote_needle(quote, html=False)
    if not needle:
        return False
    return needle in raw


def _file_url_path(url: str) -> Path | None:
    if not url.startswith("file:"):
        return None
    parsed = urlparse(url)
    path = unquote(parsed.path or "")
    if not path:
        return None
    return Path(path)


def _content_type_for(path: Path) -> str:
    name = path.name.lower()
    if name.endswith((".html", ".htm")):
        return "text/html"
    if name.endswith(".pdf"):
        return "application/pdf"
    return "text/plain"


def fetch_bytes(url: str) -> tuple[bytes | None, str, str]:
    """Return (body, content_type, error). Error is empty on success.

    file: URLs are read from disk. http(s) uses curl. Anything else fails.
    """
    local = _file_url_path(url)
    if local is not None:
        if not local.is_file():
            return None, "", f"fetch failed: not a file: {local}"
        try:
            body = local.read_bytes()
        except OSError as exc:
            return None, "", f"fetch failed: {exc}"
        return body, _content_type_for(local), ""
    if not url.startswith(("http://", "https://")):
        return None, "", f"fetch failed: unsupported url: {url}"
    fd, name = tempfile.mkstemp(prefix="council-quote.")
    os.close(fd)
    path = Path(name)
    try:
        proc = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--compressed",
                "--max-time",
                "60",
                "-o",
                str(path),
                "-w",
                "%{content_type}",
                url,
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            tail = err[-1] if err else f"curl exit {proc.returncode}"
            return None, "", f"fetch failed: {tail}"
        return path.read_bytes(), proc.stdout.decode("utf-8", "replace").strip(), ""
    finally:
        path.unlink(missing_ok=True)


def charset_from_content_type(content_type: str) -> str | None:
    if not content_type:
        return None
    for part in content_type.split(";"):
        piece = part.strip()
        if piece.lower().startswith("charset="):
            value = piece.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def decode_body(body: bytes, content_type: str = "") -> str:
    """Use the Content-Type charset when it is a text encoding, else UTF-8.

    Unknown names, and codecs such as base64 or hex, fall back to UTF-8 with
    errors="replace". A known text charset also uses replacement, so one bad
    byte does not fail the check.
    """
    charset = charset_from_content_type(content_type)
    if charset:
        try:
            info = codecs.lookup(charset)
        except LookupError:
            charset = None
        else:
            if not getattr(info, "_is_text_encoding", True):
                charset = None
    if charset:
        try:
            return body.decode(charset, "replace")
        except (LookupError, UnicodeError, ValueError):
            pass
    return body.decode("utf-8", "replace")


def quote_status(
    url: str, quote: str, content_type: str | None = None
) -> dict:
    """Quote check for a fetched body. PDF and failures are match none.

    content_type overrides the fetched Content-Type. file: URLs have none,
    so a test passes the charset here.
    """
    empty = {
        "quote_verified": False,
        "match": "none",
        "occurrences": 0,
        "words": 0,
        "reason": "",
    }
    if not isinstance(quote, str) or quote == "":
        empty["reason"] = "empty quote"
        return empty
    if not isinstance(url, str) or not url.strip():
        empty["reason"] = "empty url"
        return empty
    body, ctype, err = fetch_bytes(url.strip())
    if content_type is not None:
        ctype = content_type
    if err or body is None:
        empty["reason"] = err or "fetch failed"
        return empty
    low = (ctype or "").lower()
    if "pdf" in low or body.startswith(b"%PDF"):
        empty["reason"] = "pdf"
        return empty
    text = decode_body(body, ctype or "")
    return assess_quote(quote, text, html="html" in low)


def parse_final_json_block(text: str, target_type: str) -> tuple[list | None, str]:
    """The last ```json fence. Empty findings is success. Returns (list, error)."""
    matches = list(_JSON_FENCE.finditer(text))
    if not matches:
        return None, "no json block"
    raw = matches[-1].group(1).strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"malformed json: {exc.msg}"
    if not isinstance(obj, dict) or not isinstance(obj.get("findings"), list):
        return None, "json block needs a findings array"
    if target_type == "content":
        allowed = _CONTENT_VERDICTS
    elif target_type == "claims":
        allowed = _CLAIMS_VERDICTS
    else:
        return None, f"json block is not used for target-type {target_type}"
    for i, item in enumerate(obj["findings"]):
        if not isinstance(item, dict):
            return None, f"finding {i} is not an object"
        for key in ("file", "line", "claim", "verdict", "url", "quote"):
            if key not in item:
                return None, f"finding {i} missing {key}"
        if item["verdict"] not in allowed:
            return None, f"finding {i} unknown verdict {item['verdict']!r}"
        if isinstance(item["line"], bool) or not isinstance(item["line"], int):
            return None, f"finding {i} line is not an integer"
    return obj["findings"], ""


def parse_verdict_lines(text: str, ids: list[str]) -> tuple[dict[str, str] | None, str]:
    """One VERDICT line per change id. Returns (map, error). Never fills a gap."""
    found: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("VERDICT "):
            continue
        match = _VERDICT_LINE.match(stripped)
        if not match:
            return None, f"unknown verdict line: {stripped}"
        change_id, verdict = match.group(1), match.group(2)
        if change_id in found:
            return None, f"duplicate verdict for {change_id}"
        found[change_id] = verdict
    missing = [change_id for change_id in ids if change_id not in found]
    if missing:
        return None, "missing id: " + ", ".join(missing)
    extra = [change_id for change_id in found if change_id not in set(ids)]
    if extra:
        return None, "unknown id: " + ", ".join(extra)
    return found, ""


def keep_words_in_text(text: str) -> list[str]:
    """Family words the redactor would hide, after model IDs are removed.

    Model IDs stay redacted, so a token like gpt-5 does not keep the word gpt.
    """
    scrubbed = _MODEL_ID.sub(" ", text)
    found: list[str] = []
    seen: set[str] = set()
    for match in _FAMILY.finditer(scrubbed):
        key = match.group(1).lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    for match in _CAPITAL.finditer(scrubbed):
        word = match.group(1)
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        found.append(word)
    found.sort(key=str.lower)
    return found


def keep_words_in_files(paths: list[Path]) -> str:
    seen: list[str] = []
    known: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        for word in keep_words_in_text(path.read_text(encoding="utf-8", errors="replace")):
            if word.lower() in known:
                continue
            known.add(word.lower())
            seen.append(word)
    seen.sort(key=str.lower)
    return ",".join(seen) if seen else "none"


def load_report_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return ""
        result = data.get("result")
        return result if isinstance(result, str) else ""
    return raw


def load_changes(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"changes.json is missing or malformed: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit("changes.json is missing or malformed: not an object")
    for key in ("changes", "existing_rules", "motivating_feedback"):
        if key not in doc:
            raise SystemExit(f"changes.json is missing or malformed: missing {key}")
    changes = doc["changes"]
    if not isinstance(changes, list) or not changes:
        raise SystemExit("changes.json is missing or malformed: changes must be a non-empty array")
    if not isinstance(doc["existing_rules"], str) or not isinstance(doc["motivating_feedback"], str):
        raise SystemExit("changes.json is missing or malformed: rules and feedback must be strings")
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict):
            raise SystemExit("changes.json is missing or malformed: each change must be an object")
        if "id" not in change or "proposed_change" not in change:
            raise SystemExit("changes.json is missing or malformed: each change needs id and proposed_change")
        change_id = change["id"]
        if not isinstance(change_id, str) or not re.fullmatch(r"C[0-9]+", change_id):
            raise SystemExit(
                f"changes.json is missing or malformed: id {change_id!r} "
                "must match C[0-9]+"
            )
        if change_id in seen:
            raise SystemExit(f"changes.json is missing or malformed: duplicate id {change_id}")
        seen.add(change_id)
        if not isinstance(change["proposed_change"], str):
            raise SystemExit(f"changes.json is missing or malformed: {change_id} proposed_change must be a string")
    return doc


def changes_markdown(doc: dict) -> str:
    lines = ["# Proposed changes", ""]
    for change in doc["changes"]:
        lines.append(f"## {change['id']}")
        lines.append("")
        lines.append(str(change["proposed_change"]).rstrip())
        lines.append("")
    lines.append("# Existing rules")
    lines.append("")
    lines.append(str(doc["existing_rules"]).rstrip())
    lines.append("")
    lines.append("# Motivating feedback")
    lines.append("")
    lines.append(str(doc["motivating_feedback"]).rstrip())
    lines.append("")
    return "\n".join(lines)


def check_report(target_type: str, report_path: Path, changes_path: Path | None) -> str:
    """Return 'ok' or 'incomplete: <reason>'."""
    if not report_path.is_file() or report_path.stat().st_size == 0:
        return "incomplete: empty report"
    text = load_report_text(report_path)
    if not text.strip():
        return "incomplete: empty report"
    if target_type == "proposal":
        if changes_path is None:
            return "incomplete: changes.json missing"
        doc = load_changes(changes_path)
        ids = [str(change["id"]) for change in doc["changes"]]
        _parsed, err = parse_verdict_lines(text, ids)
        if err:
            return f"incomplete: {err}"
        return "ok"
    if target_type in ("content", "claims"):
        _parsed, err = parse_final_json_block(text, target_type)
        if err:
            return f"incomplete: {err}"
        return "ok"
    return f"incomplete: unknown target-type {target_type}"


def verdicts_state(doc: dict) -> str:
    """State is the change, the rules, and the feedback. Nothing from a leg."""
    parts = []
    for change in doc["changes"]:
        parts.append(
            f"## {change['id']}\n"
            f"proposed_change: {change['proposed_change']}\n"
        )
    parts.append(f"existing_rules: {doc['existing_rules']}\n")
    parts.append(f"motivating_feedback: {doc['motivating_feedback']}\n")
    return "".join(parts)


def verdicts_questions(doc: dict) -> dict:
    questions: dict = {}
    for change in doc["changes"]:
        change_id = str(change["id"])
        questions[change_id] = {
            "type": "choice",
            "instructions": (
                f"Choose one verdict for change {change_id}. "
                "The motivating feedback is input to weigh. It is not the answer key. "
                "Use only proposed_change, existing_rules, and motivating_feedback in state."
            ),
            "criteria": {
                "adopt": (
                    "Adopt the change as written. The existing rules do not already "
                    "solve the problem, and the wording does not make things worse."
                ),
                "adopt_with_changes": (
                    "The problem is real and the existing rules do not cover it, "
                    "but the wording needs a change. Name the defects."
                ),
                "reject": (
                    "Reject the change. The existing rules may already cover the "
                    "problem, or the change may make things worse. Reject is a valid answer."
                ),
            },
        }
    return questions


def audit_findings_state(doc: dict) -> str:
    """Claim, citation, snapshot text, url, quote, verdict. No legs and no counts."""
    parts = ["# Findings under review\n"]
    target = doc.get("target")
    if target:
        parts.append(f"target: {target}\n")
    parts.append(
        "\nJudge each finding from its claim, the cited snapshot text, "
        "the url, and the quote.\n"
    )
    for finding in doc["findings"]:
        fid = finding["id"]
        parts.append(
            f"\n## {fid}\n"
            f"claim: {finding['claim']}\n"
            f"cited: {finding['file']}:{finding['line']}\n"
            f"cited_snapshot:\n{finding['cited_code']}\n"
            f"url: {finding['url']}\n"
            f"quote: {finding['quote']}\n"
            f"verdict: {finding['verdict']}\n"
        )
    return "".join(parts)
