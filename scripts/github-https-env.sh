# Sourced by conclave.sh and scripts/with-github-https.sh. Not executable.
#
# cursor-agent syncs its plugin marketplace with
#   git fetch ... origin   (origin = git@github.com:cursor/plugins.git)
# at the start of every call. On a host where github.com:22 hangs, that fetch
# hangs, so every cursor-agent call hangs: the Fable voter leg and a
# cursor-agent chair (2026-10-21, orderly-needs-you-design).
#
# The usual process-scoped fix, GIT_CONFIG_COUNT/KEY/VALUE, does not reach
# that fetch: cursor-agent sets its own GIT_CONFIG_COUNT and KEY/VALUE pairs
# for its git children and replaces ours. GIT_CONFIG_GLOBAL does reach it.
# This points GIT_CONFIG_GLOBAL, for this process tree only, at a small file
# that includes the caller's own global config and rewrites GitHub SSH URLs to
# HTTPS. It never edits ~/.gitconfig, ~/.config/git/config, or ~/.ssh.
#
# Pushes to a rewritten GitHub remote go over HTTPS too, so a push from a
# child needs an HTTPS credential helper (for example
# `git -c credential.helper='!gh auth git-credential' push`).
#
# CONCLAVE_GITHUB_HTTPS=0 (alias COUNCIL_GITHUB_HTTPS) turns this off.

conclave_github_https_env() {
  local on="${CONCLAVE_GITHUB_HTTPS-${COUNCIL_GITHUB_HTTPS-1}}"
  [ "$on" = 0 ] && return 0
  # Already applied in a parent: keep it, do not nest includes.
  if [ -n "${CONCLAVE_GITHUB_HTTPS_FILE:-}" ] \
     && [ "${GIT_CONFIG_GLOBAL:-}" = "$CONCLAVE_GITHUB_HTTPS_FILE" ] \
     && [ -f "$CONCLAVE_GITHUB_HTTPS_FILE" ]; then
    return 0
  fi
  local dir file tmp sum
  dir="${XDG_CACHE_HOME:-${HOME:-/tmp}/.cache}/conclave"
  mkdir -p "$dir" 2>/dev/null || dir="${TMPDIR:-/tmp}"
  tmp="$(mktemp "$dir/.gitconfig-github-https.XXXXXX")" || return 0
  {
    printf '# Written by conclave scripts/github-https-env.sh. Safe to delete.\n'
    # Git reads only GIT_CONFIG_GLOBAL when it is set, so include whatever
    # git would have read as global config without this file.
    if [ -n "${GIT_CONFIG_GLOBAL:-}" ]; then
      printf '[include]\n\tpath = %s\n' "$GIT_CONFIG_GLOBAL"
    else
      [ -n "${HOME:-}" ] && [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/git/config" ] \
        && printf '[include]\n\tpath = %s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/git/config"
      [ -n "${HOME:-}" ] && [ -f "$HOME/.gitconfig" ] \
        && printf '[include]\n\tpath = %s\n' "$HOME/.gitconfig"
    fi
    printf '[url "https://github.com/"]\n'
    printf '\tinsteadOf = git@github.com:\n'
    printf '\tinsteadOf = ssh://git@github.com/\n'
  } >"$tmp" || { rm -f "$tmp"; return 0; }
  # One file per distinct content, so repeated runs reuse it.
  sum="$(cksum <"$tmp" | cut -d' ' -f1)"
  file="$dir/gitconfig-github-https.$sum"
  mv -f "$tmp" "$file" || { rm -f "$tmp"; return 0; }
  export GIT_CONFIG_GLOBAL="$file"
  export CONCLAVE_GITHUB_HTTPS_FILE="$file"
}
conclave_github_https_env
