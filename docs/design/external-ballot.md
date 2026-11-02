# External ballot hook

Conclave can take a ballot from a runner it did not start.
`apply_kind_voters` seats a voter, then the launch loops start that voter here. An outside runner needs a packet to read and a ballot the current blind path will accept. This note specifies that path. The scripts do not implement it yet.

## Runner setting

Each id in `$VOTERS` is `local` or `external`. A missing line means `local`, and the current launch function still runs.

The lines sit in the brief header, with `kind:` and `author:`. `parse_brief_header` in `scripts/conclave.sh` already reads that header. `external` carries a name. The name is a label, such as `remote-cli`. Conclave stores the label and does not pass it to a shell.

```
runner.claude: local
runner.codex: external remote-cli
```

`parse_brief_header` ends the header on the first unknown line once any key is set, so this key has to be known there. An empty name on `external`, extra text on `local`, or an id absent from `$VOTERS` stops the run before launch.

`Seat` in `scripts/lineup.py` stays the pool name. `voter_name` folds each pool into one voter id, and the two rows that share `claude` stay one launch. The launch `case` starts one process per id. The runner key is that id, one of `claude`, `opus`, `codex`, `grok`, `muse`, and `argon`.

## Pickup and ingest

Conclave copies the packet to `tmp/council/<id>/external/<voter>/packet/` and writes `ready` beside it. Design and mixed copy the tree from `prepare_packet_workspace`. Review and audit copy the prompt and brief the local function would have received, such as `run_claude` or `review_run_codex`.

The runner writes `ballot.md.tmp` in its own directory, then renames it to `ballot.md`. That matches `atomic_write_text` in `scripts/muse-usage.py`. Conclave reads `ballot.md` only after the rename. Every other name in the directory stays unused.

Conclave copies those bytes to the path the kind already uses, and opens only the path it built from the voter id.

Design and mixed land on `voter-<voter>.md`. `has_voter_schema` requires `## Findings`, `## Approach`, `## Would not do`, and `## Uncertainties`. `mark_voter_ok` writes the empty `.ok`. A runner `.ok` is ignored. `publish_voter_opinions` then runs `anonymize`.

Review and audit land on the path from `review_report_file`. For `claude` and `opus`, `scripts/blind.sh` keeps the JSON `.result` string, so conclave puts the body in that one field. Other ids get the body as the report file. Conclave writes `voter-<voter>.model.txt` as a fixed `ok` line with no runner name, so `review_report_present` can pass, and `review_blind` runs as it does now.

`usage.txt`, when under the size cap, is stored raw in the sidecar.

## Timeout and failure

The wait starts at `ready`. The limit is `conclave_env EXTERNAL_BALLOT_TIMEOUT`, default 900, the same default as `argon_print_timeout`. `review_timed_leg` only records seconds for a local process.

At the deadline a missing `ballot.md` is `timeout`. A file that shows up later is `late`, and its bytes stay unread. An invalid body is `invalid`. Design and mixed get no `.ok`. Review and audit get `voter-<voter>.fail` holding that one word. The long check text goes in `voter-<voter>.meta`, with the runner name, the wall seconds, and any usage text. `log_err` records the voter id. `review_record_seat_failure` gets `nonzero`, the fallback `review_classify_failure` already uses. The voter stays out of `publish_voter_opinions` and `review_blind`.

`argon_skip` remains the local Argon skip. An external failure stays on the missing-ballot path. Seconds also go to `voter-<voter>.seconds`. A fresh run has to extend `clear_ballot_artifacts` so it removes `external/` and the sidecars. A dry run stays under `tmp/council/<id>/preview/` and does not wait.

## Blinding

`build_state` sends the brief and the anonymized opinions. `build_findings_state` sends the claim, the citation, the cited code, and the evidence. Neither reads the sidecar, the seconds file, or `external/`. `scripts/blind.sh` writes `blind/leg-*.md` and `blind/key.txt` from the report files.

`anonymize` and the Perl pass in `blind.sh` strip names they already list. The runner label is outside those lists. Conclave rejects a body that contains the label as a whole word. It does not copy that body to `opinion-*.md` or to the file `review_blind` reads. `council_write_header_json` keeps the label out of every `voter_*_model` field.

`run_jev_judge` and `run_findings_mode` still call `scripts/jev-judge.py`. The outside runner does not.

## Lineup

`load_lineup_ballot` and `apply_kind_voters` still choose who votes. `--voters` still wins. The runner line is read after `$VOTERS` is fixed, and it only picks launch or wait. `review_reseat_failed` still replaces one failed voter from a lineup list, using that replacement's own line. `launch_muse_shadow` keeps its current launcher.

## Security

Conclave measures the file before reading it for headings. The cap is `conclave_env EXTERNAL_BALLOT_MAX_BYTES`, default 150000, the same default as `argon_max_bytes`. Over the cap, the body stays unread and the seat fails.

The ingested file is a regular `ballot.md` in that voter's directory. A symlink is refused. A name containing `..` is refused. Body text is not opened as a path and is not passed to a shell. The runner name and `usage.txt` are treated the same way.

## Tests to add

This change adds no tests. The implementation should cover these cases.

- A missing runner line still calls the local launcher.
- `external` starts no local launcher and does not exec the name.
- Only a renamed `ballot.md` that passes `has_voter_schema` reaches `publish_voter_opinions`.
- `ballot.md.tmp`, a late file, a symlink, a `..` name, and an over-cap body stay out of `run_jev_judge`.
- A body that contains the runner name stays out of `opinion-*.md` and `blind/leg-*.md`.
- `build_state` and `build_findings_state` receive no sidecar.

## Open questions

The timeout and the cap reuse the Argon defaults. A person at the other machine may need longer. `review_record_seat_failure` has no `timeout` reason, so this design passes `nonzero` and keeps `timeout`, `late`, and `invalid` in the sidecar. `usage.txt` has no schema. The brief is the only config site.
