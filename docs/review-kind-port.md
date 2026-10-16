# Review-kind port

Goal: make `kind: review` in `scripts/conclave.sh` run the review procedure
that today lives in the `multi-review` skill (`~/.claude/skills/multi-review/`
in the dotfiles repo, about 1,700 lines plus `blind.sh`, `resolve-models.sh`,
`reviewer-persona.md`, and `~/.local/bin/jev-ask`). When this lands, that
skill is deleted and the "until the port lands" sentence in CLAUDE.md goes
with it. See `docs/routing.md` for the decisions this implements.

## Carry over, on purpose

1. **Per-voter worktrees** from a synthesized base (`reset --soft`), one
   per voter, under the scratchpad, never inside the target worktree.
2. **Native methods.** Anthropic: native `/code-review` with a bare target
   and no model words in it (the skill has no model flag; pin the model on
   the CLI), whole-codebase mode is `/code-review high` with no target.
   OpenAI: `codex review --uncommitted` with house rules through
   `-c developer_instructions=...`. Grok, opt-in: `/review --local`, report
   collected from disk, and the exit-0-but-died check with a one-line
   resume.
   On 2026-09-30 the Anthropic leg moved to Fable on the brief. See `docs/review.md` Targets.
3. **Read-only proof.** HEAD and `status --porcelain` before and after each
   voter, compared.
4. **Blinding.** `blind.sh`: shuffle reports, redact vendor and model names
   and leg paths, write `key.txt`, and the chair reads the key only after
   the convergence table exists.
5. **Jev per finding.** One `choice` question per finding: post, hold,
   drop, with confidence. An executed repro posts only on Jev post at 0.7 or
   above, and otherwise goes to second look (2026-10-01); a read-only
   finding posts only at 0.7 or above; a drop sends it back for a second
   look; disagreements go to the human with both views. Carry the
   calibration notes (four runs so far) and do not move the threshold
   without data.
6. **Consolidation.** The convergence table that separates disagreement
   from coverage gaps; ranking by blast radius, verification tier, and fix
   cost; no scores.
7. **Output.** Drafted PR comments, inline and review-level, with the
   anchoring rules and the post-only-what-is-unambiguous gate. Post only
   when asked. Every ticket, SHA, and file citation in a comment is a
   markdown link.
8. **Non-diff targets** (docs, live pages): all voters on the brief, Jev as
   the verdict leg.
9. **Lineup table before spending**: vendor, verifiable model ID,
   methodology (native or brief), pool and pace. A capped pool moves a leg
   to the next seat; ahead of pace does not drop Fable.
10. **The resolver** moves into `scripts/` and serves every role, and
    `judgment.md` opens with a fixed header: decision, confidence, the
    resolved model ID per role (chair, voter A, voter B, builder), branch,
    PR link. The Bot acts on that header only. If an ID changes between
    two runs being compared, say so in the comparison.

## Drop, on purpose

- Grok as a default voter, and the Cursor-Grok fallback for it.
- Opus native when Fable is ahead of pace. Opus native only when the
  Fable slice is capped; Cursor Fable only when the Claude subscription is session-limited.
  Superseded 2026-10-01: Opus native is a coverage leg on review kind; its
  agreement with Fable is one vote.
- CE and structural passes: keep as flags if cheap, else drop.
- Batch mode over a Linear board: drop for now.

## Shape of the change

- `conclave.sh`: a `review` branch in the voter runners that creates the
  worktree, runs the native method, and collects the report; the existing
  packet path stays for design and mixed.
- `jev-judge.py`: a findings mode beside the Decision mode, or call
  `jev-ask`. The key stays in the Jev child process only. The script reads
  the key from the 1Password reference in `CONCLAVE_JEV_OP_REF`.
- `blind.sh`, `resolve-models.sh`, `reviewer-persona.md` move here.
- `AGENTS.md` and both skill copies: remove the "pending port" note.

## Test plan

1. A toy repo with two planted bugs (the skill verified Opus at low finds
   both on a one-file repo, 2026-09-30). Run `--kind review`, confirm two
   blinded reports, a Jev-gated table, and drafted comments.
2. One real PR, with the human's go, compared against a prior multi-review
   run's findings on the same PR. Record the numbers: findings per run,
   overlap, and which findings each voter caught alone. That is the data
   for the 0.7 threshold and for "is the Conclave better than one Fable
   session".
3. Then delete the skill from dotfiles and its chezmoi entry, and remove
   the CLAUDE.md sentence. Not yet (2026-09-30): steps 1 and 2 passed, but
   non-diff targets are not in the script, and the 2026-09-30 review of
   the routing docs used the skill for exactly that. Deletion waits for a
   follow-up that scripts non-diff targets (all voters on the brief, Jev
   as the verdict leg).
   2026-10-01: non-diff targets are now `kind: audit`. See `docs/review.md`
   ("Non-diff targets: kind audit").

## How to run this task

Chair it; do not hand-write the script changes in the chair session. The
builder is the newest Grok through `cursor-agent` in its own worktree of
this repo, with this file as the brief and the honesty gate attached. The
chair verifies the diff and runs the test plan. Review the port with the
old skill one last time before deleting it.

## Gotchas, all learned the hard way

- Never native `/code-review` under Fable without the Opus env var:
  Fable subagents hit the session limit twice at about $20 a run.
- `claude -p --disallowedTools` swallows a positional prompt; pipe the
  brief on stdin.
- `codex exec` hangs without `< /dev/null`; its sandbox has no network.
- `cursor-agent` prints only the final message with `--output-format
  text`; the brief must end with the report.
- A Grok leg that exits 0 may have died mid-task; check `events.jsonl` for
  `turn_ended`.
- One background Bash call per leg, each with the maximum timeout; one
  call that waits on all legs dies with the slowest.
