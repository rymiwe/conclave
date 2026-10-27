#!/usr/bin/env python3
"""Offline checks for the CURSOR_GROK pick in scripts/resolve-models.sh.

A stub cursor-agent prints a model list. No network and no API call.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def resolve(tmp: Path, ids: list[str]) -> subprocess.CompletedProcess[str]:
    home = tmp / "home"
    cache = home / ".codex" / "models_cache.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps({"models": [{"visibility": "list", "priority": 1, "slug": "gpt-6-astra"}]}),
        encoding="utf-8",
    )
    codex = tmp / "bin" / "codex"
    codex.parent.mkdir()
    codex.write_text("#!/bin/bash\necho codex-cli 0.170.0\n", encoding="utf-8")
    codex.chmod(0o755)
    listing = "Available models\n\n" + "".join(f"{i} - {i}\n" for i in ids)
    agent = tmp / "bin" / "cursor-agent"
    agent.write_text(f"#!/bin/bash\nprintf %s {shlex.quote(listing)}\n", encoding="utf-8")
    agent.chmod(0o755)
    jqdir = tmp / "jq-bin"
    jqdir.mkdir()
    jq = shutil.which("jq")
    if not jq:
        raise SystemExit("FAIL jq is not on PATH")
    os.symlink(jq, jqdir / "jq")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = f"{tmp / 'bin'}:{jqdir}:/usr/bin:/bin"
    env.pop("COUNCIL_CODEX_BIN", None)
    env.pop("CONCLAVE_CODEX_BIN", None)
    return subprocess.run(
        ["bash", str(SCRIPTS / "resolve-models.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def grok(proc: subprocess.CompletedProcess[str]) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith("CURSOR_GROK="):
            parts = shlex.split(line)
            return parts[0].split("=", 1)[1] if parts else ""
    raise SystemExit(f"FAIL no CURSOR_GROK line in {proc.stdout!r}")


def main() -> None:
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)

        p = resolve(tmp / "order", ["grok-4.9-xhigh-fast", "grok-4.10-xhigh-fast", "grok-4.9-xhigh", "grok-4.10-xhigh"])
        check(grok(p) == "grok-4.10-xhigh-fast", f"4.10 beats 4.9 ({grok(p)})")
        check(p.returncode == 0 and p.stderr == "", f"ordering case is quiet, rc 0 (rc={p.returncode}, stderr={p.stderr!r})")

        p = resolve(tmp / "order-rev", ["grok-4.10-xhigh-fast", "grok-4.9-xhigh-fast"])
        check(grok(p) == "grok-4.10-xhigh-fast", f"4.10 beats 4.9 in listing order 4.10 first ({grok(p)})")

        p = resolve(tmp / "none", ["grok-4.7-xhigh", "grok-4.7-high-fast", "gpt-5.2"])
        check(grok(p) == "", f"no xhigh-fast id leaves CURSOR_GROK empty ({grok(p)!r})")
        check(p.returncode == 0, f"no fast id keeps exit 0 (rc={p.returncode})")
        check(
            "no grok-N-xhigh-fast id" in p.stderr and "refuse to launch" in p.stderr,
            f"no fast id warns on stderr ({p.stderr!r})",
        )

        p = resolve(tmp / "newer-plain", ["grok-4.7-xhigh-fast", "grok-4.7-xhigh", "grok-5-xhigh"])
        check(grok(p) == "grok-4.7-xhigh-fast", f"newer plain id does not change the pick ({grok(p)})")
        check(p.returncode == 0, f"newer plain id keeps exit 0 (rc={p.returncode})")
        check(
            "grok-5-xhigh has no -fast variant" in p.stderr and "stays on grok-4.7-xhigh-fast" in p.stderr,
            f"newer plain id warns on stderr ({p.stderr!r})",
        )

        p = resolve(tmp / "plain-4-10", ["grok-4.9-xhigh-fast", "grok-4.10-xhigh"])
        check("grok-4.10-xhigh has no -fast variant" in p.stderr, f"4.10 plain vs 4.9 fast warns ({p.stderr!r})")

        p = resolve(tmp / "matched", ["grok-5-xhigh-fast", "grok-5-xhigh", "grok-4.7-xhigh-fast"])
        check(grok(p) == "grok-5-xhigh-fast" and p.stderr == "", f"matched fast id is quiet ({grok(p)}, {p.stderr!r})")

    print("test-resolve-grok: ok")


if __name__ == "__main__":
    main()
