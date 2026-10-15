#!/usr/bin/env bash
# Drive review_check_db and review_chair_db against a fake pg_isready and a
# fake docker. With COUNCIL_SOURCE_ONLY set, council.sh only defines functions.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
export COUNCIL_SOURCE_ONLY=1
# shellcheck disable=SC1091
. "$ROOT/scripts/council.sh"

fail() {
  printf 'FAIL %s\n' "$*" >&2
  exit 1
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/bin" "$WORK/empty" "$WORK/out" "$WORK/repo/config"
# The fake records its arguments and exits with FAKE_PG_EXIT.
cat >"$WORK/bin/pg_isready" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$FAKE_PG_LOG"
if [ -f "$FAKE_STARTED" ] && [ "${FAKE_PG_AFTER_START:-up}" = up ]; then
  exit 0
fi
exit "${FAKE_PG_EXIT:-0}"
SH
# The fake docker logs each call. FAKE_DOCKER_STATUS is the container state;
# empty means no such container. `start` marks the container started.
cat >"$WORK/bin/docker" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$1" in
  info) exit "${FAKE_DOCKER_INFO_EXIT:-0}" ;;
  inspect)
    [ -n "${FAKE_DOCKER_STATUS:-}" ] || exit 1
    printf '%s\n' "$FAKE_DOCKER_STATUS"
    ;;
  start)
    # FAKE_START_DELAY: Postgres answers only some seconds after the start.
    if [ -n "${FAKE_START_DELAY:-}" ]; then
      (sleep "$FAKE_START_DELAY"; : >"$FAKE_STARTED") >/dev/null 2>&1 &
    else
      : >"$FAKE_STARTED"
    fi
    ;;
  *) exit 99 ;;
esac
SH
chmod +x "$WORK/bin/pg_isready" "$WORK/bin/docker"
cat >"$WORK/repo/config/setup.yml" <<'YML'
app_name: "Example"

database:
  port: 5433
  name: "db-test"  # Docker container name.
  version: "15"

redis:
  port: 6379
  name: "redis-test"
YML
cat >"$WORK/repo/config/database.yml" <<'YML'
default: &default
  adapter: postgresql
  pool: <%= ENV.fetch("RAILS_MAX_THREADS") { 5 } %>
  host: <%= ENV.fetch("DB_HOST", "localhost") %>
  port: <%= ENV.fetch("DB_PORT", "5433") %>

development:
  <<: *default
  legacy:
    host: elsewhere
    port: 1433
YML

REPO="$WORK/repo"
DIR="$WORK/out"
ERRORS_FILE="$DIR/errors.md"
export FAKE_PG_LOG="$WORK/pg.log"
export FAKE_DOCKER_LOG="$WORK/docker.log"
export FAKE_STARTED="$WORK/started"
unset DB_HOST DB_PORT

reset() {
  : >"$ERRORS_FILE"
  : >"$FAKE_PG_LOG"
  : >"$FAKE_DOCKER_LOG"
  rm -f "$FAKE_STARTED"
}

# Reachable: no WARN, no note, pg_isready asked the ERB defaults.
reset
FAKE_PG_EXIT=0 PATH="$WORK/bin:$PATH" review_check_db
[ -z "$REVIEW_DB_WARN" ] || fail "reachable warned: $REVIEW_DB_WARN"
[ ! -s "$ERRORS_FILE" ] || fail "reachable note: $(cat "$ERRORS_FILE")"
grep -qx -- '-q -h localhost -p 5433' "$FAKE_PG_LOG" \
  || fail "reachable args: $(cat "$FAKE_PG_LOG")"

# Unreachable: the probe sets WARN and writes nothing. The chair decides.
reset
FAKE_PG_EXIT=2 PATH="$WORK/bin:$PATH" review_check_db
case "$REVIEW_DB_WARN" in
  *'localhost:5433'*unreachable*) ;;
  *) fail "unreachable warn: $REVIEW_DB_WARN" ;;
esac
[ ! -s "$ERRORS_FILE" ] || fail "probe wrote a note: $(cat "$ERRORS_FILE")"
[ ! -s "$FAKE_DOCKER_LOG" ] || fail "probe called docker"

# An env var overrides the ERB default, the same as Rails.
reset
DB_PORT=6543 FAKE_PG_EXIT=0 PATH="$WORK/bin:$PATH" review_check_db
grep -qx -- '-q -h localhost -p 6543' "$FAKE_PG_LOG" \
  || fail "env override args: $(cat "$FAKE_PG_LOG")"

# No database.yml: skip silently, pg_isready never called.
reset
REPO="$WORK/empty"
FAKE_PG_EXIT=2 PATH="$WORK/bin:$PATH" review_check_db
[ -z "$REVIEW_DB_WARN" ] || fail "no yml warned"
[ ! -s "$ERRORS_FILE" ] || fail "no yml note"
[ ! -s "$FAKE_PG_LOG" ] || fail "no yml called pg_isready"
REPO="$WORK/repo"

