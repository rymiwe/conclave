#!/usr/bin/env bash
# Freshness check against temp local repos: up to date, behind clean,
# behind dirty, diverged, fetch failure, skip.
set -u
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/freshness-check.sh"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok $1"; else echo "FAIL $1 (want '$3', got '$2')"; fail=1; fi; }

fresh() { # build origin + clone in $T/$1
  git init -q --bare -b main "$T/$1-o.git"
  git clone -q "$T/$1-o.git" "$T/$1" 2>/dev/null
  git -C "$T/$1" checkout -q -b main 2>/dev/null
  echo a >"$T/$1/f"; printf 'tmp/council/**\n' >"$T/$1/.gitignore"
  git -C "$T/$1" add . && git -C "$T/$1" commit -qm one && git -C "$T/$1" push -q -u origin main 2>/dev/null
}
advance() { # push a new commit to origin of $1 via a second clone
  git clone -q "$T/$1-o.git" "$T/$1-w" 2>/dev/null
  echo b >>"$T/$1-w/f"; git -C "$T/$1-w" commit -qam two; git -C "$T/$1-w" push -q origin main 2>/dev/null
}

fresh a; out="$(council_freshness_check "$T/a" 2>&1)"; rc=$?
check "up to date: rc 0" "$rc" 0
case "$out" in *"up to date"*"$(git -C "$T/a" rev-parse --short HEAD)"*) echo "ok up to date prints sha";; *) echo "FAIL up to date output: $out"; fail=1;; esac

fresh b; advance b; mkdir -p "$T/b/tmp/council/x"; echo s >"$T/b/tmp/council/x/scratch"
old="$(git -C "$T/b" rev-parse --short HEAD)"
out="$(council_freshness_check "$T/b" 2>&1)"; rc=$?
check "behind clean (ignored scratch present): rc 0" "$rc" 0
check "behind clean: fast-forwarded" "$(git -C "$T/b" rev-parse HEAD)" "$(git -C "$T/b-o.git" rev-parse main)"
case "$out" in *"$old..$(git -C "$T/b" rev-parse --short HEAD)"*) echo "ok prints old..new";; *) echo "FAIL old..new output: $out"; fail=1;; esac

fresh c; advance c; echo dirty >>"$T/c/f"; before="$(git -C "$T/c" rev-parse HEAD)"
out="$(council_freshness_check "$T/c" 2>&1)"; rc=$?
check "behind dirty: rc 1" "$rc" 1
check "behind dirty: HEAD unmoved" "$(git -C "$T/c" rev-parse HEAD)" "$before"
case "$out" in *dirty*) echo "ok dirty message";; *) echo "FAIL dirty output: $out"; fail=1;; esac

fresh d; advance d; echo local >"$T/d/g"; git -C "$T/d" add g; git -C "$T/d" commit -qm local
before="$(git -C "$T/d" rev-parse HEAD)"
out="$(council_freshness_check "$T/d" 2>&1)"; rc=$?
check "diverged: rc 1" "$rc" 1
check "diverged: HEAD unmoved" "$(git -C "$T/d" rev-parse HEAD)" "$before"

fresh e; git -C "$T/e" remote set-url origin "$T/nonexistent.git"
out="$(council_freshness_check "$T/e" 2>&1)"; rc=$?
check "fetch fails: rc 0" "$rc" 0
case "$out" in *WARNING*) echo "ok fetch failure warns";; *) echo "FAIL warn output: $out"; fail=1;; esac
out="$(COUNCIL_REQUIRE_FRESH=1 council_freshness_check "$T/e" 2>&1)"; rc=$?
check "fetch fails + REQUIRE_FRESH: rc 1" "$rc" 1

out="$(COUNCIL_SKIP_UPDATE_CHECK=1 council_freshness_check "$T/e" 2>&1)"; rc=$?
check "skip: rc 0, silent" "$rc:$out" "0:"

# Wiring: council.sh --dry-run must stop on a failed check before touching anything.
fresh f; git -C "$T/f" remote set-url origin "$T/nonexistent.git"
mkdir -p "$T/f/scripts"; cp "$ROOT"/scripts/*.sh "$ROOT"/scripts/*.py "$T/f/scripts/" 2>/dev/null
out="$(cd "$T/f" && COUNCIL_REQUIRE_FRESH=1 bash scripts/council.sh x --dry-run 2>&1)"; rc=$?
check "council.sh --dry-run honors COUNCIL_REQUIRE_FRESH: rc 1" "$rc" 1
case "$out" in *"COUNCIL_REQUIRE_FRESH=1; stopping"*) echo "ok wired into council.sh";; *) echo "FAIL wiring output: $out"; fail=1;; esac

[ "$fail" -eq 0 ] && echo ok
exit "$fail"
