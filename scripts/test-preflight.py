#!/usr/bin/env python3
"""Offline preflight and stage-hook checks.

Every external tool is a PATH stub. The only network is a local HTTP server
on 127.0.0.1. Fixtures live under tmp/ in this worktree and are removed.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import preflight  # noqa: E402
import stage_hook  # noqa: E402

SECRETS = ('SECRET-OUT-123', 'SECRET-ERR-456', 'SECRET-TOKEN-789', 'SECRET-PASS-000', 'SECRET-ENV-321')
CLEAR = ('PREFLIGHT', 'PREFLIGHT_DONE', 'STAGE_HOOK', 'STAGE_HOOK_TIMEOUT', 'WAVE_PRECHECK', 'SEAT_USAGE',
         'VOTER', 'CHAIR', 'VOTER_SANDBOX', 'JEV_OP', 'JEV_OP_REF')


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f'FAIL {label}')
    print(f'ok {label}')


def write_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding='utf-8')
    path.chmod(0o755)
    return path


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(['git', '-C', str(repo), *args], text=True, capture_output=True)
    if proc.returncode:
        raise SystemExit(f'FAIL git fixture {args}: {proc.stderr}')
    return proc.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, 'init', '-q', '-b', 'main')
    git(path, 'config', 'user.email', 'test@example.test')
    git(path, 'config', 'user.name', 'Test')
    git(path, 'config', 'commit.gpgsign', 'false')
    (path / 'a.txt').write_text('one\n')
    git(path, 'add', '.')
    git(path, 'commit', '-q', '-m', 'one')
    return path


def declare(repo: Path, checks: list[dict] | str, commit: bool = True) -> None:
    """Write the declaration and, by default, commit it to the checked-out branch.

    Preflight reads it from a base commit, so a fixture that wants it to run
    commits it. commit=False leaves it untracked, which must never run.
    """
    (repo / '.conclave').mkdir(exist_ok=True)
    text = checks if isinstance(checks, str) else json.dumps({'version': 1, 'checks': checks})
    (repo / '.conclave/preflight.json').write_text(text)
    if commit:
        git(repo, 'add', '.conclave/preflight.json')
        git(repo, 'commit', '-q', '-m', 'declare preflight')


def base_env(bin_dir: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for name in CLEAR:
        env.pop(f'CONCLAVE_{name}', None)
        env.pop(f'COUNCIL_{name}', None)
    for name in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
        env.pop(name, None)
    env.update(PATH=f'{bin_dir}:{os.environ.get("PATH", "/usr/bin:/bin")}', NO_PROXY='127.0.0.1,localhost',
               no_proxy='127.0.0.1,localhost', **extra)
    return env


def preflight_cli(env: dict, repo: Path, dest: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPTS / 'preflight.py'), '--repo', str(repo), '--dir', str(dest), *extra],
                          env=env, text=True, capture_output=True, timeout=120)


HOOK = '''\
#!/usr/bin/env python3
import json, os, sys
with open(os.environ['HOOK_LOG'], 'a') as f:
    f.write(json.dumps({'argv': sys.argv[1:], 'event': os.environ.get('CONCLAVE_STAGE_EVENT'),
                        'payload': os.environ.get('CONCLAVE_STAGE_PAYLOAD'),
                        'stdin_tty': sys.stdin.isatty(), 'stdin': sys.stdin.read()}) + '\\n')
sys.exit(int(os.environ.get('HOOK_EXIT', '0')))
'''

SEAT = '''\
#!/bin/sh
printf '%s\\n' "$*" >>"$SEAT_LOG"
cat "$SEAT_STUB_JSON"
'''

GH = '''\
#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:2] == ['pr', 'view']:
    if args[2] in os.environ.get('MISSING_PR', '').split(','):
        sys.exit('no pull requests found')
    print(json.dumps({'number': int(args[2]), 'state': os.environ.get('PR_STATE', 'OPEN'),
                      'url': f'https://example.test/o/r/pull/{args[2]}',
                      'author': {'login': 'someone', 'is_bot': False}, 'commits': [],
                      **({'baseRefName': os.environ['PR_BASE']} if os.environ.get('PR_BASE') else {})}))
    sys.exit(0)
if args[:2] == ['repo', 'view'] and os.environ.get('DEFAULT_BRANCH'):
    print(json.dumps({'defaultBranchRef': {'name': os.environ['DEFAULT_BRANCH']}}))
    sys.exit(0)
sys.exit('unexpected gh ' + str(args))
'''


def lineup_json(*families: str, mode: str = 'default') -> str:
    seats = {'fable': 'claude', 'astra': 'chatgpt', 'grok': 'grok', 'muse': 'muse'}
    if mode == 'ask':
        return json.dumps([{'role': 'lineup', 'mode': 'ask', 'reason': 'every pool capped', 'lineup': None,
                            'skipped': [{'family': 'astra', 'code': 'capped'}]}])
    return json.dumps([{'role': 'lineup', 'mode': mode, 'reason': 'stub', 'lineup': {'voters': [
        {'family': f, 'effort': 'high', 'seat': seats[f], 'vendor': 'v'} for f in families]}}])


class Status(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        code = {'/ok': 200, '/bad': 503, '/redirect': 302}.get(self.path.split('?')[0], 404)
        self.send_response(code)
        if code == 302:
            self.send_header('Location', '/ok')
        self.send_header('Content-Length', '4')
        self.end_headers()
        self.wfile.write(b'body')

    def log_message(self, *_args) -> None:
        pass


def hook_calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_declared(tmp: Path, port: int) -> None:
    bin_dir = tmp / 'decl-bin'
    bin_dir.mkdir()
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0')
    url = f'http://127.0.0.1:{port}'

    # (4) No declaration file: no repo checks, no error, nothing but the ok line.
    repo = make_repo(tmp / 'decl-none')
    dest = tmp / 'decl-none-out'
    dest.mkdir()
    (dest / 'needs-human.md').write_text('stale list from an earlier run\n')
    got = preflight_cli(env, repo, dest, '--base', 'main')
    check(preflight.check_declared(repo, 'main') == [], 'declared: a missing .conclave/preflight.json yields no items')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n' and got.stderr == ''
          and not (dest / 'needs-human.md').exists(),
          f'declared: missing file skips silently; ok line only; stale needs-human.md removed ({got.stdout!r} {got.stderr!r})')

    # (4) A malformed or unknown-schema file is exactly one blocking item.
    for label, text, reason in (('malformed', '{"version": 1, "checks": [', 'not valid JSON'),
                                ('unknown version', json.dumps({'version': 2, 'checks': []}), 'version must be 1'),
                                ('unknown key', json.dumps({'version': 1, 'checks': [
                                    {'name': 'x', 'command': ['true'], 'needs': 'n', 'shell': 'true'}]}),
                                 "unknown key 'shell'"),
                                ('both command and url', json.dumps({'version': 1, 'checks': [
                                    {'name': 'x', 'command': ['true'], 'url': url + '/ok', 'needs': 'n'}]}),
                                 'exactly one of command and url'),
                                ('shell string', json.dumps({'version': 1, 'checks': [
                                    {'name': 'x', 'command': 'true', 'needs': 'n'}]}), 'list of strings')):
        repo = make_repo(tmp / f'decl-bad-{label.replace(" ", "-")}')
        declare(repo, text)
        dest = tmp / f'decl-bad-{label.replace(" ", "-")}-out'
        got = preflight_cli(env, repo, dest, '--base', 'main')
        needs = (dest / 'needs-human.md').read_text() if (dest / 'needs-human.md').is_file() else ''
        check(got.returncode == preflight.BLOCKED and needs.count('bad .conclave/preflight.json') == 1
              and reason in needs and '\n2. ' not in needs and 'Traceback' not in got.stderr,
              f'declared: {label} file is one blocking item, no crash ({got.returncode} {needs!r} {got.stderr[-200:]!r})')

    # (3) Each failure class is reported with its needs text and blocks. Passing checks do not appear.
    repo = make_repo(tmp / 'decl-fail')
    declare(repo, [
        {'name': 'always passes', 'command': ['true'], 'needs': 'PASSING-NEEDS'},
        {'name': 'healthy url', 'url': url + '/ok', 'needs': 'PASSING-URL-NEEDS'},
        {'name': 'exit check', 'command': ['false'], 'needs': 'run the exit fix'},
        {'name': 'slow check', 'command': ['sleep', '5'], 'timeout': 1, 'needs': 'speed it up'},
        {'name': 'missing tool', 'command': ['no-such-binary-preflight-test'], 'needs': 'install the tool'},
        {'name': 'registry', 'url': url + '/bad', 'expect_status': 200, 'timeout': 5, 'needs': 'connect the VPN'},
        {'name': 'soft check', 'command': ['false'], 'needs': 'optional fix', 'severity': 'warning'},
    ])
    dest = tmp / 'decl-fail-out'
    start = time.monotonic()
    got = preflight_cli(env, repo, dest, '--base', 'main')
    elapsed = time.monotonic() - start
    needs = (dest / 'needs-human.md').read_text() if (dest / 'needs-human.md').is_file() else ''
    blocking, _, warnings = needs.partition('## Warnings')
    check(got.returncode == preflight.BLOCKED, f'declared: failing checks block with exit 3 ({got.returncode} {got.stderr})')
    for line in ('1. exit check: exit 1. Do: run the exit fix',
                 '2. slow check: timeout after 1s. Do: speed it up',
                 '3. missing tool: not found. Do: install the tool',
                 f'4. registry (GET http://127.0.0.1:{port}/bad): status 503, wanted 200. Do: connect the VPN'):
        check(line in blocking, f'declared: blocking list has {line.split(". Do:")[0]!r}')
    check('1. soft check: exit 1. Do: optional fix' in warnings and 'soft check' not in blocking,
          'declared: a failing severity warning check is listed under Warnings only')
    check('PASSING' not in needs and 'always passes' not in needs and 'healthy url' not in needs,
          'declared: passing checks do not appear')
    check(elapsed < 30, f'declared: the timeout bounds a slow check ({elapsed:.1f}s)')
    check(needs and got.stdout == needs, 'declared: stdout is the needs-human.md text')

    # (3) Warnings alone do not block.
    repo = make_repo(tmp / 'decl-warn')
    declare(repo, [{'name': 'soft check', 'command': ['false'], 'needs': 'optional fix', 'severity': 'warning'}])
    dest = tmp / 'decl-warn-out'
    got = preflight_cli(env, repo, dest, '--base', 'main')
    check(got.returncode == 0 and '## Blocking\n\n(none)' in got.stdout and 'soft check' in got.stdout
          and (dest / 'needs-human.md').is_file(),
          f'declared: warnings alone exit 0 and still write the list ({got.returncode} {got.stdout!r})')

    # (3) Commands run in the target repo with a scrubbed env: no token, key, or op variable reaches the child.
    write_exe(bin_dir / 'probe', '''\
        #!/bin/sh
        { pwd; env; } >"$1"
        test -f a.txt
        ''')
    repo = make_repo(tmp / 'decl-env')
    probe_out = tmp / 'decl-env-probe.txt'
    declare(repo, [{'name': 'env and cwd', 'command': ['probe', str(probe_out)], 'needs': 'n'}])
    canaries = {'GH_TOKEN': 'CANARY-GH-TOKEN-111', 'OP_SERVICE_ACCOUNT_TOKEN': 'CANARY-OP-SA-222',
                'AWS_SECRET_ACCESS_KEY': 'CANARY-AWS-KEY-333', 'SOME_API_KEY': 'CANARY-API-KEY-444',
                'OP_CONNECT_TOKEN': 'CANARY-OP-CONNECT-555', 'PREFLIGHT_PROBE': 'CANARY-PROBE-666'}
    got = preflight_cli(dict(env, **canaries), repo, tmp / 'decl-env-out', '--base', 'main')
    seen = probe_out.read_text() if probe_out.is_file() else ''
    names = {line.split('=', 1)[0] for line in seen.splitlines()[1:] if '=' in line}
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n' and seen.splitlines()[:1] == [str(repo.resolve())],
          f'declared: a command runs with cwd = target repo ({got.stdout!r} {seen[:80]!r})')
    check(all(value not in seen for value in canaries.values()) and not (names & set(canaries))
          and 'HOME' in names and 'PATH' in names and names <= set(preflight.ENV_ALLOW) | {'PWD', 'SHLVL', '_', 'OLDPWD'},
          f'declared: the child sees only the allowlisted env, none of the canaries ({sorted(names)})')

    # (9) CONCLAVE_PREFLIGHT=0 skips everything, even a malformed file.
    repo = make_repo(tmp / 'decl-off')
    declare(repo, '{oops')
    dest = tmp / 'decl-off-out'
    got = preflight_cli(dict(env, CONCLAVE_PREFLIGHT='0'), repo, dest, '--base', 'no-such-ref')
    check(got.returncode == 0 and got.stdout == '' and got.stderr == '' and not dest.exists(),
          f'CONCLAVE_PREFLIGHT=0 skips every check and writes nothing ({got.returncode} {got.stdout!r})')


def test_secrets(tmp: Path, port: int) -> None:
    """(7) Command output, URL query and userinfo, and env values are never shown."""
    bin_dir = tmp / 'secret-bin'
    write_exe(bin_dir / 'hook', HOOK)
    hook_log = tmp / 'secret-hook.jsonl'
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0', CONCLAVE_STAGE_HOOK=str(bin_dir / 'hook'),
                   HOOK_LOG=str(hook_log), HOOK_EXIT='1', LEAKY_VALUE='SECRET-ENV-321')
    write_exe(bin_dir / 'chatty', '#!/bin/sh\necho SECRET-OUT-123; echo SECRET-ERR-456 >&2; echo "$LEAKY_VALUE"; exit 1\n')
    repo = make_repo(tmp / 'secret-repo')
    declare(repo, [
        {'name': 'chatty check', 'needs': 'fix the chatty thing',
         'command': ['chatty']},
        {'name': 'token url', 'url': f'http://127.0.0.1:{port}/bad?token=SECRET-TOKEN-789', 'needs': 'fix the token url'},
    ])
    dest = tmp / 'secret-out'
    got = preflight_cli(env, repo, dest, '--base', 'main')
    needs = (dest / 'needs-human.md').read_text() if (dest / 'needs-human.md').is_file() else ''
    log = (dest / 'stage-hook.log').read_text() if (dest / 'stage-hook.log').is_file() else ''
    payloads = ''.join(c['payload'] or '' for c in hook_calls(hook_log))
    check(got.returncode == preflight.BLOCKED and 'chatty check: exit 1' in needs and 'token url' in needs,
          f'secrets: the two checks fail and block ({got.returncode} {needs!r})')
    check(f'http://127.0.0.1:{port}/bad)' in needs, 'secrets: a shown URL keeps host and path only')
    check(log and 'preflight_failed failed: exit 1' in log, f'secrets: the failing hook wrote stage-hook.log ({log!r})')
    for secret in SECRETS:
        check(all(secret not in text for text in (got.stdout, got.stderr, needs, log, payloads)),
              f'secrets: {secret} is in none of stdout, stderr, needs-human.md, stage-hook.log, hook payload')
    check('token=' not in needs + got.stdout and 'admin' not in needs + got.stdout,
          'secrets: no query or userinfo is shown')


def marker_bin(tmp: Path, name: str) -> tuple[Path, Path]:
    """A bin dir whose `touch-marker` tool creates marker. Running it proves a declared command ran."""
    bin_dir = tmp / f'{name}-bin'
    marker = tmp / f'{name}-marker'
    write_exe(bin_dir / 'touch-marker', f'#!/bin/sh\n: >{marker}\n')
    return bin_dir, marker


def test_declaration_source(tmp: Path) -> None:
    """The declaration is read from the base commit only. The checkout never supplies a command."""
    bin_dir, marker = marker_bin(tmp, 'src')
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0', GH_TOKEN='CANARY-GH-TOKEN-111',
                   OP_SERVICE_ACCOUNT_TOKEN='CANARY-OP-SA-222')
    bad = [{'name': 'evil', 'command': ['touch-marker'], 'needs': 'n'}]

    # The checkout is a PR branch that carries a malicious declaration. The base has none.
    repo = make_repo(tmp / 'src-branch')
    git(repo, 'checkout', '-q', '-b', 'pr')
    declare(repo, bad)
    got = preflight_cli(env, repo, tmp / 'src-branch-out', '--base', 'main', '--head', 'pr')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n' and not marker.exists(),
          f'source: a malicious declaration on the PR branch does not run ({got.returncode} {got.stdout!r})')
    check(preflight.check_declared(repo, 'main') == [] and not marker.exists(),
          'source: check_declared reads the base commit, so the PR branch copy is invisible')

    # The base declares something harmless. The PR branch edits it to run a command.
    repo = make_repo(tmp / 'src-edit')
    declare(repo, [{'name': 'fine', 'command': ['true'], 'needs': 'n'}])
    git(repo, 'checkout', '-q', '-b', 'pr')
    declare(repo, bad)
    got = preflight_cli(env, repo, tmp / 'src-edit-out', '--base', 'main', '--head', 'pr')
    check(got.returncode == 0 and not marker.exists(), 'source: a PR branch edit of the base declaration does not run')

    # An untracked declaration in the working tree on the base branch.
    repo = make_repo(tmp / 'src-untracked')
    declare(repo, bad, commit=False)
    got = preflight_cli(env, repo, tmp / 'src-untracked-out', '--base', 'main')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n' and not marker.exists(),
          f'source: an untracked declaration on the base branch does not run ({got.stdout!r})')

    # A dirty tracked declaration: the committed text wins over the working tree.
    repo = make_repo(tmp / 'src-dirty')
    declare(repo, [{'name': 'fine', 'command': ['true'], 'needs': 'n'}])
    (repo / '.conclave/preflight.json').write_text(json.dumps({'version': 1, 'checks': bad}))
    got = preflight_cli(env, repo, tmp / 'src-dirty-out', '--base', 'main')
    check(got.returncode == 0 and not marker.exists(), 'source: an uncommitted edit of the declaration does not run')

    # No base commit to read from: nothing runs, and the skip is a warning, not silence.
    repo = make_repo(tmp / 'src-noref')
    declare(repo, bad)
    got = preflight_cli(env, repo, tmp / 'src-noref-out')
    check(got.returncode == 0 and not marker.exists() and 'was not read' in got.stdout and 'Warnings' in got.stdout,
          f'source: with no base ref the file is not read and one warning says so ({got.returncode} {got.stdout!r})')

    # The committed declaration on the base does run (the control for the cases above).
    repo = make_repo(tmp / 'src-control')
    declare(repo, bad)
    got = preflight_cli(env, repo, tmp / 'src-control-out', '--base', 'main')
    check(got.returncode == 0 and marker.exists(), 'source: a declaration committed on the base does run')
    marker.unlink()

    # A declaration that is a symlink is refused, not followed.
    repo = make_repo(tmp / 'src-symlink')
    (repo / '.conclave').mkdir()
    (repo / '.conclave/preflight.json').symlink_to('../a.txt')
    git(repo, 'add', '.conclave/preflight.json')
    git(repo, 'commit', '-q', '-m', 'link')
    got = preflight_cli(env, repo, tmp / 'src-symlink-out', '--base', 'main')
    check(got.returncode == preflight.BLOCKED and 'must be a regular file' in got.stdout and not marker.exists(),
          f'source: a symlinked declaration is refused ({got.returncode} {got.stdout!r})')


def test_stacked_pr_declaration(tmp: Path) -> None:
    """A stacked PR's base is the lower PR's head. Its declaration never runs; the default branch's does."""
    bin_dir, evil = marker_bin(tmp, 'stacked-evil')
    good = tmp / 'stacked-good'
    write_exe(bin_dir / 'touch-good', f'#!/bin/sh\n: >{good}\n')
    write_exe(bin_dir / 'gh', GH)
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0', PR_BASE='feat-a')

    def stacked(name: str) -> Path:
        # main declares a harmless check. feat-a (PR 11's head, author-controlled) adds a command.
        # PR 12 is based on feat-a. Both branches are fetched, as in a normal clone.
        repo = make_repo(tmp / name)
        declare(repo, [{'name': 'fine', 'command': ['touch-good'], 'needs': 'n'}])
        git(repo, 'update-ref', 'refs/remotes/origin/main', 'main')
        git(repo, 'checkout', '-q', '-b', 'feat-a')
        declare(repo, [{'name': 'fine', 'command': ['touch-good'], 'needs': 'n'},
                       {'name': 'deps', 'command': ['touch-marker'], 'needs': 'n'}])
        git(repo, 'update-ref', 'refs/remotes/origin/feat-a', 'feat-a')
        git(repo, 'checkout', '-q', '-b', 'feat-b')
        return repo

    def reset() -> None:
        for path in (evil, good):
            path.unlink(missing_ok=True)

    # gh names the default branch: its declaration runs, the lower PR's command does not.
    repo = stacked('stacked-gh')
    reset()
    got = preflight_cli(dict(env, DEFAULT_BRANCH='main'), repo, tmp / 'stacked-gh-out', '--pr', '12')
    check(not evil.exists(), f'stacked: a command the lower PR adds to {preflight.DECLARATION} does not run '
          f'({got.returncode} {got.stdout!r})')
    check(got.returncode == 0 and good.exists() and got.stdout == 'preflight: ok\n',
          f'stacked: the default branch declaration runs instead ({got.returncode} {got.stdout!r})')

    # gh cannot answer: origin/HEAD names the default branch.
    repo = stacked('stacked-head')
    git(repo, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
    reset()
    got = preflight_cli(env, repo, tmp / 'stacked-head-out', '--pr', '12')
    check(got.returncode == 0 and good.exists() and not evil.exists(),
          f'stacked: origin/HEAD resolves the default branch when gh cannot ({got.returncode} {got.stdout!r})')

    # Neither resolves: nothing runs, one warning, and the PR base is never the fallback.
    repo = stacked('stacked-none')
    reset()
    got = preflight_cli(env, repo, tmp / 'stacked-none-out', '--pr', '12')
    check(got.returncode == 0 and not good.exists() and not evil.exists()
          and 'default branch could not be resolved' in got.stdout.split('## Warnings')[-1],
          f'stacked: no default branch skips the declared checks with a warning ({got.returncode} {got.stdout!r})')
    reset()


def test_command_rules(tmp: Path, port: int) -> None:
    """Shells, wrappers, inline interpreters and checkout paths are rejected. URLs are http(s) without userinfo."""
    bin_dir, marker = marker_bin(tmp, 'rules')
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0')
    touch = f'touch {marker}'
    rejected = [
        ['sh', '-c', touch], ['bash', '-lc', touch], ['/bin/sh', '-c', touch], ['zsh', '-c', touch],
        ['dash', '-c', touch], ['env', 'touch-marker'], ['/usr/bin/env', 'true'], ['xargs', 'true'],
        ['nohup', 'touch-marker'], ['timeout', '5', 'touch-marker'], ['sudo', 'true'], ['find', '.', '-exec', 'true', ';'],
        ['python3', '-c', 'print(1)'], ['python3.12', '-c', 'print(1)'], ['python', '-Sc', 'print(1)'],
        ['node', '-e', '1'], ['node', '--eval=1'], ['perl', '-e', '1'], ['ruby', '-e', '1'], ['php', '-r', '1'],
        ['./touch-marker'], ['bin/touch-marker'], ['../touch-marker'],
    ]
    for argv in rejected:
        checks, reason = preflight.parse_declaration(json.dumps({'version': 1, 'checks': [
            {'name': 'x', 'command': argv, 'needs': 'n'}]}))
        check(checks is None and reason, f'rules: {" ".join(argv[:2])} is rejected ({reason})')
    accepted = [['true'], ['git', 'status'], ['python3', 'check.py'], ['python3', '-m', 'pytest', '-x'],
                ['node', 'check.js', '-e'], ['/usr/bin/test', '-f', 'a.txt'], ['pg_isready', '-h', 'localhost']]
    for argv in accepted:
        checks, reason = preflight.parse_declaration(json.dumps({'version': 1, 'checks': [
            {'name': 'x', 'command': argv, 'needs': 'n'}]}))
        check(checks and not reason, f'rules: {" ".join(argv[:2])} is accepted ({reason})')

    # End to end: sh -c on the base is one blocking item and nothing runs.
    repo = make_repo(tmp / 'rules-sh')
    declare(repo, [{'name': 'wrapped', 'command': ['sh', '-c', touch], 'needs': 'n'}])
    got = preflight_cli(env, repo, tmp / 'rules-sh-out', '--base', 'main')
    check(got.returncode == preflight.BLOCKED and 'bad .conclave/preflight.json' in got.stdout
          and 'sh is a shell or wrapper' in got.stdout and not marker.exists(),
          f'rules: a committed sh -c declaration blocks and nothing runs ({got.returncode} {got.stdout!r})')

    # URLs: http(s) only, no userinfo, no redirect following.
    for label, url, reason in (
            ('userinfo', f'http://admin:SECRET-PASS-000@127.0.0.1:{port}/ok', 'user name or password'),
            ('user only', f'http://admin@127.0.0.1:{port}/ok', 'user name or password'),
            ('file', 'file:///etc/hostname', 'http or https'), ('ftp', 'ftp://127.0.0.1/x', 'http or https'),
            ('no host', 'http:///ok', 'http or https')):
        repo = make_repo(tmp / f'rules-url-{label.replace(" ", "-")}')
        declare(repo, [{'name': 'url check', 'url': url, 'needs': 'n'}])
        got = preflight_cli(env, repo, tmp / f'rules-url-{label.replace(" ", "-")}-out', '--base', 'main')
        check(got.returncode == preflight.BLOCKED and reason in got.stdout and 'SECRET-PASS-000' not in got.stdout + got.stderr,
              f'rules: a {label} url is rejected and no password is shown ({got.returncode} {got.stdout!r})')
    repo = make_repo(tmp / 'rules-redirect')
    declare(repo, [{'name': 'redirecting', 'url': f'http://127.0.0.1:{port}/redirect', 'needs': 'n'}])
    got = preflight_cli(env, repo, tmp / 'rules-redirect-out', '--base', 'main')
    check(got.returncode == preflight.BLOCKED and 'status 302, wanted 200' in got.stdout,
          f'rules: a redirect is an answer, not followed ({got.returncode} {got.stdout!r})')


def test_stage_hook(tmp: Path) -> None:
    bin_dir = tmp / 'hook-bin'
    write_exe(bin_dir / 'hook', HOOK)
    write_exe(bin_dir / 'sleeper', '#!/bin/sh\nsleep 10\n')
    write_exe(bin_dir / 'not-exec', '#!/bin/sh\nexit 0\n').chmod(0o644)
    log = tmp / 'hook.jsonl'
    dest = tmp / 'hook-run'
    dest.mkdir()
    env = base_env(bin_dir, CONCLAVE_STAGE_HOOK=f"{bin_dir / 'hook'} --flag 'two words'", HOOK_LOG=str(log))

    def emit(event: str, payload: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / 'stage_hook.py'), 'emit', event, '--dir', str(dest),
                               '--json', payload], env=dict(env, **(extra or {})), text=True, capture_output=True,
                              timeout=60)

    # (5) Argv shape, env, payload.
    got = emit('ballot_done', json.dumps({'wave': 2, 'ballot': 'p-1', 'pr': 1, 'outcome': 'ok'}))
    calls = hook_calls(log)
    payload = json.loads(calls[0]['payload']) if calls else {}
    check(got.returncode == 0 and len(calls) == 1, f'hook: called once per event ({calls})')
    check(calls[0]['argv'][:3] == ['--flag', 'two words', 'ballot_done'] and len(calls[0]['argv']) == 4
          and json.loads(calls[0]['argv'][3]) == payload and calls[0]['event'] == 'ballot_done',
          f"hook: argv is the shlex-split hook, the event, then one JSON payload; env matches ({calls[0]['argv']})")
    check(payload.get('event') == 'ballot_done' and payload.get('wave') == 2 and payload.get('dir') == str(dest)
          and str(payload.get('ts', '')).endswith('Z') and 'T' in payload.get('ts', ''),
          f'hook: payload has event, ts (UTC ISO), dir, and the event fields ({payload})')
    check(calls[0]['stdin'] == '' and not calls[0]['stdin_tty'], 'hook: stdin is /dev/null')

    # repros_done is reserved and accepted. Unknown names are logged and ignored.
    check('repros_done' in stage_hook.EVENTS, 'hook: repros_done is in the known events')
    check('ballot_started' in stage_hook.EVENTS and 'ballot_done' in stage_hook.EVENTS
          and 'wave' + '_done' not in stage_hook.EVENTS,
          f'hook: ballot_started and ballot_done replace the per-wave event ({stage_hook.EVENTS})')
    got = emit('repros_done', '{}')
    check(got.returncode == 0 and len(hook_calls(log)) == 2 and hook_calls(log)[1]['event'] == 'repros_done',
          'hook: repros_done is accepted and runs the hook')
    got = emit('not_an_event', '{}')
    check(got.returncode == 0 and len(hook_calls(log)) == 2 and 'not_an_event ignored: unknown event' in got.stderr
          and 'not_an_event ignored' in (dest / 'stage-hook.log').read_text(),
          'hook: an unknown event is logged and ignored, never raised')
    saved = {k: os.environ.pop(k) for k in ('CONCLAVE_STAGE_HOOK', 'COUNCIL_STAGE_HOOK') if k in os.environ}
    try:
        check(stage_hook.emit('ballot_done', {}, dest) is False, 'hook: unset hook runs nothing')
        os.environ['COUNCIL_STAGE_HOOK'] = env['CONCLAVE_STAGE_HOOK']
        os.environ['HOOK_LOG'] = str(log)
        check(stage_hook.emit('holistic_done', {'outcome': 'ok'}, dest) is True
              and hook_calls(log)[-1]['event'] == 'holistic_done',
              'hook: emit() for Python callers; COUNCIL_STAGE_HOOK is the alias')
    finally:
        os.environ.pop('COUNCIL_STAGE_HOOK', None)
        os.environ.pop('HOOK_LOG', None)
        os.environ.update(saved)

    # (6) Failing hooks never break the caller.
    (dest / 'stage-hook.log').unlink()
    for label, extra, want in (
            ('exit 1', {'HOOK_EXIT': '1'}, 'ballot_done failed: exit 1'),
            ('missing', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'no-such-hook')}, 'ballot_done failed: not found'),
            ('not executable', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'not-exec')}, 'ballot_done failed: not executable'),
            ('timeout', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'sleeper'), 'CONCLAVE_STAGE_HOOK_TIMEOUT': '1'},
             'ballot_done failed: timeout after 1s')):
        start = time.monotonic()
        got = emit('ballot_done', '{"wave": 2}', extra)
        elapsed = time.monotonic() - start
        text = (dest / 'stage-hook.log').read_text() if (dest / 'stage-hook.log').is_file() else ''
        check(got.returncode == 0 and f'stage-hook: {want}' in got.stderr and want in text and elapsed < 8,
              f'hook: a hook that fails ({label}) logs one line and exits 0 ({got.stderr.strip()} {elapsed:.1f}s)')
    check(str(bin_dir) not in (dest / 'stage-hook.log').read_text(), 'hook: the hook command is never logged')

    # (6) A failing hook leaves a blocked preflight at exit 3.
    repo = make_repo(tmp / 'hook-blocked-repo')
    declare(repo, '{bad')
    for label, extra in (('exit 1', {'HOOK_EXIT': '1'}), ('missing', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'nope')}),
                         ('timeout', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'sleeper'),
                                      'CONCLAVE_STAGE_HOOK_TIMEOUT': '1'})):
        out = tmp / f"hook-blocked-{label.replace(' ', '-')}"
        got = preflight_cli(dict(env, CONCLAVE_WAVE_PRECHECK='0', **extra), repo, out, '--base', 'main')
        text = (out / 'stage-hook.log').read_text() if (out / 'stage-hook.log').is_file() else ''
        check(got.returncode == preflight.BLOCKED and 'preflight_failed failed' in text,
              f'hook: a failing hook ({label}) leaves preflight exit 3 and writes stage-hook.log ({got.returncode} {text!r})')


