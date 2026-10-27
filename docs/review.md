# Review kind

Procedure for `kind: review`. The script does the mechanical phases. The chair
does the judgment. Family names (Fable, Astra, Opus, Grok, Jev) are roles.
Model IDs come from `scripts/resolve-models.sh` at run time.

Design and mixed stay packet-first. Their ballot is every voter named in
`--voters`, at least two, each with a valid opinion. Jev judges that ballot.
A `no automatic decision` also writes `options.md` beside `judgment.md`.

`--attach FILE` is repeatable, and an `attach:` header holds a comma- or
space-separated list. The script unions the two, drops duplicates, and
accepts only a regular file inside the target repo or the Conclave clone. A
relative path is resolved against the target repo root first. If no file
exists there, it is resolved against the directory the script was started
from. A missing file, or a symlink that resolves outside those roots, is refused.
Each file is copied into the packet beside `brief.md` under its path relative
to the root that contains it, and listed with its full contents under
"Attached files" in `PROMPT.md`. Every voter gets that same packet. Tool-less
voters have no other way to read the file. `kind: review` rejects `--attach`
and `attach:` because each leg already has the repo. A dry run writes the
copies and `PROMPT.md` under `preview/` only.

## Phases

| Phase | Who | What |
|---|---|---|
| 0 | `scripts/conclave.sh <id> --dry-run` | Lineup, target, diff shape. Writes only `tmp/council/<id>/preview/`. Local listings only: no voter prompt, no Jev call. |
| 1 | `scripts/conclave.sh <id>` | One worktree per voter, read-only baseline. The default ballot is the lineup. <!-- ballot-rule:start -->Review seats two voters from seat-usage --route lineup --kind review, on two vendor families, excluding a named author or a caller-passed builder or --builder-vendor and nobody when none of those is set, and falls back to Fable and Astra when that route is missing, fails, or asks.<!-- ballot-rule:end --> Fable is on the reviewer brief. The other seated legs run their own methods, in parallel; Grok is on `cursor-agent` unless `CONCLAVE_GROK_NATIVE=1`. Opus native is a coverage leg on review kind; its agreement with Fable is one vote. Then model check, baseline compare, `scripts/blind.sh`, stop. |
| 2 | Chair | Read only `blind/leg-*.md`. Write `findings.json`. Do not read `blind/key.txt` yet. |
| 3 | `scripts/conclave.sh <id> --findings` | One Jev `choice` per finding, or no call when there are no findings. Writes `judgment.md` (fixed header, then the convergence table) and `decide.md` beside it. `convergence.md` is the same table without the header. |
| 4 | Chair | Fill `decide.md`: replace every `TODO(chair)`. The what-breaks line says what breaks and for whom, with no code identifiers. Then draft comments. When the pull request has an automated risk rating, the summary states it. Above low, the summary names what drives it and suggests concrete changes that would bring it to low. Save a throwaway repro script as `repro/<finding-id>.<ext>` in the ballot directory. `scripts/check-comment-links.py` checks citations. `scripts/check-comment-labels.py` checks labels on a post payload, and fails the draft when a detected rating is missing from the summary. The script writes no comment prose and posts nothing. |

Phase 0 prints pace from `~/.local/bin/seat-usage` when that file is executable
(`CONCLAVE_SEAT_USAGE` overrides the path). Otherwise the pace cell is
`pace unknown`. The script does not move a leg because of pace. Ahead of pace
is not a reason to drop Fable (2026-09-30). A capped Fable slice is the chair's
decision to pass `--claude-model opus`. Fable itself always runs on
`cursor-agent` with the resolver's Cursor Fable id. `--claude-model cursor-fable`
is that same leg. `--fable-overflow cursor` is accepted on review and does not
start a Claude subscription leg. See "Fable on Cursor" under "Stack review".
The script never chooses any of
these from pace. The stack runner does, from `seat-usage`.

When the Conclave reviews its own repo, run `conclave.sh` from a pinned
detached worktree (`git worktree add --detach <path> <head>`), so the script
cannot change under the run. The ballot then lands under that worktree's
`tmp/council/<id>/`, because the script writes ballots under its own repo.
Copy the ballot to the Conclave clone before removing the worktree.

## Lineup

One row per voter that will actually run, plus a Jev row for the findings
judge. Columns: vendor, model id or launch alias, CLI and version, methodology
(`native` or `brief`), pool, pace.

The default ballot is the lineup: every available leg. Design excludes
nobody. Review and mixed exclude a named author, or on review the vendor a
commit trailer names. With neither, and for `--author human`, they exclude
nobody. A pace-picked builder is not excluded. Audit excludes that vendor
when `author:` names the family, and excludes nobody when the author is
unknown. The chair's vendor still votes. The lineup names who sat out and
why. Kind review takes exactly two voters from `seat-usage --route lineup --kind review`.
The two are different vendor families. They omit an excluded vendor when
the router names one. `--builder-vendor` comes from the brief `author:`
family, or from commit trailers that name exactly one vendor. A user author with
no model trailer passes `--author human` and no `--builder-vendor`.
An explicit `--voters` list replaces the route. `fable` and `astra` in that list
mean the claude and codex legs. Argon runs when the route or the list names it.
A capped pool is not seated. A pool ahead of pace still votes.
When seat-usage is missing, fails, or returns mode ask, review uses Fable and Astra
and writes a warning. Other kinds use that same pair. The lineup says so.
Grok runs on `cursor-agent`; `CONCLAVE_GROK_NATIVE=1` runs the native CLI, with no fallback between the two. Opus native is a coverage
leg on review kind; its agreement with Fable is one vote. `--claude-model opus`
(or the brief `claude:` header) turns the Fable seat into Opus native and
skips the coverage leg, so Opus native runs once. The lineup then says
`opus leg skipped: the claude leg is Opus native`. The script never chooses
that override from pace. Design and mixed use the same lineup, reject
`opus` in `--voters`, and still need at least two legs.

The Anthropic cell is the launch alias. The id is proven after the run from
`modelUsage` in the JSON. For the default brief launch, the proof is a Fable
key and no key outside Fable, Opus, and Haiku. An Opus key is allowed and
not required. The Opus coverage leg, and the Opus override on the `claude`
seat, each require an Opus key, no Fable key, and nothing outside Opus and
Haiku.
Why the default is not native `/code-review`: Targets, below. Anything else
means the column is not what it claims (2026-08-10: a run that passed model
words in the target made 34 requests, all one Claude model, and looked like
a normal review).

OpenAI uses the resolver's Codex binary. Candidates are every `codex` on
PATH and every npx-cached binary under `~/.npm/_npx`. The version is the
token after `codex-cli ` on the `--version` line; other stdout lines are
ignored. Stable means exactly `N.N.N`. Alpha means `N.N.N-alpha...`. A
`-beta` or `-rc` build is rejected. Accept a stable version at or above
0.159.3 (the first stable verified to run Astra, on the executor, 2026-10-01).
Accept an `-alpha` at or above 0.154 as the fallback. Reject anything else,
including stable 0.154.0 (it rejects Astra). Pick the newest accepted stable;
only if none, the newest accepted alpha. `npx -y
@openai/codex@alpha` is not a resolver: on 2026-09-08 a registry cutoff
installed 0.152.0 while the run reported itself as alpha, and the server
rejected Astra with a message that looked like a missing model. The banner
is the evidence.

Cursor Fable and the Cursor chair/builder id are printed under the table.
The Cursor Fable line says `selected`, `overflow armed`, or `not selected`. Add one Cursor sentence when you show the lineup to the human:
whether any leg would draw on Cursor, which bar (Cursor Models for Grok, Other
Models for Fable), and why. On-demand spend is mostly Bot overage and is never
a reason to skip a Cursor leg (Ryan, 2026-09-28). Cursor reports no token usage
(checked 2026-09-15).

`CONCLAVE_CODEX_BIN` overrides discovery. Leave it unset on a real run.

## Targets

The target repo is `--repo` (default: the directory the script was started
from, captured before it changes directory).

| Mode | Flags | Base |
|---|---|---|
| Pull request | `--pr N`, or brief header `pr:` (N, `#N`, or the PR URL) | Real base from `gh pr view` (`baseRefName`). The head is `headRefOid`, fetched from `pull/N/head` and checked against that oid. The base commit is the `merge-base` of `origin/<base>` and that head oid. Not the repo's default branch. Stacked PRs are common; reviewing against the wrong base fills the diff with the parent (preflight, and the same reason batch mode derived the base from `baseRefName`). |
| Ref pair | `--base REF --head REF` | `merge-base` of those refs. `--head` defaults to `HEAD`. |
| Whole codebase | `--whole` | Synthesized empty tree: `commit-tree` of the empty tree, then `reset --soft` in each worktree, with signing off so a headless worktree does not hang. `git diff HEAD` is the tracked tree as additions. |

A run with no target stops (ask-human). The whole codebase is never a
fallback: on 2026-10-13 a work pull request ran with no `--pr`, the script ignored
the brief's `pr:` header, and every leg got the whole tree against an empty tree.
The `pr:` header now targets like `--pr`, and selects `kind: review` when no kind is set. A `pr:` URL may carry a tab suffix (`/files`, `/commits`, `.diff`), `www.`, or any scheme case; the repo check compares host, owner, repo, and number. A `--pr`, `--base`, or `--whole`
that disagrees with it stops the run. A `branch:` header must equal the PR's
head branch, and a `pr:` URL must be the PR `gh` finds in `--repo`. A PR or
ref-pair diff over 300 files stops too; set `CONCLAVE_REVIEW_MAX_FILES` to go on.

Phase 1 proves every worktree's `write-tree` equals the head tree, then checks
the diff shape. For a PR the insertion and deletion counts must match
`gh pr view` additions and deletions. A mismatch is the wrong base; the script
fetches once more and rebuilds, then stops if they still differ. For a whole
codebase, deletions must be zero. State the shape in the lineup as
`N files, +X/-Y` and name the base. Never invent a PR number for a synthesized
base (2026-09-20, a service repo: 9 files, 3,990 lines, base the empty tree).

The whole-codebase path is what a repo with no remote uses. On a whole
codebase the Opus native command is `/code-review high` with no target, from
inside the worktree. Verified 2026-09-30 with Opus at low on a one-file repo
with two planted bugs: both found. The Fable-parent shape runs the same
command and was not yet verified on a whole codebase when this was written.
On 2026-09-30 a live whole-codebase phase 1 used that launch (Fable alias,
the subagent variable set, `/code-review high`, no target). The JSON had
`local_command` `code_review`, `num_turns` 0, `subagent_stats.spawned` 0,
and a `modelUsage` with exactly one key, an Opus ID. No Fable key and no
Haiku key. The model check failed. That is the right verdict: the column is
not what the lineup said. The same shape was observed on this real
whole-codebase target too. The FAIL line and the `judgment.md` header carry
the IDs that ran, followed by `(lineup said fable)`.

A PR-number target does the same. On 2026-09-30, `/code-review 2 high` on
[`rymiwe/conclave-archive` PR 2](https://github.com/rymiwe/conclave-archive/pull/2) (15 files, +3199/-228), launched with the Fable alias
and `CLAUDE_CODE_SUBAGENT_MODEL=opus`, returned `local_command` `code_review`,
`num_turns` 0, `subagent_stats.spawned` 0, and one `modelUsage` key,
`claude-opus-5-5` (18,352 output tokens, 1.11M cache-read, 1.29 dollars,
190 s). So native `/code-review` never runs Fable, for any target: it runs as
a built-in command on Opus whatever `--model` says.

The default Anthropic leg is Fable on the reviewer brief. Opus native runs
beside it as a coverage leg, and `--claude-model opus` turns the Fable seat
into Opus native: that launch asks for Opus and gets Opus.

### Whole-codebase brief extras

Native legs do not receive the brief. When a leg is on the brief (a revision,
a non-diff target), say what the thing is for in domain language and name the
README as the specification. A README-versus-code mismatch is a finding in
either direction; one service repo had two (2026-09-20). Name settled architectural
decisions or the findings come back as design opinions. If there is no test
suite, say "add tests is not a finding".

## Worktrees, isolation, and the read-only baseline

Worktrees and per-leg `TMPDIR`s live under one `mktemp` directory outside the
target repo and outside this repo. After they are built, that directory is
mode `311` (enter and create, not list). A leg's cwd is its own worktree. It
cannot list another leg's directory by listing parents. Transcripts and
reports go under `tmp/council/<id>/`, which is not a parent or sibling of any
worktree. Verified 2026-09-12: a Cursor Fable leg listed a sibling
`FINDINGS.md` while still running, because the output directory was beside the
worktrees.

Grok's `/review` writes under `$TMPDIR/grok-$USER` (the username, not the
uid). Two legs on the default temp directory share that folder and can read
each other (2026-08-14, on a work pull request: the native leg found the structural leg's file
from three minutes earlier). Each Grok leg gets its own `TMPDIR`. Do not grep
the old uid path.

Capture the target repo's read-only baseline before building worktrees, and
compare it after the voters finish. The checkout check is strict. It covers
`HEAD`, `status --porcelain=v1 -z --ignored`, `diff HEAD --binary`, sha256
of every untracked non-ignored file in the work tree (not only the directory
the script was started from), and sha256 of ignored regular files. An ignored
dangling symlink is skipped. An untracked dangling symlink still fails the
hash. A mismatch is a failure. Branch and
tag refs are recorded separately. A ref that was created, deleted, or moved
is a failure only when a leg created that commit after setup: a post-setup
`HEAD` reflog entry whose subject starts with commit, merge, cherry-pick,
rebase, revert, or am (a checkout does not count), and an annotated tag is
attributed on the commit it peels to. The check records a ref move it cannot
tie to a leg
(`ref moved, not attributed to a leg`), and that is not a failure. A leg
that moves or deletes a branch without moving its own HEAD (`git branch -f`,
`git branch -D`, `git update-ref`) is recorded, not failed. The same move is
also a `note:` line in `errors.md`, and note lines are not failures. Remote-tracking
refs are left out because `git fetch`
moves them. The leg worktrees are supposed to be dirty (`reset --soft` put
the diff there). Do not treat that dirt as a finding. The script writes
`readonly.txt` and removes the worktrees unless `--keep-worktrees`.
If the hash cannot be computed (for example, an untracked dangling
symlink), the run stops before any leg launches and names the path. If
the after-hash fails, `readonly.txt` says `result: error (hash incomplete)`
with the reason, `errors.md` gets a line, and the model check and blinding
still run.

