# Its stdin stays the pty. Do not exec the payload with stdin
# /dev/null: on tmux 3.5a that closes the pane.
DETACH_BOOT=$(cat <<'EOF'
log=$1; shift
printf 'conclave: output goes to %s (tail -f it). Ctrl-C here stops the run.\n' "$log"
"$@" </dev/null >>"$log" 2>&1
status=$?
exit $status
EOF
)

detach_refuse_mode() {
  if [ "${ASK_MODE:-0}" -eq 1 ] || [ "${FINDINGS_MODE:-0}" -eq 1 ] \
    || [ "${VERDICTS_MODE:-0}" -eq 1 ] || [ "${JUDGE_MODE:-0}" -eq 1 ]; then
    die "conclave: --detach runs a ballot; --ask, --findings, --verdicts, and --judge stay in the foreground"
  fi
}

detach_session_name() {
  # tmux rejects '.' and ':', so an id that contains either is encoded.
  # '_' is doubled first so encoded ids stay distinct (a.b.c and a_db.c).
  local id="$1"
  case "$id" in
    *.*|*:*)
      id="${id//_/__}"
      id="${id//./_d}"
      id="${id//:/_c}"
      printf 'conclave+%s\n' "$id"
      ;;
    *)
      printf 'conclave-%s\n' "$id"
      ;;
  esac
}

detach_alive() {
  tmux -L "$1" has-session -t "=$1" 2>/dev/null
}

detach_size() {
  if [ -f "$1" ]; then
    echo $(( $(wc -c <"$1") ))
  else
    echo 0
  fi
}

detach_line_after() {
  local file="$1" off="$2" size
  size="$(detach_size "$file")"
  if [ "$size" -lt "$off" ]; then
    off=0
  fi
  if [ "$size" -le "$off" ]; then
    return 0
  fi
  tail -c +"$((off + 1))" "$file" 2>/dev/null | head -n 1 || true
}

detach_tail() {
  local file="$1" off="$2" lines size
  size="$(detach_size "$file")"
  if [ "$size" -lt "$off" ]; then
    off=0
  fi
  if [ "$size" -le "$off" ]; then
    printf '%s\n' '(run.log has no output from this launch)'
    return 0
  fi
  lines="$(tail -c +"$((off + 1))" "$file" 2>/dev/null | tail -n 20 || true)"
  if [ -n "$lines" ]; then
    printf '%s\n' "$lines"
  else
    printf '%s\n' '(run.log has no output from this launch)'
  fi
}

detach_main() {
  local wait session hint log progress log_at progress_at ticks line began elapsed
  detach_refuse_mode
  command -v tmux >/dev/null 2>&1 \
    || die "conclave: --detach needs tmux; tmux not found. Install it or run without --detach. conclave.sh never falls back to nohup or setsid."
  wait="$(conclave_env DETACH_WAIT 60)"
  [[ "$wait" =~ ^[0-9]+$ ]] \
    || die "conclave: CONCLAVE_DETACH_WAIT must be a whole number of seconds (got: $wait)"
  wait=$((10#$wait))
  session="$(detach_session_name "$ID")"
  hint="tmux -L $session attach -t $session"
  if detach_alive "$session"; then
    die "conclave: tmux session $session is already running. attach: $hint"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'detach: --dry-run stays in the foreground; a real launch starts tmux session %s\n' "$session" >&2
    return 0
  fi
  log="$DIR/run.log"
  progress="$DIR/progress.md"
  log_at="$(detach_size "$log")"
  progress_at="$(detach_size "$progress")"
  # A new server copies this client's environment. Drop the judge keys here,
  # and skip a second freshness check: the launcher already ran it.
  if ! env -u TYPESAFE_API_KEY -u JEV_API_KEY CONCLAVE_SKIP_UPDATE_CHECK=1 \
    tmux -L "$session" -f /dev/null new-session -d -s "$session" -c "$INVOKE_CWD" -- \
    "$BASH" -c "$DETACH_BOOT" conclave-detach "$log" "$BASH" "$ROOT/scripts/conclave.sh" "${DETACH_ARGS[@]}"; then
    die "detach: tmux could not start session $session (above); nothing was launched"
  fi
  # Read the line, then liveness, so success means both held together.
  # A wait of 0 still does this one check.
  ticks=$((wait * 5))
  began=$SECONDS
  while :; do
    line="$(detach_line_after "$progress" "$progress_at" || true)"
    if detach_alive "$session"; then
      if [ -n "$line" ]; then
        printf 'session: %s\ndir: %s\nattach: %s\n' "$session" "$DIR" "$hint"
        exit 0
      fi
    else
      elapsed=$((SECONDS - began))
      printf 'detach: session %s ended after %ss. Last lines this launch wrote to %s:\n' \
        "$session" "$elapsed" "$log" >&2
      detach_tail "$log" "$log_at" >&2
      exit 1
    fi
    if [ "$ticks" -le 0 ]; then
      printf 'detach: session %s is running but wrote no progress line in %ss. Left running. attach: %s\nLast lines this launch wrote to %s:\n' \
        "$session" "$wait" "$hint" "$log" >&2
      detach_tail "$log" "$log_at" >&2
      exit 3
    fi
    ticks=$((ticks - 1))
    sleep 0.2
  done
}
