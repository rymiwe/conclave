# Executor setup

Goal: make one machine the executor, where all repo work runs, reachable
from Grok Bot through the desktop app's local execution. Minimal harness:
the vendor CLIs, their logins, this repo, and nothing copied from another
machine. See `docs/routing.md` for why.

Report failures verbatim. A step that does not work is a finding, not a
reason to improvise around it.

## 1. Base

- WSL2 with a current Ubuntu. `git`, `gh`, `jq`, `python3`, `node` (for an
  npx Codex alpha, when no stable Codex at or above 0.159.3 is on PATH),
  `perl` (the blinding script uses it).
- `gh auth login` with OAuth. Clone over HTTPS.
- 1Password: store the Jev key in a vault item and export its reference as
  `CONCLAVE_JEV_OP_REF` (a 1Password secret reference). There is no
  default. The script reads the reference with `op`. When
  `CONCLAVE_JEV_OP` is unset, a failed read is retried with `op.exe` if
  that binary is on PATH. Verify:
  `op read "$CONCLAVE_JEV_OP_REF" | wc -c`
  prints a nonzero count. Until this works the Conclave has no Jev
  decision.

## 2. CLIs and logins

All logins are device-auth: post the URL and code, never a token.

| CLI | Install | Login | Verify |
|---|---|---|---|
| `claude` | npm global | `claude` then `/login` (Opus on the Claude subscription) | Fable does not use this CLI. Opus does, when the route names it |
| `codex` | Put a stable `codex` at or above 0.159.3 on PATH, or cache an alpha with `npx @openai/codex@alpha --version`. `scripts/resolve-models.sh` takes every `codex` on PATH and every npx-cached binary. The version is the token after `codex-cli ` on the `--version` line. Stable means exactly `N.N.N`. Alpha means `N.N.N-alpha...`. A `-beta` or `-rc` build is rejected. It picks the newest stable at or above 0.159.3 (the first stable verified to run Astra, on the executor, 2026-10-01). Only if there is none, it picks the newest `-alpha` at or above 0.154. Stable 0.154.0 is rejected (it rejects Astra). `CONCLAVE_CODEX_BIN` overrides that pick. | `login --device-auth` on the binary the resolver picked (a ChatGPT plan that includes Astra) | That binary's `--version` is stable `N.N.N` at or above 0.159.3, or `-alpha` at or above 0.154 |
| `cursor-agent` | Cursor's installer | `cursor-agent login` | `cursor-agent models` lists the newest `grok-*` and `claude-fable-*` |
| `grok` | xAI installer to `~/.local/bin/grok` | `grok login` (a Grok subscription) | The voter uses `cursor-agent` and `CURSOR_GROK`. `CONCLAVE_GROK_NATIVE=1` uses this CLI instead. `grok models` prints a default model for that opt-in |
| `muse` | Meta installer at `https://dev.meta.ai/install.sh`, binary at `~/.local/bin/muse` | `muse login` | `muse exec --help` shows the headless command. The shadow leg is off unless `CONCLAVE_MUSE_SHADOW=1` |
| `agy` | Google Antigravity CLI, binary on PATH or at `~/.local/bin/agy`. `CONCLAVE_AGY` overrides it (`COUNCIL_AGY` is the alias) | Sign in before a ballot. `agy -p "/usage"` prints the pool rows | Design and mixed seat Argon unless `CONCLAVE_ARGON=0`. Do not opt in to data sharing. Do not mark the repo trusted in `agy`. `CONCLAVE_ARGON_MODEL` overrides the model id. `CONCLAVE_ARGON_TIMEOUT` is the print timeout in seconds (default 900) |

When the shadow leg runs, `muse exec --json` is the event stream. `scripts/muse-usage.py` stores a `subscription_usage` snapshot at `~/.cache/quartermaster/muse-usage.json` when that event is in the stream. The write is a temp file renamed into place. A missing event or a write error leaves the council run unchanged. `docs/routing.md` records the limits of that stream.

Install `seat-usage` from the dotfiles. `docs/routing.md` makes its pace
line the only tripwire, and the chair reports it to the Bot.

After each login, record the usage line (`/usage` in Claude Code, the
`rate_limits` block from the first Codex run, the Cursor dashboard bars) so
there is a baseline before any Conclave runs.

`cursor-agent` needs `-f` on an untrusted directory and `--output-format
text` prints only the final message. `codex exec` needs `< /dev/null`.

### GitHub over SSH