def test_seats_and_targets(tmp: Path) -> None:
    bin_dir = tmp / 'seat-bin'
    write_exe(bin_dir / 'seat-usage', SEAT)
    write_exe(bin_dir / 'gh', GH)
    seat_json = tmp / 'seat.json'
    env = base_env(bin_dir, CONCLAVE_SEAT_USAGE=str(bin_dir / 'seat-usage'), SEAT_STUB_JSON=str(seat_json),
                   SEAT_LOG=str(tmp / 'seat.log'))
    repo = make_repo(tmp / 'seat-repo')

    # (9) Blocked seats block; inconclusive only warns; WAVE_PRECHECK=0 skips the seat check.
    seat_json.write_text(lineup_json(mode='ask'))
    got = preflight_cli(env, repo, tmp / 'seat-ask')
    check(got.returncode == preflight.BLOCKED and 'seats capped: seat-usage asked for a human' in got.stdout
          and 'every pool capped' in got.stdout and 'astra:capped' in got.stdout,
          f'seats: an ask route blocks and names the reason and skipped codes ({got.stdout!r})')
    check('Default: wait for the capped seat to reset' in got.stdout and '--voters' in got.stdout
          and 'CONCLAVE_PREFLIGHT=0' in got.stdout and '--wave-wait' not in got.stdout,
          f'seats: the exit-3 message offers the options and names a default ({got.stdout!r})')
    stack = preflight.check_seats(lambda *a, **k: subprocess.CompletedProcess(a, 0, lineup_json(mode='ask'), ''), caller='stack')
    check(stack and 'Default: wait for the capped seat to reset' in stack[0]['todo'] and '--wave-wait SECONDS' in stack[0]['todo']
          and 'CONCLAVE_PREFLIGHT=0' in stack[0]['todo'] and '--voters' not in stack[0]['todo'],
          f"seats: the stack-run message offers --wave-wait, not --voters ({stack and stack[0]['todo']!r})")
    check((tmp / 'seat.log').read_text().splitlines() == ['--route lineup --json --kind review'],
          'seats: preflight asks the same route as the wave gate')
    seat_json.write_text(lineup_json('grok'))
    got = preflight_cli(env, repo, tmp / 'seat-one')
    check(got.returncode == preflight.BLOCKED and 'fewer than 2 voters' in got.stdout, 'seats: one voter blocks')
    seat_json.write_text(lineup_json('fable', 'astra'))
    got = preflight_cli(env, repo, tmp / 'seat-ok')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n', f'seats: two voters pass ({got.stdout!r})')
    for label, extra in (('missing binary', {'CONCLAVE_SEAT_USAGE': str(tmp / 'no-seat-usage')}),
                         ('empty output', {'SEAT_STUB_JSON': '/dev/null'})):
        got = preflight_cli(dict(env, **extra), repo, tmp / f"seat-{label.replace(' ', '-')}")
        check(got.returncode == 0 and '## Blocking\n\n(none)' in got.stdout and 'seat check inconclusive' in got.stdout,
              f'seats: {label} is inconclusive and only warns ({got.stdout!r})')
    seat_json.write_text(lineup_json(mode='ask'))
    got = preflight_cli(dict(env, CONCLAVE_WAVE_PRECHECK='0'), repo, tmp / 'seat-skip')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n', 'seats: CONCLAVE_WAVE_PRECHECK=0 skips the seat check')
    got = preflight_cli(env, repo, tmp / 'seat-voters', '--no-seats')
    check(got.returncode == 0, 'seats: --no-seats (explicit --voters) skips the seat check')

    # (9) One shared function: stack-run re-exports it and Runner.precheck delegates.
    spec = importlib.util.spec_from_file_location('stack_run_for_preflight', SCRIPTS / 'stack-run.py')
    assert spec and spec.loader
    stack_run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stack_run)
    check(stack_run.precheck_verdict is preflight.precheck_verdict and stack_run.route_detail is preflight.route_detail,
          'seats: stack-run uses the preflight verdict functions, not a copy')
    saved = dict(os.environ)
    try:
        os.environ.update({k: v for k, v in env.items() if k in ('CONCLAVE_SEAT_USAGE', 'SEAT_STUB_JSON', 'SEAT_LOG')})
        for k in ('CONCLAVE_WAVE_PRECHECK', 'COUNCIL_WAVE_PRECHECK'):
            os.environ.pop(k, None)
        runner = types.SimpleNamespace(process=subprocess.run)
        seat_json.write_text(lineup_json(mode='ask'))
        blocked = stack_run.Runner.precheck(runner)
        seat_json.write_text(lineup_json('fable', 'astra'))
        passed = stack_run.Runner.precheck(runner)
        os.environ['CONCLAVE_SEAT_USAGE'] = str(tmp / 'no-seat-usage')
        missing = stack_run.Runner.precheck(runner)
        os.environ['CONCLAVE_WAVE_PRECHECK'] = '0'
        skipped = stack_run.Runner.precheck(runner)
    finally:
        os.environ.clear()
        os.environ.update(saved)
    check(blocked[0] == 'blocked' and 'every pool capped' in blocked[1] and passed == ('ok', 'voters claude,codex (stub)')
          and missing[0] == 'inconclusive' and skipped == ('skipped', 'CONCLAVE_WAVE_PRECHECK=0'),
          f'seats: Runner.precheck behaves as before ({blocked}, {passed}, {missing}, {skipped})')

    # (1) Targets: a missing PR blocks, a merged PR warns, an unresolvable ref blocks.
    env = dict(env, CONCLAVE_WAVE_PRECHECK='0')
    got = preflight_cli(dict(env, MISSING_PR='41'), repo, tmp / 'target-pr', '--pr', '41')
    check(got.returncode == preflight.BLOCKED and '1. PR #41 not found' in got.stdout,
          f'targets: a PR gh cannot find blocks ({got.stdout!r})')
    got = preflight_cli(dict(env, PR_STATE='MERGED'), repo, tmp / 'target-merged', '--pr', '7')
    check(got.returncode == 0 and 'PR #7 is merged' in got.stdout.split('## Warnings')[1],
          f'targets: a merged PR is a warning, not blocking ({got.stdout!r})')
    got = preflight_cli(env, repo, tmp / 'target-refs', '--base', 'main', '--head', 'no-such-ref')
    check(got.returncode == preflight.BLOCKED and 'ref no-such-ref does not resolve' in got.stdout
          and 'ref main ' not in got.stdout, f'targets: an unresolvable head blocks; a good base does not ({got.stdout!r})')
    got = preflight_cli(env, repo, tmp / 'target-dash', '--base=-x')
    check(got.returncode == preflight.BLOCKED and 'is not a valid ref' in got.stdout,
          'targets: a ref that looks like a flag is refused')

    # Handoff: under stack-run only the target is checked.
    seat_json.write_text(lineup_json(mode='ask'))
    declare(repo, '{bad')
    hand = dict(env, CONCLAVE_PREFLIGHT_DONE='1')
    hand.pop('CONCLAVE_WAVE_PRECHECK')
    got = preflight_cli(hand, repo, tmp / 'handoff-ok', '--base', 'main')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n',
          f'handoff: CONCLAVE_PREFLIGHT_DONE=1 skips the seat and repo checks ({got.stdout!r})')
    got = preflight_cli(hand, repo, tmp / 'handoff-bad', '--base', 'no-such-base')
    check(got.returncode == preflight.BLOCKED and 'no-such-base' in got.stdout and 'seats' not in got.stdout
          and 'preflight.json' not in got.stdout, 'handoff: the target is still checked')


