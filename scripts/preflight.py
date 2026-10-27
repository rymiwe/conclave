#!/usr/bin/env python3
# Usage: scripts/preflight.py --repo DIR --dir DIR [--pr N | --base REF --head REF] [--ballot ID] [--no-seats] [--plan]
"""Launch-time checks before any voter runs. One list of what the human must fix.

Checks the review target, the seat-usage route, and the target repo's
optional .conclave/preflight.json. Every check runs even when one fails.
Blocking items exit 3 (BLOCKED) before anything launches. Warnings alone do
not block. The list goes to stdout and to needs-human.md in the run dir.
--plan (the dry run) runs no declared command and fetches no declared URL:
it lists each one with its argv or URL and source ref instead.

CONCLAVE_PREFLIGHT=0 skips every check. CONCLAVE_PREFLIGHT_DONE=1 means
stack-run already ran the seat and repo checks, so only the target is checked.
Command output, env values, headers, URL queries and credentials are never
printed. The repo's declared checks are read with git show from the default
branch (--pr, stack-run) or --base, never from the working tree or a PR's
base branch, and run with a scrubbed environment.
See docs/preflight.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from conclave_env import setting  # noqa: E402
import lineup  # noqa: E402
import stage_hook  # noqa: E402

BLOCKED = 3
SEAT_TIMEOUT = 25
GH_TIMEOUT = 60
GIT_TIMEOUT = 30
CHECK_TIMEOUT = 15
CHECK_TIMEOUT_MAX = 120
DECLARATION = '.conclave/preflight.json'
DECLARATION_MAX = 65536
# Declared commands get these variables and nothing else. A token, a key, or a
# 1Password variable in the caller's environment never reaches a child.
ENV_ALLOW = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR', 'TZ', 'USER')
DEFAULT_PATH = '/usr/local/bin:/usr/bin:/bin'
# A shell, or a tool that runs another command, is a way to run anything.
WRAPPERS = frozenset((
    'sh', 'bash', 'zsh', 'dash', 'ash', 'ksh', 'csh', 'tcsh', 'fish', 'busybox', 'env', 'xargs', 'nohup', 'nice',
    'ionice', 'timeout', 'time', 'sudo', 'doas', 'su', 'runuser', 'exec', 'command', 'eval', 'stdbuf', 'setsid',
    'chroot', 'watch', 'flock', 'unshare', 'nsenter', 'strace', 'parallel', 'find', 'awk', 'gawk', 'mawk', 'nawk'))
# An interpreter may run a script or module. It may not run code from argv.
INLINE_LETTERS = {'python': 'c', 'perl': 'eE', 'ruby': 'e', 'node': 'ep', 'nodejs': 'ep', 'php': 'r', 'lua': 'e',
                  'pwsh': 'c', 'powershell': 'c', 'deno': '', 'bun': 'e', 'osascript': 'e'}
INLINE_LONG = frozenset(('--eval', '--print', '--command', '-command', '-encodedcommand', '--execute', 'eval'))
NEEDS_FILE = 'needs-human.md'
CHECK_KEYS = {'name', 'command', 'url', 'expect_status', 'timeout', 'needs', 'severity'}


def item(severity: str, what: str, todo: str) -> dict:
    return {'severity': severity, 'what': what, 'todo': todo}


def enabled() -> bool:
    return setting('PREFLIGHT', '1') != '0'


def handed_off() -> bool:
    """stack-run already ran the seat and repo checks for this child."""
    return setting('PREFLIGHT_DONE', '') == '1'


# Seat-usage route. stack-run's per-wave pre-check calls this same function.

def route_detail(raw: str) -> str:
    """The route's reason and skipped codes, as ' (...)', or ''."""
    try:
        doc = json.loads(raw)
    except ValueError:
        return ''
    if isinstance(doc, list):
        doc = doc[0] if doc else {}
    if not isinstance(doc, dict):
        return ''
    parts = []
    reason = doc.get('reason') or doc.get('why')
    if reason:
        parts.append(str(reason))
    codes = [f"{item.get('family') or '-'}:{item['code']}" for item in doc.get('skipped') or []
             if isinstance(item, dict) and item.get('code')]
    if codes:
        parts.append('skipped ' + ', '.join(codes))
    return f" ({'; '.join(parts)})" if parts else ''


