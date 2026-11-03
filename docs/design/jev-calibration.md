# Jev calibration on stored cases

`docs/routing.md` keeps every design Decision with Ryan until three design Decisions have been compared with his own picks. This note specifies a report that measures how often Jev's stored lean matches those picks. The scripts do not implement it yet.

Jev is the council judge. `scripts/jev-judge.py` calls the TypeSafe API and writes `decision_confidence`. `CHOICE_CONF_MIN` defaults to 0.55 through `JEV_CHOICE_CONF_MIN`. Confidence under `CHOICE_CONF_MIN` forces `no automatic decision` in `bind_decision`. The report does not move that cutoff.

## Explain it back

The report reads the hitl-scorecard log that already exists and maps the uneven field names into one case. It prints how often the stored Jev lean matches Ryan's pick inside each confidence band. Each rate sits next to its sample size. The default run calls nothing. Replay calls Jev only after the scorer or the 0.55 cutoff has changed. The report does not auto-decide. Ryan has to say yes before that cutoff moves, and before another kind may auto-decide.

## The log

Cases come from the existing log at `/home/box/sand-data/fleet/experiments/hitl-scorecard/log.jsonl`. There is no second store. The command reads that file and keeps the cases in memory for one run.

A census of field names and counts, used to write this note, found 319 parsed rows and no `kind` field. The census has no row values. This note does not claim what any row says.

The command opens the log read-only. It does not append to the log, truncate the log, or rewrite a row.

## The normalizer

The normalizer maps aliases onto one case. It writes nothing back to the log. When two aliases for the same fact are both present and the values differ, the row is a conflict. Conflicts stay out of the agreement rates. The report prints a conflict count with its n.

The decision id is `decision_id`, or else `id`. Ryan's pick is the first present value among `ryan_pick`, `user_pick`, `human_pick`, and `pick`, in that order. Jev's lean is `jev_lean`, or else `jev`. Confidence is the first present value among `jev_conf`, `jev_confidence`, and `conf`, in that order.

`ryan`, `decision`, `bot_lean`, `wes_lean`, `jev_wifi8_lean`, and `jev_wifi8_conf` are not aliases. The census lists those names. This note does not guess their meaning.

A row missing a pick, a lean, or a confidence value is a skip. Skips stay out of the rates. The report prints the skip count with its n.

Agreement compares the trimmed pick with the trimmed lean. The compare is case-sensitive and has no synonym table. The compare uses the lean. The bound label `no automatic decision` is a separate fact. A lean whose confidence is under 0.55 can still match a pick. The match does not mean the script would have auto-decided.

## What a case holds

A case is metadata. The fields are the decision id, the kind, Ryan's pick, Jev's lean, and Jev's confidence. The normalizer drops `options`, `notes`, `note`, `question`, `ryan_pick_text`, `items`, and every other text field.

Work cases are rows whose `bot` is `Work Wes`, `work-wes`, or `Work Wes overnight`. Those are the SOFware and Work Wes rows. For a work row the command keeps the decision id and the outcome. The outcome is match or miss. The command drops the pick text, the lean text, and every ballot, option, or code string before it writes anything. Nothing the command writes puts that text on the box. The command does not delete text that is already in the log.

## Kind on new rows

The appender that writes a row to this log adds `kind` on each new row. The value is `design`, `review`, `mixed`, or `audit`. Old rows stay as they are. A row with no `kind` is `unknown`.

This repo has no writer for that log. Adding `kind` is a requirement on the appender, wherever that program lives.

Only rows with kind `design` count toward the three comparisons in `docs/routing.md`. The report prints how many design rows were compared. That line counts rows. `unknown` is its own group and does not add to the three. The command does not backfill kind and does not rewrite old rows to add it.

## The default report

The default command reads stored leans and stored confidences. It makes no HTTP call. It does not read `TYPESAFE_API_KEY` or `JEV_API_KEY`, even when the environment has them.

The command prints one agreement rate per confidence band and prints n on that same line. It does not print one agreement rate for the whole log.

Each band line prints the numeric edges it used.

- Confidence under `CHOICE_CONF_MIN`. The default edge is 0.55. `bind_decision` forces `no automatic decision` under that edge.
- Confidence from `CHOICE_CONF_MIN` up to, and not including, `FINDING_POST_MIN`.
- Confidence at `FINDING_POST_MIN` or more. That constant defaults to 0.70 and gates a review finding. `docs/routing.md` says 0.70 is not a build threshold. The band keeps a design rate away from the review gate.

An empty band prints the band and an n of 0, and prints no rate. The same three bands appear once per kind. The design group also lists each compared decision id with match or miss. The list names the rows in the design compared count. The list has no pick text and no lean text.

The command writes the table to `tmp/council/calibration/report.md` and prints the same table on stdout. That path is gitignored scratch. The command does not commit the report and does not copy the report to the box.

## Replay

Replay is a separate explicit run. The default does not replay. Run replay only when the scorer has changed, or when `CHOICE_CONF_MIN` has changed. Jev calls cost money. The command does not infer a change and then call Jev.

The replay process is the only process that reads `TYPESAFE_API_KEY`. It uses the same key path as `scripts/jev-judge.py`. Replay prints a band table. It does not change the threshold and it does not write the log.

## Ryan still says yes

The command reports. Changing 0.55, or `JEV_CHOICE_CONF_MIN`, needs Ryan's yes. Letting a new kind auto-decide needs Ryan's yes. A design compared count of three is a row count in the table. The command does not turn auto-decide on when that count reaches three.

## Security

The command reads the hitl-scorecard log named above, and it uses those bytes only to build the in-memory cases. The written output is `tmp/council/calibration/report.md` plus the same table on stdout. The report contains ids, kinds, rates, row counts, n, and the design id list. A work row loses its text before the write. A pick string and a lean string stay out of the file.

The report contains no API key, no ballot, no option text, and no code. `TYPESAFE_API_KEY` belongs to the replay process only. The default command never reads that variable. Replay does not print the key.

## Done

This note adds no test. The build is done when a test proves the default report makes no network calls. The test feeds a fixture log, sets a dummy `TYPESAFE_API_KEY`, and replaces the HTTP opener that `scripts/jev-judge.py` uses. The default command exits 0 and prints each band with its n. The test fails if that opener is called.

## Open questions

The census cannot show whether `ryan` or `decision` holds a pick or an id. Those names stay unmapped until the values are checked. Exact string match counts a wording difference as a miss. A synonym table is a later change, and it is not part of this report. Old work rows may already store `options` text in the log. Removing that text would rewrite history. This command leaves the log bytes alone. A strip of old work text needs its own yes from Ryan. The appender that adds `kind` was not in this repo when this note was written.
