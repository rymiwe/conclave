#!/usr/bin/env python3
"""Capture a Muse subscription_usage snapshot from a shadow-leg stream.

Choice: the shadow launch passes `muse exec --json`. That is Muse's JSONL
event stream, and it is the least invasive place to see the
`subscription_usage` event Meta returns on the POST /v1/responses SSE
stream. Answer text from `muse exec` leaves that event inside the client.
This helper runs after `muse` exits. It reads stdout and stderr only.

Limits, also in docs/routing.md:
- Journal captures published with muse-codes (through the run.output.delta
  fingerprint) contain no subscription event and no token accounting. This
  writes a file only when the snapshot is in the stream. It does not proxy
  the API, call `muse serve`, or open a second client.
- The verdict kept for the council is the joined `run.output.delta` text.
  When that text is empty, the last `run.terminal.*` text is used. Tool
  results stay out of the verdict. Stdout that already has a `## ` heading
  stays the answer; usage lines in that text are removed.
- The cache file stores `captured_at`, `window`, and `weekly`. Tier,
  credentials, and the rest of the stream stay out of the file.
- `--proof FILE` writes a voter leg's model proof line from the
  `run.model.configured` records: ok only when every id equals the
  resolved Wonder model.
- A missing snapshot leaves an existing file in place. A parse or write
  error exits 0.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

USAGE_EVENTS = ("response.subscription_usage", "subscription_usage")
MODEL_MISMATCH_NOTE = "journal model_id does not match the resolved model\n"
_MAX_DEPTH = 12


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _reset(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _snapshot(value):
    """Return window and weekly, or None when the object is a different shape."""
    if not isinstance(value, dict):
        return None
    window = value.get("window")
    weekly = value.get("weekly")
    if not isinstance(window, dict) or not isinstance(weekly, dict):
        return None
    used = _num(window.get("used_percent"))
    duration = _num(window.get("window_duration_mins"))
    window_reset = _reset(window.get("resets_at"))
    weekly_used = _num(weekly.get("used_percent"))
    weekly_reset = _reset(weekly.get("resets_at"))
    if None in (used, duration, window_reset, weekly_used, weekly_reset):
        return None
    return {
        "window": {
            "used_percent": used,
            "window_duration_mins": duration,
            "resets_at": window_reset,
        },
        "weekly": {
            "used_percent": weekly_used,
            "resets_at": weekly_reset,
        },
    }


def _interesting(text):
    if "subscription_usage" in text:
        return True
    return (
        "used_percent" in text
        and "window_duration_mins" in text
        and "resets_at" in text
    )


def _json_objects(text):
    decoder = json.JSONDecoder()
    idx = 0
    length = len(text)
    while idx < length:
        start = text.find("{", idx)
        if start < 0:
            return
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(value, dict):
            yield start, value
        idx = end if end > start else start + 1


def _sse_objects(text):
    event = None
    data = []
    data_pos = None
    offset = 0

    def flush():
        nonlocal event, data, data_pos
        name, payload, pos = event, "\n".join(data).strip(), data_pos
        event, data, data_pos = None, [], None
        if name not in USAGE_EVENTS or not payload or pos is None:
            return None
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            return None
        if isinstance(value, dict):
            return pos, value
        return None

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("event:"):
            item = flush()
            if item:
                yield item
            event = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("data:") and event in USAGE_EVENTS:
            if data_pos is None:
                data_pos = offset + line.find("data:")
            data.append(stripped.split(":", 1)[1].lstrip())
        elif stripped == "":
            item = flush()
            if item:
                yield item
        offset += len(line)
    item = flush()
    if item:
        yield item


def _collect(value, found, depth):
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, dict):
        snap = _snapshot(value)
        if snap:
            found.append(snap)
            return
        for child in value.values():
            _collect(child, found, depth + 1)
        return
    if isinstance(value, list):
        for child in value:
            _collect(child, found, depth + 1)
        return
    if isinstance(value, str) and _interesting(value):
        for _pos, inner in _json_objects(value):
            _collect(inner, found, depth + 1)
        for _pos, inner in _sse_objects(value):
            _collect(inner, found, depth + 1)


def extract_subscription_usage(text):
    """Return the last window/weekly snapshot in text, or None."""
    if not text:
        return None
    found = []

    def add(pos, value):
        snaps = []
        _collect(value, snaps, 0)
        for snap in snaps:
            found.append((pos, snap))

    for pos, value in _json_objects(text):
        add(pos, value)
    for pos, value in _sse_objects(text):
        add(pos, value)
    if not found:
        return None
    found.sort(key=lambda item: item[0])
    return found[-1][1]


def line_records(text):
    """JSONL journal records. Empty when a `## ` heading marks plain text."""
    records = []
    for line in text.splitlines():
        if line.startswith("## "):
            return []
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(value, dict)
            and isinstance(value.get("payload_type"), str)
            and isinstance(value.get("payload"), dict)
        ):
            records.append(value)
    return records


def verdict_from_records(records):
    parts = []
    for rec in records:
        if rec.get("payload_type") != "run.output.delta":
            continue
        payload = rec.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            parts.append(payload["text"])
    joined = "".join(parts)
    if joined.strip():
        return joined
    terminal = ""
    for rec in records:
        kind = rec.get("payload_type")
        if not isinstance(kind, str) or not kind.startswith("run.terminal."):
            continue
        payload = rec.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("text"), str):
            terminal = payload["text"]
    if terminal.strip():
        return terminal
    return joined


def _event_name(stripped):
    if not stripped.startswith("event:"):
        return None
    return stripped.split(":", 1)[1].strip()


def _line_is_usage_json(stripped):
    if not stripped.startswith("{"):
        return False
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    found = []
    _collect(value, found, 0)
    return bool(found)


def strip_usage_carriers(text):
    """Drop SSE usage blocks and whole-line usage JSON from a plain answer."""
    lines = text.splitlines(keepends=True)
    kept = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if _event_name(stripped) in USAGE_EVENTS:
            index += 1
            while index < len(lines) and lines[index].strip().startswith("data:"):
                index += 1
            if index < len(lines) and lines[index].strip() == "":
                index += 1
            continue
        if _line_is_usage_json(stripped):
            index += 1
            continue
        kept.append(lines[index])
        index += 1
    return "".join(kept)


def verdict_replacement(text):
    """New stdout text, or None when the file should stay as it is."""
    records = line_records(text)
    if records:
        return verdict_from_records(records)
    stripped = strip_usage_carriers(text)
    if stripped != text:
        return stripped
    return None


def mismatched_model(records, expected):
    """True when a journal model id is present and differs from the resolved model."""
    expected = (expected or "").strip()
    if not expected:
        return False
    for rec in records:
        if rec.get("payload_type") != "run.model.configured":
            continue
        payload = rec.get("payload")
        if not isinstance(payload, dict):
            continue
        model_id = payload.get("model_id")
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() != expected:
            return True
    return False


def model_proof(records, expected, voter="muse"):
    """One proof line for a voter leg. Every configured id must equal the resolved model."""
    expected = (expected or "").strip()
    ids = []
    for rec in records:
        if rec.get("payload_type") != "run.model.configured":
            continue
        payload = rec.get("payload")
        model_id = payload.get("model_id") if isinstance(payload, dict) else None
        if isinstance(model_id, str) and model_id.strip() and model_id.strip() not in ids:
            ids.append(model_id.strip())
    if not ids:
        return f"{voter}: FAIL model not proven (no run.model.configured)"
    if not expected or any(i != expected for i in ids):
        return f"{voter}: FAIL ran={','.join(ids)} (lineup said {expected or 'none'})"
    return f"{voter}: ok model={expected}"


def usage_document(snapshot, captured_at=None):
    return {
        "captured_at": captured_at or utc_now_iso(),
        "window": snapshot["window"],
        "weekly": snapshot["weekly"],
    }


def atomic_write_text(path, text):
    """Write text via a temp file in the destination directory, then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def atomic_write_usage(path, snapshot, captured_at=None):
    body = json.dumps(usage_document(snapshot, captured_at), indent=2) + "\n"
    atomic_write_text(path, body)


