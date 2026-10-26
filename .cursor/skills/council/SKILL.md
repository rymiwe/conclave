---
name: council
description: >-
  Chair a Conclave. The default ballot is every available leg from the
  lineup. A named author sits out on review and mixed. A pace-picked builder does not. On review, Opus
  native is a coverage leg. Jev
  decides under locked rules; the human decides a split. Use for /council,
  multi-vendor design, planning, or review. kind: review is in
  docs/review.md: Fable is on the reviewer brief; Opus native is a coverage
  leg on review kind; its agreement with Fable is one vote. Fable and Grok
  run on cursor-agent; OpenAI stays native. Not for typos or keep-going.
---
# Conclave

When the user asks for a Conclave, /council, multi-vendor design, planning, or review:

You are the CHAIR, not the implementer and not the judge.

## Flow

1. **Triage (advisory):** Briefly weigh in (you may note an optional cheap Jev opinion) on whether this is worth a full Conclave and what `kind` fits. **Do not** start voters until the human says go (or clearly orders `/council` / "run it" on a brief).
2. **Draft brief** in this repo at `tmp/council/<id>/brief.md` (from another project: `$CONCLAVE_ROOT/tmp/council/<id>/brief.md`) with header when needed (`kind`, `difficulty`, `effort`) plus Goal, Constraints, In/out of scope, Files/diff, Rubric, Decision needed. If kind is missing and cannot be inferred, the script **stops** (ask-human). Set `kind:` or `--kind`. `kind: audit` is never inferred. For design and mixed, paste chair-run test/DB evidence into the brief so voters stay packet-equal. For `kind: review`, the default Anthropic leg receives the reviewer brief the script writes; the other legs (Opus coverage, OpenAI, and Grok) do not receive the brief. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. The chair's judgment procedure is `docs/review.md`.
3. **`--dry-run`** via `scripts/conclave.sh <id> --dry-run` (or `$CONCLAVE_ROOT/scripts/conclave.sh` when used from another project). The target repo is the directory you started from, or `--repo DIR`. Show routing/preview. Get human OK on the brief unless they already ordered a full run.
4. **Full run:** `scripts/conclave.sh <id>`. Do not fan out voters yourself unless the script is missing. Do **not** override `routing.md` — user overrides only via brief header or script flags.
5. Design and mixed voters are **packet-first** (brief + review-snapshot; out-of-repo temp cwd outside the git worktree, mirrored at `tmp/council/<id>/packet/`). Claude tool-less; Codex read-only sandbox (not a full FS jail). Do not loosen Codex by kind mid-ballot. `kind: review` gives each listed voter its own worktree (`docs/review.md`). The default Anthropic leg is Fable on the reviewer brief; Opus native is a coverage leg on review kind; its agreement with Fable is one vote. Every Fable leg runs on `cursor-agent` (`CURSOR_FABLE`); Opus is the only native Claude leg. OpenAI stays native on Codex. Grok runs on `cursor-agent` (`CURSOR_GROK`); the native `grok` CLI only with `CONCLAVE_GROK_NATIVE=1`. Review and audit voters are read-only by default (`CONCLAVE_VOTER_SANDBOX`; `test-db` is the opt-in). `--claude-model opus` turns the Fable seat into Opus native and skips the coverage leg. Design and mixed reject `opus` in `--voters`. `--dry-run` writes only under `tmp/council/<id>/preview/` and must not touch ballot outputs.
6. Do not edit application source. Do not read one voter's output into another voter's prompt.
7. Do not judge in this thread. Design and mixed: the Decision judge is **Jev** (`judgment.md`). `kind: review`: after blinding, write `findings.json` from the blinded reports only, then `scripts/conclave.sh <id> --findings`, which writes `judgment.md` (fixed header, then the convergence table). `kind: audit`: content and claims use `--findings`; a proposal uses `--verdicts`. Do not use `--judge` for either. Before asking whether to post comments or apply copy changes, show every draft in full in chat, as it would go out. A file path is not a preview. If Jev is unavailable (`op` signed out / no key), tell the user to re-auth, then `scripts/conclave.sh <id> --judge` for design and mixed, `--findings` for review and audit findings, or `--verdicts` for an audit proposal. There is no generative fallback judge.
8. On `no automatic decision`, give the human a form: one row per option with each voter's lean, tradeoffs, risks, accretiveness, and cost to try. The human picks a row, or both when both are cheap. The chair gives no lean of its own, in the form or in chat. If the human wants a tiebreak, it comes from a vendor that neither voted nor chairs, on the plans unlabeled (`docs/routing.md`, Divergence).
9. Implementer only when the human says to build. Today no kind builds without the human. Review returns post, hold, or second look and never `implement`. Design and mixed `implement*` Decisions go to the human with `judgment.md` until the calibration in `docs/routing.md` exists. The implementer is the newest Grok via `cursor-agent`, pinned, in its own worktree; fallback Opus at low effort.

