#!/usr/bin/env python3
"""Print the newest Muse model in the Wonder family, or nothing.

The family is the hyphen token "wonder". A Spark id or a contributor id
is refused. Newest is the latest releaseDate. Equal dates keep catalog
order, which is newest first.

--catalog FILE reads a saved listing and does not start muse. Without it,
this asks `muse serve` for model/list and gives up after a few seconds.
A missing binary, a bad file, or a failed listing prints nothing and exits 0.
"""

import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

LIST_TIMEOUT_S = 8


def is_wonder(model_id):
    if not isinstance(model_id, str):
        return False
    low = model_id.strip().lower()
    if not low or "spark" in low or "contributor" in low:
        return False
    return "wonder" in low.split("-")


def entries_of(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    models = payload.get("models")
    if isinstance(models, list):
        return models
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("models"), list):
        return result["models"]
    if isinstance(result, list):
        return result
    return []


def newest_wonder(entries):
    """The newest Wonder id, or "" when the listing has none."""
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("modelId")
        if not is_wonder(model_id):
            continue
        when = entry.get("releaseDate")
        when = when.strip() if isinstance(when, str) else ""
        rows.append((when, model_id.strip()))
    rows.sort(key=lambda row: row[0], reverse=True)
    return rows[0][1] if rows else ""


def _send(proc, obj):
    proc.stdin.write((json.dumps(obj) + "\n").encode())
    proc.stdin.flush()


def _read(proc, buf, deadline):
    while time.time() < deadline:
        if b"\n" in buf[0]:
            line, rest = buf[0].split(b"\n", 1)
            buf[0] = rest
            if not line.strip():
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
        if proc.poll() is not None:
            return None
        ready, _, _ = select.select([proc.stdout], [], [], 0.3)
        if not ready:
            continue
        chunk = os.read(proc.stdout.fileno(), 65536)
        if not chunk:
            return None
        buf[0] += chunk
    return None


def list_live(binary=None, timeout=LIST_TIMEOUT_S):
    """Wonder id from `muse serve` model/list, or ""."""
    binary = binary or shutil.which("muse")
    if not binary:
        return ""
    proc = subprocess.Popen(
        [binary, "serve", "--provider", "meta", "--no-session-log",
         "--disable-shell", "--disable-write"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd="/tmp", start_new_session=True,
    )
    buf = [b""]
    deadline = time.time() + timeout
    try:
        _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {"clientInfo": {"name": "conclave", "version": "0"}}})
        init = None
        while time.time() < deadline and init is None:
            msg = _read(proc, buf, deadline)
            if msg is None:
                break
            if msg.get("id") == 1:
                init = msg
        if not init or "result" not in init:
            return ""
        _send(proc, {"jsonrpc": "2.0", "method": "initialized"})
        _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}})
        while time.time() < deadline:
            msg = _read(proc, buf, deadline)
            if msg is None:
                break
            if msg.get("id") != 2:
                continue
            if "error" in msg:
                return ""
            return newest_wonder(entries_of(msg.get("result")))
        return ""
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                proc.kill()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def from_catalog(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    return newest_wonder(entries_of(payload))


def main(argv):
    catalog = None
    i = 0
    while i < len(argv):
        if argv[i] == "--catalog" and i + 1 < len(argv):
            catalog = argv[i + 1]
            i += 2
            continue
        raise SystemExit("usage: muse-models.py [--catalog FILE]")
    if catalog is None:
        catalog = os.environ.get("CONCLAVE_MUSE_CATALOG") or os.environ.get("COUNCIL_MUSE_CATALOG")
    model_id = from_catalog(catalog) if catalog else list_live()
    if model_id:
        print(model_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
