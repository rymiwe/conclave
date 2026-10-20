# AGENTS

## Roles

| Role | Who |
|------|-----|
| **Control plane** | Grok Bot, from any client (phone, car, another machine). Talks to the executor through the Bot desktop app's local execution. Reads `judgment.md` and PR links. Never holds the repo, never reads a diff. |
| **Chair** | Conclave skill + `scripts/conclave.sh`, run per task on the executor by `cursor-agent` with the newest Grok pinned (fallback: Opus on the Claude subscription when both Grok pools are over pace or unusable; native Grok Build chairs when that pool is the one to spend). Owns routing; does not judge; does not edit app source. The script is host-agnostic. |
| **Voters** | The default ballot is every available leg from `seat-usage --route lineup`, print-only via `scripts/conclave.sh`. Families map to Claude (Fable), Codex (Astra), Grok, and Muse. On `kind: review`, that ballot adds Opus native unless Anthropic built the diff. A capped pool is not seated. A pool ahead of pace still votes, at low. Design excludes nobody. Review and mixed exclude the vendor that built the diff, which is the lineup's builder unless the brief says `author:`. Audit excludes that vendor when `author:` names the family, and excludes nobody when it does not. The chair's vendor still votes. Muse is seated unless a Muse window is capped. On design and mixed, Conclave also seats Argon (Gemini 4 via `agy` on the Google AI Pro subscription) unless `CONCLAVE_ARGON=0` (`COUNCIL_ARGON` is the alias). The lineup does not seat it. `--voters` wins. Review and audit do not seat Argon; naming it there skips the leg. A missing `agy`, a failed usage check, a 0% Argon pool, or a bad answer skips the leg and does not fail the run. `agy` must be signed in. Do not opt in to data sharing. Do not mark the repo trusted in `agy`. When seat-usage is missing, fails, or returns mode ask, the fixed default is Fable and Astra, and review also adds Opus native. The lineup says so. `--voters` wins. Voter effort follows burn. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. `--claude-model opus` (or the brief `claude:` header) turns the Fable seat into Opus native and skips the coverage leg. Design and mixed reject `opus` in `--voters` and still need at least two legs. On `kind: audit`, every seated leg is on the same brief. Audit is never inferred. |
| **Shadow** | Muse Wonder, non-voting, off unless `CONCLAVE_MUSE_SHADOW=1` (`COUNCIL_MUSE_SHADOW` is the alias). Design and mixed only. Packet-first and tool-less, same as the Claude and Grok voters. Its verdict stays on the ballot and is not given to the chair or to Jev. A subscription usage snapshot from the leg's `--json` stream is stored at `~/.cache/quartermaster/muse-usage.json` when the stream includes one. A missing snapshot or a write error leaves the run unchanged. |
| **Judge** | **Jev** via `scripts/jev-judge.py` — outside the vote and implement set. No generative fallback judge. Without Jev there is no automatic Decision; the human decides from the options form. |
| **Implementer** | Newest Grok via `cursor-agent`, pinned, one worktree per ticket — only when the human says to build. Today no kind builds without the human. Review returns post, hold, or second look and never `implement`. Design and mixed `implement*` Decisions go to the human with `judgment.md` until the calibration in `docs/routing.md` exists. Fallback: Opus at low effort. Not a voter. |

Roles are families. Model IDs resolve at run time; do not write them into prose or prompts.

## Using from other projects

One source of truth: this repo. Do **not** fork copies of the skill/script.

In another project: symlink or submodule this repo (or just `.cursor/skills/council` + `scripts/`), set `CONCLAVE_ROOT`, and run. `COUNCIL_ROOT` and `scripts/council.sh` are deprecated aliases. A `CONCLAVE_*` value wins when both names are set:

```sh
mkdir -p "$CONCLAVE_ROOT/tmp/council/<id>"
# write "$CONCLAVE_ROOT/tmp/council/<id>/brief.md"
"$CONCLAVE_ROOT/scripts/conclave.sh" <id>
```

The target repo is the directory you started from, or `--repo DIR`. Design and mixed snapshot that tree (`git -C`). Review reads it the same way. The brief and every ballot file stay in this repo under `tmp/council/<id>/`. Do not write them into the target: a review leg can find the target's main checkout through git worktree metadata.

A new executor machine needs the vendor CLIs, their logins, and a clone of this repo. Nothing else.

## Jev API key

