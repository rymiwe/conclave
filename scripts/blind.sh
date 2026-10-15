#!/usr/bin/env bash
# Blind leg reports before the chair reads them.
# Usage: scripts/blind.sh <output> <report>...   (a .json report is Claude's; .result is used)
# Writes <output>/blind/leg-{A,B,...}.md in shuffled order and the key to
# <output>/blind/key.txt. Prints nothing about the mapping: do not cat key.txt
# until the convergence table is built.
set -euo pipefail
# --keep word1,word2 leaves those family words unredacted. Model IDs and leg
# paths are still redacted. kind: review does not pass --keep.
keep=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --keep)
      [ "$#" -ge 2 ] || { echo "blind.sh: --keep requires a comma list" >&2; exit 1; }
      keep=$2
      shift 2
      ;;
    --keep=*)
      keep=${1#--keep=}
      shift
      ;;
    *)
      break
      ;;
  esac
done
[ "$#" -ge 1 ] || { echo "blind.sh: usage: blind.sh [--keep word1,word2] <output> <report>..." >&2; exit 1; }
out=$1; shift
rm -rf "$out/blind"
mkdir -p "$out/blind"
: > "$out/blind/key.txt"
letters=(A B C D E F)
(( $# <= ${#letters[@]} )) || { echo "blind.sh: at most ${#letters[@]} reports" >&2; exit 1; }
# NUL-separated so spaces, newlines and glob characters in paths survive.
mapfile -d '' srcs < <(printf '%s\0' "$@" | shuf -z)
i=0
for src in "${srcs[@]}"; do
  dst="$out/blind/leg-${letters[$i]}.md"
  case $src in
    *.json) jq -er '.result | strings' "$src" > "$dst" \
              || { echo "blind.sh: no .result string in $src" >&2; exit 1; } ;;
    *) cp -- "$src" "$dst" ;;
  esac
  [ -s "$dst" ] || { echo "blind.sh: empty report $src" >&2; exit 1; }
  # 1. This run's scratch (.../council-review.<id>.<rand>/leg.<rand>/{wt,tmp}/).
  #    The council-review prefix is kept so this redaction still matches.
  #    and the old skill's wt-<slug>-<leg> names, Grok's review files, CLI
  #    home directories. 2. Model IDs. 3. Vendor and model names, including
  #    before ':' or '-' (labels, "Fable-native"), but not inside a path or
  #    identifier (src/grok/client.ts, GrokClient). A name that starts a
  #    relative path (grok/x.ts) is redacted; cite paths from the repo root. "cursor", "sol",
  #    "muse", and "meta" are common code words, so only their capitalized
  #    product forms are names.
  BLIND_KEEP="$keep" perl -i -pe '
    my %keep = map { $_ => 1 } grep { length } split /,/, ($ENV{BLIND_KEEP} // "");
    my %keep_lc = map { lc($_) => 1 } keys %keep;
    s{(?:/[\w.@+-]+)*/council-review\.[\w.-]+/leg\.[\w.-]+/(?:wt|tmp)/}{}g;
    s{\bwt-[\w-]*?-(?:fable|gpt|astra|grok|review|structural|ce)\b}{wt-[leg]}gi;
    s{\bgrok-(?:review-[\w-]+|\$USER|\$\(id -u\)|[a-z_][\w-]*(?=/))}{[leg-file]}gi;
    s{\.(?:grok|codex|claude|cursor)/}{.[cli]/}gi;
    s{\bcursor-agent\b}{[cli]}gi;
    s{(?<![\w/.-])(?:claude|gpt|grok|muse)-[\w.-]*\w(?!\w)}{[model]}gi;
    s{(?<![\w/.-])(?i:fable|opus|sonnet|haiku|claude|anthropic|grok|xai|x\.ai|astra|codex|openai|chatgpt|gpt|supergrok|typesafe|jev)(?!\w|\.\w)}{ $keep_lc{lc($&)} ? $& : "[model]" }ge;
    s{(?<![\w/.-])(?:Cursor|Sol|Muse|Meta)(?!\w|\.\w)}{ ($keep{$&} || $keep_lc{lc($&)}) ? $& : "[model]" }ge;
    s{(?<=\[model\]/)\w+}{[model]}g;
  ' "$dst"
  printf '%s=%s\n' "${letters[$i]}" "$src" >> "$out/blind/key.txt"
  i=$((i+1))
done
