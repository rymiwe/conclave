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


def declare(repo: Path, checks: list[dict] | str) -> None:
    (repo / '.conclave').mkdir(exist_ok=True)
    text = checks if isinstance(checks, str) else json.dumps({'version': 1, 'checks': checks})
    (repo / '.conclave/preflight.json').write_text(text)


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
                      'author': {'login': 'someone', 'is_bot': False}, 'commits': []}))
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
        code = {'/ok': 200, '/bad': 503}.get(self.path.split('?')[0], 404)
        self.send_response(code)
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
    got = preflight_cli(env, repo, dest)
    check(preflight.check_declared(repo) == [], 'declared: a missing .conclave/preflight.json yields no items')
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
        got = preflight_cli(env, repo, dest)
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
    got = preflight_cli(env, repo, dest)
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
    got = preflight_cli(env, repo, dest)
    check(got.returncode == 0 and '## Blocking\n\n(none)' in got.stdout and 'soft check' in got.stdout
          and (dest / 'needs-human.md').is_file(),
          f'declared: warnings alone exit 0 and still write the list ({got.returncode} {got.stdout!r})')

    # (3) Commands run in the target repo with the caller's env.
    repo = make_repo(tmp / 'decl-env')
    declare(repo, [{'name': 'env and cwd', 'command': ['sh', '-c', 'test -f a.txt && test "$PREFLIGHT_PROBE" = yes'],
                    'needs': 'n'}])
    got = preflight_cli(dict(env, PREFLIGHT_PROBE='yes'), repo, tmp / 'decl-env-out')
    check(got.returncode == 0 and got.stdout == 'preflight: ok\n',
          f'declared: a command runs with cwd = target repo and the caller env ({got.stdout!r})')

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
    repo = make_repo(tmp / 'secret-repo')
    declare(repo, [
        {'name': 'chatty check', 'needs': 'fix the chatty thing',
         'command': ['sh', '-c', 'echo SECRET-OUT-123; echo SECRET-ERR-456 >&2; echo "$LEAKY_VALUE"; exit 1']},
        {'name': 'token url', 'url': f'http://127.0.0.1:{port}/bad?token=SECRET-TOKEN-789', 'needs': 'fix the token url'},
        {'name': 'userinfo url', 'url': f'http://admin:SECRET-PASS-000@127.0.0.1:{port}/bad#frag', 'needs': 'fix the login url'},
    ])
    dest = tmp / 'secret-out'
    got = preflight_cli(env, repo, dest)
    needs = (dest / 'needs-human.md').read_text() if (dest / 'needs-human.md').is_file() else ''
    log = (dest / 'stage-hook.log').read_text() if (dest / 'stage-hook.log').is_file() else ''
    payloads = ''.join(c['payload'] or '' for c in hook_calls(hook_log))
    check(got.returncode == preflight.BLOCKED and 'chatty check: exit 1' in needs and 'token url' in needs
          and 'userinfo url' in needs, f'secrets: the three checks fail and block ({got.returncode} {needs!r})')
    check(f'http://127.0.0.1:{port}/bad)' in needs, 'secrets: a shown URL keeps host and path only')
    check(log and 'preflight_failed failed: exit 1' in log, f'secrets: the failing hook wrote stage-hook.log ({log!r})')
    for secret in SECRETS:
        check(all(secret not in text for text in (got.stdout, got.stderr, needs, log, payloads)),
              f'secrets: {secret} is in none of stdout, stderr, needs-human.md, stage-hook.log, hook payload')
    check('token=' not in needs + got.stdout and 'admin' not in needs + got.stdout and '#frag' not in needs,
          'secrets: no query, userinfo, or fragment is shown')


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
    got = emit('wave_done', json.dumps({'wave': 2, 'ballots': ['p-1'], 'outcomes': ['ok']}))
    calls = hook_calls(log)
    payload = json.loads(calls[0]['payload']) if calls else {}
    check(got.returncode == 0 and len(calls) == 1, f'hook: called once per event ({calls})')
    check(calls[0]['argv'][:3] == ['--flag', 'two words', 'wave_done'] and len(calls[0]['argv']) == 4
          and json.loads(calls[0]['argv'][3]) == payload and calls[0]['event'] == 'wave_done',
          f"hook: argv is the shlex-split hook, the event, then one JSON payload; env matches ({calls[0]['argv']})")
    check(payload.get('event') == 'wave_done' and payload.get('wave') == 2 and payload.get('dir') == str(dest)
          and str(payload.get('ts', '')).endswith('Z') and 'T' in payload.get('ts', ''),
          f'hook: payload has event, ts (UTC ISO), dir, and the event fields ({payload})')
    check(calls[0]['stdin'] == '' and not calls[0]['stdin_tty'], 'hook: stdin is /dev/null')

    # repros_done is reserved and accepted. Unknown names are logged and ignored.
    check('repros_done' in stage_hook.EVENTS, 'hook: repros_done is in the known events')
    got = emit('repros_done', '{}')
    check(got.returncode == 0 and len(hook_calls(log)) == 2 and hook_calls(log)[1]['event'] == 'repros_done',
          'hook: repros_done is accepted and runs the hook')
    got = emit('not_an_event', '{}')
    check(got.returncode == 0 and len(hook_calls(log)) == 2 and 'not_an_event ignored: unknown event' in got.stderr
          and 'not_an_event ignored' in (dest / 'stage-hook.log').read_text(),
          'hook: an unknown event is logged and ignored, never raised')
    saved = {k: os.environ.pop(k) for k in ('CONCLAVE_STAGE_HOOK', 'COUNCIL_STAGE_HOOK') if k in os.environ}
    try:
        check(stage_hook.emit('wave_done', {}, dest) is False, 'hook: unset hook runs nothing')
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
            ('exit 1', {'HOOK_EXIT': '1'}, 'wave_done failed: exit 1'),
            ('missing', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'no-such-hook')}, 'wave_done failed: not found'),
            ('not executable', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'not-exec')}, 'wave_done failed: not executable'),
            ('timeout', {'CONCLAVE_STAGE_HOOK': str(bin_dir / 'sleeper'), 'CONCLAVE_STAGE_HOOK_TIMEOUT': '1'},
             'wave_done failed: timeout after 1s')):
        start = time.monotonic()
        got = emit('wave_done', '{"wave": 2}', extra)
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
        got = preflight_cli(dict(env, CONCLAVE_WAVE_PRECHECK='0', **extra), repo, out)
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

        # Dry run stays a preview: no preflight.
        got = c.run('--dry-run', '--repo', str(c.repo), '--base', 'main', '--head', 'no-such-head')
        check('# Needs from human' not in got.stdout and 'preflight: ok' not in got.stdout and not c.needs(),
              f'conclave.sh: --dry-run runs no preflight ({got.returncode})')
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
            test_stage_hook(tmp)
            test_seats_and_targets(tmp)
            test_conclave(tmp)
            test_post_plan_ready(tmp)
    finally:
        server.shutdown()
    print('ok')


if __name__ == '__main__':
    main()