class Conclave:
    """A real conclave.sh launch with every CLI stubbed. Voter stubs log any call
    that is not a routing probe (--version, models)."""

    def __init__(self, tmp: Path, name: str) -> None:
        self.work = tmp / f'conclave-{name}'
        self.bin = self.work / 'bin'
        home = self.work / 'home'
        (home / '.codex').mkdir(parents=True)
        (home / '.codex/models_cache.json').write_text(
            json.dumps({'models': [{'slug': 'gpt-test-astra', 'visibility': 'list', 'priority': 1}]}))
        (self.work / 'muse-catalog.json').write_text(json.dumps({'models': [
            {'modelId': 'muse-wonder-1.0', 'releaseDate': '2026-10-12'},
            {'modelId': 'muse-wonder-full-1.0', 'releaseDate': '2026-10-12'}]}))
        self.voters = self.work / 'voters.log'
        probe = '''\
            #!/bin/sh
            case "${{1:-}}" in
              --version) echo "{version}"; exit 0 ;;
              models) {models}; exit 0 ;;
            esac
            printf '%s %s\\n' "$(basename "$0")" "$*" >>"$VOTER_LOG"
            exit 0
            '''
        for name, version, models in (('codex', 'codex-cli 0.200.0', ':'), ('grok', 'grok 0', 'echo "Default model: grok-test-1"'),
                                      ('claude', 'claude 0', ':'), ('muse', 'muse 0', ':'),
                                      ('cursor-agent', 'cursor 0', ':'), ('agy', 'agy 0', ':')):
            write_exe(self.bin / name, probe.format(version=version, models=models))
        write_exe(self.bin / 'seat-usage', SEAT)
        write_exe(self.bin / 'gh', GH)
        write_exe(self.bin / 'hook', HOOK)
        self.seat = self.work / 'seat.json'
        self.seat.write_text(lineup_json('fable', 'astra'))
        self.hook_log = self.work / 'hook.jsonl'
        self.repo = make_repo(self.work / 'target')
        self.id = f'preflight-test-{name}-{os.getpid()}'
        self.dir = ROOT / 'tmp/council' / self.id
        self.dir.mkdir(parents=True)
        (self.dir / 'brief.md').write_text('kind: review\ndifficulty: low\n\n# Conclave brief: review\n\n## Goal\nCheck the change.\n')
        self.env = base_env(
            self.bin, HOME=str(home), CONCLAVE_ARGON='0', CONCLAVE_GITHUB_HTTPS='0', CONCLAVE_SKIP_UPDATE_CHECK='1',
            CONCLAVE_CODEX_BIN=str(self.bin / 'codex'), CONCLAVE_SEAT_USAGE=str(self.bin / 'seat-usage'),
            CONCLAVE_AGY=str(self.bin / 'agy'), CONCLAVE_MUSE_CATALOG=str(self.work / 'muse-catalog.json'),
            CONCLAVE_JEV_OP='/bin/false', SEAT_USAGE_RUNS=str(self.work / 'runs.jsonl'),
            SEAT_STUB_JSON=str(self.seat), SEAT_LOG=str(self.work / 'seat.log'), VOTER_LOG=str(self.voters),
            HOOK_LOG=str(self.hook_log), CONCLAVE_STAGE_HOOK=str(self.bin / 'hook'))
        for name in ('TYPESAFE_API_KEY', 'JEV_API_KEY'):
            self.env.pop(name, None)

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(['bash', str(SCRIPTS / 'conclave.sh'), self.id, *args],
                              env=dict(self.env, **extra), cwd=str(self.repo), text=True, capture_output=True,
                              timeout=300)

    def needs(self) -> str:
        path = self.dir / 'needs-human.md'
        return path.read_text() if path.is_file() else ''

    def launched(self) -> list[str]:
        return self.voters.read_text().splitlines() if self.voters.exists() else []

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def test_conclave(tmp: Path) -> None:
    c = Conclave(tmp, 'launch')
    try:
        # (1) A ref that does not resolve: exit 3 before any voter.
        got = c.run('--repo', str(c.repo), '--base', 'main', '--head', 'no-such-head')
        detail = f'{got.returncode} stdout={got.stdout[-400:]!r} stderr={got.stderr[-600:]!r}'
        check(got.returncode == 3, f'conclave.sh: an unresolvable --head exits 3 ({detail})')
        check(not c.launched(), f'conclave.sh: no voter was invoked ({c.launched()})')
        needs = c.needs()
        check('## Blocking\n\n1. ref no-such-head does not resolve' in needs and '\n2. ' not in needs.split('## Warnings')[0]
              and needs in got.stdout, f'conclave.sh: needs-human.md has the one blocking ref and matches stdout ({needs!r})')
        events = hook_calls(c.hook_log)
        payload = json.loads(events[0]['payload']) if events else {}
        check([e['event'] for e in events] == ['preflight_failed'] and payload.get('ballot') == c.id
              and payload.get('blocking') == 1 and payload.get('needs_file') == str(c.dir / 'needs-human.md'),
              f'conclave.sh: preflight_failed fires once with ballot, blocking, needs_file ({events})')

        # (1) A PR gh says does not exist.
        c.hook_log.unlink()
        got = c.run('--repo', str(c.repo), '--pr', '404', MISSING_PR='404')
        check(got.returncode == 3 and not c.launched() and '1. PR #404 not found' in c.needs(),
              f'conclave.sh: a missing PR exits 3 before any voter ({got.returncode} {c.needs()!r} {got.stderr[-400:]!r})')

        # (2) Several failures at once: one list, numbered, same text on stdout.
        declare(c.repo, [{'name': 'test db reachable', 'command': ['false'], 'needs': 'start the test database'}])
        c.seat.write_text(lineup_json(mode='ask'))
        got = c.run('--repo', str(c.repo), '--base', 'main', '--head', 'no-such-head')
        needs = c.needs()
        blocking = needs.split('## Warnings')[0]
        check(got.returncode == 3 and not c.launched(), f'conclave.sh: three failures exit 3, no voter ({got.returncode})')
        check(needs.startswith('# Needs from human\n') and needs.count('# Needs from human') == 1
              and '1. ref no-such-head does not resolve' in blocking and '2. seats capped' in blocking
              and '3. test db reachable: exit 1. Do: start the test database' in blocking and '\n4. ' not in blocking,
              f'conclave.sh: bad ref, capped seats and a failed declared check share one numbered list ({needs!r})')
        check(needs in got.stdout and got.stdout.count('# Needs from human') == 1,
              'conclave.sh: stdout carries the same list once')

        # (9) CONCLAVE_PREFLIGHT=0: no list, no exit 3; the old failure path, still no voter.
        (c.dir / 'needs-human.md').unlink()
        got = c.run('--repo', str(c.repo), '--base', 'main', '--head', 'no-such-head', CONCLAVE_PREFLIGHT='0')
        check(got.returncode not in (0, 3) and not c.needs() and '# Needs from human' not in got.stdout
              and not c.launched(), f'conclave.sh: CONCLAVE_PREFLIGHT=0 skips preflight ({got.returncode})')

        git(c.repo, 'reset', '-q', '--hard', 'HEAD~1')  # drop the declared failing check
        # A dry run shows the stop a real launch would hit: same list, same exit 3, nothing launched or fired.
        (c.dir / 'needs-human.md').unlink(missing_ok=True)
        c.hook_log.unlink(missing_ok=True)
        c.seat.write_text(lineup_json(mode='ask'))
        real = c.run('--repo', str(c.repo), '--base', 'main', '--head', 'main')
        c.hook_log.unlink(missing_ok=True)
        (c.dir / 'needs-human.md').unlink(missing_ok=True)
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'main')
        preview = c.dir / 'preview/needs-human.md'
        check(real.returncode == 3 and got.returncode == 3,
              f'dry run: a capped route exits 3 in a dry run as in a real launch ({real.returncode} {got.returncode})')
        check('seats capped' in got.stdout and 'a real launch would stop here with exit 3' in got.stderr
              and preview.is_file() and 'seats capped' in preview.read_text(),
              f'dry run: the stop and its reason are shown, and written under preview/ ({got.stdout[-300:]!r} {got.stderr[-300:]!r})')
        check(not c.needs() and not hook_calls(c.hook_log) and not c.launched(),
              'dry run: no needs-human.md in the ballot dir, no stage event, no voter')

        # With seats free the dry run reports ok and exits 0.
        c.seat.write_text(lineup_json('fable', 'astra'))
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'main')
        check(got.returncode == 0 and 'preflight: ok' in got.stdout and 'would stop' not in got.stderr,
              f'dry run: clear preflight exits 0 ({got.returncode} {got.stdout[-200:]!r})')

        # CONCLAVE_PREFLIGHT=0 keeps the dry run a plain preview.
        c.seat.write_text(lineup_json(mode='ask'))
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'main', CONCLAVE_PREFLIGHT='0')
        check(got.returncode == 0 and 'preflight:' not in got.stdout + got.stderr and 'Needs from human' not in got.stdout,
              f'dry run: CONCLAVE_PREFLIGHT=0 skips the preview check too ({got.returncode})')
    finally:
        c.close()


