# Preflight and stage hooks

Preflight finds launch problems at minute 0, before any voter runs. It prints one list of what the human must fix. A stage hook lets the operator run a command when a stage ends: send a notice, write a log line, or wake a chair.

Code: `scripts/preflight.py` and `scripts/stage_hook.py`. Tests: `scripts/test-preflight.py` and `scripts/test-stack-run.py`.

## Where preflight runs

- **`scripts/conclave.sh`, kind review.** In the real launch, after the target is resolved and before the ballot's earlier voter files are cleared. `--dry-run` runs the same preflight in plan mode (see "Dry run" below). Design, mixed, audit, and ask mode run none. `--findings`, `--judge`, and `--verdicts` launch no voters and run none.
- **`scripts/stack-run.py`.** Once, before any chair or ballot starts. `--dry-run` and `--reconcile-only` run none; stack-run's dry run only prints the wave plan.

## Checks

Every check runs, even when an earlier one fails, so the list is complete. Each check gives blocking items or warnings.

**Review target.**

- `--pr N`: `gh pr view N` must succeed in the target repo. A PR that does not resolve is blocking. A closed or merged PR is a warning.
- `--base` and `--head`: each ref must resolve with `git rev-parse --verify <ref>^{commit}` in the target repo. A ref that does not resolve is blocking. `--head` defaults to `HEAD`.
- `--whole` has no target to check.
- stack-run checks every PR it was given (`--pr`, or each of `--prs`) with `gh pr view`. It then walks the stack. When the whole-stack ballot will run, it reads the top head oid, fetches `pull/<n>/head`, and checks the two refs that ballot passes: `origin/<bottom base>` and the top head oid. Per-PR ballots pass `--pr`, so their `conclave.sh` checks the PR again and fetches its refs itself.

**Seats.** The seat-usage route a review seats from: `seat-usage --route lineup --json --kind review`, read by `scripts/lineup.py`. stack-run's per-wave pre-check calls the same function (`seat_precheck` in `scripts/preflight.py`).

- Blocking: the route asks for a human, gives fewer than two different voters, or prints JSON that cannot be read. The item names the route's reason and skipped codes.
- Warning: `seat-usage` is missing, exits nonzero, prints nothing, or does not answer within 25 seconds. The launch goes on.
- `CONCLAVE_WAVE_PRECHECK=0` skips this check, here and before each wave.
- `conclave.sh` skips it when `--voters` is given, because `--voters` wins over the route.
- stack-run skips it when no scheduled ballot needs voters: every one is done, needs decide, needs findings, or blocked. A resume that only reuses ballots, runs chair steps, or reconciles is not stopped by a capped route. The per-wave pre-check exempts the same ballots.
- stack-run with `--wave-wait S` turns capped seats into a warning. The first wave then waits for seats as before.
- A blocked seat item lists the options and a default. `conclave.sh`: wait for the capped seat to reset and rerun (the default), pass `--voters` with seats that have quota, or set `CONCLAVE_PREFLIGHT=0` to run the old fixed default pair. stack-run: wait for the reset and rerun (the default), pass `--wave-wait SECONDS` so the run waits for seats before the first wave, or set `CONCLAVE_PREFLIGHT=0` (the per-wave pre-check still stops a capped wave).

Before preflight, a review with an asking route fell back to the fixed default pair. It now stops at minute 0 instead. `CONCLAVE_PREFLIGHT=0` or `--voters` keeps the old launch.

**Repo prerequisites.** An optional file in the target repo, `.conclave/preflight.json`, read from a committed ref, never the working tree. It names what a reviewer needs to run a repro: a test database, installed dependencies, a private registry.

```json
{"version": 1,
 "checks": [
   {"name": "test db reachable", "command": ["pg_isready", "-h", "localhost"], "timeout": 10, "needs": "start the test database (docker compose up -d db)"},
   {"name": "deps installed", "command": ["bundle", "check"], "timeout": 30, "needs": "run bundle install", "severity": "blocking"},
   {"name": "private registry", "url": "https://registry.example.com/healthz", "expect_status": 200, "timeout": 10, "needs": "connect the VPN", "severity": "warning"}
 ]}
```

