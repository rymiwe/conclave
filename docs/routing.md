# Routing (2026-09-30)

The routing this repo is moving to. Decided 2026-09-30. `scripts/` does
not implement all of it yet; `docs/review-kind-port.md` lists the gaps.
Open items at the end.

## Layers

Outer loop and inner loop are separate processes on separate pools. The
outer loop never holds the repo and never reads a diff.

| Layer | Who | Where | Pool |
|---|---|---|---|
| Control plane (outer loop) | Grok Bot | phone, car, Bot desktop app | Bot pool (Cursor tier) |
| Bridge | Bot desktop app local execution | the executor machine | none |
| Per-task chair (inner loop) | `cursor-agent`, newest Grok pinned, Fast variant at high (2026-10-02) | executor, one worktree per ticket | Cursor Models pool |
| Chair fallback | Opus on the Claude subscription when both Grok pools are over pace or unusable. Native Grok Build chairs when that pool is the one to spend (Ryan, 2026-10-08). | executor | the pool the lineup names |
| Voter A | Fable on the reviewer brief (`CLAUDE_CODE_SUBAGENT_MODEL=opus` stays set). Why not native `/code-review`: `docs/review.md` Targets. `--claude-model opus` turns this seat into Opus native and skips the coverage leg. When the Claude subscription's Fable allowance caps, `--claude-model cursor-fable` runs the same brief on Cursor Fable; `--fable-overflow cursor` switches only after a usage-limit failure on the Claude subscription. | executor, own login | Claude subscription; Cursor Other Models when on Cursor Fable |
| Coverage | Opus native, review kind only, own worktree. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. Design and mixed reject `opus` in `--voters`. | executor, own login | Claude subscription |
| Voter B | Astra on the resolver's Codex (newest stable `N.N.N` at or above 0.159.3, else newest `-alpha` at or above 0.154; `-beta` and `-rc` rejected) | executor, own login | ChatGPT subscription |
| Judge | Jev | executor | metered, cents |
| Builder | newest Grok, effort high, Fast variant (2026-10-02); xhigh when stuck | executor, one worktree per ticket | Cursor Models pool. Fast is the same model at 2x the standard token price (cursor.com/docs/models/grok-4-7, read 2026-10-02); the dashboard draw is not yet measured. |
| Builder fallback | Opus at low | executor | Claude subscription |
| Harness | Conclave script plus a pstack-style skill set in the repo | repo | none |

Roles are families. The resolver picks the newest ID at run time. No
model names in prose.

Exclusion follows what the ballot judges. Design excludes nobody. Review
and mixed exclude the vendor that built the diff. That author defaults to
the lineup's builder. A brief header `author:` overrides it. Audit excludes
the vendor that wrote the target when `author:` names the family, and
excludes nobody when the author is unknown. A plain question excludes
nobody. The chair's vendor still votes. When Claude chairs a review and
Grok built the diff, Fable votes and Grok sits out. Fable is required when
the excluded vendor is xAI. The chair exports `CONCLAVE_CHAIR_MODEL` so
`judgment.md` records it. Blinding
stays on but does not hide the vendor: a format check alone named it on 35
of 39 past blinded reports. Review kind adds an Opus native coverage leg on
the same vendor as Fable; its agreement with Fable is one vote. The Bot reads
`judgment.md` and PR links, approves splits, and triggers implementation.
Reviews are read on GitHub.

## Lineup (2026-10-08)

Ryan picked Astra's plan (router-picks-lineup, row 3) plus an advisory host
role. Jev returned no automatic decision (0.19).

`seat-usage --route lineup` returns one legal lineup, or `ask` with the
rejected pairs.

- With no `--kind`, chair and builder come from one vendor and that vendor
  does not vote. This is the control plane's pre-launch call. While either
  Grok pool is under pace, Grok chairs and builds, and the other vendors vote.
  Ordinary Grok work draws Cursor Models first. The Grok subscription is spent
  first only when it would otherwise expire unused.
- `--kind` is `design`, `review`, `mixed`, or `audit`. The chair is routed on
  its own and may differ from the builder. Design excludes nobody. Review and
  mixed exclude `--author`, or the builder when `--author` is omitted. Audit
  excludes `--author` when it is present. `--author` is a family. Fable is
  required when the excluded vendor is xAI. A builder family the caller already
  named is kept when that pool can take the work, including when the pool is
  ahead of pace, and dropped when the pool is capped.
- Opus chairs and builds when both Grok pools are over pace, or when both
  have nothing usable, on a call with no kind. The lineup names what is weaker.
  It asks only when nothing usable is left or a reading failed.
- Effort follows burn for the chair, the builder, and the voters. High by
  default, xhigh when a pool would otherwise expire unused, low when a pool
  is ahead of pace. Opus as builder stays at low unless that pool is burning.