def test_post_plan_ready(tmp: Path) -> None:
    """(8) post_plan_ready fires at the end of the findings gate when it binds comments to post."""
    c = Conclave(tmp, 'post')
    finding = {'id': 'f1', 'claim': 'SECRET-CLAIM-TEXT breaks', 'file': 'a.txt', 'line': 1, 'cited_code': 'one',
               'evidence': 'read', 'repro_executed': False, 'legs': {'A': 'found', 'B': 'found'}}
    try:
        for label, findings, choice, fires in (('post', [finding], 'post', True), ('hold', [finding], 'hold', False),
                                               ('clean', [], None, False)):
            c.hook_log.unlink(missing_ok=True)
            (c.dir / 'findings.json').write_text(json.dumps({'findings': findings}))
            canned = c.work / f'canned-{label}.json'
            canned.write_text(json.dumps({'model': 'test', 'answers': {'f1': {'choice': choice, 'confidence': 0.99}}}))
            got = c.run('--findings', '--canned', str(canned))
            events = [e for e in hook_calls(c.hook_log) if e['event'] == 'post_plan_ready']
            judged = (c.dir / 'judgment.md').read_text() if (c.dir / 'judgment.md').is_file() else ''
            if not fires:
                check(got.returncode == 0 and judged and not events,
                      f'post_plan_ready: does not fire on a {label} gate ({got.returncode} {got.stderr[-300:]!r})')
                continue
            payload = json.loads(events[0]['payload']) if events else {}
            check(got.returncode == 0 and 'decision: comments to post (1 post' in judged and len(events) == 1,
                  f'post_plan_ready: fires once when the gate binds comments to post ({got.returncode} {got.stderr[-300:]!r})')
            check(payload.get('drafted') == 1 and payload.get('decision') == 'comments to post'
                  and payload.get('dir') == str(c.dir) and payload.get('ballot') == c.id
                  and 'SECRET-CLAIM-TEXT' not in events[0]['payload'],
                  f'post_plan_ready: payload has dir, drafted, decision and no comment text ({payload})')
        c.hook_log.unlink(missing_ok=True)
        (c.dir / 'findings.json').write_text(json.dumps({'findings': [finding]}))
        got = c.run('--findings', '--canned', str(c.work / 'canned-post.json'), CONCLAVE_STAGE_HOOK=str(c.bin / 'nope'))
        check(got.returncode == 0 and 'post_plan_ready failed: not found' in got.stderr,
              'post_plan_ready: a missing hook leaves the findings gate at exit 0')
    finally:
        c.close()


