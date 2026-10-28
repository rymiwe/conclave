#!/usr/bin/env bash
# Freshness check against temp local repos: up to date, behind clean,
# behind dirty, untracked tmp/ run dirs, diverged, fetch failure, skip, and the default without
# CONCLAVE_AUTO_UPDATE (reports, never pulls or stops).
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

fresh n; advance n; echo dirty >>"$T/n/f"; before="$(git -C "$T/n" rev-parse HEAD)"
out="$(council_freshness_check "$T/n" 2>&1)"; rc=$?
check "default, behind dirty: rc 0" "$rc" 0
check "default: HEAD unmoved" "$(git -C "$T/n" rev-parse HEAD)" "$before"
case "$out" in *"behind origin/main by 1"*"git pull"*) echo "ok default reports behind";; *) echo "FAIL default output: $out"; fail=1;; esac
git -C "$T/n" checkout -q f
out="$(council_freshness_check "$T/n" 2>&1)"; rc=$?
check "default, behind clean: rc 0, no pull" "$rc:$(git -C "$T/n" rev-parse HEAD)" "0:$before"

export CONCLAVE_AUTO_UPDATE=1
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

fresh g; advance g; mkdir -p "$T/g/tmp/chairs/x"; echo s >"$T/g/tmp/chairs/x/file"
out="$(council_freshness_check "$T/g" 2>&1)"; rc=$?
check "behind, only untracked tmp/ run dir: rc 0" "$rc" 0
check "untracked tmp/: fast-forwarded" "$(git -C "$T/g" rev-parse HEAD)" "$(git -C "$T/g-o.git" rev-parse main)"
check "untracked tmp/: run dir kept" "$(cat "$T/g/tmp/chairs/x/file")" s

fresh h; advance h; echo s >"$T/h/stray"; mkdir -p "$T/h/tmp/chairs"; echo s >"$T/h/tmp/chairs/ok"
before="$(git -C "$T/h" rev-parse HEAD)"
out="$(council_freshness_check "$T/h" 2>&1)"; rc=$?
check "untracked outside tmp/: rc 1, HEAD unmoved" "$rc:$(git -C "$T/h" rev-parse HEAD)" "1:$before"
case "$out" in *"behind origin/main by 1 but the working tree is dirty; not pulling."*) echo "ok untracked outside tmp/ dirty message";; *) echo "FAIL untracked outside tmp/ output: $out"; fail=1;; esac

fresh i; mkdir -p "$T/i/tmp/keep"; echo r >"$T/i/tmp/keep/README"
git -C "$T/i" add tmp && git -C "$T/i" commit -qm readme && git -C "$T/i" push -q origin main 2>/dev/null
advance i; echo edit >>"$T/i/tmp/keep/README"; before="$(git -C "$T/i" rev-parse HEAD)"
out="$(council_freshness_check "$T/i" 2>&1)"; rc=$?
check "tracked edit under tmp/: rc 1, HEAD unmoved" "$rc:$(git -C "$T/i" rev-parse HEAD)" "1:$before"
case "$out" in *dirty*) echo "ok tracked edit under tmp/ dirty message";; *) echo "FAIL tracked tmp/ output: $out"; fail=1;; esac

fresh j; git clone -q "$T/j-o.git" "$T/j-w" 2>/dev/null; mkdir -p "$T/j-w/tmp/x"; echo theirs >"$T/j-w/tmp/x/new"
git -C "$T/j-w" add tmp && git -C "$T/j-w" commit -qm incoming && git -C "$T/j-w" push -q origin main 2>/dev/null
mkdir -p "$T/j/tmp/x"; echo mine >"$T/j/tmp/x/new"; before="$(git -C "$T/j" rev-parse HEAD)"
out="$(council_freshness_check "$T/j" 2>&1)"; rc=$?
check "untracked tmp/ file the pull would overwrite: rc 1, HEAD unmoved" "$rc:$(git -C "$T/j" rev-parse HEAD)" "1:$before"
check "colliding tmp/ file untouched" "$(cat "$T/j/tmp/x/new")" mine
case "$out" in *"working tree is dirty"*) echo "ok collision dirty message";; *) echo "FAIL collision output: $out"; fail=1;; esac

# Wiring: council.sh --dry-run must stop on a failed check before touching anything.
fresh f; git -C "$T/f" remote set-url origin "$T/nonexistent.git"
mkdir -p "$T/f/scripts"; cp "$ROOT"/scripts/*.sh "$ROOT"/scripts/*.py "$T/f/scripts/" 2>/dev/null
out="$(cd "$T/f" && COUNCIL_REQUIRE_FRESH=1 bash scripts/council.sh x --dry-run 2>&1)"; rc=$?
check "council.sh --dry-run honors COUNCIL_REQUIRE_FRESH: rc 1" "$rc" 1
case "$out" in *"COUNCIL_REQUIRE_FRESH=1; stopping"*) echo "ok wired into council.sh";; *) echo "FAIL wiring output: $out"; fail=1;; esac

[ "$fail" -eq 0 ] && echo ok
exit "$fail"
