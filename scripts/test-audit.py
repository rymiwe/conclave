#!/usr/bin/env python3
"""Audit quote check, gate, parsers, and keep-list. No network and no API call."""
from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "audit"


def load(name: str, filename: str):
    path = ROOT / filename
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def _council_brief(brief: str, flag_kind: str = "") -> str:
    """Run parse_brief_header and brief_body. Output is headers, then the body."""
    repo = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        """\
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        # shellcheck disable=SC1091
        . "$1"
        BRIEF="$2"
        FLAG_KIND="$3"
        parse_brief_header
        printf 'HEADER_KIND=%s\\n' "$HEADER_KIND"
        printf 'HEADER_EFFORT=%s\\n' "$HEADER_EFFORT"
        printf 'HEADER_TARGET=%s\\n' "$HEADER_TARGET"
        printf 'HEADER_TARGET_TYPE=%s\\n' "$HEADER_TARGET_TYPE"
        printf '%s\\n' '---BODY---'
        brief_body
        """
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "brief.md"
        path.write_text(brief, encoding="utf-8")
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(repo / "scripts" / "council.sh"), str(path), flag_kind],
            check=True,
            capture_output=True,
            text=True,
        )
    return proc.stdout


def _field(text: str, name: str) -> str:
    prefix = name + "="
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise SystemExit(f"missing {name}")


def test_brief_header() -> None:
    review = (
        "kind: review\n"
        "target: the retry path in the importer\n"
        "\n"
        "Check the date math.\n"
    )
    got = _council_brief(review)
    body = got.split("---BODY---\n", 1)[1]
    # brief_body at 94f9443, for these inputs.
    check(
        body == "target: the retry path in the importer\n\nCheck the date math.\n",
        "review brief_body matches 94f9443",
    )
    check(
        body == "target: the retry path in the importer\n\nCheck the date math.\n",
        "review keeps the target line",
    )
    check("HEADER_TARGET=\n" in got and "HEADER_KIND=review\n" in got, "review does not set target")

    # kind: after the target lines is still audit, so both keys are headers.
    audit_after = (
        "target: page.html\n"
        "target-type: content\n"
        "kind: audit\n"
        "\n"
        "Check the page.\n"
    )
    got = _council_brief(audit_after)
    body = got.split("---BODY---\n", 1)[1]
    check(body == "Check the page.\n", "audit strips target keys when kind comes after")
    check("HEADER_TARGET=page.html\n" in got, "audit sets target when kind comes after")
    check("HEADER_TARGET_TYPE=content\n" in got, "audit sets target-type when kind comes after")

    audit_before = (
        "kind: audit\n"
        "target-type: claims\n"
        "target: https://example.test/page\n"
        "\n"
        "Check the page.\n"
    )
    got = _council_brief(audit_before)
    body = got.split("---BODY---\n", 1)[1]
    check(body == "Check the page.\n", "audit strips target keys when kind comes first")
    check("HEADER_TARGET=https://example.test/page\n" in got, "audit sets target")
    check("HEADER_TARGET_TYPE=claims\n" in got, "audit sets target-type")

    # --kind wins over the header line.
    got = _council_brief(audit_before, "review")
    body = got.split("---BODY---\n", 1)[1]
    check(
        body
        == "target-type: claims\ntarget: https://example.test/page\n\nCheck the page.\n",
        "flag review keeps target lines like 94f9443",
    )
    check("HEADER_TARGET=\n" in got, "flag review does not set HEADER_TARGET")

    # A target line ends the scan on a non-audit kind, as 94f9443's *) branch did.
    # HEADER_EFFORT and HEADER_TARGET stay empty; the body keeps both lines.
    stopped = "kind: review\ntarget: x\neffort: low\n\nBody line.\n"
    got = _council_brief(stopped)
    check(_field(got, "HEADER_EFFORT") == "", "effort after target matches 94f9443")
    check(_field(got, "HEADER_TARGET") == "", "target on review matches 94f9443")
    check(
        got.split("---BODY---\n", 1)[1] == "target: x\neffort: low\n\nBody line.\n",
        "body after target matches 94f9443",
    )