# No pg_isready on PATH: skip silently.
reset
PATH="$WORK/empty" review_check_db
[ -z "$REVIEW_DB_WARN" ] || fail "no pg_isready warned"
[ ! -s "$ERRORS_FILE" ] || fail "no pg_isready note"

# The chair. Each case runs in a subshell because die exits.
# chair STATUS PG_AFTER_START -> stdout "rc=N", stderr to $WORK/chair.err
chair() {
  ( FAKE_DOCKER_STATUS="$1" FAKE_PG_AFTER_START="$2" FAKE_PG_EXIT=2 \
    CONCLAVE_DB_WAIT=2 PATH="$WORK/bin:$PATH" review_chair_db ) 2>"$WORK/chair.err"
  printf 'rc=%s\n' "$?"
}

# Database answers: the chair touches nothing.
reset
rc="$( ( FAKE_PG_EXIT=0 PATH="$WORK/bin:$PATH" review_chair_db ); printf 'rc=%s' "$?")"
[ "$rc" = rc=0 ] || fail "up: $rc"
[ ! -s "$FAKE_DOCKER_LOG" ] || fail "up called docker: $(cat "$FAKE_DOCKER_LOG")"

# Stopped container: docker start on the setup.yml name, then the run goes on.
for st in exited created; do
  reset
  rc="$(set +e; chair "$st" up)"
  [ "$rc" = rc=0 ] || fail "$st: $rc $(cat "$WORK/chair.err")"
  grep -qx 'start db-test' "$FAKE_DOCKER_LOG" || fail "$st start: $(cat "$FAKE_DOCKER_LOG")"
  grep -q '^note: chair started container db-test' "$ERRORS_FILE" \
    || fail "$st note: $(cat "$ERRORS_FILE")"
done

# Postgres answers a second after the start: the chair waits for it.
reset
rc="$(set +e; FAKE_START_DELAY=1 chair exited up)"
[ "$rc" = rc=0 ] || fail "slow start: $rc $(cat "$WORK/chair.err")"

# Started but still silent after the wait: die, no leg.
reset
rc="$(set +e; chair exited down)"
[ "$rc" = rc=1 ] || fail "still down: $rc"
grep -q 'started db-test and waited 2s' "$WORK/chair.err" || fail "still down msg: $(cat "$WORK/chair.err")"

# Running, missing, or paused: never start, restart, or create. Die.
for st in running "" paused; do
  reset
  rc="$(set +e; chair "$st" up)"
  [ "$rc" = rc=1 ] || fail "status '$st': $rc"
  ! grep -qE '^(start|restart|run|create)' "$FAKE_DOCKER_LOG" \
    || fail "status '$st' mutated: $(cat "$FAKE_DOCKER_LOG")"
  grep -q 'Voters not launched' "$WORK/chair.err" || fail "status '$st' msg: $(cat "$WORK/chair.err")"
done

# Docker daemon down: say so, do not inspect or start.
reset
rc="$( ( FAKE_DOCKER_INFO_EXIT=1 FAKE_DOCKER_STATUS=exited FAKE_PG_EXIT=2 \
  PATH="$WORK/bin:$PATH" review_chair_db ) 2>"$WORK/chair.err"; printf 'rc=%s' "$?")"
[ "$rc" = rc=1 ] || fail "daemon down: $rc"
grep -q 'docker daemon does not answer' "$WORK/chair.err" || fail "daemon msg: $(cat "$WORK/chair.err")"
[ "$(cat "$FAKE_DOCKER_LOG")" = info ] || fail "daemon down calls: $(cat "$FAKE_DOCKER_LOG")"

# No setup.yml: die without calling docker.
reset
mv "$WORK/repo/config/setup.yml" "$WORK/setup.yml"
rc="$(set +e; chair exited up)"
mv "$WORK/setup.yml" "$WORK/repo/config/setup.yml"
[ "$rc" = rc=1 ] || fail "no setup.yml: $rc"
grep -q 'No container name in config/setup.yml' "$WORK/chair.err" || fail "no setup msg: $(cat "$WORK/chair.err")"
[ ! -s "$FAKE_DOCKER_LOG" ] || fail "no setup.yml called docker"

# The reviewer brief carries the rule on both review and audit kinds.
rule='Never start, stop, restart, or reconfigure a database server, container, or other shared service. If a database is unreachable, say so in the report and review without executing.'
review_claude_brief_instructions | grep -qxF "$rule" || fail "review brief lacks the rule"
review_claude_brief_instructions audit | grep -qxF "$rule" || fail "audit brief lacks the rule"

printf 'test-db-check: ok\n'
