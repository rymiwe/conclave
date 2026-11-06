#!/usr/bin/env bash
# Emit the "## Reviewer preferences" section for a gate/chair prompt.
# When CONCLAVE_REVIEWER_PREFS names an existing file, print its contents.
# When unset or missing, print a clear generic default (no house rules).
# The prefs file must live outside this repository (fleet / Work Wes owned).
#
# Safe to source: does not change the caller's shell options. `set -euo pipefail`
# runs only when this file is executed directly.

emit_reviewer_prefs() {
  printf '%s\n' "## Reviewer preferences"
  if [ -n "${CONCLAVE_REVIEWER_PREFS:-}" ] && [ -f "${CONCLAVE_REVIEWER_PREFS}" ]; then
    # Refuse to read a path inside this repo when ROOT is known.
    if [ -n "${ROOT:-}" ]; then
      case "$(cd "$(dirname -- "$CONCLAVE_REVIEWER_PREFS")" && pwd -P)/$(basename -- "$CONCLAVE_REVIEWER_PREFS")" in
        "$(cd "$ROOT" && pwd -P)"/*)
          printf '%s\n' "error: CONCLAVE_REVIEWER_PREFS must be outside the conclave repository" >&2
          return 2
          ;;
      esac
    fi
    printf '%s\n' "(from CONCLAVE_REVIEWER_PREFS)"
    cat -- "$CONCLAVE_REVIEWER_PREFS"
  else
    if [ -n "${CONCLAVE_REVIEWER_PREFS:-}" ]; then
      printf '%s\n' "CONCLAVE_REVIEWER_PREFS set but not found: ${CONCLAVE_REVIEWER_PREFS}; using defaults" >&2
    fi
    printf '%s\n' "None configured. Using the generic template defaults above."
    printf '%s\n' "Set CONCLAVE_REVIEWER_PREFS to an absolute path outside this repository for house rules."
  fi
}

if [[ "${BASH_SOURCE[0]-}" == "$0" ]]; then
  set -euo pipefail
  emit_reviewer_prefs
fi