def precheck_verdict(raw: str) -> tuple[str, str]:
    """Judge one seat-usage --route lineup answer: ok, blocked, or inconclusive.

    Empty output is inconclusive, as it is for conclave.sh. Other unreadable
    output is blocked.
    """
    if not raw.strip():
        return 'inconclusive', 'seat-usage printed nothing'
    lines = lineup.parse(raw).splitlines()
    status = lines[0] if lines else 'bad'
    detail = route_detail(raw)
    if status in ('ok', 'downgrade'):
        voters = [v for v in (lines[1] if len(lines) > 1 else '').split(',') if v]
        if len(set(voters)) >= 2:
            return 'ok', 'voters ' + ','.join(voters) + detail
        return 'blocked', f"fewer than 2 voters ({','.join(voters) or 'none'}){detail}"
    if status == 'ask':
        return 'blocked', f'seat-usage asked for a human{detail}'
    return 'blocked', f'seat-usage returned no usable lineup{detail}'


def seat_precheck(run=subprocess.run) -> tuple[str, str]:
    """Ask the route conclave.sh seats from. A missing or failing binary is inconclusive.

    run is subprocess.run or a drop-in (stack-run passes its child tracker).
    """
    if setting('WAVE_PRECHECK', '1') == '0':
        return 'skipped', 'CONCLAVE_WAVE_PRECHECK=0'
    seat_cli = os.path.expanduser(setting('SEAT_USAGE', '~/.local/bin/seat-usage'))
    try:
        proc = run([seat_cli, '--route', 'lineup', '--json', '--kind', 'review'],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   timeout=SEAT_TIMEOUT)
    except OSError as exc:
        return 'inconclusive', f'seat-usage did not run: {exc.strerror or exc}'
    except subprocess.TimeoutExpired:
        return 'inconclusive', f'seat-usage timed out after {SEAT_TIMEOUT}s'
    if proc.returncode:
        return 'inconclusive', f'seat-usage exited {proc.returncode}'
    return precheck_verdict(proc.stdout)


SEAT_OPTIONS = {
    'conclave': ('Default: wait for the capped seat to reset (the reason says when), then rerun. '
                 'Or pass --voters with seats that have quota. '
                 'Or set CONCLAVE_PREFLIGHT=0 to skip preflight and run the old fixed default pair'),
    'stack': ('Default: wait for the capped seat to reset (the reason says when), then rerun. '
              'Or pass --wave-wait SECONDS, for example 1800, so the run waits for seats before the first wave. '
              'Or set CONCLAVE_PREFLIGHT=0 to skip preflight; the per-wave pre-check still stops a capped wave'),
}


def check_seats(run=subprocess.run, waiting: bool = False, caller: str = 'conclave') -> list[dict]:
    """Seat items. waiting: the caller re-checks capped seats later, so blocked only warns."""
    status, message = seat_precheck(run)
    if status == 'blocked':
        if waiting:
            return [item('warning', f'seats capped: {message}',
                         'the run waits for seats (--wave-wait) before the first wave')]
        return [item('blocking', f'seats capped: {message}', SEAT_OPTIONS[caller])]
    if status == 'inconclusive':
        return [item('warning', f'seat check inconclusive: {message}',
                     'nothing; the run proceeds without a seat check')]
    return []


# Review targets.

