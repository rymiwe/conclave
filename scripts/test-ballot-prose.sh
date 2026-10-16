#!/usr/bin/env bash
# The coverage-seat sentence has one source. Copies that drop the exclusion fail.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/ballot_rules.py" --check "$ROOT"