def test_nul_declaration(tmp: Path) -> None:
    """A NUL byte in a declared argv is one blocking malformed-declaration item, not a crash."""
    checks, reason = preflight.parse_declaration(json.dumps({'version': 1, 'checks': [
        {'name': 'x', 'command': ['true', '\u0000'], 'needs': 'n'}]}))
    check(checks is None and 'NUL' in reason, f'nul: parse_declaration rejects a NUL in an argument ({reason!r})')
    bin_dir = tmp / 'nul-bin'
    bin_dir.mkdir()
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0')
    repo = make_repo(tmp / 'nul-decl')
    declare(repo, json.dumps({'version': 1, 'checks': [
        {'name': 'nul arg', 'command': ['true', '\u0000'], 'needs': 'n'}]}))
    dest = tmp / 'nul-decl-out'
    got = preflight_cli(env, repo, dest, '--base', 'main')
    needs = (dest / 'needs-human.md').read_text() if (dest / 'needs-human.md').is_file() else ''
    check(got.returncode == preflight.BLOCKED and needs.count('bad .conclave/preflight.json') == 1
          and 'NUL' in needs and '\n2. ' not in needs and 'Traceback' not in got.stderr,
          f'nul: a committed NUL argv is one blocking item, exit 3, no crash ({got.returncode} {needs!r} {got.stderr[-200:]!r})')