def test_gzip_quote(gate) -> None:
    """A server that gzips even when the client did not ask must still verify."""
    sentence = "Python 3.12.0 is the newest major release of the Python programming language"
    payload = gzip.compress(
        f"<html><body><p>{sentence}</p></body></html>".encode("utf-8")
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/page.html"
        status = gate.quote_status(url, sentence)
        check(
            status["quote_verified"] and status["match"] == "strong",
            f"gzip body quote ({status['match']} {status['reason']})",
        )
    finally:
        server.shutdown()
        server.server_close()


def test_quotes(gate) -> None:
    page = (FIXTURES / "page.html").read_text(encoding="utf-8")
    check(gate.quote_in_html("The price is $12 today.", page), "tags across the quote")
    check(gate.quote_in_html("A & B shipped.", page), "html entity")
    check(gate.quote_in_html('She said "hello" and left.', page), "curly quotes")
    check(gate.quote_in_html("hello world", page), "whitespace")
    check(not gate.quote_in_html("The price is $13 today.", page), "absent quote")
    check(gate.quote_in_text("Exact.", "Exact."), "direct text")
    check(not gate.quote_in_text("exact.", "Exact."), "direct text is case sensitive")
    page_url = (FIXTURES / "page.html").as_uri()
    status = gate.quote_status(page_url, "The price is $12 today.")
    check(
        status["quote_verified"] and status["match"] == "weak",
        f"file url quote ({status['match']} {status['reason']})",
    )
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "tiny.pdf"
        pdf.write_bytes(b"%PDF-1.4\nnot a real pdf\n")
        status = gate.quote_status(pdf.as_uri(), "not a real pdf")
        check(
            (not status["quote_verified"]) and status["reason"] == "pdf",
            f"pdf ({status['reason']})",
        )
    test_gzip_quote(gate)
    status = gate.quote_status((FIXTURES / "missing-page.html").as_uri(), "x")
    check(
        (not status["quote_verified"]) and status["reason"].startswith("fetch failed"),
        f"missing file ({status['reason']})",
    )
    # The live page wraps the sentence in &quot; and has no trailing period.
    blocked = (
        "<p>the error message &quot;Something went wrong. Your parent has "
        "blocked access to games and apps that aren't published by "
        "Microsoft&quot; it means</p>"
    )
    quoted = (
        "Something went wrong. Your parent has blocked access to games and "
        "apps that aren't published by Microsoft."
    )
    check(gate.quote_in_html(quoted, blocked), "trailing period on the quote")
    check(
        not gate.quote_in_html(quoted.replace("games", "game"), blocked),
        "inner word differs",
    )
    check(not gate.quote_in_html("...", blocked), "empty quote after stripping")
    check(not gate.quote_in_html('"."', blocked), "punctuation-only quote")
    status = gate.quote_status(page_url, "...")
    check(
        (not status["quote_verified"]) and status["reason"] == "empty quote",
        f"empty quote status ({status['reason']})",
    )
    check(gate.html_to_text("<p>One.</p><p>Two.</p>") == "One. Two.", "paragraph gap")
    check(gate.html_to_text("<p>hello<br>world</p>") == "hello world", "br gap")
    check(
        gate.html_to_text("<td>Price</td><td>$12</td>") == "Price $12",
        "table cell gap",
    )
    check(gate.html_to_text("aren<span>'</span>t") == "aren't", "inline span stays joined")
    raw = "<p>Write &amp;lt;b&amp;gt; for a tag.</p>"
    text = gate.html_to_text(raw)
    check(text == "Write &lt;b&gt; for a tag.", f"single unescape ({text!r})")
    check("Write <b> for a tag." not in text, "no second unescape")
    check(gate.quote_in_html("Write &lt;b&gt; for a tag.", raw), "literal entities verify")
    check(not gate.quote_in_html("Write <b> for a tag.", raw), "over-decoded tag does not verify")
    short = gate.assess_quote("e", "<p>Anything here.</p>", html=True)
    check(
        short["match"] == "weak" and short["quote_verified"] and short["words"] == 1,
        f"single letter is weak ({short})",
    )
    sentence = "Eight words make a strong quote match here."
    once = gate.assess_quote(sentence, f"<p>{sentence}</p>", html=True)
    check(
        once["match"] == "strong" and once["occurrences"] == 1 and once["words"] == 8,
        f"eight words once is strong ({once})",
    )
    twice = gate.assess_quote(
        sentence, f"<p>{sentence}</p><p>{sentence}</p>", html=True
    )
    check(
        twice["match"] == "weak"
        and twice["quote_verified"]
        and twice["occurrences"] == 2,
        f"eight words twice is weak ({twice})",
    )
    page_text = "The café opened in 1999 \u201clate\u201d by most accounts."
    encoded = f"<p>{page_text}</p>".encode("cp1252")
    decoded = gate.decode_body(encoded, "text/html; charset=windows-1252")
    check("café" in decoded and "\u201c" in decoded, "windows-1252 decode")
    quote = 'The café opened in 1999 "late" by most accounts.'
    with tempfile.TemporaryDirectory() as tmp:
        page_path = Path(tmp) / "cafe.html"
        page_path.write_bytes(encoded)
        status = gate.quote_status(
            page_path.as_uri(),
            quote,
            content_type="text/html; charset=windows-1252",
        )
    check(status["match"] == "strong", f"charset quote ({status})")
    broken = gate.decode_body(encoded, "text/html")
    check("café" not in broken, "utf-8 fallback loses the accent")


def test_gate(judge) -> None:
    cases = [
        ("Misleading", True, "post", 0.95, "second look"),
        ("false-claim", True, "post", 0.7, "post"),
        ("broken-step", True, "post", 0.7, "post"),
        ("Inaccurate", True, "post", 0.7, "post"),
        ("false-claim", False, "post", 0.91, "second look"),
        ("Inaccurate", True, "post", 0.69, "second look"),
        ("false-claim", True, "drop", 0.99, "second look"),
        ("false-claim", True, "hold", 0.99, "second look"),
    ]
    for verdict, verified, choice, conf, want in cases:
        got, note = judge.bind_audit_finding(verdict, verified, choice, conf)
        if got != want:
            raise SystemExit(
                f"FAIL gate {verdict} verified={verified} {choice} {conf}: "
                f"got {got!r} ({note}), want {want!r}"
            )
        print(f"ok gate {verdict} verified={verified} {choice} {conf} -> {got}")


def test_json(gate) -> None:
    good = 'preamble\n```json\n{"findings": []}\n```\n'
    parsed, err = gate.parse_final_json_block(good, "content")
    check(parsed == [] and err == "", f"empty list ({err})")
    missing, err = gate.parse_final_json_block("no fence here", "content")
    check(missing is None and err == "no json block", f"missing block ({err})")
    bad, err = gate.parse_final_json_block("```json\n{not json}\n```\n", "content")
    check(bad is None and err.startswith("malformed json"), f"malformed ({err})")
    wrong, err = gate.parse_final_json_block(
        '```json\n{"findings": [{"file": "a", "line": 1, "claim": "c", '
        '"verdict": "Inaccurate", "url": "u", "quote": "q"}]}\n```\n',
        "content",
    )
    check(wrong is None and "unknown verdict" in err, f"wrong verdict for content ({err})")
    last, err = gate.parse_final_json_block(
        '```json\n{"findings": []}\n```\n'
        '```json\n{"findings": [{"file": "a", "line": 2, "claim": "c", '
        '"verdict": "false-claim", "url": "u", "quote": "q"}]}\n```\n',
        "content",
    )
    check(
        err == "" and last is not None and len(last) == 1 and last[0]["line"] == 2,
        "last json block wins",
    )


def test_verdicts(gate) -> None:
    ids = ["C1", "C2"]
    good, err = gate.parse_verdict_lines(
        "note\nVERDICT C1: adopt\nVERDICT C2: adopt with changes\n",
        ids,
    )
    check(err == "" and good == {"C1": "adopt", "C2": "adopt with changes"}, f"good verdicts ({err})")
    _parsed, err = gate.parse_verdict_lines("VERDICT C1: reject\n", ids)
    check(err.startswith("missing id"), f"missing id ({err})")
    _parsed, err = gate.parse_verdict_lines(
        "VERDICT C1: ship it\nVERDICT C2: reject\n", ids
    )
    check(err.startswith("unknown verdict line"), f"unknown value ({err})")
    _parsed, err = gate.parse_verdict_lines(
        "VERDICT C1: reject\nVERDICT C2: reject\nVERDICT C3: adopt\n", ids
    )
    check(err.startswith("unknown id"), f"unknown id ({err})")


def test_keep(gate) -> None:
    check(gate.keep_words_in_text("Use Claude today.") == ["claude"], "keep claude")
    check(gate.keep_words_in_text("gpt-5.4 is listed") == [], "model id is not kept")
    check(gate.keep_words_in_text("src/grok/client.ts") == [], "path is not a word")
    check(gate.keep_words_in_text("the grok tool") == ["grok"], "keep grok")
    check(gate.keep_words_in_text("Cursor and Sol") == ["Cursor", "Sol"], "keep Cursor and Sol")
    check(gate.keep_words_in_text("affable octopus") == [], "no substring match")
    text = (FIXTURES / "notes.txt").read_text(encoding="utf-8")
    check(gate.keep_words_in_text(text) == ["claude"], "context file keep list")


def test_confidence(judge) -> None:
    check(
        judge.confidence_of_value("design", "no automatic decision")
        == "voters propose different plans",
        "design confidence_of",
    )
    check(
        judge.confidence_of_value("mixed", "implement")
        == "voters propose the same plan",
        "mixed implement",
    )
    check(
        judge.confidence_of_value("mixed", "implement with changes")
        == "voters share a plan with minor changes",
        "mixed with changes",
    )
    check(
        judge.confidence_of_value(
            "review", "comments to post (1 post, 0 hold, 0 second look)"
        )
        == "lowest Jev confidence among findings gated to post",
        "review post confidence_of",
    )
    check(
        judge.confidence_of_value("review", "human review (0 post, 1 hold, 0 second look)")
        == "none",
        "review none",
    )
    check(
        judge.confidence_of_value("audit", "human review", {"gate": "verdicts"})
        == "none",
        "proposal confidence_of",
    )
    check(
        judge.confidence_of_value("audit", "changes to draft (1 post, 0 hold, 1 second look)")
        == "lowest Jev confidence among findings gated to post",
        "audit findings confidence_of",
    )
    header = judge.format_judgment_header(
        decision="clean (0 post, 0 hold, 0 second look)",
        confidence="n/a",
        judge_model="n/a",
        meta={"kind": "review"},
    )
    lines = header.splitlines()
    check(lines[2] == "confidence: n/a", "confidence stays put")
    check(lines[3].startswith("confidence_of:"), "confidence_of follows confidence")
    # One opinion, Jev says implement at 0.90, the script overrides.
    decision, _notes, _changes = judge.bind_decision(
        "implement", 0.90, 0.99, 0.99, 1, ["only one opinion"]
    )
    check(decision == "no automatic decision", f"one opinion override ({decision})")
    overridden = judge.header_confidence("implement", decision, 0.90)
    check(overridden == "n/a", f"override confidence ({overridden})")
    header = judge.format_judgment_header(
        decision=decision,
        confidence=overridden,
        judge_model="test",
        meta={"kind": "design"},
    )
    lines = header.splitlines()
    check(lines[0] == "kind: design", "override kind")
    check(lines[1] == "decision: no automatic decision", "override decision")
    check(lines[2] == "confidence: n/a", "override confidence line")
    check(lines[3] == "confidence_of: none", "n/a confidence_of is none")


def _write_findings(directory: Path, doc: dict) -> Path:
    path = directory / "findings.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _expect_exit(fn, label: str) -> None:
    try:
        fn()
    except SystemExit as exc:
        print(f"ok {label} ({exc})")
        return
    raise SystemExit(f"FAIL {label} did not die")


def test_source_quote(judge) -> None:
    sentence = "Use the claude tool only when the chair asks for it."
    blinded = "Use the [model] tool only when the chair asks for it."
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        page = d / "page.html"
        page.write_text(f"<p>{sentence}</p>\n", encoding="utf-8")
        report = d / "voter-a.json"
        block = {
            "findings": [
                {
                    "file": "snapshot.html",
                    "line": 1,
                    "claim": "c",
                    "verdict": "false-claim",
                    "url": page.as_uri(),
                    "quote": sentence,
                }
            ]
        }
        text = "preamble\n```json\n" + json.dumps(block) + "\n```\n"
        report.write_text(json.dumps({"result": text}), encoding="utf-8")
        blind = d / "blind"
        blind.mkdir()
        (blind / "key.txt").write_text(f"A={report}\n", encoding="utf-8")
        (d / "header.json").write_text(
            json.dumps({"kind": "audit", "target_type": "content"}) + "\n",
            encoding="utf-8",
        )
        finding = {
            "id": "f1",
            "claim": "c",
            "file": "snapshot.html",
            "line": 1,
            "cited_code": "x",
            "evidence": "no run was made",
            "repro_executed": False,
            "legs": {"A": "found"},
            "verdict": "false-claim",
            "url": "https://example.test/blinded",
            "quote": blinded,
            "source": {"leg": "A", "row": 0},
        }
        doc = {"findings": [finding]}
        loaded = judge.load_findings(_write_findings(d, doc), audit=True)
        matches = judge.write_quote_check(d, loaded)
        check(matches["f1"] == "strong", f"source quote is strong ({matches})")
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check("claude" not in raw.lower(), "quote-check has no family word")
        check(sentence not in raw, "quote-check has no unblinded quote")
        check(page.as_uri() not in raw, "quote-check has no unblinded url")
        check('"source_used": true' in raw, "source_used")
        request = json.dumps(judge.audit_findings_request(loaded))
        check("claude" not in request.lower(), "jev request has no family word")
        check("[model]" in request, "jev keeps the blinded quote")

        bad_row = dict(finding)
        bad_row["source"] = {"leg": "A", "row": 5}
        judge.write_quote_check(d, {"findings": [bad_row]})
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check('"match": "none"' in raw and "row out of range" in raw, "row out of range")
        check("claude" not in raw.lower(), "bad row leaks nothing")

        unknown = dict(finding)
        unknown["source"] = {"leg": "B", "row": 0}
        judge.write_quote_check(d, {"findings": [unknown]})
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check("unknown leg B" in raw and '"source_used": false' in raw, "unknown leg")

        (blind / "key.txt").unlink()
        judge.write_quote_check(d, doc)
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check("missing key file" in raw, "missing key")

        (blind / "key.txt").write_text(f"A={d / 'notes.md'}\n", encoding="utf-8")
        (d / "notes.md").write_text("no json here\n", encoding="utf-8")
        judge.write_quote_check(d, doc)
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check("unparsable report" in raw, "unparsable report")

        shape = dict(finding)
        shape["source"] = {"leg": "AA", "row": 0}
        _expect_exit(
            lambda: judge.load_findings(_write_findings(d, {"findings": [shape]}), audit=True),
            "source leg shape",
        )
        shape["source"] = {"leg": "A", "row": -1}
        _expect_exit(
            lambda: judge.load_findings(_write_findings(d, {"findings": [shape]}), audit=True),
            "source row shape",
        )


def _source_finding(url: str, quote: str, *, file: str, line: int, row: int) -> dict:
    return {
        "id": "f1",
        "claim": "c",
        "file": file,
        "line": line,
        "cited_code": "x",
        "evidence": "no run was made",
        "repro_executed": False,
        "legs": {"A": "found"},
        "verdict": "false-claim",
        "url": "https://example.test/blinded",
        "quote": "blinded quote text stays here",
        "source": {"leg": "A", "row": row},
    }


def _write_leg_report(d: Path, findings: list) -> None:
    report = d / "voter-a.json"
    text = "preamble\n```json\n" + json.dumps({"findings": findings}) + "\n```\n"
    report.write_text(json.dumps({"result": text}), encoding="utf-8")
    blind = d / "blind"
    blind.mkdir(exist_ok=True)
    (blind / "key.txt").write_text(f"A={report}\n", encoding="utf-8")
    (d / "header.json").write_text(
        json.dumps({"kind": "audit", "target_type": "content"}) + "\n",
        encoding="utf-8",
    )


def test_nontext_charset(gate) -> None:
    body = "hello from the page\n".encode("utf-8")
    for name in ("base64", "hex"):
        decoded = gate.decode_body(body, f"text/plain; charset={name}")
        check(decoded == "hello from the page\n", f"charset {name} returns text")


def test_source_fetch_reason(judge) -> None:
    quote = "Eight words make a strong quote match here."
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        missing = d / "missing-secret-page.html"
        cases = (
            (
                "http://council-quote.invalid/hidden-quote-path",
                "fetch failed",
                ("council-quote.invalid", "hidden-quote-path", "http://council-quote.invalid"),
            ),
            (
                "ftp://secret-ftp.example/hidden-file",
                "unsupported url",
                ("secret-ftp.example", "hidden-file", "ftp://"),
            ),
            (
                missing.as_uri(),
                "fetch failed",
                ("missing-secret-page.html", missing.as_uri()),
            ),
        )
        for url, want, banned in cases:
            row = {
                "file": "snapshot.html",
                "line": 1,
                "claim": "c",
                "verdict": "false-claim",
                "url": url,
                "quote": quote,
            }
            _write_leg_report(d, [row])
            finding = _source_finding(url, quote, file="snapshot.html", line=1, row=0)
            loaded = judge.load_findings(
                _write_findings(d, {"findings": [finding]}), audit=True
            )
            judge.write_quote_check(d, loaded)
            got = json.loads((d / "quote-check.json").read_text(encoding="utf-8"))
            reason = got["findings"][0]["reason"]
            check(reason == want, f"source reason {want} ({reason!r})")
            for bit in banned:
                check(bit not in reason, f"source reason hides {bit} ({reason!r})")


def test_source_row_mismatch(judge) -> None:
    sentence = "Eight words make a strong quote match here."
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        page = d / "right.html"
        page.write_text(f"<p>{sentence}</p>\n", encoding="utf-8")
        other = d / "other.html"
        other.write_text("<p>Something else entirely on this page.</p>\n", encoding="utf-8")
        rows = [
            {
                "file": "a.txt",
                "line": 1,
                "claim": "c",
                "verdict": "false-claim",
                "url": other.as_uri(),
                "quote": "Something else entirely on this page.",
            },
            {
                "file": "b.txt",
                "line": 2,
                "claim": "c",
                "verdict": "false-claim",
                "url": page.as_uri(),
                "quote": sentence,
            },
        ]
        _write_leg_report(d, rows)
        finding = _source_finding(page.as_uri(), sentence, file="a.txt", line=1, row=1)
        loaded = judge.load_findings(_write_findings(d, {"findings": [finding]}), audit=True)
        matches = judge.write_quote_check(d, loaded)
        raw = (d / "quote-check.json").read_text(encoding="utf-8")
        check(matches["f1"] == "none", f"wrong source row is none ({matches})")
        check('"reason": "source not resolved"' in raw, "wrong source row is source not resolved")
        check(sentence not in raw, "wrong source row did not verify the other quote")
        check(page.as_uri() not in raw, "wrong source row did not fetch the other url")


def test_source_only_exec() -> None:
    repo = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env["COUNCIL_SOURCE_ONLY"] = "1"
    env.pop("TYPESAFE_API_KEY", None)
    env.pop("JEV_API_KEY", None)
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "council.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    check(proc.returncode != 0, f"source-only exec exits non-zero ({proc.returncode})")
    check("Usage: scripts/conclave.sh" in proc.stderr, "source-only exec prints usage")


def test_audit_names() -> None:
    repo = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        r"""
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        . "$1"
        work="$2"
        BRIEF="$work/brief.md"
        printf '%s\n' 'kind: audit' 'target-type: content' '' 'Look at the date math.' >"$BRIEF"
        AUDIT_TYPE=content
        AUDIT_FINAL_URL='https://docs.claude.com/en/docs'
        AUDIT_FILE_LIST='snapshot.html'
        AUDIT_CAPTURED='2026-10-01T00:00:00Z'
        audit_check_brief_words
        rest="$(audit_brief_rest)"
        word="$(review_forbidden_word "$rest")"
        [ -z "$word" ]
        target="$(audit_target_line)"
        hit="$(review_forbidden_word "$target")"
        [ -n "$hit" ]
        printf '%s\n' 'kind: audit' '' 'Ask claude about the date math.' >"$BRIEF"
        set +e
        err="$(audit_check_brief_words 2>&1)"
        ec=$?
        set -e
        [ "$ec" -ne 0 ]
        printf '%s\n' "$err" | grep -q 'claude'
        printf '%s\n' "$err" | grep -q -- '--context' && exit 1
        printf 'body-dies\n'
        printf 'lineup body\n' >"$work/lineup.md"
        printf 'notes\n' >"$work/grok-notes.txt"
        printf 'page\n' >"$work/second.html"
        printf 'file\n' >"$work/context"
        AUDIT_TARGET="$work/lineup.md"
        AUDIT_TYPE=content
        CONTEXT_FILES=("$work/grok-notes.txt" "$work/second.html")
        audit_capture_into "$work/dest"
        [ -f "$work/dest/target.md" ]
        [ ! -e "$work/dest/lineup.md" ]
        [ -f "$work/dest/context/context-1.txt" ]
        [ -f "$work/dest/context/context-2.html" ]
        AUDIT_TARGET="$work/context"
        CONTEXT_FILES=()
        audit_capture_into "$work/dest2"
        [ -f "$work/dest2/target" ]
        [ -d "$work/dest2/context" ] || mkdir -p "$work/dest2/context"
        printf 'names-ok\n'
        """
    )
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(repo / "scripts" / "council.sh"), tmp],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise SystemExit(
            f"FAIL audit names\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    check("body-dies" in proc.stdout and "names-ok" in proc.stdout, "neutral names and word check")


def test_dry_run_target_bytes() -> None:
    """audit_dry_run must not overwrite the copied target with metadata or lineup."""
    repo = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        r"""
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        export COUNCIL_SEAT_USAGE=/no/such/seat-usage
        . "$1"
        work="$2"
        KIND=audit
        DRY_RUN=1
        ID=audit-target-bytes
        VOTERS=""
        FLAG_TARGET_TYPE=content
        FLAG_PR=""
        FLAG_BASE=""
        FLAG_HEAD=""
        VERDICTS_MODE=0
        CONTEXT_FILES=()
        BRIEF="$work/brief.md"
        printf '%s\n' 'kind: audit' 'target-type: content' '' 'Look at the date math.' >"$BRIEF"
        run_one() {
          local srcname="$1" rel="$2"
          local dir="$work/out-$rel"
          mkdir -p "$dir"
          DIR="$dir"
          FLAG_TARGET="$work/$srcname"
          printf 'source-bytes-%s\n' "$srcname" >"$FLAG_TARGET"
          audit_dry_run >/dev/null
          cmp -s "$FLAG_TARGET" "$dir/preview/$rel"
          if cmp -s "$FLAG_TARGET" "$dir/preview/snapshot.txt"; then
            printf 'metadata collided: %s\n' "$srcname" >&2
            exit 1
          fi
        }
        run_one notes.txt target.txt
        run_one lineup.md target.md
        [ -f "$work/out-target.md/preview/lineup.md" ]
        if cmp -s "$work/lineup.md" "$work/out-target.md/preview/lineup.md"; then
          printf 'lineup collided\n' >&2
          exit 1
        fi
        run_one snapshot.txt target.txt
        printf 'bytes-ok\n'
        """
    )
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(repo / "scripts" / "council.sh"), tmp],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0 or "bytes-ok" not in proc.stdout:
        raise SystemExit(
            f"FAIL dry-run target bytes\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    check(True, "dry run keeps target.txt, target.md, and a snapshot.txt source")


def _c1_row(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("| C1 |"):
            return line
    return ""


def test_stale_verdicts(judge) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "changes.json").write_text(
            (FIXTURES / "changes.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (d / "header.json").write_text(
            json.dumps({"kind": "audit", "target_type": "proposal"}) + "\n",
            encoding="utf-8",
        )
        canned = FIXTURES / "verdicts-canned.json"
        with contextlib.redirect_stdout(io.StringIO()):
            judge.verdicts_main([str(d), "--canned", str(canned)])
        row = _c1_row((d / "verdicts.md").read_text(encoding="utf-8"))
        check("0.580" in row, f"canned C1 ({row})")
        # Marker wins even when the previous raw file is still on disk.
        (d / "jev-verdicts-unavailable").write_text("no API key\n", encoding="utf-8")
        text = judge.verdicts_render(d)
        row = _c1_row(text)
        check("unavailable" in row and "0.580" not in row, f"marker first ({row})")
        saved = {}
        for key in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
            saved[key] = os.environ.pop(key, None)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                judge.verdicts_main([str(d)])
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value
        row = _c1_row((d / "verdicts.md").read_text(encoding="utf-8"))
        check(row.endswith("| unavailable |"), f"no-key C1 ({row})")
        check(not (d / "jev-verdicts-raw.json").exists(), "stale raw removed")
        check((d / "jev-verdicts-unavailable").is_file(), "unavailable marker")


def test_change_ids(gate) -> None:
    base = json.loads((FIXTURES / "changes.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "changes.json"
        doc = json.loads(json.dumps(base))
        doc["changes"][0]["id"] = "C12"
        doc["changes"][1]["id"] = "C1"
        path.write_text(json.dumps(doc), encoding="utf-8")
        loaded = gate.load_changes(path)
        check(
            [change["id"] for change in loaded["changes"]] == ["C12", "C1"],
            "C12 and C1",
        )
        for bad in ("gpt-review", "opus"):
            doc["changes"][0]["id"] = bad
            path.write_text(json.dumps(doc), encoding="utf-8")
            _expect_exit(lambda: gate.load_changes(path), f"reject {bad}")


def test_verdicts_header_fallback() -> None:
    """--verdicts with no kind on the brief reads kind and target_type from header.json."""
    repo = Path(__file__).resolve().parent.parent
    cid = "audit-test-verdicts-header"
    d = repo / "tmp" / "council" / cid
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    try:
        (d / "brief.md").write_text("Check the wording.\n", encoding="utf-8")
        (d / "header.json").write_text(
            json.dumps(
                {"kind": "audit", "target_type": "proposal", "gate": "verdicts"}
            )
            + "\n",
            encoding="utf-8",
        )
        (d / "changes.json").write_text(
            (FIXTURES / "changes.json").read_text(encoding="utf-8"), encoding="utf-8"
        )
        env = os.environ.copy()
        env.pop("TYPESAFE_API_KEY", None)
        env.pop("JEV_API_KEY", None)
        proc = subprocess.run(
            [
                "bash",
                "scripts/council.sh",
                cid,
                "--verdicts",
                "--canned",
                str(FIXTURES / "verdicts-canned.json"),
            ],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(
                f"FAIL verdicts header fallback\nstdout={proc.stdout}\nstderr={proc.stderr}"
            )
        row = _c1_row((d / "verdicts.md").read_text(encoding="utf-8"))
        check("0.580" in row, f"header.json kind fallback ({row})")
        check("kind inconclusive" not in proc.stderr, "did not die on kind")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_snapshot_ignores_global_gitconfig() -> None:
    repo = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        r"""
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        . "$1"
        work="$2"
        git init -q "$work/repo"
        printf 'hello\n' >"$work/repo/notes.md"
        printf '*.md\n' >"$work/exclude"
        mkdir -p "$work/hooks"
        printf '#!/bin/sh\nexit 1\n' >"$work/hooks/pre-commit"
        chmod +x "$work/hooks/pre-commit"
        export GIT_CONFIG_GLOBAL="$work/global.gitconfig"
        git config --file "$GIT_CONFIG_GLOBAL" core.excludesFile "$work/exclude"
        git config --file "$GIT_CONFIG_GLOBAL" core.hooksPath "$work/hooks"
        audit_commit_snapshot "$work/repo"
        git -C "$work/repo" ls-files --error-unmatch notes.md >/dev/null
        printf 'snapshot-ok\n'
        """
    )
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            ["bash", "-c", script, "bash", str(repo / "scripts" / "council.sh"), tmp],
            capture_output=True,
            text=True,
        )
    if proc.returncode != 0:
        raise SystemExit(
            f"FAIL snapshot gitconfig\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    check("snapshot-ok" in proc.stdout, "snapshot ignores global excludes and hooks")


def test_changes(gate) -> None:
    doc = gate.load_changes(FIXTURES / "changes.json")
    md = gate.changes_markdown(doc)
    check("## C1" in md and "## C2" in md, "changes.md ids")
    state = gate.verdicts_state(doc)
    check("leg" not in state.lower(), "verdict state has no leg")
    check("motivating_feedback:" in state and "existing_rules:" in state, "verdict state fields")


def _bash_instructions(mode: str) -> str:
    repo = Path(__file__).resolve().parent.parent
    script = textwrap.dedent(
        """\
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        . "$1"
        if [ -n "$2" ]; then
          review_claude_brief_instructions "$2"
        else
          review_claude_brief_instructions
        fi
        """
    )
    proc = subprocess.run(
        ["bash", "-c", script, "bash", str(repo / "scripts" / "council.sh"), mode],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout


def test_review_instructions_match_master() -> None:
    got = _bash_instructions("")
    fixture = (ROOT / "fixtures" / "review-instructions.txt").read_text(encoding="utf-8")
    check(got == fixture, "review instructions match the 94f9443 fixture")
    audit = _bash_instructions("audit")
    for banned in (
        'Finish with a short "Checked and found clean"',
        'End with a "Checked and found clean"',
        "Execute anything with computable behaviour",
        "sibling directories",
        "fenced json",
    ):
        check(banned not in audit, f"audit instructions omit {banned}")
    check(
        "- Do not read other local directories. Web access is allowed for checking sources."
        in audit,
        "audit instructions allow web access",
    )
    check("## Honesty gate (non-negotiable)" in audit, "audit keeps the honesty gate")
    check("## Findings rules" in audit, "audit keeps the findings rules")
    check(
        "Every finding must cite `file:line`" in audit,
        "audit keeps the findings citation rule",
    )
    kept = []
    for line in got.splitlines(keepends=True):
        if "Execute anything with computable behaviour" in line:
            continue
        if 'Finish with a short "Checked and found clean"' in line:
            continue
        if 'End with a "Checked and found clean"' in line:
            continue
        line = line.replace(
            "Do not read sibling directories or anything outside the worktree.",
            "Do not read other local directories. Web access is allowed for checking sources.",
        )
        kept.append(line)
    check(audit == "".join(kept), "audit instructions change only those lines")
    word = subprocess.run(
        [
            "bash",
            "-c",
            textwrap.dedent(
                """\
                set -eu
                export COUNCIL_SOURCE_ONLY=1
                . "$1"
                review_forbidden_word "$(review_claude_brief_instructions audit)"
                """
            ),
            "bash",
            str(Path(__file__).resolve().parent.parent / "scripts" / "council.sh"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    check(word.stdout.strip() == "", "audit instructions pass the family-word check")
    repo = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        proposal = subprocess.run(
            [
                "bash",
                "-c",
                textwrap.dedent(
                    """\
                    set -eu
                    export COUNCIL_SOURCE_ONLY=1
                    . "$1"
                    work="$2"
                    BRIEF="$work/brief.md"
                    printf '%s\\n' 'kind: audit' 'target-type: proposal' '' 'Weigh the edits.' >"$BRIEF"
                    AUDIT_TYPE=proposal
                    AUDIT_FINAL_URL=''
                    AUDIT_FILE_LIST='target.md'
                    AUDIT_CAPTURED='2026-10-01T00:00:00Z'
                    rest="$(audit_brief_rest)"
                    word="$(review_forbidden_word "$rest")"
                    [ -z "$word" ]
                    printf '%s\\n' "$rest"
                    """
                ),
                "bash",
                str(repo / "scripts" / "council.sh"),
                tmp,
            ],
            capture_output=True,
            text=True,
        )
    if proposal.returncode != 0:
        raise SystemExit(
            f"FAIL proposal brief\nstdout={proposal.stdout}\nstderr={proposal.stderr}"
        )
    check("Checked and found clean" not in proposal.stdout, "proposal brief has no clean-list ending")
    check("fenced json" not in proposal.stdout, "proposal brief has no json-block ending")
    check("VERDICT <id>: adopt" in proposal.stdout, "proposal brief keeps the verdict lines")
    check(
        "Web access is allowed for checking sources." in proposal.stdout,
        "proposal brief allows web access",
    )


def main() -> None:
    gate = load("audit_gate", "audit_gate.py")
    judge = load("jev_judge", "jev-judge.py")
    test_brief_header()
    test_review_instructions_match_master()
    test_quotes(gate)
    test_nontext_charset(gate)
    test_gate(judge)
    test_json(gate)
    test_verdicts(gate)
    test_keep(gate)
    test_confidence(judge)
    test_source_quote(judge)
    test_source_fetch_reason(judge)
    test_source_row_mismatch(judge)
    test_source_only_exec()
    test_audit_names()
    test_dry_run_target_bytes()
    test_stale_verdicts(judge)
    test_change_ids(gate)
    test_verdicts_header_fallback()
    test_snapshot_ignores_global_gitconfig()
    test_changes(gate)
    # The canned response is json and names no live call.
    canned = json.loads((FIXTURES / "content-canned.json").read_text(encoding="utf-8"))
    check(canned["answers"]["f2"]["confidence"] == 0.69, "fixture 0.69")
    verdicts = json.loads((FIXTURES / "verdicts-canned.json").read_text(encoding="utf-8"))
    cell = judge._jev_cell(verdicts, "C1")
    check(
        cell
        == "adopt, confidence 0.580 (probabilities: adopt=0.720, "
        "adopt_with_changes=0.180, reject=0.100)",
        f"verdict cell ({cell})",
    )
    print("ok")


if __name__ == "__main__":
    main()