`/council` = triage if needed, draft brief, dry-run when useful, run on human go, then **stop**.

Do not run a Conclave for typos, tiny refactors, or "keep going" implementation.

For one question that needs no ballot, use ask mode: `scripts/conclave.sh <id> --ask TEXT`, or `--template cold-read|tell-scan --input FILE` for the writing checks. No brief. Jev picks the effort only. Read `tmp/council/<id>/ask-<name>/answers.md`. See `docs/review.md`, "Ask mode".

## Ballot (locked)

Print-only and mutual-blind. The default ballot is every available leg from the lineup. Design excludes nobody. Review and mixed exclude a named author, or on review the vendor a commit trailer names. With neither, and on review for a user-authored PR with no model trailer, they exclude nobody. A pace-picked builder is not excluded. Audit excludes that vendor when `author:` names the family, and excludes nobody when the author is unknown. The chair's vendor still votes. The lineup names who sat out and why. <!-- ballot-rule:start -->Review seats two voters from seat-usage --route lineup --kind review, on two vendor families, excluding a named author or a caller-passed builder or --builder-vendor and nobody when none of those is set, and falls back to Fable and Astra when that route is missing, fails, or asks.<!-- ballot-rule:end --> A capped pool is not seated. A pool ahead of pace still votes, at low. When seat-usage is missing, fails, or asks, the fixed default is Claude and Codex. Review warns and uses that pair. The lineup says so. `--voters` wins. Design and mixed still need at least two legs. Audit legs share one brief. Audit is never inferred. `kind: audit` reviews an existing artifact, or proposed edits to an existing doc, rule, or config. A new design or plan with no artifact yet stays `kind: design`.

| Seat | Who |
|------|-----|
| Claude | Fable, high effort, on the reviewer brief for `kind: review`. `--claude-model opus` turns this seat into Opus native and skips the coverage leg |
| Opus | Coverage leg on `kind: review`. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. Design and mixed reject it in `--voters`. On `kind: audit` it joins only when `--voters` names it, and it reads the brief |
| Codex | Astra |
| Grok | Seated unless the author is Grok, a review trailer names that vendor, or audit `author:` names it. A pace-picked Grok still votes. A capped pool is not seated. On audit it reads the brief and web search stays on. `--voters` can name it on any kind |
| Muse | Seated unless a Muse window is capped. Ahead of pace still votes, at low. `--voters ...,muse` names it on any kind. Its opinion goes to Jev like any other, and the shadow does not run beside it. See `docs/review.md`, "Muse" |
| Argon | Design and mixed only, via `agy` on the Google AI Pro subscription. Seated unless `CONCLAVE_ARGON=0` (`COUNCIL_ARGON` is the alias). The lineup does not seat it. `--voters` wins. Review and audit skip a named argon. A missing `agy`, a failed usage check, a 0% Argon pool, or a bad answer skips the leg and does not fail the run. `agy` must be signed in. Do not opt in to data sharing. Do not mark the repo trusted in `agy` |