def test_nul_run_command(tmp: Path) -> None:
    """Any Popen argument error (here an embedded NUL) is a reason class, not an exception."""
    try:
        got = preflight.run_command(['true', 'a\0b'], tmp, 5)
    except ValueError as exc:
        got = f'raised ValueError: {exc}'
    check(got == 'could not run', f'nul: run_command reports a Popen ValueError as could not run ({got!r})')


def test_plan_cli(tmp: Path) -> None:
    """preflight.py --plan lists each declared command with its argv and source ref, and runs none."""
    bin_dir = tmp / 'plan-bin'
    bin_dir.mkdir()
    write_exe(bin_dir / 'gh', GH)
    marker = tmp / 'plan-cli-ran'
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0', DEFAULT_BRANCH='main')
    repo = make_repo(tmp / 'plan-cli')
    declare(repo, [{'name': 'makes marker', 'command': ['touch', str(marker)], 'needs': 'n'},
                   {'name': 'would block', 'command': ['false'], 'needs': 'n'}])
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'main')
    dest = tmp / 'plan-cli-out'
    got = preflight_cli(env, repo, dest, '--pr', '12', '--plan')
    detail = f'{got.returncode} stdout={got.stdout!r} stderr={got.stderr[-300:]!r}'
    check(not marker.exists(), f'plan: preflight.py --plan does not run a declared command ({detail})')
    check(got.returncode == 0 and 'would stop' not in got.stderr,
          f'plan: a declared command that would fail does not block a dry run ({detail})')
    argv = json.dumps(['touch', str(marker)])
    check(f'makes marker: {argv} from origin/main. not run (dry run)' in got.stdout
          and 'would block: ["false"] from origin/main. not run (dry run)' in got.stdout,
          f'plan: each declared command is listed with its argv and default-branch ref ({detail})')
    check(not (dest / 'needs-human.md').exists(), 'plan: a listed command is not a needs-human item')

    # The declaration is still parsed and validated: a NUL argv still blocks the dry run.
    declare(repo, json.dumps({'version': 1, 'checks': [{'name': 'nul', 'command': ['true', '\u0000'], 'needs': 'n'}]}))
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'main')
    got = preflight_cli(env, repo, dest, '--pr', '12', '--plan')
    check(got.returncode == preflight.BLOCKED and 'bad .conclave/preflight.json' in got.stdout and 'NUL' in got.stdout,
          f'plan: a malformed declaration still blocks the dry run ({got.returncode} {got.stdout!r})')

    # The control: a real launch runs it.
    declare(repo, [{'name': 'makes marker', 'command': ['touch', str(marker)], 'needs': 'n'}])
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'main')
    got = preflight_cli(env, repo, dest, '--pr', '12')
    check(got.returncode == 0 and marker.exists(), f'plan: without --plan the declared command runs ({got.returncode})')


