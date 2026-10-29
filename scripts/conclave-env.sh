# Resolve one Conclave setting.
# CONCLAVE_<NAME> wins when it is set, including empty.
# Otherwise COUNCIL_<NAME>. Otherwise the default.
# NAME is the suffix, for example CODEX_BIN.
# Sourced by conclave.sh, council.sh (the alias), and scripts that
# can run or be sourced on their own.

conclave_env() {
  local name="$1" def="${2-}"
  local new="CONCLAVE_${name}" old="COUNCIL_${name}"
  if [ -n "${!new+x}" ]; then
    printf '%s' "${!new}"
  elif [ -n "${!old+x}" ]; then
    printf '%s' "${!old}"
  else
    printf '%s' "$def"
  fi
}

# The variable name that won: CONCLAVE_<NAME> when set, else COUNCIL_<NAME>.
conclave_env_name() {
  local name="$1"
  local new="CONCLAVE_${name}"
  if [ -n "${!new+x}" ]; then
    printf '%s' "$new"
  else
    printf '%s' "COUNCIL_${name}"
  fi
}

# Publish one value under both names so old and new readers see it.
conclave_export() {
  local name="$1" value="$2"
  printf -v "CONCLAVE_${name}" '%s' "$value"
  printf -v "COUNCIL_${name}" '%s' "$value"
  export "CONCLAVE_${name}" "COUNCIL_${name}"
}

# The agy prompt cap in bytes, read by the review Argon packet and the merge
# gate. agy silently stops reading near 190 KB.
argon_max_bytes() {
  local t
  t="$(conclave_env ARGON_MAX_BYTES 150000)"
  # Not an integer >= 1 (00 included) is the default. 10# reads 010 as ten.
  case "$t" in
    ''|*[!0-9]*) t=150000 ;;
    *) t=$((10#$t)); [ "$t" -ge 1 ] || t=150000 ;;
  esac
  printf '%s\n' "$t"
}
