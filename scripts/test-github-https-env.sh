#!/usr/bin/env bash
# cursor-agent fetches git@github.com:cursor/plugins.git at start-up. Where
# github.com:22 hangs, every cursor-agent leg hangs. A child of conclave.sh
# must resolve that remote to HTTPS, keep the caller's global git config, and
# leave the real global config files untouched. Hermetic: no network, temp HOME.
set -u
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fail=0
check() { # name want got
  if [ "$2" = "$3" ]; then echo "ok $1"; else echo "FAIL $1: want [$2] got [$3]"; fail=1; fi
}

home="$work/home"; mkdir -p "$home" "$work/bin"
printf '[user]\n\tname = Hermetic Tester\n' >"$home/.gitconfig"
before="$(cksum <"$home/.gitconfig")"
git init -q "$work/plugins"
git -C "$work/plugins" remote add origin git@github.com:cursor/plugins.git

# Stand-in for cursor-agent: like the real one, it replaces the caller's
# GIT_CONFIG_COUNT/KEY/VALUE pairs with its own before running git.
cat >"$work/bin/cursor-agent" <<SH
#!/usr/bin/env bash
export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=false
printf '%s|%s' "\$(git -C '$work/plugins' ls-remote --get-url origin)" "\$(git -C '$work/plugins' config user.name)"
SH
chmod +x "$work/bin/cursor-agent"

base_env=(env -i PATH="$work/bin:/usr/local/bin:/usr/bin:/bin" HOME="$home" XDG_CACHE_HOME="$work/cache" TMPDIR="$work")
https="https://github.com/cursor/plugins.git|Hermetic Tester"
ssh="git@github.com:cursor/plugins.git|Hermetic Tester"

# 1. Reproducer: no fix. The plugin remote stays on SSH.
check "without the fix the agent's git uses SSH" "$ssh" "$("${base_env[@]}" cursor-agent)"

# 2. Why GIT_CONFIG_COUNT pairs are not the fix: the agent replaces them.
check "GIT_CONFIG_COUNT insteadOf from the caller is clobbered" "$ssh" \
  "$("${base_env[@]}" GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=url.https://github.com/.insteadOf \
      GIT_CONFIG_VALUE_0=git@github.com: cursor-agent)"

# 3. A child of conclave.sh (a voter leg) gets HTTPS and keeps user config.
got="$("${base_env[@]}" COUNCIL_SOURCE_ONLY=1 bash -c \
  ". '$ROOT/scripts/conclave.sh' >/dev/null 2>&1; cursor-agent")"
check "a cursor-agent child of conclave.sh fetches GitHub over HTTPS" "$https" "$got"

# 4. The chair wrapper does the same for one command.
check "with-github-https.sh cursor-agent fetches over HTTPS" "$https" \
  "$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 5. A caller's own GIT_CONFIG_GLOBAL is kept (included), not dropped.
printf '[user]\n\tname = Custom Global\n' >"$work/custom.gitconfig"
check "an existing GIT_CONFIG_GLOBAL is included" \
  "https://github.com/cursor/plugins.git|Custom Global" \
  "$("${base_env[@]}" GIT_CONFIG_GLOBAL="$work/custom.gitconfig" "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 6. Opt-out.
check "CONCLAVE_GITHUB_HTTPS=0 leaves SSH" "$ssh" \
  "$("${base_env[@]}" CONCLAVE_GITHUB_HTTPS=0 "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7. Nested use reuses the same file instead of stacking includes.
got="$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" "$ROOT/scripts/with-github-https.sh" \
  bash -c 'printf %s "$GIT_CONFIG_GLOBAL"')"
one="$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" bash -c 'printf %s "$GIT_CONFIG_GLOBAL"')"
check "nested wrapper reuses one config file" "$one" "$got"

# 8. The real global config is never edited.
check "~/.gitconfig unchanged" "$before" "$(cksum <"$home/.gitconfig")"
check "no ~/.ssh written" "absent" "$([ -e "$home/.ssh" ] && echo present || echo absent)"

[ "$fail" = 0 ] && echo "PASS test-github-https-env" || echo "FAIL test-github-https-env"
exit "$fail"