def check_pr(repo: str | Path, number: int | str) -> list[dict]:
    """The PR must resolve in repo. A closed or merged PR only warns.

    A missing repo directory is named as such: subprocess raises the same
    FileNotFoundError for a missing cwd as for a missing gh.
    """
    if not Path(repo).is_dir():
        return [item('blocking', f'PR #{number}: --repo {repo} is not a directory',
                     'pass --repo with the path of the target repo checkout')]
    argv = ['gh', 'pr', 'view', str(number), '--json', 'number,state,url']
    try:
        proc = subprocess.run(argv, cwd=str(repo), stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=GH_TIMEOUT)
    except FileNotFoundError:
        return [item('blocking', f'PR #{number}: gh not found', 'install gh and run gh auth login')]
    except OSError:
        return [item('blocking', f'PR #{number}: gh could not run', 'check the gh install')]
    except subprocess.TimeoutExpired:
        return [item('blocking', f'PR #{number}: gh pr view timed out after {GH_TIMEOUT}s',
                     'check the network and gh auth status')]
    if proc.returncode:
        return [item('blocking', f'PR #{number} not found in {repo} (gh pr view exit {proc.returncode})',
                     'check the PR number and --repo, and gh auth status')]
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        doc = None
    if not isinstance(doc, dict):
        return [item('blocking', f'PR #{number}: gh pr view returned no PR', 'check the PR number and --repo')]
    state = str(doc.get('state') or '').upper()
    if state in ('CLOSED', 'MERGED'):
        return [item('warning', f'PR #{number} is {state.lower()}', 'confirm a review of a closed PR is wanted')]
    return []