Prefer **1Password CLI** (`op`). Do **not** export the key into a shell that will run voters. The script reads it only for the Jev process (the 1Password reference in `CONCLAVE_JEV_OP_REF`, which has no default, or a key saved only for that run). `CONCLAVE_JEV_OP` overrides the `op` binary. When that override is unset, a failed `op read` is retried with `op.exe` if it is on PATH. A trailing CR is stripped. To run with no key (a test or a builder smoke), set `CONCLAVE_JEV_OP=/bin/false`: unsetting `TYPESAFE_API_KEY` and `JEV_API_KEY` alone still reads 1Password and calls Jev (2026-10-02, [PR 6](https://github.com/rymiwe/conclave-archive/pull/6) round 3).

```sh
# the reference only, never the key; the script runs `op read` for the judge process:
export CONCLAVE_JEV_OP_REF='<your 1Password secret reference>'
```

If `op` is signed out or the key cannot be read: **no Jev Decision**. Re-auth, then `scripts/conclave.sh <id> --judge` (existing opinion files, no voters). `kind: review` uses `--findings`, not `--judge`. `kind: audit` uses `--findings` or `--verdicts`. Never commit the key.

### Handoff

- `no automatic decision` → the human picks a row from `options.md` (see `docs/routing.md`, "Divergence"). Both rows when both are cheap: two worktrees, then a review-kind Conclave on the two diffs.
- `implement` / `implement with changes` → the human, with `judgment.md`, until the calibration in `docs/routing.md` exists. Review returns post, hold, or second look and never `implement`. Today no kind builds without the human.
- File existence alone is **not** authorization

## When to Conclave

1. **Triage (advisory):** chair or cheap Jev weigh-in on "worth a Conclave?" / likely `kind` — advice only; no human check here (the single check is step 2).
2. Chair **drafts** the brief in this repo at `tmp/council/<id>/brief.md` (headers + Goal, Constraints, In/out, Files/diff, Rubric, Decision needed). The brief opens with a two-line restatement: line 1 the goal, line 2 the problem. From another project that path is `$CONCLAVE_ROOT/tmp/council/<id>/brief.md`. Send that restatement to Ryan as the **single human check** (go/no-go and brief OK in one); once he says yes, run the council as usual.
3. `scripts/conclave.sh <id> --dry-run` — show routing/preview; no second human check.
4. `scripts/conclave.sh <id>` — full ballot.
5. Read `judgment.md` before implementing. Every kind opens with the fixed header (`kind`, `decision`, `confidence`, `confidence_of`, model per role, `branch`, `pr`; audit also writes `target`, `target_type`, `snapshot_sha256`, `target_drift`, `redaction_kept`, `gate`). `confidence_of` says what `confidence` refers to. Design and mixed bind `decision` from Jev. Review binds it from the findings gate (`comments to post`, `human review`, or `clean`). Audit findings bind `changes to draft`, `human review`, or `clean`. An audit proposal binds `human review`.

The default ballot is every available leg from the lineup. On design and mixed, Conclave also seats Argon via `agy` unless `CONCLAVE_ARGON=0`. The lineup does not seat it, and a failed leg is skipped. On `kind: review`, that ballot adds Opus native unless Anthropic built the diff. A capped pool is not seated. A pool ahead of pace still votes, at low. Design excludes nobody. Review and mixed exclude the diff's builder unless `author:` names another family. Audit excludes the named author, or nobody when `author:` is absent. The chair's vendor still votes. Voter effort follows burn. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. Design and mixed stay packet-first, reject `opus` in `--voters`, and still need at least two legs. `kind: audit` is never inferred. Every seated audit leg is on one brief. `--voters` wins. When seat-usage is missing, fails, or asks, the fixed default is Fable and Astra, and review also adds Opus native.

## Operating posture

- **Readiness:** OK for real design/review Conclaves, with ongoing scrutiny (not "set and forget").
- **Defaults (enforced in script):** design and mixed stay packet-first (out-of-repo temp cwd, never under the git worktree; mirror under `tmp/council/<id>/packet/`); Claude tool-less; Codex read-only sandbox (**not** a full FS jail — accept packet-first + human skim; do not punch sandbox holes by kind). `kind: review` uses a per-voter worktree. The default Anthropic leg is Fable on the reviewer brief; Opus native is a coverage leg on review kind; its agreement with Fable is one vote. OpenAI and Grok stay on native methods. See `docs/review.md` (Targets for why the Fable leg is not native `/code-review`). If kind is inconclusive, script dies ask-human (`--kind` / brief header `kind:`) — no silent default, no classifier. `--dry-run` writes only under `tmp/council/<id>/preview/` and must not touch ballot routing/snapshot/packet/voters/judgment.
- **Code review evidence:** the default Anthropic leg receives the reviewer brief the script writes, and nothing from any other leg. Native review legs do not receive the brief or anything already suspected. For packet-first kinds, the chair runs probes and pastes results into the brief/snapshot so both voters stay packet-equal. Do not give Codex alone a freer lab mid-ballot unless you explicitly accept unequal inputs.
- **`kind: review`:** `scripts/conclave.sh` does the mechanical phases (lineup, worktrees, Fable on the reviewer brief, Opus native, the other legs' native methods, blinding, the Jev findings gate). Opus native is a coverage leg on review kind; its agreement with Fable is one vote. The chair writes `findings.json` from the blinded reports and drafts comments. Procedure: `docs/review.md`.
- **`kind: audit`:** reviews an existing artifact, or proposed edits to an existing doc, rule, or config. A new design or plan with no artifact yet stays `kind: design`. The script snapshots the artifact, gives each leg a worktree, and puts every leg on the same brief. Content and claims use the findings gate. A proposal uses `--verdicts`. Procedure: `docs/review.md` ("Non-diff targets: kind audit"). Audit is never inferred.
- **Design / planning:** keep packet-first; no voter roaming. A review of an existing doc or page is `kind: audit`, where legs have tools and web access.
- **Scratch:** `tmp/council/` is local ballot scratch (gitignored). Push/PR this practice repo only if you want backup or sharing.
- **Scrutiny checklist (each real cycle):** every `voter-*.ok`; fresh `judgment.md` Decision line; no Jev key in voter env; opinions look schema-complete; treat surprise `implement` with a human skim before coding.
