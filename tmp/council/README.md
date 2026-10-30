# tmp/council/

Scratch space for multi-vendor Conclave cycles. **Not product source.**

## Layout

```
tmp/council/<id>/
  brief.md
  routing.md                 # design and mixed
  review-snapshot.md         # design and mixed: target repo path, short HEAD, git/diff
  voter-*.md / voter-*.ok
  opinion-1.md … opinion-N.md   # one per named voter; at least two
  header.json                # kind, models, branch, pr for the judgment header
  jev-raw.json / judgment.md # header, then the decision body; absent when Jev has no key
  options.md                 # design and mixed no automatic decision: same header, no option rows
  errors.md

  lineup.md                  # kind: review
  preview/lineup.md          # review dry-run only
  diffshape.txt
  readonly.txt
  model-check.md
  voter-claude.json / voter-codex.md / voter-grok-report.md
  blind/leg-*.md
  blind/key.txt              # chair reads this only after judgment.md exists
  findings.json              # chair writes this from the blinded reports
  jev-findings-request.json / jev-findings-raw.json
  judgment.md                # header, then the convergence table
  convergence.md             # the table without the header

  round.json                 # kind: review on a PR: round number, reviewed range, carried findings
  carried-findings.md        # open findings from earlier rounds, for the chair
  review-files.txt           # files the legs reviewed

tmp/council/rounds/
  <host>_<owner>_<repo>_<N>.json      # one round log per PR (docs/review.md, Revision recipes)
  <host>_<owner>_<repo>_<N>.park.md   # written when a 4th round is refused

tmp/council/examples/
  design-high-brief.md
  review-med-brief.md
```

Design and mixed write one opinion file per named voter, at least two, and
that ballot is what Jev judges. `kind: review` uses the voters actually listed.
Review procedure: `docs/review.md`.

## Git

Scratch ids are gitignored. `README.md` and `examples/` are kept. Also ignore `__pycache__/`.

## Usage

```sh
scripts/conclave.sh <id>
scripts/conclave.sh <id> --dry-run
scripts/conclave.sh <id> --judge
```

The target repo is `--repo`, or the directory the script was started from. Design and mixed snapshot that tree. The brief and every file above stay in this repo.

Jev is the Decision judge. If there is no key or the judge fails, there is no `judgment.md`. Re-auth, then `scripts/conclave.sh <id> --judge` (existing opinions, no voters). `kind: review` uses `--findings`.
