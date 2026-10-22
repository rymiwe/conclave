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

# Print one [include] path as a quoted, escaped git-config value.
conclave_gitconfig_include() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//\"/\\\"}"
  v="${v//$'\n'/\\n}"
  printf '[include]\n\tpath = "%s"\n' "$v"
}

conclave_github_https_env() {
  local on="${CONCLAVE_GITHUB_HTTPS-${COUNCIL_GITHUB_HTTPS-1}}"
  [ "$on" = 0 ] && return 0
  # Already applied in a parent: keep it, do not nest includes.
  if [ -n "${CONCLAVE_GITHUB_HTTPS_FILE:-}" ] \
     && [ "${GIT_CONFIG_GLOBAL:-}" = "$CONCLAVE_GITHUB_HTTPS_FILE" ] \
     && [ -f "$CONCLAVE_GITHUB_HTTPS_FILE" ]; then
    return 0
  fi
  local dir file tmp sum base
  # A private, user-owned cache directory only. No shared /tmp fallback: if
  # the directory is unusable, leave the environment as it was.
  dir="${XDG_CACHE_HOME:-${HOME:-}/.cache}/conclave"
  case "$dir" in /*) ;; *) return 0 ;; esac
  mkdir -p -m 700 "$dir" 2>/dev/null || return 0
  if [ -L "$dir" ] || [ ! -d "$dir" ] || [ ! -O "$dir" ]; then
    return 0
  fi
  # mkdir -m does not tighten an existing directory. Ours, so make it private,
  # and give up if that does not hold.
  chmod 700 "$dir" 2>/dev/null || return 0
  case "$(ls -ld "$dir" 2>/dev/null)" in drwx------*) ;; *) return 0 ;; esac
  tmp="$(mktemp "$dir/.gitconfig-github-https.XXXXXX")" || return 0
  {
    printf '# Written by conclave scripts/github-https-env.sh. Safe to delete.\n'
    # Git reads only GIT_CONFIG_GLOBAL when it is set, so include whatever
    # git would have read as global config without this file. Set but empty
    # means "no global config": include nothing.
    if [ "${GIT_CONFIG_GLOBAL+set}" = set ]; then
      if [ -n "$GIT_CONFIG_GLOBAL" ]; then
        base="$GIT_CONFIG_GLOBAL"
        # Git resolves a relative GIT_CONFIG_GLOBAL against its cwd, an
        # include against the including file. Pin it to this cwd.
        case "$base" in /*) ;; *) base="$PWD/$base" ;; esac
        conclave_gitconfig_include "$base"
      fi
    else
      [ -n "${HOME:-}" ] && [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/git/config" ] \
        && conclave_gitconfig_include "${XDG_CONFIG_HOME:-$HOME/.config}/git/config"
      [ -n "${HOME:-}" ] && [ -f "$HOME/.gitconfig" ] \
        && conclave_gitconfig_include "$HOME/.gitconfig"
    fi
    printf '[url "https://github.com/"]\n'
    printf '\tinsteadOf = git@github.com:\n'
    printf '\tinsteadOf = ssh://git@github.com/\n'
  } >"$tmp" || { rm -f "$tmp"; return 0; }
  # One file per distinct content, so repeated runs reuse it.
  sum="$(cksum <"$tmp" | cut -d' ' -f1)"
  file="$dir/gitconfig-github-https.$sum"
  # Never publish into or onto something that is not a regular file.
  if [ -L "$file" ] || { [ -e "$file" ] && [ ! -f "$file" ]; }; then
    rm -f "$tmp"
    return 0
  fi
  mv -f "$tmp" "$file" 2>/dev/null || { rm -f "$tmp"; return 0; }
  [ -f "$file" ] || return 0
  export GIT_CONFIG_GLOBAL="$file"
  export CONCLAVE_GITHUB_HTTPS_FILE="$file"
}
conclave_github_https_env