- The file is read with `git show --no-textconv <ref>:.conclave/preflight.json`, never from the working tree. For `--pr` and for stack-run the ref is `origin/<default branch>`: `gh repo view --json defaultBranchRef` names the branch, and `origin/HEAD` is the fallback. It is never the PR's base branch. On a stacked PR the base is the lower PR's head, which that PR's author controls, so reading it would let the lower PR add a command that runs before any voter. When neither names a default branch that resolves, the file is skipped and one warning says so; the PR base is never the fallback. For refs (`--base`), the ref is the `--base` the operator passed. An untracked file, an uncommitted edit, and any PR branch's copy are never read, so a PR cannot add a command to its own review or to the review of a PR stacked on it. With no ref to read from, the file is skipped and one warning says so. The file must be a regular file of at most 64 KB. A symlink is refused.
- `version` must be `1`. `checks` is a list.
- `name` is required. It is the label in the report.
- `needs` is required. It is the text that tells the human what to do.
- Each check has exactly one of `command` or `url`.
- `command` is an argv list, never a shell string. It runs with the target repo as its working directory, stdin from `/dev/null`, and a scrubbed environment: `PATH` (absolute entries only), `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `TZ`, and `USER`, and nothing else. Tokens, keys, and `OP_*` variables in the caller's environment never reach the child. Output is discarded.
- `command[0]` must be a tool on `PATH` or an absolute path. A path that points into the checkout (`./run.sh`, `bin/check`) is rejected.
- A shell or wrapper is rejected as `command[0]`: `sh`, `bash`, `zsh`, `dash`, `env`, `xargs`, `nohup`, `timeout`, `sudo`, `find`, `awk`, and similar. An interpreter (`python`, `node`, `perl`, `ruby`, `php`) is rejected when an inline-code flag such as `-c`, `-e`, `-r`, or `--eval` appears before any other argument. The scan stops at the first argument without a leading dash, so a flag after a value-taking option is not caught: `python3 -W ignore -c ...` and `node --require fs -e ...` are accepted. `python3 check.py` and `python3 -m pytest` are fine.
- `url` gets one HTTP GET. It must be `http` or `https` with a host and no user name or password. Redirects are not followed, so a 3xx is the answer. `expect_status` defaults to 200. The body is never read.
- `timeout` is in seconds. Default 15, at most 120.
- `severity` is `blocking` (the default) or `warning`.
- A check passes on exit 0, or on the expected status. It fails on a nonzero exit, a timeout, a missing or non-executable binary, a connection error, or another status.
- No file: these checks are skipped, with no message.
- A file that is not valid JSON, has an unknown key, or does not match this format is one blocking item: `bad .conclave/preflight.json: <reason>`.

### What this does not stop

The deny list is defense in depth, not a jail. A declaration comes from the default branch (or the operator's `--base`), so whoever can push there is trusted. A tool they name still runs in the target checkout, and the checkout may be on the PR branch. A tool that reads project files there (`bundle`, `make`, `npm`, `pytest`) can run PR code. Prefer checks that only probe (`pg_isready`, `bundle check`) and keep them off tools that execute the project.

Preflight cannot tell a work repo from a personal one reliably and cheaply. It restricts the checks instead of guessing: URL checks are http(s) only and carry no credentials, so a work repo's declaration cannot send a token to a host. To skip URL checks, leave `url` out of the declaration.

## Dry run

`conclave.sh --dry-run` on a review runs preflight in plan mode (`scripts/preflight.py --plan`) after it prints the lineup. It runs the same checks and exits with the same code, but it writes the list under `tmp/council/<id>/preview/needs-human.md`, leaves any real `needs-human.md` alone, and fires no stage event. When a real launch would stop, the dry run prints the list, says `a real launch would stop here with exit 3`, and exits 3. Read the lineup above that line as the plan only when the dry run exits 0. `CONCLAVE_PREFLIGHT=0` skips the plan check too.

## What is never printed

The report shows only the check name, the `needs` text, and a short reason: `exit 1`, `timeout after 10s`, `not found`, `not executable`, `status 503, wanted 200`, or `connection failed`. It never shows command output, environment values, headers, or credentials. A URL is shown as scheme, host, port, and path. Userinfo, query, and fragment are dropped. A `gh` failure shows its exit code, not its output.

## The list

Preflight prints one list on stdout and writes the same text to `needs-human.md` in the run directory. For `conclave.sh` that is `tmp/council/<id>/`. For stack-run it is the stack directory, `tmp/council/<stack-id>/`.

```markdown
# Needs from human

## Blocking

1. ref feature-x does not resolve to a commit in /path/to/repo. Do: fetch it (git fetch origin) or fix --base/--head
2. test db reachable: exit 2. Do: start the test database (docker compose up -d db)

## Warnings