`git worktree add` brings tracked files only. If the main worktree has
`config/database.yml` or `.env` and git ignores that path, the script copies
it into each leg (a copy, not a symlink, so a leg cannot write through into
the main checkout). Without it, reviewers stop executing and reason about SQL
instead, and they do not announce the failure (2026-09-08). Confirm the
diffstat still matches after the copy.

The read-only hash covers the target checkout only. It cannot see a leg that
restarts a database. On 2026-10-13 one Fable leg started its own Postgres on
5434 and another restarted a project's database container. The reviewer brief now
says never to start, stop, restart, or reconfigure a database server,
container, or other shared service. The chair checks the database before
any leg launches. It reads host and port from `config/database.yml` and runs
`pg_isready`. Conclave never starts, stops, or restarts a container. When the
database is down, the run stops with a message that names the host and port
and the container from `database.name` in `config/setup.yml`. You start it,
then rerun. No leg launches. With no `database.yml` or no `pg_isready`, the
check is skipped. Test: `scripts/test-db-check.sh`. It also fails if any
non-test script under `scripts/` runs `docker start`, `stop`, or `restart`.

Gems and node are the other way a worktree stops running code. When the
branch's `Gemfile` matches the main checkout but `Gemfile.lock` is newer,
`bundle install` can stop on a private source, and `.tool-versions` can pin a
node the machine lacks (2026-09-16, a work pull request: two of three legs said
Rails would not boot). Export `BUNDLE_GEMFILE` and `ASDF_NODEJS_VERSION` into
the environment before the run instead of touching the worktree lockfile. The
script does not set those; they are project-specific.

## Native commands

Fire the listed voters in parallel, one process each. One wait that is bound
to the slowest leg dies with it (2026-09-29: a single call hit a one-hour
limit while Grok compacted and never ended a turn). The script waits each
process on its own.

### Anthropic

Default is Fable on the reviewer brief, from the leg worktree. The brief is
`tmp/council/<id>/voter-claude.brief.md` (the dry run writes a copy under
`preview/`). It goes on stdin. Why this is not native `/code-review`: Targets,
above.

```
env CLAUDE_CODE_SUBAGENT_MODEL=opus \
  claude -p --model fable --effort high --permission-mode bypassPermissions \
  --output-format json < <reviewer brief file>
```

The subagent variable stays set on that command. There is no native
`/code-review` launch under Fable. Fable subagents re-read the diff:
One work pull request (2026-09-16) used 7.09M Fable cache-read tokens, 17.63 of 20.18
dollars, and hit the session limit with no result. Another (2026-09-14)
cost 20.57 with no output.

The Opus coverage leg does not run that command. It runs the native
`/code-review` launch shown below, in its own worktree. Opus native
is a coverage leg on review kind; its agreement with Fable is one vote.
Outputs are `voter-opus.json`, `voter-opus.err`, `voter-opus.fail`, and
`voter-opus.model.txt`. The launch is the function the `claude` seat already
uses when `CLAUDE_SKU=opus`; it is not a second copy of the command.

`--claude-model opus` (or the brief `claude:` header) turns the `claude` leg
into this same launch. The coverage leg is then skipped. The lineup says
`opus leg skipped: the claude leg is Opus native`.

`--model opus`, the subagent variable unset, read-only tools, and the slash
command as an argument:

```
env -u CLAUDE_CODE_SUBAGENT_MODEL \
  claude -p --model opus --effort high --permission-mode dontAsk \
  --strict-mcp-config --tools Read,Grep,Glob --allowedTools Read,Grep,Glob \
  --disallowedTools Task,Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch \
  --add-dir <diff dir> --append-system-prompt "<the diff is in <diff dir>/changes.diff>" \
  --output-format json \
  "/code-review <bare target> high"
```

With no shell the leg cannot run `git diff` or `gh`, so the script writes the
worktree's staged diff to `changes.diff` in a temp dir outside the worktree,
lets the leg read that dir, and removes it after the run. The dir name has no
model word, because the report may quote the path. Under
`CONCLAVE_VOTER_SANDBOX=test-db` the leg also gets Bash (still no Task, Edit,
Write, or web) and the test-database rule in the appended system prompt. On
2026-10-26 a default run on a one-line staged bug returned its `.result`
report and `opus: ok` model proof.

Whole codebase and ref-pair: `/code-review high` with no target. A PR passes
the number only. The prompt has an explicit effort word and no model words.
Pin the model on the CLI. `/code-review` has no model flag; a model word in
the target is parsed as part of the target (2026-08-10). Do not swap in Opus
to avoid a retention label. Cursor's NO ZDR label on Fable is Anthropic's
covered-model retention, the same window as on the Claude subscription, not a Cursor-only policy.

Use `--output-format json` and read `.result` for the review text. `modelUsage`
is the proof and must not reach the blinded copy; `blind.sh` extracts `.result`.

A "requires usage credits" refusal is a plan fact: the current Claude plan does
not include Fable. `~/.claude/.credentials.json` goes stale: on 2026-09-08 it
still named the previous plan after the account had changed plans. Read `/usage`. A `Current week (Fable)` line means the plan covers Fable.
Do not enable usage credits to work around a refusal.

`claude -p` with a tool-disabling flag swallows a positional prompt. The
design path already pipes the brief on stdin. The default review leg does
too. Opus native passes the slash command as an argument, which is the
shape that works for `/code-review`.

If the Claude subscription is session-limited, pass `--claude-model cursor-fable`. The script
runs the same reviewer brief through `cursor-agent` in the leg's own worktree
(`--workspace`, and `-f` or the run stops on an untrusted-directory prompt and
exits 0). The column reads `<id> (via cursor)`. See "Fable on Cursor" under
"Stack review".

### OpenAI

```
"$CODEX" -C <worktree> review --uncommitted \
  -c model="$CODEX_REVIEW_MODEL" \
  -c model_reasoning_effort=$CODEX_EFFORT \
  -c sandbox_mode=read-only \
  -c sandbox_workspace_write.network_access=false \
  -c developer_instructions="$ASTRA_RULES" \
  --title "<bare target>" \
  < /dev/null
```

Review kind sets `CODEX_EFFORT` to `high`. An explicit brief `effort:` header overrides that. Difficulty, explicit or inferred, does not change the OpenAI leg on review kind. The lineup `effort:` line gives that OpenAI value, then says Anthropic and Grok stay high.

`-C` is a top-level option on the resolver's binary (`codex --help`).
`codex review --help` does not list it. Passing it after `review` is rejected.

### Voter sandbox

`CONCLAVE_VOTER_SANDBOX` (`COUNCIL_VOTER_SANDBOX` is the alias) sets what
review and audit voters may touch. Voters may touch only the test database,
inside a transaction that is rolled back. Nobody starts, stops, or restarts a
container.

- `read-only` is the default, when the variable is unset or empty. The Codex
  review leg gets `-c sandbox_mode=read-only` and
  `-c sandbox_workspace_write.network_access=false`. The cursor-agent voter
  legs (Fable review, Grok review, Grok audit) get `--sandbox enabled`. The
  Opus native coverage leg gets `--permission-mode dontAsk`,
  `--strict-mcp-config`, and only Read, Grep, and Glob (Task, Bash, Edit,
  Write, NotebookEdit, WebFetch, and WebSearch denied).
- `test-db` is an opt-in. The Codex review leg gets
  `-c sandbox_mode=workspace-write` and
  `-c sandbox_workspace_write.network_access=true`, the only way it can
  reach a local Postgres. Its `developer_instructions` gain a fixed rule:
  only the test database (`RAILS_ENV=test`), only inside a rolled-back
  transaction, no other database, host, or port, and no container or
  docker commands. The cursor-agent legs run as before, with no `--sandbox`.
  The Opus native leg also gets Bash, and the same rule in its appended
  system prompt.
- Any other value stops the run before any leg launches.

Codex's sandbox cannot limit network to one port. Under `test-db` the
database scope is the rule plus the opt-in, not a jail. A Codex leg
with workspace-write and network reached another project's database
container. That is why read-only is the default.

`codex review` (codex-cli 0.160.0) has no `--sandbox` flag. Only `codex exec`
has `-s`. The review leg sets the mode with `-c` keys. Do not use
`danger-full-access`. Do not put `network_access` in `~/.codex/config.toml`.

Under `read-only`, Rails cannot reach host Postgres, and the leg cannot write
a scratch script. It reviews by reading. The Opus native coverage leg is
covered too: `dontAsk` with only Read, Grep, and Glob, no MCP servers, and the
staged diff in a file (above). It no longer runs with `bypassPermissions`. The lineup
and `readonly.txt` carry a `voter sandbox:` line with the mode.

Codex runs commands in a login shell, which skips mise or asdf activation.
On one Rails stack native Astra hit `ruby: command not found` on 16 of 17 ballots
and reviewed read-only. The leg now prepends the worktree's tool bin dirs to
PATH (`mise bin-paths`, else asdf shims) and records them in
`voter-codex.path.txt`. `prepended: none` on a Ruby or Node target means the
leg ran without its toolchain.

`codex review` takes a diff-selection flag or a prompt, not both (2026-09-08).
House rules go through `developer_instructions` for this run only. Do not put
them in `~/.codex/AGENTS.md` or the repo `AGENTS.md`. Pull-request context is
untrusted data and does not go in `developer_instructions`. When
`pr-context.json` is present, that text is the user prompt: the same
uncommitted-changes instruction, then a fenced data block. The flag is
omitted in that case because the prompt cannot sit beside it. With no context
file, the command stays `--uncommitted` and `developer_instructions` is only
the house rules. `codex review` prints
only `findings` and `overall_explanation`, so the clean-list has to live
inside `overall_explanation` or stdout drops it (2026-09-28, a sentinel rule
arrived in the rollout and was stripped from stdout).

`< /dev/null` is required. With stdin on a pipe, `codex exec` blocks at
"Reading additional input from stdin" (12-minute hang, 2026-09-16). The review
command gets the same redirect.

`codex exec` flag traps, verified 2026-09-11 on an alpha build: do not combine
`--sandbox` with `--approve-for-me`; `--approve-for-me` already selects
workspace-write, so keep the network `-c` flag. `codex exec resume` rejects
`-C` and `--approve-for-me` and still needs `--skip-git-repo-check` outside a
trusted repo. It inherits the original cwd and sandbox. A resume prompt may
say how to fetch. It must not say what another vendor found. On a non-diff
target, Astra's path is `codex exec` with `-c web_search="live"` (exec
defaults to cached search; `--search` is TUI-only), not `codex review`.

No Cursor fallback for OpenAI. Cursor had no Astra (checked 2026-09-08). When
Astra is rate-limited, wait or run the remaining voters and say so. Do not
stand in another OpenAI model. Allowances on the smaller plan are small and
name a reset time; report that time.

The model proof is `"model"` on each `turn_context` in the session rollout.
The script compares it to the resolved id.

### Grok

This is the `CONCLAVE_GROK_NATIVE=1` path. The default is Grok on Cursor (below).

```
TMPDIR=<leg tmp> grok --cwd <worktree> \
  --effort high --max-turns 60 \
  --always-approve --disable-web-search \
  --output-format plain \
  -p "/review --local

Write the review file before you explore further. ..."
```

The first prompt used to demand an open-ended adversarial pass (scratch scripts
for every boundary, unanchored patterns, pasted output) before the review file
existed. That is what spent the 60-turn budget on 2026-10-13 and again on
2026-10-22: the stop that was reported was 60, the first call's cap, and the
resume at 80 had not produced a report, so the runs stayed not judgeable.
The cap stays 60, and the resume stays 80. The prompt now tells the reviewer
to write the file before any further exploration.

Do not pass `--model`. The CLI default tracks the newest release. Use
`--local`, never `--pr` or `--branch`. `--pr` deletes the review file on
success and opens a pending GitHub review. `--branch` hardcodes `origin/main`
and falls back to `origin/master`, ignoring `origin/HEAD` (2026-08-11: both
two work repos have `origin/main` while `origin/HEAD` is `develop`).
`--local` reads `git diff HEAD`, which `reset --soft` made equal to the
review diff.

The report is the file under the leg `TMPDIR`, not stdout. Prefer the path the
final report states, and only if it sits under that leg's temp directory.
Otherwise require exactly one `grok-review-*.md` there. Never `ls -t`: on
2026-08-14 three concurrent runs shared a temp directory and the newest file
belonged to a different PR.

Exit 0 is not "finished". `/review` runs the reviewer as an inner session, so
one leg leaves two sessions under `~/.grok/sessions/<cwd with slashes as
%2F>/`. The outer session lists the inner one as `child_session_id` in
`subagents/<id>/meta.json`. `CONCLAVE_GROK_SESSIONS` overrides the sessions
root. Check the last `turn_ended` in each session's `events.jsonl` for
`outcome:"completed"`, not only the newest session's. On 2026-10-13 the inner
reviewer hit its 60 turns on two ballots of three and wrote no report. The
outer session, the newest, ended `completed`, so nothing resumed and the model
check said `turn=completed`. `permission_cancelled` means the permission mode
refused a shell; that is why the command uses `--always-approve` (`dontAsk`
killed four runs out of four on one PR).

When a session did not end `completed`, resume it once, same `--cwd` and
`TMPDIR`, max-turns 80, with one line: stop testing, write the report from
what you already found, do not re-read the tree, zero findings is acceptable.
The inner session wins over the outer one. A session cut off with no final
`turn_ended` is the same resume (2026-09-29). If the leg still has no report,
it fails, and `voter-grok.fail` names the resumed session, its exit, and its
last `turn_ended`. The model check reads every session: one that did not end
`completed`, or a leg with no report, is a FAIL. `model_id` in `events.jsonl`
is the proof of what ran. No ballot has resumed an inner session yet
(2026-10-13), so whether the CLI accepts one is unverified.