`cursor-agent` fetches its plugin marketplace from
`git@github.com:cursor/plugins.git` at the start of every call. Where
`github.com:22` hangs, every `cursor-agent` call hangs before the model
runs (seen 2026-10-21: the Fable voter leg and a cursor-agent chair).
`GIT_CONFIG_COUNT` pairs from the caller do not help, because
`cursor-agent` replaces them in its git children. `scripts/conclave.sh`
sources `scripts/github-https-env.sh`, which points `GIT_CONFIG_GLOBAL`, for
that run only, at a cached file that includes your global git config and
rewrites GitHub SSH URLs to HTTPS. Launch a cursor-agent chair through
`scripts/with-github-https.sh cursor-agent ...` for the same effect. Neither
edits `~/.gitconfig` or `~/.ssh`. That wrapper exports `CONCLAVE_CHAIR=1`
on the chair process, and `scripts/stack-run.py` sets the same variable on
each chair it starts. `CONCLAVE_GITHUB_HTTPS=0` turns it off.
A push from such a process goes over HTTPS, so pass an HTTPS credential
helper. Test: `scripts/test-github-https-env.sh`.

### HOME

Every CLI keeps its login under `HOME`. The Bot desktop app can start the
WSL shell with a Windows `HOME` (`C:Usersryanw`, backslashes lost, seen
2026-10-02). `scripts/conclave.sh` and `scripts/resolve-models.sh` source
`scripts/home-env.sh`, which takes `HOME` from the account record
(`getent passwd`) when `HOME` is not an existing absolute directory. The
Bot's chair launch does the same before it uses `~`. Nothing in this repo
hardcodes a home path; keep it that way.

### Linear and Notion for the chair

The chair reads Linear tickets and Notion pages to write a review brief.
The Cursor plugins for both load without a login, and a headless chair
cannot sign in to them (`mcp_auth`: "Interactive MCP authentication is
only available in the Cursor desktop IDE"). Configure the remote servers
in `~/.cursor/mcp.json` instead:

```json
{
  "mcpServers": {
    "linear": { "url": "https://mcp.linear.app/mcp" },
    "notion": { "url": "https://mcp.notion.com/mcp" }
  }
}
```

Then, at the executor, in its own terminal, run `cursor-agent mcp login
linear` and `cursor-agent mcp login notion` and approve each in a browser
on the same machine. The callback is `localhost:8787`, so a phone or
another machine cannot finish it. Run the logins from the repo folder the
chair uses as `--workspace`: the sign-in is stored per folder, under
`~/.cursor/projects/<folder>/mcp-auth.json` (found 2026-10-02). A new
repo folder needs its own two logins. `cursor-agent mcp list` from that
folder should then show `needs approval` or `ready`, not
`requires_authentication`; the chair launch passes `--approve-mcps`, so
approval needs no extra step. Linear grants `read write`
only; the chair reads Linear and Notion and never writes to them.
Done on the executor 2026-10-02.

## 3. This repo

```bash
git clone https://github.com/rymiwe/conclave ~/code/conclave
mkdir -p ~/.claude/skills
ln -sfn ~/code/conclave/.agents/skills/council ~/.claude/skills/council
echo 'export CONCLAVE_ROOT="$HOME/code/conclave"' >> ~/.profile
export CONCLAVE_ROOT="$HOME/code/conclave"
```

`CONCLAVE_ROOT` is the current name. `COUNCIL_ROOT` still works, and so does every other `COUNCIL_*` setting: the matching `CONCLAVE_*` name wins when both are set. `scripts/council.sh` is a deprecated alias for `scripts/conclave.sh`. The clone path above is this machine's checkout and is unchanged.

Dry-run a Conclave on the example brief from inside any git repo. The brief
lives in the Conclave clone. The script snapshots the repo you started in
(or `--repo`). Ballot files stay in the clone: a review leg can see the
target's main checkout through git worktree metadata, so they must not be
written there.

```bash
mkdir -p "$CONCLAVE_ROOT/tmp/council/smoke"
cp "$CONCLAVE_ROOT/tmp/council/examples/design-high-brief.md" "$CONCLAVE_ROOT/tmp/council/smoke/brief.md"
"$CONCLAVE_ROOT/scripts/conclave.sh" smoke --dry-run
```

## 4. Work repos

Clone the repos you work in from GitHub and follow each repo's own setup.
One worktree per ticket. Nothing from this machine goes anywhere but
GitHub.

## 5. Grok Bot desktop app

- Install from the Bot page in the Cursor dashboard. Keep it open and
  online; local execution needs it.
- Settings > Computer > Execution on this computer: **ask every time**
  until the security check clears.
- Only this machine runs the desktop app. Two open at once flap.
- Local execution probably lands in a Windows shell; find the wrapper that
  reaches WSL (`wsl.exe -e bash -lc '...'` or similar) and record it in
  the Bot's standing instruction. Unverified; test it first.
- Smoke test from the phone: ask the Bot to run `uname -a` here, then to
  print the first lines of `~/code/conclave/docs/routing.md`.

## Done when

From the phone, the Bot starts a chair on this machine that runs the
dry-run above and reports the routing preview back, and
`op read "$CONCLAVE_JEV_OP_REF" | wc -c` prints a nonzero count. Record
what each step took and what failed.

## Optional, later

The status-line script from the other machine's dotfiles. Add it only if
the chair's pool reports are missed.