- Voters are one leg per vendor, from the families the lineup seats:
  Fable, Astra, Grok, and Muse. A voter's vendor is the model's
  provider, not the seat it bills to. At most one vendor sits out, and only
  for the reason the kind names. A capped pool is not seated. A pool ahead of pace
  still votes, at low. An unknown reading asks. When
  Claude's 5-hour window is full, Fable votes through Cursor Other Models
  if that pool has room. One remaining voter is a weaker lineup, not an ask.
- Jev is not in this choice.
- `--route host` suggests Grok Bot, then Opus at high on the Claude subscription. It does not
  start a session (`selects` is false). The JSON row includes both hosts.
  Host candidates are those two. Effort follows burn.
- `--json` is a one-element list, same as the other routes.
- `--cached` on `lineup` or `host` reads the usage cache. It calls no vendor
  and writes no file.
- `scripts/conclave.sh` appends `~/.local/state/seat-usage/runs.jsonl`
  (`SEAT_USAGE_RUNS` overrides the path). One line at the start of a real
  ballot and one at exit, joined by `run_id`. The review line is a later
  call, because that part does not exist when the run ends.   The script calls
  `seat-usage --route lineup --kind KIND` for the default ballot. Review and
  mixed add `--author` only when the brief has `author:`. Audit does the same,
  and excludes nobody when that header is absent. Families map to legs: fable
  to claude, astra to codex, grok, muse. <!-- ballot-rule:start -->Review adds Opus native unless Anthropic built the diff.<!-- ballot-rule:end -->
  `--voters` still wins. When that call is missing, fails, or returns mode ask,
  the script uses the fixed default (Fable and Astra; review also adds Opus)
  and says so in the lineup and in `routing.md`. The same places name the
  excluded vendor and why when the router returns one.
  It does not stop the run. Ask mode calls `--route ask` and stays at two
  voters by headroom. The runs record still leaves lineup, host, usage,
  and chair reuse `unknown`
  unless the control plane passed them. Pool draw and builder rounds stay
  `unknown`. Tests pass a temporary path. Dry-run, `--findings`,
  `--verdicts`, and `--judge` do not write. When the Muse Wonder shadow
  leg is armed, the exit line records whether its verdict agreed with the
  Decision. The verdict text is not stored. The leg is off unless
  `CONCLAVE_MUSE_SHADOW=1` (`COUNCIL_MUSE_SHADOW` is the alias). It runs
  on design and mixed, packet-first and tool-less, and is not a voter.
  `--voters ...,muse` makes Muse a named voter instead (docs/review.md,
  "Muse"); the shadow then does not run.
  A missing `muse` binary, an auth failure, or an unavailable Wonder model
  skips it and does not fail the run. The lineup seats Muse unless a Muse
  window is capped. Ahead of pace still votes.
  Design and mixed also seat Argon (family `argon`, vendor Google) beside
  that lineup. The router does not. `CONCLAVE_ARGON=0` turns it off
  (`COUNCIL_ARGON` is the alias). `--voters` wins. Review and audit do not
  seat it. `CONCLAVE_AGY` is the `agy` binary. `CONCLAVE_ARGON_MODEL`
  overrides the model id. `CONCLAVE_ARGON_TIMEOUT` is the print timeout in
  seconds (default 900). The effort follows the resolved voter effort:
  low, medium, or high. `agy` must be signed in. Do not opt in to data
  sharing. Do not mark the repo trusted in `agy`. A missing binary, a
  failed `agy -p /usage`, an Argon row at 0% remaining, or a bad answer
  writes `voter-argon.skipped` and does not fail the run.
- Review launches stay as they are. The Fable brief, Opus native, the
  Grok review, and Muse pass high. Codex passes `CODEX_EFFORT`. Opus
  native never inherits the host's `effortLevel`. Audit passes high on every leg, including Codex, Muse, and the
  coverage voter. Design and mixed pass the resolved efforts. `ran` records
  those launch efforts, including Muse.

The Muse shadow launch passes `muse exec --json`. That flag is the CLI's
JSONL event stream, and it is the least invasive place to read the
`subscription_usage` event Meta returns on the `POST /v1/responses` SSE
stream (`window.used_percent`, `window.window_duration_mins`,
`window.resets_at`, `weekly.used_percent`, `weekly.resets_at`). Answer
text from `muse exec` leaves that event inside the client.
`scripts/muse-usage.py` reads the shadow stdout and stderr after `muse`
exits. When the snapshot is present it writes
`~/.cache/quartermaster/muse-usage.json` (`MUSE_USAGE_PATH` overrides the
path) with those fields and a UTC `captured_at`. The write creates a temp
file in that directory and renames it over the destination. The verdict
file is the joined `run.output.delta` text, or the last `run.terminal.*`
text when the deltas are empty. Stdout that already contains a `## `
heading stays the verdict. A `run.model.configured` model id other than
the resolver pin skips the leg. Published `--json` journals (muse-codes,
through the `run.output.delta` fingerprint) contain no subscription
event, so a live run updates the cache only when the event is in that
stream. The helper does not proxy the API or call `muse serve`. A missing
event leaves the cache file as it was. A parse or write error is ignored,
and the council result stays on the existing skip and normalize path.

