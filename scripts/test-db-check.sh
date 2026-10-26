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
exit "${FAKE_PG_EXIT:-0}"
SH
# The fake docker records its argv. Conclave must never start, stop, or
# restart a container, so a lifecycle call here is a failure.
cat >"$WORK/bin/docker" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>"$FAKE_DOCKER_LOG"
case "$1" in
  info) exit 0 ;;
  inspect) printf '%s\n' "${FAKE_DOCKER_STATUS:-exited}" ;;
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
unset DB_HOST DB_PORT

reset() {
  : >"$ERRORS_FILE"
  : >"$FAKE_PG_LOG"
  : >"$FAKE_DOCKER_LOG"
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
# chair STATUS -> stdout "rc=N", stderr to $WORK/chair.err
chair() {
  ( FAKE_DOCKER_STATUS="$1" FAKE_PG_EXIT=2 \
    PATH="$WORK/bin:$PATH" review_chair_db ) 2>"$WORK/chair.err"
  printf 'rc=%s\n' "$?"
}

no_lifecycle() {
  ! grep -qE '(^|[[:space:]])(start|stop|restart)([[:space:]]|$)' "$FAKE_DOCKER_LOG" \
    || fail "$1 called docker start/stop/restart: $(cat "$FAKE_DOCKER_LOG")"
}

# Database answers: the chair touches nothing.
reset
rc="$( ( FAKE_PG_EXIT=0 PATH="$WORK/bin:$PATH" review_chair_db ); printf 'rc=%s' "$?")"
[ "$rc" = rc=0 ] || fail "up: $rc"
[ ! -s "$FAKE_DOCKER_LOG" ] || fail "up called docker: $(cat "$FAKE_DOCKER_LOG")"

# Database down, whatever the container state: refuse, name the container and
# host:port, exit non-zero, no leg. Docker is never asked to start anything.
for st in exited created running "" paused; do
  reset
  rc="$(set +e; chair "$st")"
  [ "$rc" = rc=1 ] || fail "status '$st': $rc"
  no_lifecycle "status '$st'"
  for want in 'Container db-test' 'localhost:5433' 'Conclave does not start containers' \
    'Start it yourself' 'Voters not launched'; do
    grep -qF "$want" "$WORK/chair.err" || fail "status '$st' msg lacks '$want': $(cat "$WORK/chair.err")"
  done
done

# No setup.yml: refuse with no container name, without calling docker.
reset
mv "$WORK/repo/config/setup.yml" "$WORK/setup.yml"
rc="$(set +e; chair exited)"
mv "$WORK/setup.yml" "$WORK/repo/config/setup.yml"
[ "$rc" = rc=1 ] || fail "no setup.yml: $rc"
grep -q 'No container name in config/setup.yml' "$WORK/chair.err" || fail "no setup msg: $(cat "$WORK/chair.err")"
grep -qF 'Conclave does not start containers' "$WORK/chair.err" || fail "no setup msg: $(cat "$WORK/chair.err")"
[ ! -s "$FAKE_DOCKER_LOG" ] || fail "no setup.yml called docker"

# The reviewer brief carries the rule on both review and audit kinds.
rule='Never start, stop, restart, or reconfigure a database server, container, or other shared service. If a database is unreachable, say so in the report and review without executing.'
review_claude_brief_instructions | grep -qxF "$rule" || fail "review brief lacks the rule"
review_claude_brief_instructions audit | grep -qxF "$rule" || fail "audit brief lacks the rule"

# No launcher script may start, stop, or restart a container. Every non-test
# file under scripts/ is checked, comments included.
hits="$(find "$ROOT/scripts" -type f ! -name 'test-*' -print0 \
  | xargs -0 grep -nE 'docker[[:space:]]+(container[[:space:]]+)?(start|stop|restart)' || true)"
[ -z "$hits" ] || fail "container lifecycle in launcher scripts: $hits"

printf 'test-db-check: ok\n'
