#!/usr/bin/env bash
# Pre-run freshness check: fetch and compare HEAD to its upstream. With
# CONCLAVE_AUTO_UPDATE=1 it fast-forwards when that is safe and stops on a
# dirty or diverged tree; without it, it only reports how far behind.
# Sourced by conclave.sh; tests call it directly.
#   CONCLAVE_AUTO_UPDATE=1        pull when behind (COUNCIL_AUTO_UPDATE still works)
#   CONCLAVE_SKIP_UPDATE_CHECK=1  bypass (COUNCIL_SKIP_UPDATE_CHECK still works)
#   CONCLAVE_REQUIRE_FRESH=1      a failed fetch stops the run instead of warning
#     (COUNCIL_REQUIRE_FRESH still works). The new name wins when both are set.
# Prints on stdout/stderr; returns nonzero when the run must stop.
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/conclave-env.sh"

council_freshness_check() {
  local dir="$1" up behind ahead old new skip_name fresh_name auto_name
  skip_name="$(conclave_env_name SKIP_UPDATE_CHECK)"
  fresh_name="$(conclave_env_name REQUIRE_FRESH)"
  auto_name="$(conclave_env_name AUTO_UPDATE)"
  [ "$(conclave_env SKIP_UPDATE_CHECK 0)" = "1" ] && return 0
  if ! timeout "$(conclave_env FETCH_TIMEOUT 30)" git -C "$dir" fetch --quiet 2>/dev/null; then
    if [ "$(conclave_env REQUIRE_FRESH 0)" = "1" ]; then
      echo "conclave: git fetch failed and ${fresh_name}=1; stopping." >&2
      return 1
    fi
    echo "conclave: WARNING: git fetch failed (offline?). Running on a possibly STALE checkout." >&2
    return 0
  fi
  if ! up="$(git -C "$dir" rev-parse --abbrev-ref '@{u}' 2>/dev/null)"; then
    echo "conclave: no upstream branch; freshness not checked." >&2
    return 0
  fi
  read -r behind ahead <<EOF2
$(git -C "$dir" rev-list --left-right --count "$up...HEAD")
EOF2
  old="$(git -C "$dir" rev-parse --short HEAD)"
  if [ "$behind" -eq 0 ]; then
    echo "conclave: up to date with $up at $old ($(git -C "$dir" log -1 --format=%cs HEAD))"
    return 0
  fi
  if [ "$(conclave_env AUTO_UPDATE 0)" != "1" ]; then
    echo "conclave: WARNING: checkout is behind $up by $behind; \`git pull\` to update, or set ${auto_name}=1." >&2
    return 0
  fi
  if [ "$ahead" -ne 0 ]; then
    echo "conclave: checkout is behind $up by $behind and ahead by $ahead (diverged); not pulling. Reconcile by hand, or set ${skip_name}=1." >&2
    return 1
  fi
  # Untracked-and-ignored files (tmp/council/**) do not show in --porcelain.
  if [ -n "$(git -C "$dir" status --porcelain)" ]; then
    echo "conclave: checkout is behind $up by $behind but the working tree is dirty; not pulling. Commit or stash, or set ${skip_name}=1." >&2
    return 1
  fi
  if ! git -C "$dir" pull --ff-only --quiet >/dev/null 2>&1; then
    echo "conclave: git pull --ff-only failed; not continuing." >&2
    return 1
  fi
  new="$(git -C "$dir" rev-parse --short HEAD)"
  echo "conclave: updated $up $old..$new ($behind commits)"
  git -C "$dir" log --oneline "$old..$new" | sed 's/^/  /'
  return 0
}