The PreToolUse hook still prints the per-role routes. It does not yet check
that a chosen lineup is still legal.

## Kinds

One script. `kind` decides what voters see and what they return.

| kind | Voters see | Output | Jev |
|---|---|---|---|
| review (a diff) | own worktree each; Fable on the reviewer brief, Opus native as a coverage leg, OpenAI and Grok on their native methods. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. | ranked findings drafted as PR comments | post / hold / drop per finding |
| design, planning | packet only, no tools, out-of-repo cwd | Plan F and Plan A: lean, tradeoffs, risks, accretiveness, cost to try | implement / with changes / no automatic decision, plus confidence |
| mixed | packet plus diff snapshot | both | both |
| audit (no diff: a page, a doc, or a proposal) | one snapshot repo; own worktree each; every leg on the same brief | content and claims: findings. proposal: one verdict per change | findings: post or second look. proposal: adopt, adopt with changes, or reject. The decision is always human review |

`kind: audit` reviews an existing artifact, or proposed edits to an existing doc, rule, or config. A new design or plan with no artifact yet stays `kind: design`.

Ask mode is not a kind: one prompt to each named voter, no ballot (`docs/review.md`, "Ask mode"). It calls `seat-usage --route ask` for a plain question and records the effort each leg ran. Jev picks that effort's lean. It does not rank the answers.

