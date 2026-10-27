#!/usr/bin/env python3
# Usage: scripts/preflight.py --repo DIR --dir DIR [--pr N | --base REF --head REF] [--ballot ID] [--no-seats]
"""Launch-time checks before any voter runs. One list of what the human must fix.

Checks the review target, the seat-usage route, and the target repo's
optional .conclave/preflight.json. Every check runs even when one fails.
Blocking items exit 3 (BLOCKED) before anything launches. Warnings alone do
not block. The list goes to stdout and to needs-human.md in the run dir.

CONCLAVE_PREFLIGHT=0 skips every check. CONCLAVE_PREFLIGHT_DONE=1 means
stack-run already ran the seat and repo checks, so only the target is checked.
Command output, env values, headers, URL queries and credentials are never
printed. See docs/preflight.md.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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


def check_seats(run=subprocess.run, waiting: bool = False) -> list[dict]:
    """Seat items. waiting: the caller re-checks capped seats later, so blocked only warns."""
    status, message = seat_precheck(run)
    if status == 'blocked':
        if waiting:
            return [item('warning', f'seats capped: {message}',
                         'the run waits for seats (--wave-wait) before the first wave')]
        return [item('blocking', f'seats capped: {message}',
                     'wait for usage to reset, or pass --voters with seats that have quota')]
    if status == 'inconclusive':
        return [item('warning', f'seat check inconclusive: {message}',
                     'nothing; the run proceeds without a seat check')]
    return []


# Review targets.

def check_pr(repo: str | Path, number: int | str) -> list[dict]:
    """The PR must resolve in repo. A closed or merged PR only warns."""
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


def check_refs(repo: str | Path, refs: list[str]) -> list[dict]:
    """Each ref must name a commit in repo."""
    found = []
    for ref in refs:
        if not ref or ref.startswith('-') or any(c in ref for c in '\n\r\x00'):
            found.append(item('blocking', f'ref {ref!r} is not a valid ref', 'fix --base/--head'))
            continue
        try:
            proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=GIT_TIMEOUT)
            ok = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
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


def load_declaration(repo: str | Path) -> tuple[list[dict] | None, str]:
    """(checks, '') or (None, reason). A missing file is ([], '')."""
    path = Path(repo) / DECLARATION
    if not path.exists():
        return [], ''
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError) as exc:
        return None, f'cannot read it ({type(exc).__name__})'
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
        expect = row.get('expect_status', 200)
        if url is not None:
            try:
                scheme = urllib.parse.urlsplit(url).scheme if isinstance(url, str) else ''
            except ValueError:
                scheme = ''
            if scheme not in ('http', 'https'):
                return None, f'{where}: url must be an http or https URL'
            if type(expect) is not int or not 100 <= expect <= 599:
                return None, f'{where}: expect_status must be an HTTP status code'
        elif 'expect_status' in row:
            return None, f'{where}: expect_status needs url'
        checks.append({'name': name.strip(), 'needs': needs.strip(), 'severity': severity,
                       'timeout': min(float(timeout), CHECK_TIMEOUT_MAX), 'command': command,
                       'url': url, 'expect_status': expect})
    return checks, ''


def run_command(argv: list[str], cwd: str | Path, timeout: float) -> str:
    """'' on exit 0, else a reason class. Output is discarded, never read."""
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError:
        return 'not found'
    except PermissionError:
        return 'not executable'
    except OSError:
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


def fetch_status(url: str, expect: int, timeout: float) -> str:
    """'' when GET url answers expect, else a reason class. The body is never read."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='GET'), timeout=timeout) as resp:
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


def check_declared(repo: str | Path) -> list[dict]:
    checks, reason = load_declaration(repo)
    if checks is None:
        return [item('blocking', f'bad {DECLARATION}: {reason}', f'fix {DECLARATION} in {repo} (see docs/preflight.md)')]
    found = []
    for check in checks:
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


def report(items: list[dict], dest: str | Path, context: dict | None = None) -> int:
    """Print one list, write or clear needs-human.md, fire preflight_failed. 0 or BLOCKED."""
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
    args = parser.parse_args(argv)
    if not enabled():
        return 0
    items: list[dict] = []
    if args.pr:
        items += check_pr(args.repo, args.pr)
    if args.base:
        items += check_refs(args.repo, [args.base, args.head or 'HEAD'])
    if not handed_off():
        if not args.no_seats:
            items += check_seats()
        items += check_declared(args.repo)
    context = {'ballot': args.ballot} if args.ballot else {}
    return report(items, args.dir, context)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