def default_branch_ref(repo: str | Path) -> str:
    """origin/<default branch> when it resolves, else ''.

    The declaration for a PR or a stack is read from here and nowhere else. A
    PR's base can be another PR's head, which that PR's author controls, so
    the base is never a source. gh names the default branch; origin/HEAD is
    the offline answer.
    """
    names = []
    try:
        proc = subprocess.run(['gh', 'repo', 'view', '--json', 'defaultBranchRef'], cwd=str(repo),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GH_TIMEOUT)
        doc = json.loads(proc.stdout) if proc.returncode == 0 else None
        name = (doc.get('defaultBranchRef') or {}).get('name') if isinstance(doc, dict) else None
        if isinstance(name, str):
            names.append(name)
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        pass
    try:
        proc = subprocess.run(['git', '-C', str(repo), 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GIT_TIMEOUT)
        head = proc.stdout.strip() if proc.returncode == 0 else ''
        if head.startswith('refs/remotes/origin/'):
            names.append(head[len('refs/remotes/origin/'):])
    except (OSError, subprocess.TimeoutExpired):
        pass
    for name in names:
        if name and name != 'HEAD' and not name.startswith('-') and commit_of(repo, f'origin/{name}'):
            return f'origin/{name}'
    return ''


def check_declared_default(repo: str | Path, plan: list[str] | None = None) -> list[dict]:
    """check_declared on the default branch. Unresolved, nothing runs and one warning says so."""
    ref = default_branch_ref(repo)
    if not ref:
        return [item('warning', f"{DECLARATION} was not read: the repo's default branch could not be resolved",
                     'check gh auth status, or run git remote set-head origin --auto; '
                     'the file is only read from origin/<default branch>')]
    return check_declared(repo, ref, plan)


def valid_ref(ref: str) -> bool:
    return bool(ref) and not ref.startswith('-') and not any(c in ref for c in '\n\r\x00')


def commit_of(repo: str | Path, ref: str) -> str:
    """The commit sha ref names in repo, or ''."""
    if not valid_ref(ref):
        return ''
    try:
        proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return proc.stdout.strip() if proc.returncode == 0 else ''


def check_refs(repo: str | Path, refs: list[str]) -> list[dict]:
    """Each ref must name a commit in repo."""
    found = []
    for ref in refs:
        if not valid_ref(ref):
            found.append(item('blocking', f'ref {ref!r} is not a valid ref', 'fix --base/--head'))
        elif not commit_of(repo, ref):
            found.append(item('blocking', f'ref {ref} does not resolve to a commit in {repo}',
                              'fetch it (git fetch origin) or fix --base/--head'))
    return found


# Repo-declared repro prerequisites.

def safe_url(url: str) -> str:
    """scheme://host[:port]/path. No userinfo, query or fragment."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ''
        if ':' in host:
            host = f'[{host}]'
        port = f':{parts.port}' if parts.port else ''
    except ValueError:
        return '(url)'
    return urllib.parse.urlunsplit((parts.scheme, host + port, parts.path, '', ''))


def read_declaration(repo: str | Path, sha: str) -> str | None:
    """The declaration as committed at sha, or None when that commit has none.

    Only the object store is read, with git show. The working tree is never
    opened, so an untracked file, an uncommitted edit, or the PR branch's own
    copy cannot supply a command. Raises ValueError with a reason when the
    path is not a regular file or cannot be read.
    """
    git = ['git', '-C', str(repo)]
    try:
        tree = subprocess.run([*git, 'ls-tree', '-z', sha, '--', DECLARATION], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=GIT_TIMEOUT)
        if tree.returncode:
            raise ValueError('cannot read the base commit')
        if not tree.stdout:
            return None
        mode = tree.stdout.split(' ', 1)[0]
        if mode not in ('100644', '100755'):
            raise ValueError('must be a regular file')
        shown = subprocess.run([*git, 'show', '--no-textconv', f'{sha}:{DECLARATION}'], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError('cannot read the base commit') from None
    if shown.returncode:
        raise ValueError('cannot read the base commit')
    if len(shown.stdout) > DECLARATION_MAX:
        raise ValueError(f'larger than {DECLARATION_MAX} bytes')
    try:
        return shown.stdout.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError('not UTF-8') from None


def wrapper_reason(command: list[str]) -> str:
    """Why argv runs arbitrary code through a shell, a wrapper, or an interpreter's inline flag, else ''.

    A deny list, not a jail: it stops the obvious ways to turn argv back into
    a shell string. The declaration comes from the reviewed base commit.
    """
    name = os.path.basename(command[0]).lower()
    name = re.sub(r'\.exe$', '', name)
    name = re.sub(r'[\d.]+$', '', name) or name
    if name in WRAPPERS:
        return f'{name} is a shell or wrapper; name the tool itself'
    letters = INLINE_LETTERS.get(name)
    if letters is None:
        return ''
    if name == 'deno' and command[1:2] == ['eval']:
        return 'deno eval runs code from argv'
    for arg in command[1:]:
        if not arg.startswith('-'):
            break  # the script or module name; later flags are the script's own
        flag = arg.lower()
        if flag in INLINE_LONG or (flag.startswith('--') and flag.split('=', 1)[0] in INLINE_LONG):
            return f'{name} {arg} runs code from argv'
        if not arg.startswith('--') and any(c in letters for c in arg[1:]):
            return f'{name} {arg} runs code from argv'
    return ''


def parse_declaration(text: str) -> tuple[list[dict] | None, str]:
    """(checks, '') or (None, reason) for the declaration's text."""
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return None, f'not valid JSON (line {getattr(exc, "lineno", "?")})'
    if not isinstance(doc, dict):
        return None, 'top level must be an object'
    extra = sorted(set(doc) - {'version', 'checks'})
    if extra:
        return None, f'unknown key {extra[0]!r}'
    if doc.get('version') != 1:
        return None, 'version must be 1'
    raw = doc.get('checks')
    if not isinstance(raw, list):
        return None, 'checks must be a list'
    checks = []
    for n, row in enumerate(raw, 1):
        where = f'check {n}'
        if not isinstance(row, dict):
            return None, f'{where} must be an object'
        extra = sorted(set(row) - CHECK_KEYS)
        if extra:
            return None, f'{where}: unknown key {extra[0]!r}'
        name = row.get('name')
        if not isinstance(name, str) or not name.strip():
            return None, f'{where}: name must be a non-empty string'
        needs = row.get('needs')
        if not isinstance(needs, str) or not needs.strip():
            return None, f'{where}: needs must be a non-empty string'
        severity = row.get('severity', 'blocking')
        if severity not in ('blocking', 'warning'):
            return None, f'{where}: severity must be blocking or warning'
        timeout = row.get('timeout', CHECK_TIMEOUT)
        if type(timeout) not in (int, float) or not 0 < timeout:
            return None, f'{where}: timeout must be a positive number of seconds'
        command, url = row.get('command'), row.get('url')
        if (command is None) == (url is None):
            return None, f'{where}: give exactly one of command and url'
        if command is not None and (not isinstance(command, list) or not command
                                    or not all(isinstance(a, str) and a for a in command)):
            return None, f'{where}: command must be a non-empty list of strings'
        if command is not None and any('\0' in a for a in command):
            return None, f'{where}: command arguments must not contain a NUL character'
        if command is not None:
            if '/' in command[0] and not command[0].startswith('/'):
                return None, f'{where}: command must be a tool on PATH or an absolute path, not a path in the checkout'
            why = wrapper_reason(command)
            if why:
                return None, f'{where}: {why}'
        expect = row.get('expect_status', 200)
        if url is not None:
            try:
                parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
                bad_port = parts is not None and parts.port == 0
            except ValueError:
                parts, bad_port = None, False
            if parts is None or parts.scheme not in ('http', 'https') or not parts.hostname:
                return None, f'{where}: url must be an http or https URL'
            if bad_port:
                return None, f'{where}: url port is not valid'
            if '@' in parts.netloc:
                return None, f'{where}: url must not carry a user name or password'
            if type(expect) is not int or not 100 <= expect <= 599:
                return None, f'{where}: expect_status must be an HTTP status code'
        elif 'expect_status' in row:
            return None, f'{where}: expect_status needs url'
        checks.append({'name': name.strip(), 'needs': needs.strip(), 'severity': severity,
                       'timeout': min(float(timeout), CHECK_TIMEOUT_MAX), 'command': command,
                       'url': url, 'expect_status': expect})
    return checks, ''


def scrubbed_env() -> dict[str, str]:
    """The allowlisted variables and nothing else. PATH keeps only absolute entries, so the checkout cannot shadow a tool."""
    env = {name: os.environ[name] for name in ENV_ALLOW if name in os.environ}
    path = [p for p in env.get('PATH', '').split(os.pathsep) if p.startswith('/')]
    env['PATH'] = os.pathsep.join(path) or DEFAULT_PATH
    return env


def load_declaration(repo: str | Path, sha: str) -> tuple[list[dict] | None, str]:
    """(checks, '') or (None, reason). No declaration at sha is ([], '')."""
    try:
        text = read_declaration(repo, sha)
    except ValueError as exc:
        return None, str(exc)
    return ([], '') if text is None else parse_declaration(text)


def run_command(argv: list[str], cwd: str | Path, timeout: float) -> str:
    """'' on exit 0, else a reason class. Output is discarded, never read. The env is scrubbed."""
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True, env=scrubbed_env())
    except FileNotFoundError:
        return 'not found'
    except PermissionError:
        return 'not executable'
    except (OSError, ValueError):
        return 'could not run'
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        return f'timeout after {timeout:g}s'
    if rc < 0:
        return f'killed by signal {-rc}'
    return f'exit {rc}' if rc else ''


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer (its 3xx status), not a request to somewhere the declaration did not name."""

    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch_status(url: str, expect: int, timeout: float) -> str:
    """'' when GET url answers expect, else a reason class. The body is never read. Redirects are not followed."""
    try:
        with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url, method='GET'),
                                                          timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
    except TimeoutError:
        return f'timeout after {timeout:g}s'
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            return f'timeout after {timeout:g}s'
        return 'connection failed'
    except (OSError, ValueError):
        return 'connection failed'
    return '' if status == expect else f'status {status}, wanted {expect}'


def check_declared(repo: str | Path, ref: str = '', plan: list[str] | None = None) -> list[dict]:
    """Run the checks the commit at ref declares. No ref, no read: the working tree is never a source.

    A repo that has the file in its working tree but no commit to read it from
    gets one warning, so the skip is not silent. plan: a dry run. Each declared
    check is appended to it (name, argv or URL, ref) and not run: no command
    runs and no URL is fetched.
    """
    sha = commit_of(repo, ref) if ref else ''
    if not sha:
        if (Path(repo) / DECLARATION).exists():
            return [item('warning', f'{DECLARATION} was not read: no base commit to read it from',
                         'review with --pr, or --base REF; the file is never read from the checkout')]
        return []
    checks, reason = load_declaration(repo, sha)
    if checks is None:
        return [item('blocking', f'bad {DECLARATION}: {reason}', f'fix {DECLARATION} on {ref} (see docs/preflight.md)')]
    found = []
    for check in checks:
        if plan is not None:
            what = json.dumps(check['command']) if check['command'] is not None else safe_url(check['url'])
            plan.append(f"{check['name']}: {what} from {ref}. not run (dry run)")
            continue
        if check['command'] is not None:
            failure = run_command(check['command'], repo, check['timeout'])
            label = check['name']
        else:
            failure = fetch_status(check['url'], check['expect_status'], check['timeout'])
            label = f"{check['name']} (GET {safe_url(check['url'])})"
        if failure:
            found.append(item(check['severity'], f'{label}: {failure}', check['needs']))
    return found


# Report.

def render(items: list[dict]) -> str:
    lines = ['# Needs from human', '']
    for heading, severity in (('Blocking', 'blocking'), ('Warnings', 'warning')):
        rows = [i for i in items if i['severity'] == severity]
        lines += [f'## {heading}', '']
        lines += [f"{n}. {i['what']}. Do: {i['todo']}" for n, i in enumerate(rows, 1)] or ['(none)']
        lines.append('')
    return '\n'.join(lines)


def report(items: list[dict], dest: str | Path, context: dict | None = None, plan: bool = False,
           planned: list[str] | None = None) -> int:
    """Print one list, write or clear needs-human.md, fire preflight_failed. 0 or BLOCKED.

    plan: a dry run. The same list and exit code for the checks it ran, but no
    stage event. planned: the declared checks it did not run, printed first.
    """
    if planned:
        lines = ['# Declared checks not run (dry run)', '']
        lines += [f'{n}. {line}' for n, line in enumerate(planned, 1)]
        print('\n'.join(lines) + '\n', flush=True)
    dest = Path(dest)
    path = dest / NEEDS_FILE
    if not items:
        path.unlink(missing_ok=True)
        print('preflight: ok', flush=True)
        return 0
    text = render(items)
    dest.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    print(text, end='', flush=True)
    blocking = sum(i['severity'] == 'blocking' for i in items)
    if not blocking:
        return 0
    if plan:
        print(f'preflight: a real launch would stop here with exit {BLOCKED} ({blocking} blocking). '
              f'Nothing is launched in a dry run either. See {path}', file=sys.stderr)
        return BLOCKED
    print(f'preflight: blocked ({blocking} blocking); nothing launched. See {path}', file=sys.stderr)
    stage_hook.emit('preflight_failed', {**(context or {}), 'dir': str(dest), 'needs_file': str(path),
                                         'blocking': blocking, 'warnings': len(items) - blocking}, dest)
    return BLOCKED


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--ballot')
    parser.add_argument('--pr')
    parser.add_argument('--base')
    parser.add_argument('--head')
    parser.add_argument('--no-seats', action='store_true', help='skip the seat check (--voters was given)')
    parser.add_argument('--plan', action='store_true',
                        help='dry run: declared checks are listed, not run or fetched; no stage event; --dir is the preview dir')
    args = parser.parse_args(argv)
    if not enabled():
        return 0
    items: list[dict] = []
    planned: list[str] | None = [] if args.plan else None
    if args.pr:
        items += check_pr(args.repo, args.pr)
    if args.base:
        items += check_refs(args.repo, [args.base, args.head or 'HEAD'])
    if not handed_off():
        if not args.no_seats:
            items += check_seats()
        if args.pr:
            # Never the PR's base: on a stacked PR that is the lower PR's head.
            items += check_declared_default(args.repo, planned)
        else:
            items += check_declared(args.repo, args.base or '', planned)
    context = {'ballot': args.ballot} if args.ballot else {}
    return report(items, args.dir, context, plan=args.plan, planned=planned)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