def test_plan_dry_run(tmp: Path) -> None:
    """conclave.sh --dry-run lists the declared commands and runs none."""
    c = Conclave(tmp, 'plan')
    marker = tmp / 'plan-dry-run-ran'
    try:
        declare(c.repo, [{'name': 'makes marker', 'command': ['touch', str(marker)], 'needs': 'n'}])
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'main')
        detail = f'{got.returncode} stdout={got.stdout[-500:]!r} stderr={got.stderr[-300:]!r}'
        check(not marker.exists(), f'plan: conclave.sh --dry-run does not run a declared command ({detail})')
        argv = json.dumps(['touch', str(marker)])
        check(got.returncode == 0 and f'makes marker: {argv} from main. not run (dry run)' in got.stdout
              and not c.launched() and not hook_calls(c.hook_log),
              f'plan: the dry run lists the command, its argv and its source ref ({detail})')
    finally:
        c.close()


def test_missing_repo_dir(tmp: Path) -> None:
    """A --repo that is not a directory is named as such, not blamed on gh. A missing gh keeps its own message."""
    bin_dir = tmp / 'repo-dir-bin'
    write_exe(bin_dir / 'gh', GH)
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0')
    missing = tmp / 'no-such-repo-dir'
    dest = tmp / 'repo-dir-out'
    got = preflight_cli(env, missing, dest, '--pr', '2')
    detail = f'{got.returncode} stdout={got.stdout!r} stderr={got.stderr[-300:]!r}'
    check(got.returncode == preflight.BLOCKED and f'--repo {missing} is not a directory' in got.stdout
          and 'gh not found' not in got.stdout,
          f'repo dir: a missing --repo directory is reported with its path, not as gh not found ({detail})')


