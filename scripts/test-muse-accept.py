#!/usr/bin/env python3
"""Plain Wonder must not win, and --accept exits 0 only for Wonder full.

MUSE_MODELS points at the module under test. An older module that still
returns the plain id fails this check.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULE = os.environ.get("MUSE_MODELS", str(ROOT / "muse-models.py"))
CATALOG = [
    {"modelId": "muse-wonder-1.0", "releaseDate": "2026-12-01"},
    {"modelId": "muse-wonder-full-1.0", "releaseDate": "2026-10-12"},
    {"modelId": "muse-spark-1.3", "releaseDate": "2026-11-01"},
]


def load(path: str):
    spec = importlib.util.spec_from_file_location("muse_models_under_test", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def accept(model: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, MODULE, "--accept", model],
        capture_output=True,
        text=True,
    )


def main() -> int:
    got = load(MODULE).newest_wonder(CATALOG)
    if got != "muse-wonder-full-1.0":
        print(f"FAIL newest_wonder returned {got!r}, want muse-wonder-full-1.0")
        return 1
    plain = accept("muse-wonder-1.0")
    if plain.returncode == 0:
        print("FAIL --accept muse-wonder-1.0 exited 0")
        print(plain.stdout)
        print(plain.stderr)
        return 1
    full = accept("muse-wonder-full-1.0")
    if full.returncode != 0:
        print(f"FAIL --accept muse-wonder-full-1.0 exited {full.returncode}")
        return 1
    caps = accept("Muse-Wonder-Full-1.0")
    if caps.returncode != 0:
        print(f"FAIL --accept Muse-Wonder-Full-1.0 exited {caps.returncode}")
        return 1
    print("ok muse accept")
    print(f"newest_wonder {got}")
    print(f"--accept muse-wonder-1.0 exit {plain.returncode}")
    print(f"--accept muse-wonder-full-1.0 exit {full.returncode}")
    print(f"--accept Muse-Wonder-Full-1.0 exit {caps.returncode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
