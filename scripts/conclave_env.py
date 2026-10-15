"""Resolve Conclave settings.

CONCLAVE_<NAME> wins when it is set, including empty.
Otherwise COUNCIL_<NAME>. Otherwise the default.
"""

from __future__ import annotations

import os


def setting(name: str, default=None):
    new = f"CONCLAVE_{name}"
    old = f"COUNCIL_{name}"
    if new in os.environ:
        return os.environ[new]
    if old in os.environ:
        return os.environ[old]
    return default


def publish(name: str, value: str) -> None:
    """Write one value under both names."""
    os.environ[f"CONCLAVE_{name}"] = value
    os.environ[f"COUNCIL_{name}"] = value