| Role | Who |
|------|-----|
| Chair | This skill + `scripts/conclave.sh`, run per task by `cursor-agent` (newest Grok pinned), Grok Build, or Claude Code. The script is host-agnostic. |
| Judge | **Jev** (TypeSafe System One) — outside the vote and implement set; bound to locked Decision rules via `scripts/jev-judge.py` |
| Implementer | Newest Grok via `cursor-agent`, own worktree; fallback Opus at low. Never a voter. |
| Control plane | Grok Bot, through the desktop app's local execution on the executor. Reads `judgment.md` and PR links only. |

Grok votes when the lineup seats it. Review and mixed leave Grok out when the author is Grok or a review trailer names that vendor. Audit leaves Grok out when `author:` names that family. A capped pool is not seated. `--voters` still wins. Model IDs resolve at run time; never write them into prose or prompts. The script owns routing. Review and audit procedure: `docs/review.md`. `kind: audit` is never inferred.

Separately, Muse Wonder can run as a non-voting shadow leg (`CONCLAVE_MUSE_SHADOW=1`, with `COUNCIL_MUSE_SHADOW` as the alias) on design and mixed. The shadow is not a seat. The chair and Jev do not read its verdict. When the leg runs, `muse exec --json` is parsed for a subscription usage snapshot written to `~/.cache/quartermaster/muse-usage.json`. A missing snapshot or a write error leaves the run unchanged.

Other projects: use this repo as the single source (symlink/submodule); set `CONCLAVE_ROOT`; do not fork skill/script copies. Write the brief at `$CONCLAVE_ROOT/tmp/council/<id>/brief.md`. The target repo is the directory you started from, or `--repo DIR`. Design and mixed snapshot that tree. Ballot files stay in this repo: a review leg can find the target's main checkout through git worktree metadata. `scripts/council.sh` and `COUNCIL_*` are deprecated aliases: the script accepts the same arguments, and a `CONCLAVE_*` value wins when both names are set. `/council` still starts this skill.

## Voter output schema

## Findings
## Approach
## Would not do
## Uncertainties

Design and planning kinds also carry, per approach: lean, tradeoffs, risks, accretiveness, cost to try.

## Judge contract

Input: brief + anonymized `opinion-N` files only.

Output in `judgment.md`. Every kind opens with the fixed header (`kind`, `decision`, `confidence`, `confidence_of`, `chair_model`, `voter_a_model`, `voter_b_model`, `builder_model`, `judge_model`, `branch`, `pr`; `voter_c_model` after B when Grok voted; `coverage_opus_model` when the Opus coverage leg ran; for `kind: audit` also `target`, `target_type`, `snapshot_sha256`, `target_drift`, `redaction_kept`, `gate`), then:

## Consensus
## Conflicts
## Missing
## Decision

Decision rules:

- `implement` — only if every voter agrees on the approach (same shape, not wording)
- `implement with changes` — only for minor disagreements, with the **exact** changes listed
- otherwise `no automatic decision` — list options and stop

Do not invent a further design. Record which judge model ran (`judge_model` / Jev version). Jev is outside the vote and implement set.

## Secrets

Do **not** export the Jev key into a shell that launches voters. `scripts/conclave.sh` loads the key only for the Jev child process (from the 1Password reference in `CONCLAVE_JEV_OP_REF`, which has no default). `CONCLAVE_JEV_OP` overrides the `op` binary. When that override is unset, a failed `op read` is retried with `op.exe` if it is on PATH. Prefer `op`; if both reads fail, no Jev Decision until re-auth, then `scripts/conclave.sh <id> --judge`. `kind: review` uses `--findings`. `kind: audit` uses `--findings` or `--verdicts`.

## Spending

Voters spend the Claude subscription (Fable, and the Opus coverage leg on review) and the ChatGPT subscription (Astra), the flat pools those seats exist for. Tiers are not fixed here; `seat-usage` reads the live plan. The implementer spends the Cursor Models pool. Jev is metered in cents. Headroom is the metric; see `docs/routing.md`.