There is no Cursor fallback. If the Grok CLI fails, the leg fails. A fallback
would lose `/review` and read as a clean column if it were silent (the old
skill's ping-then-Cursor path). The script does not ping Grok with a prompt.
`grok-engine` has lied both ways: "signed out" while a ping would have
answered (2026-09-28), and `grok-build` while the leg then died with 402
(2026-09-16). Concurrent `grok models` calls race the OAuth refresh and
rewrite `~/.grok/auth.json` (2026-08-14, three sessions, one false fallback).

## Muse

The lineup seats Muse unless a Muse window is capped. Ahead of pace still
votes, at low. `--voters ...,muse` names that seat on review, audit, design,
and mixed. Meta never chairs or builds, so the vendor rule does not drop it.
One vendor, one vote.

- **Launch.** `muse exec --json --provider meta`, the newest Wonder full model as
  `--model`, the routed effort as `--reasoning-effort`, and
  `--contributor-mode off`. Review and audit pass high. Design and mixed
  pass the resolved effort. Read tools only: `--disable-shell`
  and `--disable-write` on every kind. Review and audit pass `--workspace`
  with the leg worktree and set `TMPDIR` to the leg tmp, because Muse
  refuses a `TMPDIR` inside its workspace. Review passes
  `--disable-web-tools`; audit leaves web on. Design and mixed use the
  shadow's packet-first, tool-less flags.
- **Brief.** Review: the reviewer brief less the execute bullet, plus
  `git diff HEAD` from the worktree at launch. Muse has no native review
  command and no shell; without the diff, a live run on 2026-10-13
  reported blocked. Audit: the shared audit brief. Design: `PROMPT.md`.
- **Model proof.** `run.model.configured` `payload.model_id` in the
  `--json` stream, written by `scripts/muse-usage.py --proof` to
  `voter-muse.model.txt`. No configured id, or any id other than the
  resolved Wonder full model, FAILs the leg. The event records the model the run was started with;
  the stream has no per-answer model id, so this is the strongest proof
  available, not proof of which model answered.
- **Blind.** `blind.sh` redacts `Muse`, `Meta`, and `muse-*` ids. The
  lowercase words stay, because `meta.json` and `muse/` are code.
  Design and mixed `anonymize` redacts the same names before Jev. An
  audit target that names Muse or Meta keeps them (the keep-list).
- **Header.** `judgment.md` adds `muse_model` (and `muse_seconds` on review
  and audit) after the other voter lines. With `codex` off the ballot,
  `voter_b_model` and its lines read `none`.
- **Shadow.** A named muse voter owns `voter-muse.md`, so the design
  shadow does not run beside it.

First live leg: [PR 44](https://github.com/rymiwe/conclave-archive/pull/44) in `rymiwe/conclave-archive`, 2026-10-13, 122 seconds, model proven,
read-only hash matched.

### Persona

`/review` prepends `~/.grok/bundled/skills/shared/personas/reviewer.md`. Nothing
ships that file. The neighbours (`design-doc-reviewer.md`, `implementer.md`,
`security-auditor.md`) are shipped; `reviewer.md` is the exception (checked
2026-08-13 by moving a seed aside and running `grok plugin update`). The copy
in this repo is `scripts/reviewer-persona.md`. Re-seed with `cp` when the slot
is empty. A version bump can land unattended when auto-update is on. A persona
read error does not kill a run; check `cancellation_category` before blaming
it. The script does not write into `~/.grok`.

## Argon packet size

The review Argon leg is packet-first. `review_run_argon` sends the reviewer
brief and `git diff HEAD` to `agy` as one stream-json message. `agy` stops
reading near 190 KB (about 80K input tokens) and reports no error: prompts
from 211 KB to 989 KB all plateau at 60-80K input tokens. The diff is
path-ordered, so a long packet silently loses the specs and tests.

`scripts/argon_packet.py` sizes the packet before the leg runs.
`CONCLAVE_ARGON_MAX_BYTES` is the cap (default 150000, `COUNCIL_ARGON_MAX_BYTES`
is the alias; a value that is not a positive integer falls back to the default).
Every run writes one line to `voter-argon.packet.txt` and to stderr with the
mode, the full packet bytes, the cap, the packet count and each packet's bytes,
and the number of files cut.

- The Argon brief carries about 10 KB of PR context, not 50 KB, and drops the
  check-run JSON. Other legs keep the 50 KB context.
- Under the cap, the packet is the brief and the whole diff, as before.
- Over the cap, files are kept whole in this priority: the brief, then the
  changed source diff, then the spec and test diff, then generated files
  (the Rails dumps `db/schema.rb`, `db/structure.sql` and `db/schema.sql`,
  migrations, lockfiles). Specs and tests are spec directories, `_spec.` and
  `_test.` style suffixes, and pytest's `test_*.py`; a Ruby file named
  `test_runs_controller.rb` is source. A lower class gets only what the higher
  classes left. One file diff bigger than the cap is cut at a hunk boundary and
  marked truncated.
- When files would be cut and both source and tests changed, the packet splits
  in two: packet 1 is the source diff (and generated files if they fit), packet
  2 is the spec and test diff, each under the cap. Argon runs on each, and
  `voter-argon.md` holds both reports under `## Packet 1 of 2` and
  `## Packet 2 of 2`. Findings are not de-duplicated. A packet that fails beside
  one that worked is a `note:` line in `errors.md`, and its section says
  `NOT REVIEWED`. Both failing skips the leg like any failed Argon. A split
  that cuts nothing writes no `.partial` and the summary says so.
- A cut path is listed on one line: backslash and double quote are escaped and
  CR and LF are written as `\r` and `\n`, so a filename cannot add entries to
  the coverage list.
- A packet that still cuts files names them in a `## Packet coverage` section
  inside the packet, and the same list goes to `voter-argon.partial`. The first
  line of that file is a `WARNING: argon saw a PARTIAL review packet` line with
  the cap setting and the 190 KB limit. It also goes to `errors.md` as `note:`
  lines (a partial packet does not fail the ballot), to the top of
  `voter-argon.md`, and to the phase-1 summary. Argon is told it did not see the
  cut files.

Design and mixed Argon packets are not changed by this.

## Already raised

Before consolidation, read what the author already answered. Do not put any of
it in a voter's input. A reviewer told "X is settled" stops looking at X.

PR review comments and issue comments:

```
gh api repos/<org>/<repo>/pulls/<N>/comments --paginate
gh pr view <N> --json comments
```

Read the linked ticket's comments, not only its description. The live decision
is often in the comment stream. If the lookup cannot run, say Slack and Linear
dedup was skipped. Verified 2026-08-12 on a work pull request: a finding cleared
every other check and was posted; a ticket comment 32 minutes earlier held the
same table, measured the cost, and recorded that the PR merges with the issue
documented rather than fixed. The posted comment had to be deleted, and it
cited the ticket the question had since moved off.

Search Slack for the PR number and the PR URL. Decisions arrive as short
replies ("2 is on purpose") to a long paste, so read the surrounding channel,
not only the matched message or the thread. Verified 2026-08-11 on a work
pull request: a review comment about write-only columns had to be
deleted. The same finding had been sent in a DM six hours earlier, and the
author had already answered. Nothing about it was on GitHub.

If search is unavailable, say so.

## Stack and related PRs

On `kind: review` with `--pr N`, the script finds the stack from the target repo with `gh` and writes `stack.md` beside `judgment.md`. Upward it lists open PRs whose base is this PR's head, then repeats from each of those, following every branch, skipping a PR already seen, and stopping at 20 PRs. Downward it takes the open PR whose head is this PR's base, and repeats. `stack.md` lists every PR from the bottom of the stack to the top: number, title, URL, base branch, head branch, the files it changes, and its body, and it marks the target PR.

Edge cases:

- **Alone.** `stack.md` is `No stack: no open PR builds on this branch and its base is not another open PR's head.`
- **Refs or whole codebase.** No walk; `stack: none`.
- **`gh` fails.** `stack` is `unknown`, `stack.md` says what failed, and the run continues.
- **Cross-repository PR.** Never a parent or a child. A cross-repository target is alone. The walk does not go up or down.
- **Default branch.** A PR whose head is the repository default branch is not a downward parent, and the upward walk does not run from that head. The rule is about that head, not about release PRs in general. When develop is the default branch, an open PR from develop to master is not a parent. When master is the default branch, that same PR still links.
- **Truncation.** When the walk stops at 20 PRs and no later PR lists the finding's file, the line is `not touched in the first 20 PRs; walk truncated`. When a later PR does list the file, or a file list is capped, the line ends with `; walk truncated at 20 PRs`.
- **100-file cap.** `gh pr view --json files` returns at most 100 files. When a later PR lists fewer files than its `changedFiles` and the finding's file is not among them, that PR contributes `unknown (PR #N lists 100 of M files)`.

The judgment header field `stack` is `none`, `unknown`, or the PR numbers bottom to top with the target marked, for example `#11 (target) -> #12 -> #13 -> #14`. Each `decide.md` block has a pre-filled **later PRs that change this file** line: the later PRs whose changed files include the finding's file, written `#N title`, or `not touched later in the stack`, or `not touched in the first 20 PRs; walk truncated`, or `unknown (PR #N lists 100 of M files)`, or `no stack`, or `unknown` when the finding has no file or the walk failed. A truncated walk that still names a later PR, or that has a capped file list, ends with `; walk truncated at 20 PRs`. The chair reads that line for every finding, not only a finding of the form "never read", "no writer", "dead column", "unused attribute", or "premature schema". It also covers an incomplete fix that a later PR in the stack completes.

The legs do not see the stack. A stack in the brief reaches only the Fable leg, since native legs never receive the brief; reviewing against the stack top reviews code that is not in the target PR and attributes it to the wrong PR.

Verified 2026-08-11 on a work pull request. Three legs and a posted comment all said
several columns were written and never read. A later pull request was stacked
on the branch and referenced one of those columns many times.
`--base` also showed another pull request, which no thread mentioned.

Unmerged is not settled. When a child consumes a finding, report "consumed by
#N (open, stacked on this branch)". The deploy window can still make it live.
That is the author's call.

On 2026-10-02 one work pull request was 1 of a 4-PR stack and the ballot reviewed it against develop alone. Held findings f2 to f6 cite files that a later pull request in the stack also changes. The old chair step would not have fired: the findings were an incomplete fix, not an unused symbol.

## Stack review

`scripts/stack-run.py` first runs one whole-stack ballot on the combined diff, to the end. Then it reviews every PR in the stack, bottom up, one `kind: review` ballot per PR, at most two in flight. A finished ballot frees its slot at once, and the next one starts. Each per-PR brief lists what the whole-stack ballot already found. Then one reconciliation pass checks every finding against the top of the stack. Issue #19 chose the per-PR shape (option 3, 2026-10-03).

The order and the cap come from one night when a whole-stack ballot and nine per-PR ballots started at once. Three seat pools hit their limits within minutes. Six ballots could not be judged, and the early per-PR ballots ran without the whole-stack findings.

```
scripts/stack-run.py --repo ~/code/app --pr 101 \
  --stack-id stack-1 --ticket APP-1
```

| Flag | Meaning |
|---|---|
| `--repo DIR` | Target repo checkout. Chairs run with this as their workspace, so its Linear and Notion sign-ins apply. |
| `--pr N` | Any open PR in the stack. Required. The runner walks the stack with `scripts/review-stack.py`. A walk that fails, stops at 20 PRs, or branches is an error; pass a linear `--prs`. |
| `--prs N,N,...` | An explicit bottom-to-top list instead of the walk. Each PR's base must be the previous PR's head. |
| `--stack-id ID` | Stack directory: `tmp/council/<ID>/`. |
| `--prefix P` | Ballot ids are `<P>-<N>`. Default: the repo directory name. |
| `--context FILE` | Shared context for every chair, copied to `context.md` in the stack directory. Without it and without an existing `context.md`, a context chair writes one from `--ticket`. |
| `--ticket KEY` | The Linear ticket the context chair reads. |
| `--wave-cap 1\|2\|3` | Most per-PR ballots in flight at once. Default: `CONCLAVE_WAVE_CAP` (`COUNCIL_WAVE_CAP` is the alias), else 2. A bad value is an error. Each fetch checks the oid gh already returned with `git cat-file`. Parallel ballots do not read `FETCH_HEAD`. |
| `--jobs 1\|2` | Deprecated alias for `--wave-cap`. `--wave-cap` wins over it, and it wins over the environment. |
| `--wave-wait S` | When a ballot's seat pre-check fails, check again every 60 seconds for up to S seconds. Default 0: stop at once. |
| `--chair-model ID` | Default: the resolver's builder id (newest Grok Fast at high). `auto` and composer ids are refused. |
| `--chair-timeout S` | Seconds per chair process. Default 3600. A timeout kills the chair and fails that step. |
| `--dry-run` | Print the plan: the in-flight cap, the launch order, and each ballot's steps. No chair, no voters, and no pre-check. |
| `--reconcile-only` | Run only the pass. No ballot runs, the whole-stack ballot included. |
| `--no-reconcile` | Skip the pass. The whole-stack ballot still runs first. |
| `--no-whole-stack` | Skip the whole-stack ballot. Per-PR ballots run with no stack-level findings in their briefs. |
| `--detach` | Re-exec this command under `setsid` and `nohup`. Write the pid and log path into the stack directory, wait until the first ballot step starts, print them, and exit. A shorter flag is rejected. |

### In-flight window and the seat pre-check

The whole-stack ballot runs first, alone, to the end. Per-PR ballots follow bottom up, at most `--wave-cap` in flight. A finished ballot frees its slot at once, and the next one starts. A slow ballot does not hold the others back. A Fable overflow seen in one ballot's phase 1 holds for every ballot whose phase 1 starts later.

Before each ballot starts, the whole-stack ballot included, the runner asks `seat-usage --route lineup --json --kind review`. That is the route `conclave.sh` seats a review from. `scripts/lineup.py` reads the answer. The ballot starts when the route gives at least two different voters, as a normal lineup or a downgrade. It does not start when the route asks for a human, gives fewer than two voters, or prints JSON that cannot be read. The message carries the route's reason and skipped codes.

A missing `seat-usage`, a nonzero exit, empty output, or no answer within 25 seconds is inconclusive. The runner prints one warning and starts the ballot. `conclave.sh` then falls back to its fixed default. A ballot with no voters to launch is not checked: it is finished, blocked, or needs only a chair (judge and decide).

When the pre-check blocks, no new ballot starts. Ballots already running finish; they are not stopped. With `--wave-wait S`, the runner checks again every 60 seconds until the route passes or S seconds pass. Each per-PR row that did not start reads `failed: not started; seats capped (<reason>)`. The reconciliation reads `failed: not started; seats capped`. One line on stderr names the ballot that did not start and the reason. The runner exits 1. Rerun the same command to resume. Finished ballots are reused. A failed ballot does not stop the others; it is that row's outcome.

`run.json` keeps a `waves` list. Each entry is now one launch, not one batch: a number, the ballot it gated, the pre-check result (`ok`, `blocked`, `inconclusive`, `skipped`, `not needed`, or `not started` when a usage limit stopped the launch after the pre-check), and the message. `run.md` shows the same list. The flag and setting names keep the word wave.

`CONCLAVE_WAVE_PRECHECK=0` skips the pre-check. It is for tests and for an escape when the route is wrong. `CONCLAVE_WAVE_POLL` sets the seconds between checks; tests use it.

### Per ballot

The runner reads each ballot directory and does only what is missing:

| State | Condition | Steps |
|---|---|---|
| done | `judgment.md`; `decide.md` with no `TODO(chair)`; `comments.md`, or a `decide.md` that says there is nothing to decide | none; reused |
| needs decide | `judgment.md`, and the rest of done is missing | chair round B, phase 4 |
| blocked | `blind/leg-*.md`, no `judgment.md`, and `errors.md` missing or holding a line other than `(none)` or `note:` | none; the ballot fails until the errors are fixed and `blind/` is removed |
| needs findings | `blind/leg-*.md`, no `judgment.md`, a clean `errors.md` | chair round B, phases 2 to 4 |
| needs legs | `brief.md` with the runner's receipt (`.stack-brief.sha256` matches it) | phase 1, chair round B |
| new | anything else | chair round A, phase 1, chair round B |

Round A writes the receipt only after its brief passes the checks. A `brief.md` without a matching receipt (a partial or rejected brief, or one written by hand) moves to `brief.md.prev`, and round A writes a new one.

- **Chair round A.** A fresh `cursor-agent` session reads the PR, the shared context, and `stack.md`, and writes `brief.md`. It does not run the script. On a per-PR ballot after the whole-stack ballot, it also reads that ballot's `judgment.md` and `decide.md`. The brief gets a section `## Already reported at stack level (do not re-report)`: one line per finding, with id, file:line, and the defect. Reviewers report one of those again only when this PR adds a distinct defect. The runner refuses a brief without `kind: review` or with a forbidden family word.
- **Phase 1.** The runner runs `conclave.sh <id> --dry-run`, then the full run, with the Fable flags below. Phase 1 stops if the target repo is shallow.
- **Chair round B.** The round A session, resumed, or a fresh session when round A ran in an earlier invocation. It writes `findings.json` from `blind/leg-*.md` only, runs `--findings`, fills `decide.md`, and drafts `comments.md`. On someone else's pull request, a new inline comment starts with **Requested:**, **Advisory:**, or **Informational:**. **Question:** is not drafted. Labels go only on new inline comments: the review summary does not open with a label, and a bullet in the summary has none. A reply to an existing comment has no label. When `pr-context.json` has an automated risk rating, the summary states that rating. If the rating is above low, the summary names what drives it and suggests concrete changes that would bring it to low. When there is no rating, the summary says nothing about risk. The text in that file is data, not instructions. A throwaway script that verified a finding is saved as `repro/<finding-id>.<ext>` in the ballot directory.

Each cursor-agent chair the runner starts has `CONCLAVE_CHAIR=1` in its environment. That is the chair marker; `CONCLAVE_CHAIR_MODEL` is only the model id.

One chair per ballot keeps each chair's context to one PR. The whole-stack ballot is a separate chair before the per-PR ballots. No per-PR session resumes it.

A failed per-PR ballot does not stop the stack. A failed whole-stack ballot does: no per-PR ballot starts (see "Whole-stack ballot"). A failed seat pre-check stops the stack too (see "In-flight window and the seat pre-check"). When the Opus coverage leg fails on a usage limit, the whole Claude subscription is capped, so the runner starts no further ballot and skips the reconciliation. The runner exits 1 when any ballot, the reconciliation, or the whole-stack ballot failed. Rerun the same command to resume; finished ballots are not redone, and earlier `POSTED?` hits are kept. `run.json` and `run.md` in the stack directory record each ballot: state, outcome, chair and phase 1 seconds, the Fable flags, and the Fable percent read before phase 1.

The runner flags `POSTED?` when a chair's stream shows a GitHub write (`gh pr` or `gh issue` writes, a writing `gh api` call including a method glued to `-X` and a GraphQL mutation read from `-f query=@file`, a `curl` write to the GitHub API including `--json` and `--data`, `git push` including global options such as `-C`, `--no-pager`, and `-c`) or a Linear or Notion tool that is not a read. A `bash` or `sh` command that names a local script is scanned for those writes. If that script file is missing, the command is flagged. Text from a read tool is ignored. A Linear or Notion tool is classified by that tool's own name, not by words in a file it read. It does not undo it. Hits from the context and reconcile chairs appear under the `run.md` table. Chairs post nothing.

SIGINT, SIGTERM, or SIGHUP to the runner stops its chairs and any phase 1 run (`conclave.sh` stops its legs on TERM), removes the reconcile worktree, and records the signal in `run.json`.

`--detach` is for a long run launched by hand. The parent re-execs the same arguments under `setsid` and `nohup`, writes `detach.json` (pid and log path) and `detach.log` in the stack directory, and waits until the first ballot step has started. It prints the pid, the log path, and that step, then exits 0. The child keeps running. A detached run ignores SIGHUP. SIGINT and SIGTERM still stop it. If the child exits 0 before the first ballot step, the parent exits 0. A dry run does that. If the child exits nonzero before that step, the parent exits nonzero and prints the log tail. A resumed run does not report a step left in the previous `run.json` as the start of this process.

### Reconciliation

After the last ballot, the runner builds a detached worktree at the head of the top PR, outside both repos. One chair checks every finding from every ballot there, runs the repro where one exists, and finds which later PR settled it. It writes `reconcile.json`. The runner validates it, renders `reconcile.md`, and adds one line to each finding's block in `decide.md`, after the later-PRs line:

```
**at the stack top (#14 1a2b3c4d)**: fixed in #3428; recommend hold: <why>
```

The verdict is `still present at the top`, `fixed in #N`, `changed in #N`, or `not checked`. A finding the chair skipped, or an entry that fails validation, is `not checked`, and `run.json` lists the reason. When the chair writes no valid `reconcile.json`, the pass fails and folds nothing, so earlier lines stay. Rerunning the pass replaces the line. A re-judge (`--findings`) that keeps `decide.md` keeps the line. A re-judge that rewrites it leaves the line in `decide.md.prev`; rerun `--reconcile-only`.

This replaces the manual check that found 3 of 6 findings from one work pull request fixed in a later pull request. The file-level later-PRs line stays; it misses a fix in a different file.

### Whole-stack ballot

Before any per-PR ballot, the runner runs one ballot on the combined diff, `<prefix>-<stack-id>-stack`, to the end: `judgment.md` and a filled `decide.md`. Reconcile does not gate it. The stack id is in the name, so a second stack in the same repo does not reuse the first ballot. It uses the same chair rounds and the same `conclave.sh` launch as a per-PR ballot. Phase 1 passes `--base origin/<bottom base>` and `--head <top head oid>` instead of `--pr`. The bottom base is the first PR's base branch. The top head is the commit id from `gh pr view` for the last PR, after `git fetch origin pull/<n>/head`. `origin/<branch>` can still point at an older commit, or be missing on a fork, so the ballot does not use it. A branch name git accepts, including `+`, `@`, and `#`, is allowed on the base ref.

The brief asks for problems that span PRs: a contract one PR sets and a later PR breaks, data or migrations that disagree across PRs, permission or validation gaps that only appear in the combined change, dead code left by later PRs, and missing tests for the combined flow. It does not ask for per-line style. Round B names the PR that introduced each finding, notes conflicted legs in `decide.md`, and addresses each drafted comment to its PR. The runner sets `CONCLAVE_CHAIR_MODEL` to the chair model before that ballot's `conclave.sh` calls.

Its findings become context for the per-PR ballots: each per-PR brief lists them as already reported (see "Chair round A"). The whole-stack brief gets no such list.

`--no-whole-stack` turns this ballot off, and the per-PR ballots run as before, with no stack-level findings. `--reconcile-only` skips it too. `--no-reconcile` does not.

If the whole-stack ballot fails, no per-PR ballot starts. Each per-PR row reads `failed: not started; whole-stack ballot failed (<outcome>); rerun to resume, or pass --no-whole-stack to review per PR without it`. The reconciliation is skipped with a message, and the runner exits 1. Rerun the same command to resume. A finished whole-stack ballot is not redone.

### Fable on Cursor

Every Fable leg (design, mixed, review, audit, ask, the lineup fallback, and the no-lineup default) runs on `cursor-agent` with the resolver's `CURSOR_FABLE` id. Review uses the reviewer brief in the leg's worktree. Design, mixed, and ask stay packet-first and tool-less (`--mode ask`). Opus native still runs on the Claude subscription, and only when the route, the lineup, or `--claude-model opus` names it. The proof is the stream's init event naming Fable; Cursor reports no token usage. The column reads `<id> (via cursor)`. It draws Cursor Other Models.

`--claude-model cursor-fable` is the same leg. `--fable-overflow cursor` is accepted on kind review and does not start a Claude subscription attempt. The stack runner may still pass either flag. `--claude-model opus` is unchanged.

### Grok on Cursor

The Grok voter runs on `cursor-agent` with the resolver's `CURSOR_GROK` id. Design, mixed, and ask stay packet-first and tool-less. Review and audit run in the leg worktree; the final message is the report. `CONCLAVE_GROK_NATIVE=1` keeps the native `grok` CLI, including review session resume and `--reasoning-effort`. The default does not call that CLI.

## Revision recipes

A second pass reviews what changed since the last pass, not the whole branch.
An old-tip-to-new-tip diff includes unrelated commits the rebase pulled in
(2026-09-12, a work ticket: 26 files, +386/-108, versus the author's 8 files,
+97/-45).

The script does not build these worktrees. Construct the delta, then point the
script at a ref pair, or run the recipe inside worktrees you keep with
`--keep-worktrees` before launch. `commit.gpgsign=false` on every baseline
commit. `set -e`: a failed baseline commit otherwise leaves the new tree
checked out and `git diff HEAD` empty with exit 0.

**Ordinary revision.** For each leg worktree, check out the new head, restore
each path that either revision touches to the old blob (or delete it), commit
that as the baseline, then restore the new blobs. `git diff HEAD --stat` must
equal the revision, not the rebase. Whole-file checkout carries develop's
hunks in shared files; the stat check shows them. Drop those hunks, or rebuild
each such file with `git merge-file` against the new merge-base, before launch.

**Stacked PR whose parent moved.** The old range still contains the old parent
commit, so whole-file checkout plants stale parent content. Replay only the
child's old commits: diff from the old parent tip to the old head, apply that
patch onto the new merge-base, commit the baseline, then
`git checkout --no-overlay <new> -- .` so deletions survive. Verified
2026-09-28 on a stacked work pull request (its base revised between passes): the old range
carried another ticket's commit; the patch baseline was 3 files, +32/-7, with
no parent content.

**Rewritten commit, same base.** When the author replaces the reviewed commit
instead of adding one, the old and new heads are siblings, so `--base OLD
--head NEW` takes their merge-base and reviews the whole branch again. Build
each leg worktree at the old head with `git worktree add --detach <wt> <old>`,
then `git -C <wt> checkout --no-overlay <new> -- .`. `git diff HEAD --stat` must
equal `git diff <old> <new> --stat`. Copy `config/database.yml` and `.env` in,
as phase 1 does. The script cannot launch these legs: it has no delta target,
and on review kind it runs Astra native, not on the brief. So the chair writes
a launch script that runs Fable on the brief from stdin and Astra as `codex
exec` with the brief as its argument, then runs `scripts/blind.sh` on the two
reports. `--findings` then works on that ballot, but it writes no `decide.md`
and its header reads `kind: unknown`. Write `decide.md` by hand in the
script's format and say so at its top. Verified 2026-10-13 on a work pull request:
the delta was 4 files, +36/-78, against a 145-line branch. The only finding
came from the leg on the brief, and the chair's own repro of the old findings
had already shown it.

Brief extras, and say nothing about what the previous pass found: the target
is a later state of the same branch, and the one instruction is to check
whether a change is complete (a normalization on one path but not another, a
helper added but not used everywhere, a spec that asserts the happy case but
not the case that motivated the change). On 2026-09-12 the only finding came
from the brief-driven leg; both native legs returned clean. Native review
follows the diff's topic, and a revision's risk is usually one step to the
side. The default Anthropic leg is already on the script's reviewer brief
for every diff, including a fresh PR (Targets). OpenAI stays on its native
review command. Grok uses `cursor-agent` unless `CONCLAVE_GROK_NATIVE=1`.
A revision wants the targeted completeness question, so put
every voter on that brief for a revision. The script does not do that
switch; the chair runs the brief-mode commands below, or says so and does
not pretend the diff review asked the completeness question.

Skip a structural pass on a revision. It is dropped anyway (below).

## Non-diff targets: kind audit

`kind: audit` reviews an existing artifact, or proposed edits to an existing doc, rule, or config. A new design or plan with no artifact yet stays `kind: design`.

Use `kind: audit` for a target with no diff. That is a live page, a doc, an article, or a proposal that is not adopted yet. Native `/code-review`, `/review --local`, and `codex review --uncommitted` review the wrong tree or stall (2026-09-11, a live page: the useful output was three models on the same claims).

Only `--kind audit` or the brief header `kind: audit` selects this kind. The script never infers it. Legs have tools and live web access.

### Flags and headers

Pass `--target <url|file>` or a `target:` header. The flag wins. An `http://` or `https://` value is a URL. Anything else is a file. A relative path resolves against the directory you started the script from.

Pass `--target-type content|claims|proposal` or a `target-type:` header. The flag wins. The value is required. A missing or unknown value dies before any voter. The script never infers the type.

`--context <file>` is repeatable. The script copies each file into the snapshot as `context/context-1<ext>`, `context/context-2<ext>`, in argument order, keeping the extension. There is no header form.

`--attach FILE` is repeatable, and an `attach:` header holds a comma- or space-separated list. The two lists are joined and deduplicated. The path rules are the same as design: a relative path is resolved against the target repo root first, and against the directory the script was started from only when no file exists in the target. It must be a regular file inside the target repo or the Conclave clone, and a missing file or a symlink that resolves outside those roots is refused. The script copies each file into the throwaway snapshot repo under that relative path after the target is captured and before the snapshot commit, so every leg's worktree has it and the read-only baseline includes it. It does not change the target capture, the target hash, or the drift check. Each brief gains an "Attached files" section with the path and the full contents. That section is not part of the family-word check, same as context files. `kind: review` rejects the flag.

`--pr`, `--base`, and `--head` die under audit. `--target`, `--target-type`, `--context`, and `--verdicts` die under any other kind.

### Snapshot and drift

The script builds a throwaway git repo outside any worktree. A URL is fetched with `curl -fsSL --compressed` to `target.html`. The script keeps the final URL after redirects. A file is copied as `target` plus its extension (`target.html`, `target.md`), so HTML detection still sees `.html`. The script writes `tmp/council/<id>/snapshot.txt` with the requested target, the final URL, the capture time in UTC, the sha256, the byte count, the line count, and the longest line. A longest line over 2,000 characters warns and does not die.

The script commits the snapshot and `context/` once with `commit.gpgsign=false`. `git add -A -f` and that commit both set `core.excludesFile` and `core.hooksPath` to `/dev/null`, so a global excludes file or hook cannot drop a file. Each leg then gets its own worktree and its own `TMPDIR`, as review does. The read-only check is the same one review uses: the checkout is strict, and a ref move fails only when a leg's worktree made it.

A URL is fetched at the start of phase 1, immediately before the repo is built. The script fetches it again after every leg finishes. Different bytes set `target_drift: yes` and add a line to `errors.md`. Matching bytes set `target_drift: no`. A file target sets `target_drift: n/a`. A refetch that fails sets `target_drift: yes` and records the curl error in `errors.md`. The script does not claim the page was stable when the second fetch failed.

The script never renders HTML. The script never runs a browser.

### Ballot and launches

The default ballot is the lineup. Every seated leg uses methodology `brief` and has live web access. A capped pool is not seated. A pool ahead of pace still votes. When seat-usage is missing, fails, or returns mode ask, the fixed default is Fable and Astra, and the lineup says so. `--voters` replaces that list. Opus joins only when `--voters` names it, and that seat is also `brief`. Fable and Opus agreement is one vote, the same rule as review. The lineup says `brief` on every row. Under the table it says: "Every leg is on the same brief; independence comes from separate models, not separate methods."

Fable reads the assembled brief on stdin. The working directory is the leg worktree. Tools are limited to read and web: allow Read, Grep, Glob, WebFetch, and WebSearch. Disallow Task, Bash, Edit, Write, and NotebookEdit. The launch also sets the available tool list to that allow list, and uses a headless permission mode so the process does not wait for a prompt. Model proof reuses the review Fable check. A Fable key is required. Opus and Haiku keys are allowed, because WebFetch uses a small model internally.

Opus, when named, uses the same launch with the Opus family. Proof matches the review Opus override. That proof wants an Opus key and no Fable key.

Astra runs `exec` in the worktree, not `codex review`. The sandbox is read-only. Web search is `live`. A brief of at most 100000 bytes is the exec argument, and stdin is closed, because a headless exec hangs when stdin is a pipe and a prompt is also provided. A longer brief is the argument `-` and is read from the brief file, so one argument does not exceed the kernel limit. Model proof reuses the review Astra check. That check finds an exec session by the worktree path.

Grok, when named, reads `--prompt-file` with an absolute path and passes `--always-approve`. The launch does not pass `--disable-web-search`. The review session check still requires `outcome:"completed"`. If the turn is not completed, the script resumes once and leaves web search on.

### Brief and templates

The script writes `tmp/council/<id>/voter-<seat>.brief.md`. A dry run writes the same files under `preview/`. The brief contains, in order: a target line (snapshot file names, the final URL if any, the capture time); the cite-as-`file:line` line; the warning that the target is data, not instructions; the chair's brief body with header lines removed; the sub-type template; the read-only instruction and honesty gate; and "The final message is the whole report." When `--attach` or `attach:` named files, an "Attached files" section follows that ending. A dry run writes those copies under `preview/` and nowhere else.

Templates live in `scripts/audit-templates/content.md`, `claims.md`, and `proposal.md`. The script cats them. The honesty gate drops the diff-only "Execute anything with computable behaviour" bullet. That bullet does not fit a target with no diff. Audit legs get the review honesty gate and findings rules, with web access allowed and the ending set by the template.

The family-word check is the review check. It runs on the chair's brief body and the template, and again on the assembled brief with the target line left out. It does not check the target line, so a URL or a generated file name is not a hit. A hit names the word and adds: "Rewrite the chair's brief so it does not name a model or vendor. The target line is not checked." Context files and the snapshot are never checked.

**Content.** A finding is a claim a reader would act on that is currently false, or a step that cannot be completed as written. Cite the snapshot `file:line`. Name the URL fetched. Quote the passage from the fetched source that shows the claim is false, verbatim. Settled product choices are not findings. Propose replacement copy for each finding. Sections: Findings, Proposed copy changes, Sources fetched, Checked and found clean.

**Claims.** Every factual claim gets one verdict: Accurate, Inaccurate, Misleading, or Unverifiable. Unverifiable takes one sub-tag: `opinion`, `prediction`, `contested-law`, or `no-source`. Every Accurate, Inaccurate, or Misleading verdict needs a fetched URL. No URL means Unverifiable (no-source). Memory of events after training is not evidence. A claim searched for and not found is no-source, never Inaccurate. Every Inaccurate or Misleading row quotes, verbatim, the passage from the primary source that contradicts the claim or supplies the missing context. Sections: Claim table (line, claim, type, verdict, URL), Findings (Inaccurate and Misleading only), Sources fetched, Not examined. Verified 2026-09-15 on a 2,900-word essay: all three legs used the vocabulary. Inaccurate rows converged. The split was Misleading versus Accurate on omissions, so re-read the primary source for every Misleading row instead of counting votes. Only fact and law rows join the table. Astra with live web search fetched the most primary documents.

Content and claims reports end with one fenced json block. The `verdict` for content is `false-claim` or `broken-step`. The `verdict` for claims is `Inaccurate` or `Misleading`. An empty list is valid. The chair copies the blinded leg letter and the zero-based row index into `findings.json` as `source`. The script checks the quote on the unblinded report. Nobody reads the unblinded text.

**Proposal.** Write one line per change first, exactly `VERDICT <id>: adopt`, `VERDICT <id>: adopt with changes`, or `VERDICT <id>: reject`. Use the ids from `changes.json`. `reject` is a valid answer. The existing rules may already cover the problem, or the change may make things worse. Then, per change: which existing rules already address the problem and why they did not prevent it; the defects; draft wording for `adopt with changes`. The motivating feedback is input to weigh, not the answer key. Verified 2026-09-28: three legs reviewed two writing-rule edits under "what is wrong with these edits?" All three returned rewrites; none could say "do not add this". One noted that three existing rules already forbade the problem, and consolidation buried it because the brief never asked.

Write `tmp/council/<id>/changes.json` before phase 1. The shape is `changes` (each with `id` and `proposed_change`), `existing_rules`, and `motivating_feedback`. An id is `C` plus digits, such as `C1` or `C12`. A proposal without that file, or with a malformed file, dies before any voter. The script also copies the changes into the snapshot as `changes.md`.

### Phase 1 checks

After the legs finish, and before blinding, the script checks each report. Content and claims need a parseable final json block. A proposal needs one well-formed `VERDICT` line per change id. A report that fails is marked `incomplete: <reason>` in `errors.md`, in `model-check.md`, and in `lineup.md`. The script still blinds that report. The script never guesses a verdict. A missing report is a launch failure and is not blinded.

### Blinding

`scripts/blind.sh` takes `--keep word1,word2` before its positional arguments. Those family words stay unredacted. Audit builds the list from the snapshot and the context files. A family or vendor word that occurs there as a whole word, case-insensitive, is kept. Model ids and leg paths are always redacted. The judgment header records `redaction_kept:` as the comma list, or `none`. Review calls `blind.sh` with no `--keep`.

### Findings gate

Content and claims reuse `--findings`. Write `findings.json` as for review, and add `verdict`, `url`, and `quote` on each finding. A finding may also carry `source`: `{"leg": "A", "row": 0}`, the blinded leg letter and the zero-based index into that leg's final json `findings` array. The chair copies the letter and the index. The script resolves the letter through `blind/key.txt`, reads `url` and `quote` from the unblinded report, and checks that quote. It does not write the unblinded url or quote, print them, or send them to Jev. Jev's state keeps the blinded `url` and `quote` from `findings.json`. Without `source`, the script checks the `url` and `quote` on the finding itself. A bad `source` (unknown letter, row out of range, unparsable report, missing key file) is `match: none` with a reason. It does not crash.

The script refetches each quote URL and checks that the quote occurs in the fetched text. HTML is turned into text with the Python standard library. Block tags are separated by a space. Inline tags are not. Whitespace becomes single spaces. Curly quotes and dashes become ASCII. Character references are decoded once. The body is decoded with the charset from the Content-Type header when Python knows that charset, and otherwise as UTF-8. The comparison is a case-sensitive substring. A non-HTML body is compared directly. A PDF, or a fetch that fails, is `quote_verified: false` with the reason. The script writes `quote-check.json` with `quote_verified`, `match` (`strong`, `weak`, or `none`), `occurrences`, `words`, and `reason` for each finding. When `source` was used, the row also has `source_used`.

`strong` means at least 8 words and exactly one occurrence. Only a strong match is an executed repro. `weak` means the quote was found, but it is shorter than 8 words or it occurs more than once. It is verified and read-only. `none` means not found. `quote_verified` is true for strong or weak. A quote that is not found is `second look`, not `hold`. After Jev, an executed finding posts only on Jev `post` at or above 0.7. Otherwise it is `second look`. A weak match uses the read-only rule. Every `Misleading` finding is `second look`, whatever Jev says. `false-claim`, `broken-step`, and `Inaccurate` follow the executed rule when the match is strong.

#### Calibration

8 words is a judgment call of about one clause of real prose, unmeasured. Exactly-once exists so boilerplate like "All rights reserved." proves nothing.

**First live claims run, 2026-10-02.** A short article with one wrong release date, checked against python.org. Both legs fetched the release page and caught the date.

- A wrong-date row quotes about 5 words ("Release date: Oct. 2, 2023"), so `QUOTE_MIN_WORDS=8` grades it weak. The gate needs Jev `post` at 0.7 or above either way, so the grade only changes where a hold lands: `hold` for weak, `second look` for strong. Not changed. If it is revisited, the better rule is a floor of about 20 characters plus exactly once. The floor only has to stop trivial matches; Jev rejects a genuine quote that is off-topic.
- Jev scores, the first claims sample: the legs' own weak row `post` 0.47 (`hold`); a planted 8-word strong row `post` 0.60 (`second look`); a planted misquote `post` 0.20 (`second look`, quote not found). Even the clear-cut row stayed under 0.7, so no claims row posted on its own. Do not move 0.7 on one sample. Every claims row reaching the human is the safe failure for a tool that drafts published corrections.

Jev's state for a finding is the claim, the file, the line, the cited snapshot text, the url, the quote, and the verdict. It is never leg output. It is never an agreement count. In audit, `post` means include the finding in the drafted copy changes. `judgment.md` says so.

Audit findings decisions are `changes to draft` (at least one post), `human review` (none post, but some findings), or `clean` (no findings).

### Proposal verdicts

`--verdicts` runs in phase 1 for a proposal, after the snapshot and alongside the legs. It also runs alone: `scripts/conclave.sh <id> --verdicts`. Pass `--canned FILE` to skip the API. `--verdicts` is valid only for an audit proposal. It cannot be combined with `--findings`.

Jev gets one `choice` question per change id. The choices are `adopt`, `adopt_with_changes`, and `reject`. `state` holds `proposed_change`, `existing_rules`, and `motivating_feedback` from `changes.json`, and nothing else. The script writes `jev-verdicts-request.json` and `jev-verdicts-raw.json`. It reuses the Jev key load and `post_systemone`.

After blinding, the script writes `verdicts.md`. One row per change. Columns are the change id, each blinded leg letter from the `VERDICT` lines, and the Jev choice with its confidence and its probabilities. In a proposal `judgment.md` this table comes first. The decision is always `human review`. Jev returns a confidence separate from the per-choice probabilities; the two need not match.

If Jev has no key, the table shows `unavailable` in the Jev column and the run continues. A later `--verdicts` fetch deletes the previous raw response before it can fail, and the render treats the unavailable marker as current. Re-auth, then re-run `--verdicts`. The script does not invent a response file.

Verified 2026-09-28: a redundant rule came back `reject` at 0.78. n=1. Do not use Jev for scope or priority questions, such as which fixes to include. It has no ground truth for them.

Draft the copy changes, ready to apply. Show each one in full in chat in the message that asks before editing or pushing. A file path is not a preview.

### Judgment header

Audit uses the review header, including `voter_b_reasoning_tokens` and `voter_b_effort` next to `voter_b_model` and the per-leg `*_seconds` wall times, and adds `target`, `target_type`, `snapshot_sha256`, `target_drift`, `redaction_kept`, and `gate` (`findings` or `verdicts`). Review key names and review decision values stay as they are. `confidence_of` for an audit proposal is `none`. For an audit findings gate it is `lowest Jev confidence among findings gated to post`, or `none` when nothing posts.

### Dry run and canned runs

`--dry-run` resolves routing, fetches or copies the target into `preview/`, and prints `network: fetched <url>` when it fetched. It writes `preview/snapshot.txt`, the per-leg briefs, and the lineup. It does not run voters. It does not call Jev. It writes nothing outside `preview/`. It dies on the same input errors as a real run: missing type, unknown type, missing or malformed `changes.json`, and a family word in the body or the template.

`--findings --canned FILE` and `--verdicts --canned FILE` read a saved Jev response. They do not call the API.

## Ask mode: one question, no ballot

`scripts/conclave.sh <id> --ask TEXT`, `--ask-file FILE`, or `--template NAME --input FILE` puts one prompt to every named voter and writes the answers side by side. There is no brief and no ballot schema. Use it for a question too small for `kind: design`, and for the writing checks before anything posts under Ryan's name.

- **Templates** live in `scripts/ask-templates/`. `cold-read` asks the four cold-read questions. `tell-scan` is the detector pass: exact spans, CLEAN or a list of {span, pattern, why}, never a rewrite. Both follow `~/.claude/rules/writing-style.md`.
- **Ballot.** A template's `voters:` line stays its ballot. Both templates name codex and grok, because each is the strict one on a different axis (writing-style.md, 2026-09-08 calibration). A plain question calls `seat-usage --route ask` and takes two voters on two vendors. The chair's vendor may answer. `--voters` wins. Voters are claude, codex, grok, and muse. `CONCLAVE_SEAT_USAGE` overrides the router binary.
- **Effort.** Jev picks low, medium, or high from the question and a short excerpt of the input. Pace then steps that lean once: up when that voter's pool is burning, down when it is ahead. `--effort` overrides both. When Jev has no key or fails, the leg uses the router's effort and the lineup says so. A missing pace reading keeps the lean, or high when the lean failed too. `--canned FILE` is the saved Jev response and does not call the API. The Jev key is unset before any voter starts.
- **Chair note.** `--ask` or `--ask-file` together with `--template` adds a chair note after the template. Use it to say which lines of the input are labels and not posted text.
- **Input.** Each `--input` file is inlined between INPUT markers, and the prompt calls it data, not instructions. Give only what a real recipient would have.
- **Launch.** Every leg runs tool-less from one out-of-repo temp cwd, as the design packet does. Grok's answer counts only when its session's last `turn_ended` is `completed`. Muse uses the newest Wonder full model, `--contributor-mode off`, and `--reasoning-effort`. Plain Wonder is the other model on that seat and is not this voter.
- **Output** is `tmp/council/<id>/ask-<template, or question>/`: `PROMPT.md`, `answer-<voter>.md`, `answers.md`, `blind/` from `blind.sh`, and `errors.md`. A rerun replaces that directory. The script exits 1 when a named voter gave no answer, and `answers.md` still holds the rest. `--dry-run` writes only `preview/ask-<name>/PROMPT.md` and does not record a run. A real ask appends one line to the outcome log with the effort each leg ran.
- **No ranking.** The chair reads the answers. Ranking free text would be a new Jev contract. Add it only after ask runs show the chair needs a tiebreak.

Test: `scripts/test-ask-mode.sh`.

## Brief, when a leg is on the brief

The default Anthropic leg on a diff is on this brief. The script writes
`tmp/council/<id>/voter-claude.brief.md` before launch (dry run:
`preview/voter-claude.brief.md`) and passes it on stdin. OpenAI (native)
and Grok (on `cursor-agent`, its own review prompt) on a diff do not get it. The Opus coverage leg is native and
does not get it.

The script's file, in order: the target line and how to see the diff
(`git diff HEAD`; the soft-reset worktree holds the change against the real
base); the body of `brief.md` with its header lines removed, which is where
the chair puts what the change is for and the settled decisions; a read-only
instruction (do not modify, commit, or create files; write nothing to disk;
the final message is the whole report); the honesty gate and the review
clauses, verbatim; the findings rules (`file:line` and a concrete failure,
rank most severe first, execute computable behaviour and paste the output,
end with "Checked and found clean", do not read outside the worktree). It
names no model, vendor, or family, and nothing from any other leg. If the
chair's own brief body contains one of those words, the leg fails and names
the word.

Other brief-mode legs (a revision's completeness question, a non-diff
target, a Cursor fallback you chose to run) get one brief, written under
`tmp/council/<id>/`, containing:

- The target, with the real base named for a stacked PR.
- What the change is for, in two or three sentences.
- Settled decisions. Do not relitigate them. Where the project soft-deletes,
  a soft-deleted row is expected; foreign keys and join rows pointing at
  soft-deleted records are not findings. See "What may be settled" below.
- Read-only, then verify afterward anyway.
- The honesty gate from the delegation-prompt skill, verbatim.
- Findings rules: `file:line` and a concrete failure (input or state, then
  wrong behaviour). No concrete failure means it is not a finding. Do not pad.
  Zero findings is a fine answer. Rank most severe first. Execute computable
  behaviour against adversarial inputs, not the suite's inputs, and paste the
  output. End with "Checked and found clean" and the inputs you tried. Do not
  write findings into the worktree.

### What may be settled

This applies to every brief, including the body of `brief.md` that the
default Anthropic leg reads.

- A settled item is a checked fact or a recorded human decision. Never list
  as settled a mechanism the diff itself introduces, whoever chose it. That
  mechanism is what is under review.
- "Settled" and "Out of scope" limit what the author is asked to build. They
  do not limit what gets reported.
- A finding that the chosen design lets code skip CI, auth, or validation
  still reaches the human. Mark it as touching a settled decision; do not
  hold or drop it for that reason.

On a work pull request (2026-10-08) the chair listed "Skip by branch-name prefix"
as settled. The leg on the brief missed the bypass, `decide.md` held it as
"accepted by design", and a human reviewer raised it later. No recorded
decision had chosen that mechanism.

The clean-list is how a reader tells silence from a judgment. On 2026-08-10 a
leg returned no findings where two others found a regex gap; its clean-list
named the header spellings it had tried, which did not include the failing
one.

Never tell a reviewer what another reviewer found, and never seed the brief
with what you already suspect. On a diff, independence is separate
methodologies. On a non-diff target it is separate models on one brief; say
that in the lineup.

Do not launch from the parent session with the host's review skill or an
agent pinned to Fable. That inherits the host pool (the 2026-08-10 run) and,
from Grok Build, cannot spawn Fable. If you are the chair on Grok Build, you
are not the Grok leg.

## Blinding

`scripts/blind.sh` shuffles reports into `blind/leg-A.md` and so on, writes
`blind/key.txt`, and does not print the mapping. Claude JSON contributes
`.result` only. The script redacts vendor and model names and leg paths, and
leaves a name alone inside a path or identifier (`src/grok/client.ts`).
Stylistic tells survive; that is accepted. Read the key only after
`judgment.md` exists, and only to relabel columns. Build the table, the
severities, and the verification against the blinded copies. Process checks
(model verification, exit status) use the originals and do not change how a
finding is judged.

## Findings file

The Jev gate exists to decide which findings become comments. When the findings go straight to a builder instead, as on a review of your own fix commits, the chair may skip `findings.json` and the gate and verify each finding against its reproduction (2026-10-02, the second review of [PR 6](https://github.com/rymiwe/conclave-archive/pull/6)).

The chair writes `tmp/council/<id>/findings.json`:

```json
{
  "target": "what was reviewed",
  "findings": [
    {
      "id": "f1",
      "claim": "the concrete failure",
      "file": "path/from/root",
      "line": 12,
      "cited_code": "the quoted lines",
      "evidence": "$ <exact command or script>\n<its output>, or the exact string \"no run was made\"",
      "repro_executed": false,
      "legs": {"A": "found", "B": "missed", "C": "cleared"},
      "source": {"leg": "A", "row": 0}
    }
  ]
}
```

An executed finding's `evidence` starts with the exact command or script the
chair ran, then that run's output. A script written for the repro is saved as
`repro/<finding-id>.<ext>` in the ballot directory (`repro/f1.rb`, `repro/f2.py`,
`repro/f3.sh`). The drafter attaches that file to the finding's comment. The
output alone is not enough: on one stack the commands lived only in chair
transcripts, and a posted comment could not be traced back to its run
(2026-10-05).

Before Jev, run every read-only finding that a repro script already in the
ballot directory can execute with changed inputs. Mark it executed when it
runs. A script written for one finding often covers another: on a work
pull request, a repro script executed another finding with one changed input, and that finding went to
Jev as "no run was made" (2026-10-08).

Never put the brief's framing into `evidence`. "The brief records this as the
chosen mechanism" or "out of scope per the brief" steers Jev toward hold.
Evidence is the command and its output, or "no run was made".

`source` is optional. On kind audit the chair copies the blinded leg letter and the zero-based row index from that leg's final json block. The script checks the quote on the unblinded report. Nobody reads the unblinded text. The field is omitted when the finding's own `url` and `quote` are what should be checked. `leg` is one letter A to F. `row` is a non-negative integer.

`legs` uses the blinded letters only. Values are `found` (the leg reported
it), `missed` (its clean-list never examined the area), or `cleared` (it
examined the area and judged it fine). No vendor names and no agreement
counts in the claim, the cited code, or the evidence. The judge request is
built from those fields only; `legs` is not sent.

Separate "missed it" from "disagreed with it" when you fill `legs`. Collapsing
the two is the most misleading thing this procedure can do. A native Codex
clean column is read through the clean-list inside `overall_explanation`. Its
tests still skew to the suite the team wrote (2026-09-28, a work
revision again: "75 examples, 0 failures", and the clean-list's inputs were
strings and hashes, none of them a `Time`, so the miss was a coverage gap).
An area missing from the list is unchecked. Without a clean-list, treat
silence as a coverage gap.

## Jev gate

`scripts/jev-judge.py --findings` asks one `choice` question per finding:
`post`, `hold`, or `drop`. `state` is the claim, the cited code, and the
evidence. The default floor is 0.7 (`CONCLAVE_JEV_FINDING_POST_MIN`). The
script binds it the way the Decision mode binds its rules:

| Case | Gate |
|---|---|
| Repro executed, Jev `post`, confidence at or above 0.7 | `post` |
| Repro executed, Jev `drop`, `hold`, `post` below 0.7, or confidence missing | `second look` |
| Read-only, Jev `post`, confidence at or above 0.7 | `post` |
| Read-only, Jev `drop` | `second look` (not discarded) |
| Anything else | `hold` |

`post` means eligible for a drafted comment. The chair still withholds a
read-only `post` they do not agree with, and shows both verdicts. Where Jev
and the chair disagree, list the finding with both verdicts. The script does
not post.

Jev checks that the claim follows from the cited code and evidence. An executed finding needs Jev's agreement to post; otherwise the chair reads it. A finding a test run had already disproved came back `post` at 0.51 on 2026-09-28. On the fifth run a disproved finding came back `drop` at 0.99.

No read-only finding has reached the 0.7 floor on code. In practice Jev's confidence is the sort order of the list that goes to the human.

Proposal verdicts rest on one sample (`reject` at 0.78, 2026-09-28). n=1.

Do not use Jev for scope or priority questions, such as which fixes to include. It has no ground truth for them.

`judgment.md` opens with the fixed header (kind, decision, confidence,
confidence_of, model per role, branch, PR). Next to `voter_b_model` are
`voter_b_reasoning_tokens` and `voter_b_effort`. The token count is the largest run total across the rollout files the Codex
model check selected. Each file contributes
`payload.info.total_token_usage.reasoning_output_tokens` from its last
`event_msg` whose `payload.type` is `token_count`. `last_token_usage` is one
turn and is not used. A parent rollout with no `token_count` does not compete.
The effort is `turn_context.payload.effort` from that same file. A missing
or unparseable value is `unknown`. Each leg's wall time follows its model
line: `voter_a_seconds`, `voter_b_seconds`, `voter_c_seconds`, and
`coverage_opus_seconds`, the last two only when that leg ran. Design and
mixed add `argon_model` when Argon voted, or `argon_skipped` with the
one-line reason when that leg was named and skipped. The script
takes epoch seconds before and after each leg and writes
`voter-<v>.seconds`. A failed leg still records its time. A missing value is
`unknown`. Design and mixed carry no seconds. Review writes `stack` after `pr`. See
"Stack and related PRs". Then `head_oid`, the commit the legs reviewed. `--pr` uses the PR head `gh`
reported. Refs and a whole-codebase review use the commit captured when the
worktrees were built, not a resolve after the legs finish. `--pr` cannot pin
a commit, so compare `head_oid` across runs that must review the same code. Then the convergence table: a column per leg, labelled by vendor and model
after the gate (Fable and Opus are one vendor, so their agreement is one
vote), a
Jev column (`choice` and Jev's confidence in that choice, not in the finding;
a hold or drop also shows Jev's post probability, as in `hold 0.920 (post 0.02)`), a gate column, and the resolved model
from the response. The leg columns follow the blinded leg order, so it
differs between ballots: read each ballot's header row before tallying legs
across runs (one stack's scorecard miscounted on a fixed order).
`convergence.md` is that table without the header. The
header `decision` is `comments to post` when at least one finding gates to
`post`, `human review` when none post and at least one holds or needs a
second look, or `clean` when there are no findings, followed by the counts.
`confidence` is the lowest Jev confidence among findings gated to `post`, or
`n/a` when none are. Kind audit uses this same header and adds `target`, `target_type`, `snapshot_sha256`, `target_drift`, `redaction_kept`, and `gate`. Its findings decision is `changes to draft`, `human review`, or `clean`. A proposal decision is `human review`. See "Non-diff targets: kind audit". Zero findings finish phase 3 with no Jev call. When two
runs are compared and a header ID differs between them, the comparison says so.
Design and mixed `no automatic decision` writes `options.md` beside `judgment.md`.

On a review findings run the script also writes `decide.md` beside `judgment.md`. The convergence table stays in `judgment.md`. One block per finding id, in `findings.json` order.

- The first line is the id and **what breaks, for whom**, left as `TODO(chair)`. The script does not copy the claim into that line or the how-sure line.
- **how sure** is filled from the ballot: `proven by running it` when `repro_executed` is true, `read, not run` when it is false, then the gate (`post`, `hold`, or `second look`) and the Jev confidence in the same decimals as the table. A missing confidence is `unknown`.
- **later PRs that change this file** is filled from the stack walk (see "Stack and related PRs"): the later PRs that change the finding's file, `not touched later in the stack`, `not touched in the first 20 PRs; walk truncated`, `unknown (PR #N lists 100 of M files)`, `no stack`, or `unknown`. A truncated walk that still names a later PR, or that has a capped file list, ends with `; walk truncated at 20 PRs`.
- **recommend** is `TODO(chair)` and the four answers (`post as a bug` / `post as a question` / `post as advisory` / `hold`), then a `TODO(chair)` for one line of why and a `TODO(chair)` for whether the baseline before this PR had the same problem.
- **cost to fix** is `TODO(chair)`.
- The file ends by asking for the findings to post, each with its ballot id, or none. Its example uses the ballot directory's name and the first finding's id, such as `work-pr-rerun f1`. Finding ids repeat across ballots, so a bare id can name a different finding. Zero findings writes `There is nothing to decide.` and does not ask.

The chair replaces every `TODO(chair)` before drafting comments.

Rewrites:

- **Sidecar.** Each script write also stores `decide.md.sha256`, the sha256 of that file. A file that matches the hash is unedited. A missing hash means the chair edited it.
- **Fresh run.** A fresh review or audit run moves a chair-edited `decide.md` to `decide.md.prev` and prints `note: moved decide.md to decide.md.prev`. It removes an unedited file and does not touch `.prev`.
- **Re-judge.** A re-judge keeps a filled `decide.md` only when it has no `TODO(chair)` left, its finding ids match the current findings, and every pre-filled line (`how sure`, `later PRs that change this file`) equals what the script would write now. It then prints `note: decide.md left in place; finding ids and pre-filled lines match`. Otherwise a chair edit is copied to `decide.md.prev`, replacing any older `.prev`, and the file is rewritten. An unedited file is rewritten without touching `.prev`.

The key stays in the Jev child process. `CONCLAVE_JEV_OP_REF` holds
the 1Password reference, with no default. `CONCLAVE_JEV_OP` overrides the `op` binary. When
`CONCLAVE_JEV_OP` is unset, a failed `op read` is retried with `op.exe` if
that binary is on PATH. A trailing CR is stripped. `--canned FILE` feeds a
saved response and does not read the key or call the API. Without a key there
is no automatic gate; do not invent one.

### Calibration

Do not move 0.7 without data. The first four samples are this procedure's own
prose, not code in a PR. The fifth is code. The source labels are kept: the
2026-09-28 calibration, the third run, the fourth run, and the fifth run.

**Calibration, 2026-09-28.** Two findings from a review of this procedure. A
real bug (an unset path variable) came back `post` at 0.79. A finding a test
run had already disproved (that `/review --local` rejects appended text) came
back `post` at 0.51. The floor exists because of the second one.

**Third run, 2026-09-29** (this procedure reviewing itself; the response named
Jev 1.13.0). Every executed finding came back `post` at 0.72 or higher. Two
contradictions visible on the page with no run scored `post` 0.41 and `hold`
0.43. Jev under-rates text-only contradictions; weigh the chair's read over
it for those.

**Fourth run, 2026-09-30.** Same target. Eight text-only findings, each
confirmed by reading the cited lines and by one to three legs, scored 0.12
to 0.69. None reached 0.7. The contradiction between Jev and the gate itself
scored 0.69. They went to the human, not to a post.

**Fifth run, 2026-09-30, the first on code** (a service repo,
whole codebase, 9 files, 3,990 lines; the response named Jev 1.13.0).
Fourteen distinct findings from two voters. Eight had an executed repro.
Seven of those came back `post` at 0.82 to 0.97. One, a timeout that a
run showed bounding nothing (3.03 s against a 0.3 s timeout), came back
`drop` at 0.11; the executed-posts-regardless rule is what kept it. A
finding that a run disproved came back `drop` at 0.99, against `post`
0.51 for the disproved finding of 2026-09-28. Five read-only findings
scored `post` 0.40, `hold` 0.79, `hold` 0.34, `hold` 0.84 and `drop`
0.61. None posted, so the 0.7 floor has still not been crossed by a
read-only finding on code.

**Compared with the four-leg run of 2026-09-20 on the same tree** (Fable
on the brief, Grok native, Astra native, Grok structural; its fix
commit's title says eleven fixes and its body lists fourteen). The two
voters found 8 of the 14 and missed 6. Voter B alone caught four. Voter A
alone caught one. Both caught three. Of that run's seven unfixed items,
voter A alone raised three. Two findings were new and neither was a defect
at that commit. Phase 1 took 3 minutes 22 seconds. The model IDs differ between the two runs and the comparison
must be read with that: voter A's header says `claude-opus-5-5 (lineup
said fable)`, as a built-in command with zero turns and zero subagents,
where the 2026-09-20 Anthropic leg was Fable on a brief with 20
findings; voter B was `gpt-6-astra` in both. This is the first
measurement of what dropping the Grok and structural legs costs on a
review. One run; no decision rests on it yet.

**Fable on the reviewer brief against [PR 2](https://github.com/rymiwe/conclave-archive/pull/2), 2026-09-30.** A backtest of the
new default Anthropic leg on [PR 2](https://github.com/rymiwe/conclave-archive/pull/2)'s own diff (`edcb19b..b8843ba`, 15 files,
+3199/-228), run alone from a fresh single-branch clone with `gh`
unauthenticated, so the fixes in the follow-up PR were out of reach (its 18
tool calls touched nothing outside the leg worktree). Model check `ok`, one
key `claude-fable-5-1`, 19 turns, 74,646 output tokens, 8.96 dollars, 12
minutes. The two-leg review of the same diff earlier that day (Opus native
from the Fable launch, 1.29 dollars; Astra native) produced 14 findings.
The Fable leg found 5: the cross-repo read-only hash, the env key ignored
by `--findings`, tags breaking the read-only hash, the link checker's false
positives, and the spaced `--voters` header. It missed 9: the stale
judgment on a failed rerun and the unquoted `blind.sh` arguments (Astra
only); fork PRs (both legs); `--judge` judging leftover opinions, design
runs outside a git repo, `--judge` needing the resolver, the empty Grok
model id, `dontAsk`, and the Opus-key model check (Opus only). It also
raised findings neither leg had: a failed voter's leftover output counted
as a report, a brief with no `kind:` header inferring `review` by keyword
and launching a tool-enabled whole-codebase run, an empty `--pr` becoming a
whole-codebase review, a design header naming a voter that did not vote, a
header confidence that belongs to an overridden choice, and five doc
mismatches. Six of the Opus leg's ten findings appeared in no other report.
The three methods overlapped little. One run.

**First live three-leg ballot, 2026-10-01** (toy repo, two files, two
planted bugs, default voters). Model check `ok` for all three legs: Fable
on the brief (`claude-fable-5-1`, 3 turns, 1.35 dollars, 35 s), Opus native
(`claude-opus-5-5`, built-in command, 0.35 dollars, 19 s), Astra
(`gpt-6-astra`). Read-only hash matched. All three found both planted bugs.
Five findings, all executed, all `post` at 0.92 to 1.00. Fable and Opus
found four and five; Astra found the two planted bugs only. The judgment
header carried `coverage_opus_model`, and the convergence table labelled
each column by vendor with the one-vote line.

**2026-10-01.** The same finding scored 0.75 and 0.70 on two live Jev calls, so a live rerun can send a real executed finding to `second look`. That costs the chair one read, not a lost finding.

**First run on a work PR, 2026-10-02** (4 files, +67 −10,
string trimming in an import; three legs; the response named Jev 1.13.0;
started from Grok Bot through local execution on the executor, chair
`grok-4.7-high`, `chair_model` still `unknown` in the header). Six
findings. One had an executed repro (a NUL at a cell edge, removed by
`String#strip` and left in place by the new regex) and came back `post`
0.95. Five read-only findings, all the same shape (the old cleanup still
rules in five other call sites), scored `hold` 0.36, 0.63, 0.95, 0.76 and
0.65 and went to the human. Per leg: Opus native found all six; Fable on
the brief found the executed one and cleared the other five; Astra native
found none, including the executed one. First Astra miss on code and the
first run where the coverage leg carried the ballot. One run. Verified
afterwards that the Astra leg ran: rollout model `gpt-6-astra`, 40
events, 125k input tokens, 1,800 output, a "Checked and found clean" list
with executed inputs (NBSP, ZWSP, BOM, bidi controls, none of them NUL).
Its `reasoning_output_tokens` for the whole run was 66, so the leg
reasoned almost not at all; see the effort issue on the repo.

**Stack check of that run, 2026-10-02.** The pull request is 1 of 4 in a
stack, all four approved by a reviewer before the ballot. The ballot
reviewed it against develop alone. A chair on the executor re-checked each
finding at the head of the top pull request: three of the five cleanup
findings are fixed there (one shared helper); one is answered in the
pull request's own description (stored padded values are cleaned by hand
before release); one is a zero-width space in the middle of a value, which
the baseline also kept. The NUL finding is still present at the top of the
stack. Half the findings were settled later in the same stack, which is
what [PR 10](https://github.com/rymiwe/conclave-archive/pull/10)'s stack walk now shows per finding.

**Re-run, 2026-10-02**, after [PR 10](https://github.com/rymiwe/conclave-archive/pull/10) (Astra
adversarial-input rules, `decide.md`, stack walk), [PR 11](https://github.com/rymiwe/conclave-archive/pull/11), [PR 12](https://github.com/rymiwe/conclave-archive/pull/12) and
[PR 13](https://github.com/rymiwe/conclave-archive/pull/13). Not a clean comparison: the author force-pushed the pull request
between the runs, so the head commit changed.

- Six findings again, none gated to post. The NUL finding was `post`
  0.95 on the first run and `post` 0.64 here, under `post_min` 0.7, so
  it went to second look. Two other executed findings, three read-only.
- Per leg: Opus native found all six again. Fable on the brief found
  the NUL finding only and cleared two executed findings (a cell padded
  with NBSP, a header with an inner space-like character).
  Astra native found none again; it cleared three, including the
  executed NUL finding and the executed header finding.
- Astra `voter_b_reasoning_tokens` 451 at `voter_b_effort` high, against
  66 on the first run. The rules raised reasoning about sevenfold and
  did not raise finds. Wall time: Fable 210 s, Opus native 121 s,
  Astra 117 s.
- The stack line named a later pull request on every finding, and the
  chair cited it in each recommendation. The chair recommended hold on
  all six. The human posted nothing: the stack was approved, and the
  one regression (a NUL at a cell edge) needs a file a spreadsheet
  export does not write.

Two runs on one PR: the coverage leg carried both ballots, and Astra
found nothing on either. That is n=2 on one diff, so the roster stays
as it is. It is evidence for the roster experiments in
`docs/routing.md` once a few more real reviews have run.

**Stack run, 2026-10-03 to 2026-10-05** (16 per-PR ballots plus one
whole-stack ballot, Grok chair on Cursor; scorecard on the executor,
cross-checked by Grok).

- Per leg, over 114 findings (found / found by that leg alone / found and
  posting): Fable 99 / 67 / 69, Opus native 31 / 11 / 17, Astra 25 / 4 / 19.
- Caveats: the legs differ in method (Fable on the reviewer brief, Opus
  and Astra native). Fable ran on the Claude subscription for the earlier ballots and the stack
  ballot, and on Cursor (`claude-fable-5-1-thinking-xhigh`) for the later
  ballots after the 95% switch. Two ballots reran Opus after the Claude subscription session
  limit.
- Gate: reconcile at the stack top marked 76 to post. Jev had gated 61 of
  those to post; reconcile upgraded 15 that Jev held or sent to second
  look. Every one of the 61 had an executed repro.
- Cut: the Jev floor (keeping 4 of the 15 that had an executed repro),
  dedupe of whole-stack findings against per-PR copies, and Advisory
  folded per PR left 58 findings and 3 folded notes.
- Severity, Ryan's rule: High is a wrong or missing official record, a
  status shown wrongly, or data that cannot be repaired. Medium
  blocks a user from completing the main action with nothing wrong
  recorded. Low is dead code, a missing test, a misleading seed or sample,
  or developer-only. High and Medium went inline; Low went to each
  review's summary checklist. Six findings fit no rule and were placed
  Medium.
- Posted 2026-10-05 as 16 COMMENT reviews: 46 inline comments, 4 in the
  top pull request's body (lines outside that diff), 11 checklist items.
- Contrast: on the first work pull request Opus native found 6 of 6, Fable 1, Astra 0.
  On the route review Opus found 10 of 12, Fable 9, Astra 3.

## Consolidation

After the gate:

- Two vendors reaching the same finding by separate routes is the strong
  signal. It is not a score, and it is not the rank (see Ranking).
  Opus native is a coverage leg on review kind; its agreement with Fable is one vote.
- A leg that ran the code outranks legs that only read it, including a
  majority of them. A passing suite proves the cases the suite already
  imagined. When a finding touches parsing, dates, regexes, or arithmetic,
  run the inputs yourself before believing a clean list.
- Adopt the best-argued severity, and say whose reasoning you took. Do not
  average. Grok's `bug` / `suggestion` / `nit` is not another leg's
  HIGH / MEDIUM / LOW. Say that you mapped them.
- Resolve every `file:line` before relaying it. `wc -l` the file and print
  the cited line. If the line does not hold the claim, grep for the code and
  relay the corrected citation. A bad line number makes a real defect look
  invented.
- Run a proposed regex, condition, or one-liner before relaying it.
- Answer the question before you ask it. A finding that asks the author to
  decide is legitimate only after you have failed to settle it from the code
  and from recorded decisions. On a work pull request, that settled two of six
  drafted comments, split a third, and killed a fourth that claimed a sibling
  screen had a failure shield it does not have. A fifth became a spike and the
  team answered it in one line: the source called the mechanism temporary,
  which was a live plan, not a stale note.
- Drop what the PR already says. Already raised and answered, already fixed
  in a later commit, or already ticketed is not a finding. Say so once.
- Report process failures beside the findings: a cancelled-and-resumed run,
  a vendor that never started, a dirty target tree, a model check that failed.

## Ranking

Order drafted comments by this sort, stopping at the first level that
separates two findings:

1. **Blast radius.** Silent wrong data, then lost work, then a suppressed
   warning, then cosmetic. Ask what the user ends up believing or losing.
2. **Verification tier.** Executed beats verified-against-source, as a
   tiebreak inside a radius band, never across one. A reproduced cosmetic bug
   does not outrank an unreproduced data-loss bug.
3. **Fix cost.** Cheap first among equals.

Convergence is not in the sort. Agreement says whether a finding survives,
which the table already used. It says nothing about whether the finding
matters. Measured 2026-08-11 on a work pull request: the important finding (a
timeout permanently failing an import) came from one leg, and the
most-replicated one (a timestamp tie hiding a banner) was
nearly cosmetic. Ranking by agreement would have inverted them.

Do not combine severities into a number. The scales are not commensurable,
and a weighted number hides the judgment.

## Drafting comments

Draft them every time, in the words they would go out in, after the gate.
A finding that is held stays in the report and does not become a comment.

1. **Inline comments.** One per finding that anchors to lines. Give `file:line`,
   then the body. Most findings belong here.
2. **One review-level comment** for what cannot attach to a line. A few
   sentences. If nothing qualifies, skip it. When the pull request has an
   automated risk rating, this summary states the rating. A rating is a
   `risk:<level>` label, or the explicit rating line of a risk-review check
   or of a comment that opens with a risk-review heading. A label wins when
   one is present. Otherwise the latest of those comments and checks wins.
   A level mentioned only in prose is not a rating. If the rating is above
   low, the summary names what drives it and suggests concrete changes that
   would bring it to low. When there is no rating, the summary says nothing
   about risk. The summary and its bullets have no label.
3. **Replies** to an existing review comment. These are not new line comments.

Write as the user.

On someone else's pull request, a new inline comment starts with one label:
**Requested:**, **Advisory:**, or **Informational:**. **Question:** is not
drafted. It still counts as a label, so an older draft that uses it is
labeled, a reply that still carries it is stripped, and a summary that
opens with it is stripped by the drafter and refused by the lint. A reply
carries no label, on anyone's pull request. The review summary does not
open with a label, on anyone's pull request.

A summary opens with a label when, after lines that are empty or only
whitespace, the first remaining line starts with one of those bold labels.
Spaces and tabs before the label on that line do not count. A label later
on the line, or on a later line, does not open the summary. A label inside
a fenced script does not count. `post-review.py` appends a comment that
fell outside the diff after the summary. That block starts with the heading
**Outside this diff**, which is not one of the four labels, and the block
is appended, so the heading and the moved comment's own label do not make
the summary open with a label. When the summary is empty, that heading is
the first line, and it is still not a comment label.

Labels go only on new inline comments. A bullet in the summary has no
label: `SUMMARY_BULLETS_MAY_HAVE_LABELS` in `scripts/comment_draft.py` is
False (decided 2026-10-23). The drafter and
`scripts/check-comment-labels.py` both read that constant. The drafter
strips a bullet's label and the lint flags the line. A
bullet is a line that, after optional indentation, starts with `-`, `*`,
`+`, or a number (`1.` or `1)`), then whitespace, then the label.

On your own pull request, write no labels. The lint does not enforce that
on an inline comment: it does not flag a label there, and it does not
require one. It does flag a summary that opens with a label, and a labeled
reply, on every pull request.

`scripts/comment_draft.py` strips every label from a reply, strips a label
that opens the summary, and refuses to draft a new inline comment on
someone else's pull request that does not start with a label. A repro file
is attached there, except on a reply.

When the pull request author and the reviewer are not both known, an unlabeled
new inline comment is refused rather than treated as your own.

### Repro script

When a finding was verified by running a throwaway script, save that script in
the ballot directory as `repro/<finding-id>.<ext>`. One file per finding. The
extension is the script's own (`.rb`, `.py`, `.sh`). The drafted comment for
that finding gets one collapsed block:

````html
<details><summary>f1.rb</summary>

```ruby
# the full script
```

</details>
````

The summary is the filename. Two findings that share a filename, from two
ballots on one review, each get a block. An HTML comment above the details
tag names the ballot and the filename so the second is not dropped. The fence language is the usual name for that
extension (`rb` → `ruby`, `py` → `python`, `sh` → `bash`); an extension with
no usual name is the extension itself. The fence is longer than any backtick
run in the script, so the script cannot close it. A finding with no file gets
no block. A reply does not get the block.

GitHub's review-comment limit is 65536 characters. A script that does not fit
in the comment is cut inside the fence, and a note after the fence says how
many bytes were kept and names `repro/<filename>`. The comment prose is not
cut. When the prose leaves no room for that note, posting stops and names the
comment and the file instead of sending the comment without the script. A new
inline comment whose prose, without its repro block, is longer than 65536
characters is refused, and so is a reply over that limit. When comments are
moved into the review body, a body finding's block is rendered again from
`repro/<filename>` into the room they leave. A comment
moved into the review body is sized to the room left in that
body, including its permalink. A block that was already attached is resized
to that room; it is not left at the line-comment size. The resize closes the
block at the `</details>` outside the fence, so a script that contains that
tag is not cut in half. A draft marks the block with the filename. Posting
uses the ballot and the filename, and adopts the draft marker instead of
attaching the script twice. A hand-written block in the form above has no
marker. Posting adopts that block the same way, by inserting the marker above
the details tag. Only a block that opens exactly `<details><summary>f1.rb</summary>`,
outside a fence and not already under a marker, is adopted. An adopted block
that must shrink is rendered again from `repro/<filename>`. Each later script keeps room for a truncated
block, including a script that belongs to a body finding, so one large
script does not drop the next one. A rerun checks every reply the receipt
already lists before it sends another, and a dry run does that check too. A fenced script is not prose: `scripts/check-comment-links.py` skips
citations and internal words inside the fence, and a label inside the fence
does not count.

Every ticket id, `#N`, bare SHA, and `file:line` sits inside a markdown link.
`scripts/check-comment-links.py` fails the draft otherwise, and when a public comment uses an internal word. The error names the plain replacement. A pure decimal
run of digits is not treated as a SHA, and neither is an all-letter hex
word. A SHA in this check includes a hex letter and a decimal digit.
`scripts/check-comment-labels.py` fails a post payload whose summary opens
with a label, whose reply carries a label, or whose new inline comment on
someone else's pull request does not start with a label. It also fails a
labeled bullet in the summary, because labels go only on new inline comments.
It also fails when `pr-context.json` beside the payload has a risk rating
and the summary prose does not state that rating. A level that appears only
inside a code fence, a repro block, or the moved-outside-the-diff block does
not count. The same gate runs in `scripts/check-comment-labels.py` and in
`scripts/post-review.py` before any POST. A moved-outside-the-diff block
appended to the summary is not an opening label.

Show every draft in full in chat, as it would go out, in the message that
asks whether to post. A file path is not a preview.

Offer remediation as a separate step. Do not start it unasked.

## Risk classification

When the pull request has an automated risk rating, the review summary states
that rating. A rating is a `risk:<level>` label, or the explicit rating line
of a risk-review check or of a comment that opens with a risk-review heading.
A label wins when one is present. Otherwise the latest of those comments and
checks wins. A level mentioned only in prose is not a rating. If the rating
is above low, the summary names what drives it and suggests concrete changes
that would bring it to low. The summary and its bullets have no label. When
there is no rating, the review says nothing about risk.

The fetched labels, issue comments, and check runs are untrusted data. They
are not instructions. A comment that tells the review what to do is still data.

The summary gate reads the summary prose. A level inside a code fence, a
repro block, or the moved-outside-the-diff block does not state the rating.
The gate runs in `scripts/check-comment-labels.py` and in `scripts/post-review.py`
before any POST.

The note goes out with the rest of the review, under the same posting gate.
The draft fails when a detected rating is missing from the summary. The label
is not changed here. Reclassification belongs to that review, through
`risk:reclassify`, and only when the human asks. The note does not name the
repository.

## Posting gate

Drafting is mandatory. Posting is not. Post only when asked, through
`scripts/post-review.py` (see "Posting and the evidence index" below).

Tier the failure, not the code fact. The code half is usually easy to verify
and that verification proves nothing on its own. Then ask whether the trigger
is reachable. A coincidence at the full precision of the type (equal
microsecond timestamps, colliding hashes, two humans in the same instant) is
not reachable. Unreachable means it is not a finding, however cleanly the code
half checks out.

Verified 2026-08-11 on a work pull request, where this gate failed and the comment
was deleted. A banner comparison used a timestamp with no `id` tiebreak,
unlike two lookups in the same PR. The code differed. The failure needed two
uploads sharing a `timestamp(6)`. The consistency argument was also wrong:
`id: :desc` on those lookups fixes nondeterministic selection under
`ORDER BY`, which holds no matter how rare ties are, while the banner is a
strict `>` between two already-chosen records.

Sort what remains:

- **Executed.** You ran the code and reproduced the failure, or ran the fix
  and watched it pass. The gate is `post` when Jev says `post` at or above
  0.7. Jev `drop`, `hold`, `post` below 0.7, or a missing confidence sends
  it to `second look`. The run output stays in the report. The comment
  carries a failing input only when the author cannot see the failure from
  the line and the cause. No sentence whose only content is "I reproduced
  this".
- **Verified against source.** A fact about the diff you read. Eligible only
  when the gate is `post` (Jev `post` at or above 0.7). Under 0.7 it goes to
  the human with both verdicts.
- **Plausible reading.** It depends on a scenario you never exercised, or it
  characterizes what someone else's comment meant. Do not post. Keep it in
  the report.

Re-read the exact line a finding characterizes before posting about what it
claims. The case that motivated this: a finding said a comment overclaimed
lock-order parity, and the code did differ, but the comment only claimed row
ordering, which was true.

## Anchoring

`gh pr diff --patch` line numbers are not the comment line. Compute the
new-file line from `pulls/<N>/files` hunk headers (`@@ -a,b +c,d @@`), counting
context and added lines, and confirm the source text at that line before
posting. Post with `commit_id` set to the verified head, `side: "RIGHT"`, and
the resolved line. Build the JSON in a script; comment bodies carry backticks
and fences that shells mangle.

Read every comment back, especially escaped regexes. A comment on the wrong
line or with a mangled code block is worse than one never posted. Delete and
repost.

### Posting and the evidence index

Posting needs the human's go, every time. No kind posts on its own, and no
script calls the posting helper. When the human says post:

1. Write `tmp/council/<id>/post-<pr>.json`: `commit_id` (the verified head),
   `body` (the summary), `comments` (each with `finding`, `path`, `line`,
   `body`, and optionally `severity` and `expect`, text that must be on that
   line), and `body_findings` for findings carried in the summary. A finding
   id is `f2` for the payload's `ballot` (default `<id>`) or `<ballot>:f2`.
   A reply is a comment with `in_reply_to` set to the existing comment's id.
   It has a body and no line. `pr_author` and `reviewer` are GitHub logins.
   When they are omitted, `post-review.py` reads the author from the pull
   request and the reviewer from `gh api user`, and it reads the reviewer
   only when a new inline comment does not start with a label. The format
   is in the header of `scripts/post-review.py`.
2. Run `scripts/post-review.py <id> <pr> --dry-run`. It checks the PR head
   against `commit_id`, checks each line against the `pulls/<pr>/files`
   hunks, moves a line outside the diff into the review body with a
   permalink (as on one stacked pull request), checks `expect` on that line against the
   file at `commit_id`, attaches `repro/<finding-id>.<ext>` from that finding's
   ballot directory when the file is there, runs the link check and the label
   check on every body, and writes `post-<pr>.request.json`. A reply is recorded
   under `replies` and is not a new line comment. It posts nothing.
3. Read the plan, then run the same command without `--dry-run`. It posts
   one review (`side: RIGHT`, `commit_id`, line), writes
   `post-<pr>.pending.json` with the review id, then reads the comments back.
   The pending file is written before that read. A failed read-back still
   leaves the receipt, and a rerun looks the review up on GitHub and refuses
   to post again. A review with the same commit and body is refused too.
   A reply is posted after that read, to `pulls/<pr>/comments/<id>/replies`,
   and is not a new line on the review. Two replies to the same comment are
   refused. The pending receipt stores the review request that was posted and
   lists each reply that succeeded, with the text that was posted. If a reply
   POST fails, the receipt stays, and a rerun sends the replies it does not
   yet list. It does not post the review again. A rerun whose commit, review
   body, or inline comments differ from that stored request stops, and it
   does not record the new payload as posted. A rerun also stops when an
   already posted reply's text changed.
   After the replies are sent it writes `tmp/council/<id>/posted.json`:
   the review id and url, and each comment's url keyed by
   `<ballot>:<finding>`. A PR already in `posted.json` is refused.
4. Run `scripts/evidence-index.py <id> [<id> ...]`. It writes
   `tmp/council/<first id>/evidence-index.md`: one row per posted finding
   with the comment link, severity, claim, evidence, and the chair
   transcripts under `*/logs/<ballot>-chair-*.jsonl`. A finding missing from
   its `findings.json` is a named gap and exit 1. A findings file that is not
   a JSON object is the same, and the gap names that.

Both files stay under `tmp/council/`, which is gitignored. Ballot evidence
never leaves the machine that ran the ballot.

## Process failures to report

A resumed Grok leg, a voter that never started, a model check that failed, a
read-only mismatch, a diffstat that did not match the PR, a dedup source that
was skipped. Say which engine ran. Do not drop a vendor without saying so.

## Procedure notes

- A non-diff snapshot must be a git repo, or the brief must say it is not. On
  2026-09-30 leg snapshots built with `git archive` made `git ls-files` fail
  in each one. The Astra leg's honesty gate treated that as a blocker and
  stopped after three findings, before any web check.
- Run the Jev gate before reading `blind/key.txt`. On the same run the chair
  read the key first. Jev's state carried no leg labels, so its verdicts
  stayed blind, but the order was wrong.
- Every leg loads its CLI's global harness (`~/.claude/CLAUDE.md` for Claude
  and, through its loader, Grok; `~/.codex/AGENTS.md` for Codex). When the
  target is the routing rules, the voters read the rules they are judging.
  On 2026-09-30 a toy-repo leg's report cited the user-level CLAUDE.md. Say
  so in the lineup, or run such a review with the harness off. A clean home
  directory for legs is an open item, not implemented.
- The chair's own process checks can break blinding. On the 2026-09-30 toy
  run the chair listed voter file sizes and a log tail, and the sizes
  matched the blinded copies. Before `judgment.md` exists, read only the
  script's output, `model-check.md`, `readonly.txt`, and `blind/leg-*.md`.
  `CLAUDE.md` as a file name also survives blinding and identifies a leg;
  that is accepted.
- Stopping `conclave.sh` with SIGINT, SIGTERM, or SIGHUP stops the legs and
  their CLIs' children before the EXIT trap removes the worktrees (TERM, then
  KILL after 5 seconds). Each leg runs in its own process group, so this also
  holds after `pkill -f "conclave.sh <id>"`, which kills the leg shells too.
  That pattern also matches the shell that launched the run (a tool's
  `bash -c '... conclave.sh <id> ...'`), so a chair can kill its own shell
  with it (2026-10-02, both [PR 11](https://github.com/rymiwe/conclave-archive/pull/11) subagents). Use `pgrep -f 'conclave[.]sh
  <id>'`, drop your own shell's pid and group, and `kill -TERM` the rest.
  `council.sh` execs `conclave.sh`, so the process image is `conclave.sh`
  either way.
  Further signals are ignored until the legs are stopped. A signal during a
  foreground step (the seat check, a fetch, the judge call) takes effect when
  that step ends; no leg runs then. A process that leaves its leg's group
  (`setsid`) is caught only while its parent lives. SIGKILL cannot be
  trapped: after `kill -9`, `pgrep -af "conclave.sh <id>"` lists the leg
  shells still running, and `kill -TERM -- -<pid>` stops one leg with its CLI.

## Dropped on purpose

From the port brief:

- The Cursor fallback for a Grok voter (Drop, first bullet). The lineup
  seats Grok unless the ballot excludes xAI, and not on a capped pool.
  Grok runs on `cursor-agent`, or on its own CLI with `CONCLAVE_GROK_NATIVE=1`.
- Opus native when Fable is ahead of pace (Drop, second bullet). Superseded
  2026-10-01: Opus native is a coverage leg on review kind; its agreement
  with Fable is one vote. Cursor Fable only when the Claude subscription is session-limited, by
  the chair's flag or the stack runner's switch.
- Compound-engineering and the Grok structural pass (Drop, third bullet: keep
  as flags if cheap, else drop). They are separate methodologies, separate
  worktrees, and separate collection, and the structural pass is not a
  correctness column. Not cheap, so dropped. What they were for, so the dates
  are not lost: the structural pass is a maintainability audit with no
  correctness category, and on a service repo (2026-09-20) it produced the only
  finding that explained why several correctness bugs existed. CE dispatches
  many specialists; measured 2026-08-11 on a work pull request its local roster produced two
  findings no other leg had, and its cross-model pass was turned off
  (`CROSS_MODEL_MAX_PEERS=0`) because an empty peer list does not disable it
  and the chain can select a banned model. CE reads PR threads, so its
  agreement is corroboration only when the finding is not already in those
  threads. One blind comparison missed two known concrete bugs and found two
  design-level races. None of that is a current leg.
- Batch mode over a Linear board (Drop, fourth bullet). Dropped. The rule
  that survives inside the PR path: a stacked PR is reviewed against its own
  base, not against the default branch.
