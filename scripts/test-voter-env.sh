#!/usr/bin/env bash
# Every voter leg inherits COUNCIL_VOTER=1, so routing hooks stay silent in it.
set -u
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
got="$(env -u COUNCIL_VOTER COUNCIL_SOURCE_ONLY=1 bash -c \
  ". '$ROOT/scripts/council.sh' >/dev/null 2>&1; bash -c 'printf %s \"\${COUNCIL_VOTER:-unset}\"'")"
if [ "$got" = "1" ]; then
  echo "ok a child of council.sh sees COUNCIL_VOTER=1"
  exit 0
fi
echo "FAIL a child of council.sh sees COUNCIL_VOTER=$got"
exit 1