def test_missing_gh(tmp: Path) -> None:
    """A truly missing gh, with a --repo that exists, still says gh is missing."""
    repo = make_repo(tmp / 'repo-dir-ok')
    no_gh = tmp / 'repo-dir-no-gh'
    no_gh.mkdir()
    saved = os.environ.get('PATH')
    try:
        os.environ['PATH'] = str(no_gh)
        found = preflight.check_pr(repo, 2)
    finally:
        os.environ['PATH'] = saved or ''
    check(len(found) == 1 and found[0]['what'] == 'PR #2: gh not found' and 'is not a directory' not in found[0]['what'],
          f'repo dir: a truly missing gh keeps its own message ({found!r})')


class Hits(http.server.BaseHTTPRequestHandler):
    """Counts every request it gets. A dry run must send none."""
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        Hits.hits.append(self.path)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args) -> None:
        pass


def hit_server() -> http.server.ThreadingHTTPServer:
    Hits.hits = []
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Hits)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def url_declaring_repo(tmp: Path, name: str, url: str) -> tuple[Path, dict]:
    bin_dir = tmp / f'{name}-bin'
    write_exe(bin_dir / 'gh', GH)
    env = base_env(bin_dir, CONCLAVE_WAVE_PRECHECK='0', DEFAULT_BRANCH='main')
    repo = make_repo(tmp / name)
    declare(repo, [{'name': 'registry up', 'url': url, 'needs': 'n'}])
    git(repo, 'update-ref', 'refs/remotes/origin/main', 'main')
    return repo, env


def test_plan_url_cli(tmp: Path) -> None:
    """preflight.py --plan fetches no declared url. It lists it with its name and ref instead."""
    server = hit_server()
    url = f'http://127.0.0.1:{server.server_address[1]}/probe'
    try:
        repo, env = url_declaring_repo(tmp, 'plan-url-cli', url + '?tok=x')
        got = preflight_cli(env, repo, tmp / 'plan-url-cli-out', '--pr', '12', '--plan')
        detail = f'{got.returncode} hits={Hits.hits} stdout={got.stdout!r} stderr={got.stderr[-300:]!r}'
        check(Hits.hits == [], f'plan url: preflight.py --plan fetches no declared url ({detail})')
        check(got.returncode == 0 and f'1. registry up: {url} from origin/main. not run (dry run)' in got.stdout
              and 'tok=' not in got.stdout,
              f'plan url: the url is listed with its name and ref, without its query ({detail})')
    finally:
        server.shutdown()


def test_plan_url_dry_run(tmp: Path) -> None:
    """conclave.sh --dry-run fetches no declared url and lists it."""
    server = hit_server()
    url = f'http://127.0.0.1:{server.server_address[1]}/probe'
    c = Conclave(tmp, 'plan-url')
    try:
        declare(c.repo, [{'name': 'registry up', 'url': url, 'needs': 'n'}])
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'main')
        detail = f'{got.returncode} hits={Hits.hits} stdout={got.stdout[-500:]!r} stderr={got.stderr[-300:]!r}'
        check(Hits.hits == [], f'plan url: conclave.sh --dry-run fetches no declared url ({detail})')
        check(got.returncode == 0 and f'1. registry up: {url} from main. not run (dry run)' in got.stdout,
              f'plan url: conclave.sh --dry-run lists the url ({detail})')
    finally:
        c.close()
        server.shutdown()


def test_url_real_launch(tmp: Path) -> None:
    """The control: without --plan the declared url is fetched once."""
    server = hit_server()
    url = f'http://127.0.0.1:{server.server_address[1]}/probe'
    try:
        repo, env = url_declaring_repo(tmp, 'url-real', url + '?tok=x')
        got = preflight_cli(env, repo, tmp / 'url-real-out', '--pr', '12')
        check(got.returncode == 0 and Hits.hits == ['/probe?tok=x'],
              f'plan url: without --plan the declared url is fetched ({got.returncode} hits={Hits.hits} {got.stdout!r})')
    finally:
        server.shutdown()

def main() -> None:
    (ROOT / 'tmp').mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Status)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='preflight-tests-', dir=ROOT / 'tmp') as raw:
            tmp = Path(raw)
            port = server.server_address[1]
            test_declared(tmp, port)
            test_secrets(tmp, port)
            test_declaration_source(tmp)
            test_stacked_pr_declaration(tmp)
            test_command_rules(tmp, port)
            test_nul_declaration(tmp)
            test_nul_run_command(tmp)
            test_stage_hook(tmp)
            test_seats_and_targets(tmp)
            test_conclave(tmp)
            test_post_plan_ready(tmp)
            test_plan_cli(tmp)
            test_plan_dry_run(tmp)
            test_missing_repo_dir(tmp)
            test_missing_gh(tmp)
            test_plan_url_cli(tmp)
            test_plan_url_dry_run(tmp)
            test_url_real_launch(tmp)
    finally:
        server.shutdown()
    print('ok')


if __name__ == '__main__':
    main()
