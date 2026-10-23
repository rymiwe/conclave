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

# 4. The chair wrapper does the same for one command, and marks that process.
check "with-github-https.sh cursor-agent fetches over HTTPS" "$https" \
  "$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" cursor-agent)"
check "with-github-https.sh exports CONCLAVE_CHAIR=1 to the command" "1" \
  "$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" bash -c 'printf %s "${CONCLAVE_CHAIR:-unset}"')"
check "a child of conclave.sh is not marked CONCLAVE_CHAIR" "unset" \
  "$("${base_env[@]}" COUNCIL_SOURCE_ONLY=1 bash -c \
    ". '$ROOT/scripts/conclave.sh' >/dev/null 2>&1; printf %s \"\${CONCLAVE_CHAIR:-unset}\"")"

# 5. A caller's own GIT_CONFIG_GLOBAL is kept (included), not dropped.
printf '[user]\n\tname = Custom Global\n' >"$work/custom.gitconfig"
check "an existing GIT_CONFIG_GLOBAL is included" \
  "https://github.com/cursor/plugins.git|Custom Global" \
  "$("${base_env[@]}" GIT_CONFIG_GLOBAL="$work/custom.gitconfig" "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 6. Opt-out.
check "CONCLAVE_GITHUB_HTTPS=0 leaves SSH" "$ssh" \
  "$("${base_env[@]}" CONCLAVE_GITHUB_HTTPS=0 "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7. Nested and repeated use reuse one regular file instead of stacking.
one="$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" bash -c 'printf %s "$GIT_CONFIG_GLOBAL"')"; rc1=$?
got="$("${base_env[@]}" "$ROOT/scripts/with-github-https.sh" "$ROOT/scripts/with-github-https.sh" \
  bash -c 'printf %s "$GIT_CONFIG_GLOBAL"')"; rc2=$?
check "wrapper runs succeed" "0 0" "$rc1 $rc2"
check "wrapper exports a regular file" "file" "$([ -n "$one" ] && [ -f "$one" ] && echo file || echo "not-a-file:$one")"
check "nested wrapper reuses one config file" "$one" "$got"
check "one cache file after repeated and nested runs (home config)" "1" \
  "$(grep -lF "path = \"$home/.gitconfig\"" "$work/cache/conclave"/gitconfig-github-https.* 2>/dev/null | wc -l | tr -d ' ')"

# 7b. A relative GIT_CONFIG_GLOBAL is pinned to the caller's cwd.
mkdir -p "$work/rel"
printf '[user]\n\tname = Relative Global\n' >"$work/rel/rel.gitconfig"
check "a relative GIT_CONFIG_GLOBAL is resolved against the caller's cwd" \
  "https://github.com/cursor/plugins.git|Relative Global" \
  "$(cd "$work/rel" && "${base_env[@]}" GIT_CONFIG_GLOBAL=rel.gitconfig "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7c. Paths with git-config special characters are quoted and escaped.
odd="$work/odd dir #1; x\\y \"q\""
mkdir -p "$odd"
printf '[user]\n\tname = Odd Path\n' >"$odd/g.config"
check "an include path with # ; backslash quote and spaces survives" \
  "https://github.com/cursor/plugins.git|Odd Path" \
  "$("${base_env[@]}" GIT_CONFIG_GLOBAL="$odd/g.config" "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7c2. A newline in an include path is escaped, not written raw.
nl="$work/new"$'\n'"line"
mkdir -p "$nl"
printf '[user]\n\tname = Newline Path\n' >"$nl/g.config"
check "an include path with a newline survives" \
  "https://github.com/cursor/plugins.git|Newline Path" \
  "$("${base_env[@]}" GIT_CONFIG_GLOBAL="$nl/g.config" "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7c3. An existing permissive cache directory is made private before use.
mkdir -p "$work/cache3/conclave"; chmod 777 "$work/cache3/conclave"
"${base_env[@]}" XDG_CACHE_HOME="$work/cache3" "$ROOT/scripts/with-github-https.sh" true
check "a group/world-writable cache dir is tightened to 700" "drwx------" \
  "$(ls -ld "$work/cache3/conclave" | cut -c1-10)"

# 7d. GIT_CONFIG_GLOBAL set but empty keeps meaning "no global config".
check "an empty GIT_CONFIG_GLOBAL includes nothing" \
  "https://github.com/cursor/plugins.git|" \
  "$("${base_env[@]}" GIT_CONFIG_GLOBAL= "$ROOT/scripts/with-github-https.sh" cursor-agent)"

# 7e. A non-file at the publish path is refused: the env is left alone.
blocked_home="$work/home2"; mkdir -p "$blocked_home"
printf '[user]\n\tname = Hermetic Tester\n' >"$blocked_home/.gitconfig"
target="$("${base_env[@]}" HOME="$blocked_home" XDG_CACHE_HOME="$work/cache2" "$ROOT/scripts/with-github-https.sh" bash -c 'printf %s "$GIT_CONFIG_GLOBAL"')"
rm -f "$target"; mkdir -p "$target"
check "a directory at the publish path is not exported" "unset" \
  "$("${base_env[@]}" HOME="$blocked_home" XDG_CACHE_HOME="$work/cache2" "$ROOT/scripts/with-github-https.sh" bash -c 'printf %s "${GIT_CONFIG_GLOBAL:-unset}"')"

# 8. The real global config is never edited.
check "~/.gitconfig unchanged" "$before" "$(cksum <"$home/.gitconfig")"
check "no ~/.ssh written" "absent" "$([ -e "$home/.ssh" ] && echo present || echo absent)"

[ "$fail" = 0 ] && echo "PASS test-github-https-env" || echo "FAIL test-github-https-env"
exit "$fail"
