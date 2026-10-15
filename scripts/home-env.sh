# Sourced by conclave.sh (and its council.sh alias) and resolve-models.sh.
# A launcher can hand WSL a Windows HOME (the Bot desktop app sent
# "C:Usersryanw" on 2026-10-02). Every CLI keeps its login under HOME, so
# take HOME from the account record when it is not an existing absolute
# directory. No path is hardcoded.
council_fix_home() {
  case "${HOME:-}" in
    /*) [ -d "$HOME" ] && return 0 ;;
  esac
  local home
  home="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6)"
  if [ -n "$home" ] && [ -d "$home" ]; then
    printf 'note: HOME was %q; using %s from the account record\n' "${HOME:-}" "$home" >&2
    export HOME="$home"
  fi
}
council_fix_home