A PR stack is one review ballot per PR, bottom up, then one reconciliation
pass at the stack top: `scripts/stack-run.py` (`docs/review.md`, "Stack
review"). Each ballot gets its own chair, so no chair carries the whole stack.
The runner starts the Claude seat on Opus when `seat-usage` shows the weekly
Fable slice at or above 95%. After a Fable leg on the Claude subscription hits a usage limit, later
ballots in that stack stay on Cursor Fable.

`multi-review` becomes `kind: review` of the Conclave script. Blinding,
the resolver, the Grok-died-mid-task check, and the PR-comment gate move
with it. Cursor Auto and T3 come out of the Conclave repo.

## Divergence

- No kind builds without the human today. Review kind returns post /
  hold / drop per finding and drafted PR comments, which post only when
  the human says so. Design kind has no calibration yet: every design
  Decision goes to the human form until three have been compared with
  the human's own calls. The 0.7 figure is the post threshold for a
  read-only review finding, not a build threshold.
- `judgment.md` opens with a fixed header (kind, decision, confidence,
  confidence_of, model ID per role, branch, PR link; `none` where a field
  does not apply). When Argon voted, the header adds `argon_model`. When
  that leg was named and skipped, it adds `argon_skipped` with the one-line
  reason. `confidence` is Jev's confidence in its own choice.
  `confidence_of` names that choice. Design and mixed use `voters propose
  the same plan`, `voters share a plan with minor changes`, or `voters
  propose different plans`. Review findings, and an audit findings gate,
  use `lowest Jev confidence among findings gated to post`, or `none` when
  nothing posts. An audit proposal uses `none`. The Bot acts on the header
  only.
- Jev `no automatic decision`: the script writes `options.md` beside
  `judgment.md` with the same header and an empty form: option, each
  voter's lean, tradeoffs, risks, accretiveness, cost to try, a cheap
  flag, and a pick column. Jev returns one decision and no options list,
  so the chair fills one row per option from the opinions. The Bot shows
  it as the form. The human picks a row from the Bot.
- Both rows when the cost column says both are cheap: two worktrees, then
  a review-kind Conclave on the two diffs. Starting definition of cheap:
  under about 200 lines and one day; adjust after three.
- The chair fills the rows and gives no lean of its own, in the form or
  in chat. If the human wants a tiebreak, the chair gives the plans
  unlabeled, in random order, to a vendor that neither voted nor chairs,
  and reports its pick beside the voters' leans. On that run
  (Grok chairs, Fable and Astra vote) no third vendor was seated, and the form
  goes out with the voters' leans only. Why: on routing-optimizer-design
  (2026-10-03) a Claude chair leaned with Fable, and Astra kept its own
  plan when it saw both unlabeled. Grok, the third vendor, gave the read
  the human acted on.
- Design samples toward the calibration of three:
  1. routing-optimizer-design, 2026-10-03. Jev: no automatic decision
     (implement with changes 0.79, minor-only 0.51 under 0.7). The human
     picked the smaller plan (Fable's) with three changes taken from the
     Astra and Grok cross-checks: read the pool live before routing,
     drop thresholds the plan invented, and make the router the only
     home of the routing rules.
  2. router-picks-lineup, 2026-10-08. Jev: no automatic decision (0.19,
     voters proposed different plans). The human picked Astra's plan: the
     rule-based router selects a complete legal lineup, asked once per task
     by the control plane, with voter effort fixed and an outcome record per
     run. Added: an advisory `host` role for the status display. Later the same day,
     effort follows burn, voters included. Fable's pick was a
     pace-ranked legal set asked by the chair.

Blinding stays on and invisible. It costs one script.

## Pools and spend

Headroom is the metric. No API spend and no usage credits without a
deliberate decision. The control plane runs `seat-usage --route lineup`
once per task, before it launches a chair. That call reads the pools live.
`--route lineup --cached` and `--route host --cached` read the usage cache
for a caller that must not hit a vendor. The per-role rules
(`bounded-question`, `hard-bug`, `builder`, `chair`) stay the only home of
pace for one launch (2026-10-03, replacing "do not route per decision").
The session-start line
stays the overview. Voter legs get `CONCLAVE_VOTER=1` and see no pace text.
The fallback ladder in CLAUDE.md
stays as written and is not extended until a pool caps.

Tiers are not fixed here. Each user picks a tier per subscription, and
`seat-usage` reads the live plan. Raise the tier of whichever pool caps
before its reset in a week where the cap blocked work. Metered Fable on
Cursor (about $5.55 a review, measured) is an overflow, not a replacement
for a Claude plan that includes Fable: it has no ZDR and no native review
method.

xAI capacity is Cursor Models plus the Grok subscription, spent by pace. The
Grok subscription is spent first only when it would otherwise expire unused.
Grok votes unless the ballot excludes xAI, and not on a capped pool.
Review and mixed exclude xAI when Grok built the diff. Audit excludes xAI
when `author:` names that family. Design does not.

Bot VM logins (grok, cursor-agent, claude, cursor) stay for personal
projects and never touch work code; every Bot shares that disk.

## Machines

- One executor runs the Bot desktop app with local execution. Two apps
  open on two machines flap (Cursor forum, 2026-08). The other machine is
  a client.
- One machine is the executor. Every other machine, the phone, and the car
  are clients.
- No production data in dev or test. A cloud VM (Cloud Agent, Origin) earns
  its place only when a job cannot run as a worktree on the executor, the
  VM bills a flat pool, and the org accepts a standing copy of the code
  off GitHub. Origin makes that copy permanent rather than per run, so it
  does not simplify approval.
- Bot local execution is a standing inbound channel from Cursor's cloud
  into the executor. Per-command approval stays on until the security
  check clears. The Bot reads results, not source.
- Claude Code Remote Control (device card, new sessions from the phone
  since 2026-09-28) is the stand-in interface when the Bot is down.

## Harness

- Start near zero: the Conclave skill and script, and a pstack-style set
  of repo-level skills in Cursor format. No memory. Grok Build reads
  Claude and Cursor surfaces through its loader (verified with `grok
  inspect --json` on 2026-09-30: CLAUDE.md, rules, 7 hooks, 3 MCP
  servers, 166 skills, 12 plugins, 9 agents). Codex keeps adapted
  copies. `cursor-agent` reads the repo-level Cursor format directly.
- CLIs are legs, never the top-level interface. They are the only place
  the flat subscriptions can be spent.
- Two homes: CLAUDE.md holds the routing table as one dated block
  replacing the 2026-09-16 host and 2026-09-28 worker paragraphs; the
  Conclave repo holds the procedure and script.

## Open items

1. **Executor machine**: decided 2026-09-30.
2. **Security check** on Bot local execution and on Grok Build's formal
   approval. Considered safe for now per Ryan; not confirmed.
3. **Bot pool under the thin-Bot design**: one measured week, then read
   the Bot usage bar. No tier change before that.
4. **Builders unmeasured**: record the first tickets on the newest Grok and on
   Opus low (turns, wall time, pool delta).
   Parallel builders for a handoff with independent items (one builder per
   item, each in its own worktree, launched together; items that touch the
   same function share a builder; the chair merges before review): not yet
   tried. On the first case, record wall time against the sequential
   estimate, merge conflicts, and the Cursor Models delta. Promote it to the
   Daily work table after two cases that save time.
5. **Executor setup**: `docs/executor-setup.md`. Chezmoi bootstrap covers
   the Claude harness.
6. **Subscription tokens in automation.** Whether `claude setup-token` and
   `codex login` tokens may run inside a vendor VM under the Claude and
   ChatGPT subscription terms is unchecked. Decide before any "logins in cloud"
   step; flag, do not assume.
7. **Grok Cloud Agent billing**: one small run on a personal repo, usage
   line before and after, to see whether it draws the Cursor Models pool or
   is metered.