1. seat check inconclusive: seat-usage exited 1. Do: nothing; the run proceeds without a seat check
```

A section with no items reads `(none)`. With nothing blocking and nothing to warn, preflight prints `preflight: ok`, writes no list, and removes a list left by an earlier run.

When anything is blocking, preflight exits 3 before any voter, chair, or ballot starts. Nothing is partly launched. `conclave.sh` and stack-run exit 3 too. Warnings alone do not stop the launch. Exit 3 means only "preflight blocked". Other failures keep their own codes.

## Turning it off

`CONCLAVE_PREFLIGHT=0` (`COUNCIL_PREFLIGHT=0` is the alias) skips every preflight check, in both launch paths. It is for tests and for an escape when a check is wrong.

## stack-run hands off to conclave.sh

stack-run runs the seat and repo checks once. It then sets `CONCLAVE_PREFLIGHT_DONE=1` on each phase 1 `conclave.sh` process it starts, and on nothing else. Its own environment does not change, and chair processes do not get it. `conclave.sh` with that flag checks only its own target (`gh pr view`, or the two refs), which is cheap. With `CONCLAVE_PREFLIGHT=0`, stack-run runs no preflight and sets no flag.

## Stage hook

`CONCLAVE_STAGE_HOOK` (`COUNCIL_STAGE_HOOK` is the alias) is a command line. It is split like a shell would split words (`shlex`), and never run through a shell. Unset or empty, nothing runs and nothing is spawned.

On each event the hook runs as:

```text
<hook argv...> <event> <payload-json>
```

The event name and one JSON object are the last two arguments. The same two values are in the environment as `CONCLAVE_STAGE_EVENT` and `CONCLAVE_STAGE_PAYLOAD`. stdin is `/dev/null`. The hook's stdout and stderr are discarded.

`CONCLAVE_STAGE_HOOK_TIMEOUT` sets the timeout in seconds. Default 30. A hook still running then is killed, with its process group.

A hook that fails never breaks the run. That covers a missing or non-executable hook, a nonzero exit, a timeout, and an unparseable command line. One line goes to stderr, `stage-hook: <event> failed: <reason>`, and the same line with a timestamp is appended to `stage-hook.log` in the run directory. The hook command is never logged, because it may hold a token. An event name the emitter does not know is logged as `ignored: unknown event` and does nothing.

Shell callers use `python3 scripts/stage_hook.py emit <event> --dir <run dir> --json '<payload>'`. `conclave.sh` checks the variable first and starts no process when it is unset. Python callers use `stage_hook.emit(event, payload, dir)`.

### Payload

Every payload has `event`, `ts` (UTC, ISO 8601, ending in `Z`), and `dir` (the run directory). It has `ballot` or `stack_id` when known. A payload holds paths, ids, and counts only: never environment values, secrets, model or vendor names, comment text, or file contents. An outcome is a class (`ok`, `reused`, `failed`, `not started`), never the error text.

### Events

| Event | Fires | Extra fields |
|---|---|---|
| `preflight_failed` | Preflight blocked, in either launch path, after `needs-human.md` is written. | `needs_file`, `blocking`, `warnings`; `ballot` from `conclave.sh`, `stack_id` from stack-run |
| `holistic_done` | stack-run, when the whole-stack wave returns, whether it finished, failed, or did not start. | `stack_id`, `ballot`, `outcome` |
| `ballot_started` | stack-run, when a per-PR ballot takes a slot in the rolling window, after its pre-check passed and the usage cap allowed it. Each ballot is its own wave in `run.json`, so `wave` matches that record; the first per-PR ballot is wave 2 when the whole-stack ballot ran. A ballot that does not start fires neither event. | `stack_id`, `wave`, `ballot`, `pr` |
| `ballot_done` | stack-run, when that ballot finishes, before its slot goes to the next one. Always after the same ballot's `ballot_started`. Ballots overlap, so events from different ballots interleave. | `stack_id`, `wave`, `ballot`, `pr`, `outcome` |
| `repros_done` | Reserved. A later repro stage will emit it. Nothing emits it yet; the emitter already accepts the name. | (none yet) |
| `post_plan_ready` | `conclave.sh --findings`, at the end of the review findings gate, once per run, when `judgment.md` binds `comments to post` (at least one finding gated to post). The chair drafts one comment for each of those findings. | `ballot`, `drafted` (findings gated to post), `decision` (`comments to post`) |
| `stalled` | stack-run, when a chair subprocess passes `--chair-timeout`. | `stack_id`, `ballot` (or the chair label), `step`, `seconds` |

`post_plan_ready`: master has no post plan file yet. The call sits in `run_findings_mode` in `scripts/conclave.sh` (`stage_post_plan_ready`). A change that adds a post plan should keep that call, or move it to where the post plan is written.

`stalled` fires on chair timeout only. There is no no-progress watcher, and voter legs have no timeout of their own, so nothing fires for a slow voter.