def _append_mismatch(path):
    path = Path(path)
    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8", errors="replace")
    if MODEL_MISMATCH_NOTE.strip() in existing:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(MODEL_MISMATCH_NOTE)


def _main(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--err", default="")
    parser.add_argument("--usage-file", default="")
    parser.add_argument("--expect-model", default="")
    parser.add_argument("--proof", default="")
    args = parser.parse_args(argv)
    raw_path = Path(args.raw)
    if not raw_path.is_file():
        return
    raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
    err_text = ""
    err_path = Path(args.err) if args.err else None
    if err_path is not None and err_path.is_file():
        err_text = err_path.read_text(encoding="utf-8", errors="replace")
    records = line_records(raw_text)
    if args.proof:
        try:
            atomic_write_text(Path(args.proof), model_proof(records, args.expect_model) + "\n")
        except OSError:
            pass
    usage = extract_subscription_usage(raw_text + "\n" + err_text)
    replacement = verdict_replacement(raw_text)
    if replacement is not None and replacement != raw_text:
        try:
            atomic_write_text(raw_path, replacement)
        except OSError:
            pass
    if mismatched_model(records, args.expect_model) and err_path is not None:
        try:
            _append_mismatch(err_path)
        except OSError:
            pass
    if usage is not None and args.usage_file:
        try:
            atomic_write_usage(Path(args.usage_file), usage)
        except OSError:
            pass


def main(argv=None):
    try:
        _main(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        raise
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
