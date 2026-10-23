#!/usr/bin/env python3
"""Offline stack runner, reconciliation, and Cursor Fable checks.

Every external service is a PATH stub. Git integration uses local repositories
and local pull refs. All fixtures live inside this worktree and are removed.
"""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import os
from contextlib import redirect_stderr
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f'FAIL {label}')
    print(f'ok {label}')


def prompt_outside_repo(text: str, *paths: str) -> str:
    """Drop repo paths before a vendor-word scan. A .claude checkout is not a prompt word."""
    for path in sorted((p for p in paths if p), key=len, reverse=True):
        text = text.replace(path, ' ')
    return text


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / f'{name}.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding='utf-8')
    path.chmod(0o755)


def run(args: list[str], env: dict | None = None, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)


GH = '''\
#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
def row(n):
    return {'number': n, 'title': f'PR {n}', 'url': f'https://example.test/{n}',
            'body': 'domain change', 'headRefName': f'branch-{n}',
            'baseRefName': 'main' if n == 1 else f'branch-{1 if os.environ.get("BRANCHED") else n-1}',
            'isCrossRepository': False, 'headRepositoryOwner': {'login': 'test'},
            'headRefOid': os.environ.get('TOP_OID', 'a' * 40)}
if args[:2] == ['repo', 'view']:
    data = {'defaultBranchRef': {'name': 'main'}}
elif args[:2] == ['pr', 'view']:
    n = int(args[2]); data = row(n)
    if args[-1] == 'files,changedFiles':
        data = {'files': [{'path': 'a.rb'}], 'changedFiles': 1}
elif args[:2] == ['pr', 'list']:
    flag = '--base' if '--base' in args else '--head'
    branch = args[args.index(flag)+1]
    key = 'baseRefName' if flag == '--base' else 'headRefName'
    data = [row(n) for n in (1,2,3) if row(n)[key] == branch]
else:
    sys.exit('unexpected gh ' + str(args))
print(json.dumps(data))
'''

COUNCIL = '''\
#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(['council', args, os.environ.get('COUNCIL_CHAIR_MODEL'), os.environ.get('CONCLAVE_CHAIR')])+'\\n')
d = pathlib.Path(os.environ['FIXTURE_ROOT'])/'tmp/council'/args[0]
d.mkdir(parents=True, exist_ok=True)
if '--dry-run' in args: sys.exit(0)
if '--findings' in args:
    (d/'judgment.md').write_text('head_oid: ' + 'a'*40 + '\\n')
    (d/'decide.md').write_text('f1 — **what breaks, for whom**: TODO(chair)\\n**how sure**: read\\n**later PRs that change this file**: none\\n**recommend**: TODO(chair)\\n')
else:
    (d/'blind').mkdir(exist_ok=True)
    (d/'blind/leg-A.md').write_text('a blinded report')
    (d/'errors.md').write_text(os.environ.get('PHASE1_ERRORS', '(none)')+'\\n')
    if os.environ.get('PHASE1_SLEEP'):
        import time
        pathlib.Path(os.environ['CHILD_PID']).write_text(str(os.getpid()))
        time.sleep(60)
    if args[0].endswith(os.environ.get('OPUS_CAP_PR', 'never')):
        (d/'voter-opus.fail').write_text('failed')
        (d/'voter-opus.err').write_text(os.environ.get('OPUS_ERR', ''))
        (d/'voter-opus.json').write_text(os.environ.get('OPUS_JSON', '{}'))
        sys.exit(1)
    if args[0].endswith(os.environ.get('OVERFLOW_PR', 'never')):
        (d/'voter-claude.via').write_text('cursor\\n')
    sys.exit(int(os.environ.get('PHASE1_EXIT', '0')))
'''

CURSOR = '''\
#!/usr/bin/env python3
import json, os, pathlib, re, signal, subprocess, sys, time
args = sys.argv[1:]; prompt = args[-1]
with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(['cursor', args, os.environ.get('CONCLAVE_CHAIR')])+'\\n')
print(json.dumps({'type':'system', 'session_id':'test-session', 'model':'chair'}), flush=True)
if os.environ.get('DETACH_HOLD'):
    pathlib.Path(os.environ['CHILD_PID']).write_text(str(os.getpid()))
    time.sleep(30)
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
if prompt.startswith('You are a chair.') and 'Reconcile every finding' in prompt:
    if os.environ.get('RECONCILE_FAIL'): sys.exit(4)
    dest = pathlib.Path(re.search(r'Write JSON only to (\\S+);', prompt)[1])
    wt = pathlib.Path(args[args.index('--workspace')+1])
    assert (wt/'.env').read_text() == 'private local env\\n'
    assert not (wt/'.env').is_symlink()
    assert (wt/'config/database.yml').read_text() == 'database: local\\n'
    with open(os.environ['CALL_LOG'], 'a') as f: f.write(json.dumps(['worktree', str(wt)])+'\\n')
    if os.environ.get('RECONCILE_SLEEP'):
        if os.environ.get('IGNORE_TERM'):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        else:
            def terminated(signum, frame):
                pathlib.Path(os.environ['CHILD_PID'] + '.cleanup').write_text(str(wt.exists()))
                sys.exit(128 + signum)
            signal.signal(signal.SIGTERM, terminated)
        pathlib.Path(os.environ['CHILD_PID']).write_text(str(os.getpid()))
        time.sleep(60)
    if os.environ.get('POST_RECONCILE'):
        print(json.dumps({'type':'tool_call','command':'git push origin main'}))
    if 'RECONCILE_RAW' in os.environ:
        if os.environ['RECONCILE_RAW'] != 'missing': dest.write_text(os.environ['RECONCILE_RAW'])
    else: dest.write_text(json.dumps({'top_pr':3, 'top_oid':os.environ['TOP_OID'], 'findings':[
        {'ballot':'p-1','id':'f1','verdict':'fixed','pr':2,'evidence':'read diff',
         'repro_run':False,'recommend':'hold','why':os.environ.get('RECONCILE_WHY','later change')}]}))
elif 'Read Linear ticket' in prompt:
    dest = pathlib.Path(re.search(r'Write only (\\S+) with what', prompt)[1])
    dest.write_text('Ticket TEST-1: domain rules and settled decisions.\\n')
    if os.environ.get('POST_CONTEXT'):
        print(json.dumps({'type':'tool_call','command':'gh issue comment 1 --body context'}))
else:
    if 'Round A.' in prompt:
        dest = pathlib.Path(re.search(r'Write only (\\S+/brief.md) and stop', prompt)[1])
        dest.write_text('kind: review\\ndifficulty: high\\n# Council brief: review\\n## Goal\\nDomain change\\n')
        ballot = dest.parent
    else:
        ballot = pathlib.Path(re.search(r'Ballot directory: (\\S+)\\. Follow', prompt)[1])
    if ballot.name.endswith(os.environ.get('NO_RESULT_PR','never')):
        sys.exit(0)
    if ballot.name.endswith(os.environ.get('TIMEOUT_PR','never')):
        time.sleep(10)
    if ballot.name.endswith(os.environ.get('POST_PR','never')):
        print(json.dumps({'type':'tool_call','tool_call':{'shellToolCall':{'args':{'command':'gh pr comment 2 --body bad'}}}}))
    if 'Round B.' in prompt:
        if 'phase 4 only' not in prompt:
            (ballot/'findings.json').write_text(json.dumps({'findings':[]}))
            subprocess.run([os.environ['COUNCIL_SCRIPT'], ballot.name, '--findings'], check=True)
        path = ballot/'decide.md'
        path.write_text(path.read_text().replace('TODO(chair)','hold'))
        (ballot/'comments.md').write_text('**Advisory:** Draft only.\\n')
print(json.dumps({'type':'result','is_error':False,'result':os.environ.get('CHAIR_RESULT','Full report.')}, ensure_ascii=False))
'''


class Fixture:
    def __init__(self, tmp: Path, name: str) -> None:
        self.base = tmp / name
        self.root = self.base / 'council'
        self.repo = self.base / 'repo'
        self.repo.mkdir(parents=True)
        scripts = self.root / 'scripts'
        scripts.mkdir(parents=True)
        for name in ('stack-run.py', 'review-stack.py', 'conclave_env.py', 'review-kind.sh'):
            shutil.copyfile(SCRIPTS / name, scripts / name)
        self.bin = self.base / 'bin'
        write_exe(self.bin / 'gh', GH)
        write_exe(self.bin / 'council.sh', COUNCIL)
        write_exe(self.bin / 'cursor-agent', CURSOR)
        write_exe(self.bin / 'seat-usage', '#!/bin/sh\nexit 0\n')
        self.log = self.base / 'calls.jsonl'
        self.context = self.base / 'context.md'
        self.context.write_text('Ticket TEST-1: Domain constraints.\n')
        self.seat = self.base / 'seat.json'
        self.seat.write_text(json.dumps({'fable':70, 'ts':time.time()}))
        self.temp = self.base / 'scratch'
        self.temp.mkdir()
        self.env = dict(os.environ, PATH=f'{self.bin}:/usr/bin:/bin',
                        FIXTURE_ROOT=str(self.root), CALL_LOG=str(self.log),
                        COUNCIL_SCRIPT=str(self.bin / 'council.sh'),
                        COUNCIL_CURSOR_AGENT=str(self.bin / 'cursor-agent'),
                        COUNCIL_SEAT_USAGE=str(self.bin / 'seat-usage'),
                        COUNCIL_SEAT_JSON=str(self.seat), TMPDIR=str(self.temp))
        self.env.pop('CONCLAVE_CHAIR', None)
        self.env.pop('COUNCIL_CHAIR', None)

    def ballot(self, number: int) -> Path:
        p = self.root / 'tmp/council' / f'p-{number}'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def seed(self) -> tuple[Path, dict]:
        b = self.ballot(1)
        (b / 'judgment.md').write_text('head_oid: ' + 'a' * 40 + '\n')
        (b / 'decide.md').write_text('Already filled.\n')
        (b / 'comments.md').write_text('Draft only.\n')
        return b, {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in b.iterdir()}

    def args(self, *extra: str, context: bool = True) -> list[str]:
        args = [sys.executable, str(self.root / 'scripts/stack-run.py'), '--repo', str(self.repo),
                '--pr', '2', '--stack-id', 'stack', '--prefix', 'p', '--chair-model', 'test-chair']
        if context:
            args += ['--context', str(self.context)]
        return args + list(extra)

    def invoke(self, *extra: str, context: bool = True) -> subprocess.CompletedProcess:
        return run(self.args(*extra, context=context), env=self.env)

    def record(self) -> dict:
        return json.loads((self.root / 'tmp/council/stack/run.json').read_text())

    def calls(self) -> list:
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def git(self, *args: str) -> str:
        p = run(['git', '-C', str(self.repo), *args], env=self.env)
        if p.returncode:
            raise SystemExit(f'FAIL git fixture {args}: {p.stderr}')
        return p.stdout.strip()

    def reconcile_repo(self) -> str:
        self.git('init','-b','main')
        self.git('config','user.email','test@example.test')
        self.git('config','user.name','Test')
        self.git('config','commit.gpgsign','false')
        (self.repo / '.gitignore').write_text('.env\nconfig/database.yml\n')
        (self.repo / 'a.rb').write_text('one\n')
        self.git('add','.')
        self.git('commit','-m','fixture')
        oid = self.git('rev-parse','HEAD')
        self.git('update-ref','refs/pull/3/head', oid)
        self.git('remote','add','origin',str(self.repo))
        (self.repo / '.env').write_text('private local env\n')
        (self.repo / 'config').mkdir()
        (self.repo / 'config/database.yml').write_text('database: local\n')
        self.env['TOP_OID'] = oid
        return oid


def receipt(ballot: Path) -> None:
    (ballot / '.stack-brief.sha256').write_text(hashlib.sha256((ballot / 'brief.md').read_bytes()).hexdigest() + '\n')


def test_runner(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'dry')
    ballot, snapshot = f.seed()
    got = f.invoke('--dry-run')
    check(got.returncode == 0, f'dry run exits zero ({got.stderr})')
    check([line.split(':')[0] for line in got.stdout.splitlines()[:3]] == ['p-1', 'p-2', 'p-3'], 'dry run bottom-up ballot ids')
    check('p-1: done; steps: none' in got.stdout, 'dry run recognizes done')
    check(not f.calls(), 'dry run launches no chair or council')
    check({p.name for p in (f.root / 'tmp/council/stack').iterdir()} == {'stack.json','stack.md','dry-run.md'}, 'dry run writes only stack and preview artifacts')
    got = f.invoke('--no-reconcile')
    check(got.returncode == 0, f'full run exits zero ({got.stderr})')
    rows = f.record()['prs']
    check([r['outcome'] for r in rows] == ['reused','ok','ok'], 'full run outcomes')
    check({p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in ballot.iterdir()} == snapshot, 'reused ballot untouched')
    council = [c[1] for c in f.calls() if c[0] == 'council']
    expected = []
    for n in (2,3):
        rest = ['--pr',str(n),'--repo',str(f.repo),'--fable-overflow','cursor']
        expected += [[f'p-{n}','--dry-run',*rest],[f'p-{n}',*rest],[f'p-{n}','--findings']]
    check(council == expected, 'exact phase 1 council arguments and findings commands')
    chairs = [c[1] for c in f.calls() if c[0] == 'cursor']
    check(all(c[:10] == ['-p','--model','test-chair','--output-format','stream-json','-f','--trust','--approve-mcps','--workspace',str(f.repo)] for c in chairs), 'chair argument shape')
    cursor_rows = [c for c in f.calls() if c[0] == 'cursor']
    check(cursor_rows and all(c[2] == '1' for c in cursor_rows), 'every chair process sees CONCLAVE_CHAIR=1')
    phase1 = [c for c in f.calls() if c[0] == 'council' and '--findings' not in c[1]]
    check(phase1 and all(c[3] is None for c in phase1), 'runner phase 1 does not see CONCLAVE_CHAIR')
    check(chairs[1][10:12] == ['--resume','test-session'] and chairs[3][10:12] == ['--resume','test-session'], 'round B resumes round A sessions')
    check(all(not mod.FORBIDDEN.search(prompt_outside_repo(c[-1], str(ROOT), str(f.repo))) for c in chairs),
          'chair prompts contain no model or vendor words')
    shallow = 'Do not pass --depth, --shallow-*, or --unshallow to git.'
    check(all(shallow in c[-1] for c in chairs if 'Round A.' in c[-1] or 'Round B.' in c[-1]),
          'fix 4 round A and round B forbid shallow fetches')
    settled = [c[-1] for c in chairs if 'Round A.' in c[-1] and '## Settled' in c[-1]]
    check(settled and all('never a mechanism this diff introduces' in p for p in settled),
          'round A brief bars settling the mechanism under review')
    count = len(f.calls())
    check(f.invoke('--no-reconcile').returncode == 0 and len(f.calls()) == count, 'second run reuses every completed ballot')

    for pct, expected_flags in ((96,['--claude-model','opus']), (70,['--fable-overflow','cursor']), (None,['--fable-overflow','cursor'])):
        f = Fixture(tmp, f'seat-{pct}')
        if pct is None:
            f.seat.unlink()
        else:
            f.seat.write_text(json.dumps({'fable':pct, 'ts':time.time()}))
        got = f.invoke('--no-reconcile', '--prs', '1')
        check(got.returncode == 0 and f.record()['prs'][0]['claude_flags'] == expected_flags, f'seat {pct} chooses {expected_flags} ({got.stderr})')
    f = Fixture(tmp, 'sticky')
    f.env['OVERFLOW_PR'] = '-1'
    check(f.invoke('--no-reconcile').returncode == 0, 'overflow fixture completes')
    check([r['claude_flags'] for r in f.record()['prs']] == [['--fable-overflow','cursor'], ['--claude-model','cursor-fable'], ['--claude-model','cursor-fable']], 'overflow sticks for every later ballot')
    f = Fixture(tmp, 'posting')
    f.env['POST_PR'] = '-2'
    check(f.invoke('--no-reconcile').returncode == 0 and bool(f.record()['prs'][1]['POSTED?']), 'gh pr comment tool call marks POSTED?')
    hits = mod.posted_hits([{'type':'tool_use', 'name':'mcp__linear__save_issue'},
                           {'type':'tool_use','name':'mcp__notion__update_page'},
                           {'type':'tool_call','command':'gh api repos/x/comments --method POST'},
                           {'type':'result','result':'Do not gh pr comment'}])
    check(len(hits) == 3, 'detects MCP writes and API posts without flagging prose')
    f = Fixture(tmp, 'no-result')
    f.env['NO_RESULT_PR'] = '-2'
    check(f.invoke('--no-reconcile').returncode == 1, 'missing result causes final exit 1')
    check(f.record()['prs'][1]['outcome'].startswith('failed:') and f.record()['prs'][2]['outcome'] == 'ok', 'ballot failure does not stop next ballot')
    f = Fixture(tmp, 'timeout')
    f.env['TIMEOUT_PR'] = '-1'
    check(f.invoke('--no-reconcile', '--chair-timeout', '1').returncode == 1, 'chair timeout exits 1')
    check('timeout' in f.record()['prs'][0]['outcome'] and f.record()['prs'][1]['outcome'] == 'ok', 'timeout recorded and next ballot runs')
    f = Fixture(tmp, 'resume')
    for n in (1,2,3):
        b = f.ballot(n)
        (b / 'brief.md').write_text('kind: review\ndifficulty: high\n')
        receipt(b)
        if n <= 2:
            (b / 'blind').mkdir()
            (b / 'blind/leg-A.md').write_text('report')
            (b / 'errors.md').write_text('(none)\n')
        if n == 1:
            (b / 'judgment.md').write_text('judgment')
            (b / 'findings.json').write_text('{"findings":[]}')
            (b / 'decide.md').write_text('TODO(chair)')
    check(f.invoke('--no-reconcile', '--jobs', '2').returncode == 0, 'two-job resumed run completes')
    check([r['state'] for r in f.record()['prs']] == ['needs decide','needs findings','needs legs'], 'all partial resume states')
    calls = f.calls()
    check(len([c for c in calls if c[0] == 'cursor']) == 3, 'resumes skip chair A')
    check(not any(c[0] == 'council' and c[1] == ['p-1','--findings'] for c in calls), 'needs decide skips findings gate')
    f = Fixture(tmp, 'context')
    check(f.invoke('--no-reconcile', context=False).returncode == 1 and not f.calls(), 'missing context and ticket fail before any ballot')
    check(f.invoke('--no-reconcile', '--ticket','TEST-1', context=False).returncode == 0, 'ticket context chair runs before ballots')
    check('Read Linear ticket TEST-1' in f.calls()[0][1][-1], 'context prompt names ticket and read-only scope')
    for flags in (['--jobs','3'], ['--chair-model','auto'], ['--chair-model','a-composer-id']):
        check(f.invoke(*flags).returncode != 0, f'rejects {flags}')


def test_reconcile(tmp: Path, mod) -> None:
    prs = [{'number':1}, {'number':2}, {'number':3}]
    inputs = [{'ballot':'p-1','id':f'f{i}','pr':1} for i in range(1,6)]
    base = {'ballot':'p-1','verdict':'present','pr':None,'evidence':'read','repro_run':False,'recommend':'hold','why':'reason'}
    raw = {'top_pr':3,'top_oid':'abcdef12345','findings':[
        dict(base,id='f1'),dict(base,id='f2',verdict='nonsense'),
        dict(base,id='f3',verdict='fixed'),dict(base,id='f4',verdict='changed',pr=1)]}
    doc, warnings = mod.validate_reconcile(raw, inputs, prs, 'abcdef12345')
    check([r['verdict'] for r in doc['findings']] == ['present','unknown','unknown','unknown','unknown'], 'reconcile rejects bad verdict, missing pr, and non-later pr')
    check(doc['findings'][-1]['evidence'] == 'not returned by the reconcile chair', 'missing finding becomes unknown with exact evidence')
    check(len(warnings) == 4, 'invalid reconcile entries reported')
    md = mod.render_reconcile(doc)
    check('present: 1, fixed: 0, changed: 0, unknown: 4' in md and 'still present at the top' in md and 'not checked' in md, 'reconcile markdown header, counts, rows')
    raw['findings'] = [dict(base,id='f1',verdict='fixed',pr=2), dict(base,id='f1')]
    duplicate, _ = mod.validate_reconcile(raw, inputs[:1], prs, 'abcdef12345')
    check(duplicate['findings'][0]['verdict'] == 'unknown', 'duplicate finding becomes unknown')
    raw['findings'] = [dict(base,id='f1',verdict='fixed',pr=2)]
    valid, _ = mod.validate_reconcile(raw, inputs[:1], prs, 'abcdef12345')
    check(valid['findings'][0]['verdict'] == 'fixed', 'valid later PR accepted')
    text = ('intro\r\nf1 — **what breaks, for whom**: a\r\n**how sure**: read\r\n'
            '**later PRs that change this file**: #2\r\n**recommend**: hold\r\n\r\n'
            'f2 — **what breaks, for whom**: b\r\n**later PRs that change this file**: none\r\n'
            '**recommend**: keep exactly\r\nfooter\r\n')
    row = valid['findings'][0]
    folded = mod.fold_decide(text, {'f1':row}, 3, 'abcdef12345')
    line = '**at the stack top (#3 abcdef12)**: fixed in #2; recommend hold: reason\r\n'
    check(folded.replace(line,'') == text and '#2\r\n' + line in folded, 'fold inserts directly after later PRs and preserves every other byte')
    second = mod.fold_decide(folded, {'f1':dict(row,why='changed reason')}, 3, 'abcdef12345')
    check(second.count('**at the stack top') == 1 and second.replace('changed reason','reason') == folded, 'fold replaces rather than duplicates')

    f = Fixture(tmp, 'reconcile-only')
    git = f.git
    oid = f.reconcile_repo()
    b = f.ballot(1)
    (b / 'findings.json').write_text(json.dumps({'findings':[{'id':'f1','claim':'failure','file':'a.rb','line':1,
        'cited_code':'one','evidence':'read','repro_executed':False}]}))
    (b / 'judgment.md').write_text(f'head_oid: {oid}\n')
    (b / 'decide.md').write_text(text.replace('\r\n','\n'))
    before = git('worktree','list','--porcelain')
    got = f.invoke('--reconcile-only', context=False)
    check(got.returncode == 0, f'reconcile-only real worktree run ({got.stderr})')
    check(git('worktree','list','--porcelain') == before, 'reconciliation removes and prunes worktree')
    worktrees = [Path(c[1]) for c in f.calls() if c[0] == 'worktree']
    check(len(worktrees) == 1 and not worktrees[0].exists(), 'reconcile chair ran in disposable worktree with copied ignored config')
    check(not any(c[0] == 'council' for c in f.calls()), 'reconcile-only runs no ballots')
    shallow = 'Do not pass --depth, --shallow-*, or --unshallow to git.'
    check(any(shallow in c[1][-1] for c in f.calls() if c[0] == 'cursor'),
          'fix 4 reconcile prompt forbids shallow fetches')
    folded = (b / 'decide.md').read_text()
    check('fixed in #2; recommend hold: later change' in folded, 'reconcile-only folds validated verdict')
    check('p-2: missing findings.json' in (f.root / 'tmp/council/stack/reconcile-input.md').read_text(), 'missing ballots listed in reconcile input')
    check(f.invoke('--reconcile-only', context=False).returncode == 0 and (b / 'decide.md').read_text() == folded, 'reconcile-only is idempotent')
    f.env['RECONCILE_FAIL'] = '1'
    got = f.invoke('--reconcile-only', context=False)
    check(got.returncode == 1 and git('worktree','list','--porcelain') == before, 'failed reconcile chair also cleans worktree')


def test_blocked_resume(tmp: Path, mod) -> None:
    reason = 'failed: phase 1 recorded errors in errors.md; fix them, then remove blind/ or rerun conclave.sh'
    for i, errors in enumerate((None, 'read-only baseline mismatch\n', '(none)\nnote: overflow\nreal error\n')):
        f = Fixture(tmp, f'blocked-{i}')
        b = f.ballot(1)
        (b / 'blind').mkdir()
        (b / 'blind/leg-A.md').write_text('report')
        if errors is not None:
            (b / 'errors.md').write_text(errors)
        got = f.invoke('--prs', '1', '--no-reconcile')
        check(got.returncode == 1 and f.record()['prs'][0]['outcome'] == reason and not f.calls(),
              f'item 1 blocked errors case {i} runs nothing')
        preview = f.invoke('--prs', '1', '--dry-run', '--no-reconcile')
        check('blocked; steps: none' in preview.stdout, f'item 1 blocked dry run {i}')
    for errors in ('', '\n(none)\n\nnote: overflow\n'):
        (b / 'errors.md').write_text(errors)
        check(mod.state(b) == 'needs findings', 'item 1 clean errors allow findings')
    f = Fixture(tmp, 'failed-phase-resume')
    f.env.update(PHASE1_ERRORS='read-only baseline mismatch', PHASE1_EXIT='1')
    check(f.invoke('--prs','1','--no-reconcile').returncode == 1, 'item 1 phase 1 fails after blinding')
    count = len(f.calls())
    check(f.invoke('--prs','1','--no-reconcile').returncode == 1 and len(f.calls()) == count
          and f.record()['prs'][0]['outcome'] == reason, 'item 1 rerun cannot turn failed phase 1 into ok')


def test_linear_stack(tmp: Path, mod) -> None:
    for flags in ([], ['--dry-run'], ['--reconcile-only'], ['--prs','1,2,3'], ['--prs','1,2,3','--dry-run']):
        f = Fixture(tmp, 'branched-' + str(len(list(tmp.iterdir()))))
        f.env['BRANCHED'] = '1'
        got = f.invoke('--pr','1',*flags)
        check(got.returncode == 1 and '#3' in got.stderr and '#2' in got.stderr
              and 'linear --prs' in got.stderr and not f.calls(), f'item 2 rejects siblings {flags}')
    f = Fixture(tmp, 'linear-explicit')
    check(f.invoke('--prs','1,2,3','--dry-run').returncode == 0, 'item 2 accepts linear explicit order')


def test_sticky_resume(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'sticky-resume')
    # A later directory must affect the very first ballot, before it is visited.
    (f.ballot(3) / 'voter-claude.via').write_text('cursor\n')
    check(f.invoke('--no-reconcile').returncode == 0 and
          all(row['claude_flags'] == ['--claude-model','cursor-fable'] for row in f.record()['prs']),
          'item 5 resume restores sticky Cursor before first ballot')


def test_seat_age(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'seat-age')
    for label, ts in (('missing', None), ('string', 'recent'), ('bool', True),
                      ('old', time.time()-901), ('nan', float('nan'))):
        f.seat.write_text(json.dumps(dict(fable=96, **({} if ts is None else {'ts':ts}))))
        check(mod.seat_percent(f.seat) is None, f'item 6 seat timestamp {label} is unknown')
    f.seat.write_text(json.dumps({'fable':96,'ts':time.time()-60}))
    check(mod.seat_percent(f.seat) == 96, 'item 6 fresh seat timestamp accepted')
    f.env['COUNCIL_SEAT_MAX_AGE'] = '1'
    check(f.invoke('--prs','1','--no-reconcile').returncode == 0 and
          f.record()['prs'][0]['claude_flags'] == ['--fable-overflow','cursor'],
          'item 6 max age override sends stale high percent to unknown routing')


def test_posted_chairs(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'posted-chairs')
    f.reconcile_repo()
    f.env.update(POST_CONTEXT='1', POST_RECONCILE='1')
    check(f.invoke('--ticket','TEST-1',context=False).returncode == 0, 'item 7 context and reconcile chairs complete')
    md = (f.root / 'tmp/council/stack/run.md').read_text()
    hits = f.record()['POSTED?']
    check(len(hits) == 2 and 'Context and reconcile chairs POSTED?: ' in md
          and all(hit in md for hit in hits), 'item 7 context and reconcile POSTED hits rendered under table')
    f = Fixture(tmp, 'posted-none')
    f.invoke('--no-reconcile','--prs','1')
    check('Context and reconcile chairs POSTED?: none' in
          (f.root / 'tmp/council/stack/run.md').read_text(), 'item 7 absent chair hits rendered as none')


def test_posted_resume(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'posted-resume')
    f.env.update(POST_PR='-1', POST_CONTEXT='1')
    check(f.invoke('--ticket','TEST-1','--no-reconcile',context=False).returncode == 0, 'item 8 first posting run completes')
    before = f.record()
    f.env.pop('POST_CONTEXT')
    f.env.pop('POST_PR')
    check(f.invoke('--no-reconcile').returncode == 0 and f.record()['prs'][0]['POSTED?'] == before['prs'][0]['POSTED?']
          and f.record().get('POSTED?') == before['POSTED?'], 'item 8 reused ballots and top-level hits survive resume')
    b = f.ballot(1)
    (b / 'decide.md').write_text('TODO(chair)')
    f.env['POST_PR'] = '-1'
    check(f.invoke('--no-reconcile').returncode == 0 and len(f.record()['prs'][0]['POSTED?']) == 1,
          'item 8 repeated hit union keeps order without duplicates')
    previous = f.record()
    check(f.invoke('--prs','1,3','--no-reconcile').returncode == 1 and
          f.record()['prs'] == previous['prs'] and f.record()['POSTED?'] == previous['POSTED?'],
          'item 8 invalid stack on resume preserves earlier POSTED history')


def test_receipts(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'receipt')
    b = f.ballot(1)
    old = 'kind: review\ndifficulty: high\nUnreceipted draft.\n'
    (b / 'brief.md').write_text(old)
    (b / 'brief.md.prev').write_text('older draft')
    check(mod.state(b) == 'new', 'item 9 unreceipted brief is new')
    receipt(b)
    check(mod.state(b) == 'needs legs', 'item 9 matching brief receipt enables legs')
    (b / 'brief.md').write_text(old + 'changed\n')
    check(mod.state(b) == 'new', 'item 9 modified brief invalidates receipt')
    check(f.invoke('--prs','1','--no-reconcile').returncode == 0 and
          (b / 'brief.md.prev').read_text() == old + 'changed\n' and
          (b / '.stack-brief.sha256').read_text().strip() == hashlib.sha256((b / 'brief.md').read_bytes()).hexdigest(),
          'item 9 chair A replaces leftover brief and writes matching receipt')
    (b / 'comments.md').unlink()
    check(mod.state(b) == 'needs decide', 'item 9 judgment with filled decide but no comments needs decide')
    count = len(f.calls())
    check(f.invoke('--prs','1','--no-reconcile').returncode == 0 and
          len(f.calls()) == count + 1 and 'phase 4 only' in f.calls()[-1][1][-1],
          'item 9 missing comments resumes chair B phase 4')
    (b / 'comments.md').unlink()
    (b / 'decide.md').write_text('There is nothing to decide.\n')
    check(mod.state(b) == 'done', 'item 9 exact nothing-to-decide text is done without comments')
    (b / 'decide.md').write_text('There is nothing to decide. More text.\n')
    check(mod.state(b) == 'needs decide', 'item 9 nothing-to-decide exception requires exact text')
    f = Fixture(tmp, 'receipt-failed-chair')
    b = f.ballot(1)
    # A failed chair can recreate the bytes of an older accepted brief.
    (b / 'brief.md').write_text('kind: review\ndifficulty: high\n# Council brief: review\n## Goal\nDomain change\n')
    receipt(b)
    (b / 'brief.md').write_text('leftover incomplete replacement')
    f.env['NO_RESULT_PR'] = '-1'
    f.invoke('--prs','1','--no-reconcile')
    check(not (f.ballot(1) / '.stack-brief.sha256').exists() and mod.state(f.ballot(1)) == 'new',
          'item 9 failed chair leaves no receipt despite brief output')


def test_streams(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'unicode-stream')
    f.env['CHAIR_RESULT'] = 'Complete\u2028still one JSON line\u0085end'
    got = f.invoke('--prs','1','--no-reconcile')
    check(got.returncode == 0, f'item 11 chair accepts literal U+2028 and U+0085 in JSON stream ({got.stderr})')


def test_reconcile_types(tmp: Path, mod) -> None:
    prs = [{'number':1}, {'number':2}]
    inputs = [{'ballot':'p-1','id':'f1','pr':1}]
    base = dict(ballot='p-1', id='f1', verdict='present', recommend='hold', evidence='read', why='reason', repro_run=False, pr=None)
    for field in ('recommend','verdict'):
        for value in ([], {}):
            doc, warnings = mod.validate_reconcile({'top_pr':2,'top_oid':'oid','findings':[dict(base, **{field:value})]}, inputs, prs, 'oid')
            check(doc['findings'][0]['verdict'] == 'unknown' and bool(warnings), f'item 3 non-string {field} {type(value).__name__} becomes unknown')
    for invalid in ('{"findings":[{}]}', '{"findings":[null]}'):
        f = Fixture(tmp, 'malformed-input-' + str(len(list(tmp.iterdir()))))
        f.reconcile_repo()
        (f.ballot(1) / 'findings.json').write_text(invalid)
        got = f.invoke('--reconcile-only', context=False)
        check(got.returncode == 1 and f.record()['reconcile'].startswith('failed:'),
              f'item 3 malformed reconcile input records failure {invalid}')


def test_reconcile_no_output(tmp: Path, mod) -> None:
    for label, env in (('missing', {'RECONCILE_RAW':'missing'}), ('empty', {'RECONCILE_RAW':''}),
                       ('array', {'RECONCILE_RAW':'[]'}), ('null', {'RECONCILE_RAW':'null'}),
                       ('invalid', {'RECONCILE_RAW':'{' }), ('failed', {'RECONCILE_FAIL':'1'})):
        f = Fixture(tmp, 'no-reconcile-output-' + label)
        f.reconcile_repo()
        b = f.ballot(1)
        (b / 'findings.json').write_text('{"findings":[{"id":"f1"}]}')
        original = ('f1 — **what breaks, for whom**: test\n**later PRs that change this file**: #2\n'
                    '**at the stack top (#3 previous)**: fixed in #2; recommend hold: keep me\n')
        (b / 'decide.md').write_text(original)
        f.env.update(env)
        got = f.invoke('--reconcile-only', context=False)
        expected = 'failed: reconcile chair wrote no valid reconcile.json'
        check(got.returncode == 1 and (f.record()['reconcile'].startswith('failed:') if label == 'failed'
              else f.record()['reconcile'] == expected), f'item 12 {label} reconcile output fails')
        check((b / 'decide.md').read_text() == original and not (f.root / 'tmp/council/stack/reconcile.md').exists(),
              f'item 12 {label} reconcile output preserves prior folds and writes no markdown')


def test_max_cap(tmp: Path, mod) -> None:
    reason = 'failed: not started; the Claude subscription is at a usage limit (coverage leg)'
    cases = [('stderr', {'OPUS_ERR':'Claude weekly limit reached'}),
             ('error-result', {'OPUS_JSON':'{"is_error":true,"result":"Claude usage limit reached"}'}),
             ('session-limit', {'OPUS_JSON': json.dumps({
                 'is_error': False, 'subtype': 'success',
                 'result': "You've hit your session limit · resets 4am (America/Los_Angeles)"})})]
    for name, extra in cases:
        f = Fixture(tmp, 'max-cap-' + name)
        f.env.update(OPUS_CAP_PR='-1', **extra)
        check(f.invoke().returncode == 1 and [r['outcome'] for r in f.record()['prs'][1:]] == [reason,reason]
              and f.record()['reconcile'] == reason and len([c for c in f.calls() if c[0] == 'cursor']) == 1,
              f'item 13 {name} cap skips unstarted ballots and reconcile')
        f.env.pop('OPUS_CAP_PR')
        check(f.invoke('--no-reconcile').returncode == 0, f'item 13 {name} reset resumes normally')
    for name, extra in [('rate-limit', {'OPUS_ERR':'retrying after rate limit backoff'}),
                         ('success-result', {'OPUS_JSON': json.dumps({
                             'is_error': False, 'result': 'usage limit',
                             'modelUsage': {'claude-opus-5': {'inputTokens': 1}}})})]:
        f = Fixture(tmp, 'max-not-cap-' + name)
        f.env.update(OPUS_CAP_PR='-1', **extra)
        check(f.invoke('--no-reconcile').returncode == 1 and f.record()['prs'][1]['outcome'] == 'ok',
              f'item 13 {name} does not cap the Claude subscription')


def test_fold_edges(tmp: Path, mod) -> None:
    f = Fixture(tmp, 'fold-edges')
    f.reconcile_repo()
    b = f.ballot(1)
    (b / 'findings.json').write_text('{"findings":[{"id":"f1"},{"id":"f2"}]}')
    (b / 'decide.md').write_text('f1 — **what breaks, for whom**: test\n**later PRs that change this file**: #2\n'
                                'f2 — **what breaks, for whom**: no insertion line\n**recommend**: hold\n')
    f.env['RECONCILE_WHY'] = 'check TODO(chair) later'
    check(f.invoke('--reconcile-only', context=False).returncode == 0, 'item 15 edge fold completes')
    check('TODO(chair)' not in (b / 'decide.md').read_text() and 'TODO (chair)' in (b / 'decide.md').read_text(),
          'item 15 fold sanitizes TODO(chair) in why')
    check('p-1 f2: no later-PRs line; stack-top line not folded' in f.record()['reconcile_warnings'],
          'item 15 missing later-PRs line emits warning for each unfolded verdict')


def test_signals(tmp: Path, mod) -> None:
    for phase, signum in (('reconcile',signal.SIGTERM), ('reconcile',signal.SIGINT),
                           ('reconcile',signal.SIGHUP), ('phase1',signal.SIGTERM), ('kill',signal.SIGTERM)):
        f = Fixture(tmp, f'signal-{phase}-{signum}')
        f.reconcile_repo()
        before = f.git('worktree','list','--porcelain')
        pidfile = f.base / 'child.pid'
        f.env.update(CHILD_PID=str(pidfile), **({'PHASE1_SLEEP':'1'} if phase == 'phase1' else {'RECONCILE_SLEEP':'1'}))
        if phase == 'kill':
            f.env['IGNORE_TERM'] = '1'
        args = f.args('--no-reconcile') if phase == 'phase1' else f.args('--reconcile-only', context=False)
        proc = subprocess.Popen(args, env=f.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        child = None
        try:
            deadline = time.monotonic() + 10
            while not pidfile.exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            check(pidfile.exists(), f'item 10 {phase} child started before {signum.name}')
            child = int(pidfile.read_text())
            proc.send_signal(signum)
            stdout, stderr = proc.communicate(timeout=15)
            try:
                os.kill(child, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            check(proc.returncode == 128 + signum and not alive,
                  f'item 10 {phase} {signum.name} exits {128 + signum} and reaps child ({stderr.strip()})')
            check(f.git('worktree','list','--porcelain') == before, f'item 10 {phase} {signum.name} removes reconcile worktree')
            if phase == 'reconcile':
                check(Path(str(pidfile) + '.cleanup').read_text() == 'True',
                      f'item 10 {signum.name} worktree exists until reconcile chair handles TERM')
            check(f.record().get('error') == f'interrupted by signal {signum}', f'item 10 {phase} {signum.name} recorded in run.json')
        finally:
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
            for line in f.git('worktree','list','--porcelain').splitlines():
                if line.startswith('worktree ') and line[9:] != str(f.repo):
                    f.git('worktree','remove','--force',line[9:])


def test_posted_matcher(tmp: Path, mod) -> None:
    commands = []
    for subject in ('pr','issue'):
        for verb in ('review','comment','edit','merge','close','reopen','create','ready','lock','delete'):
            for flags in ('','-R org/repo ','--repo org/repo ','--repo=org/repo '):
                commands.append((f'gh {flags}{subject} {verb} 2 --body "quoted body"', True))
    for verb in ('POST','PATCH','PUT','DELETE'):
        for flag in ('-X ','-X=', '--method ', '--method='):
            commands.append((f'gh --repo=o/r api "repos/o/r/issues/1" {flag}{verb}', True))
        for flag in ('-X ','-X=', '--request ', '--request='):
            commands.append((f'curl "https://api.github.com/repos/o/r/issues/1" {flag}{verb}', True))
    for flag in ('-f ', '-f=', '-F ', '-F=', '--field ', '--field=', '--raw-field ', '--raw-field=', '--input ', '--input='):
        commands.append((f'gh api "repos/o/r/issues/1/comments" {flag}"body=hello"', True))
    for flag in ('-d ', '-d=', '--data ', '--data=', '--data-raw ', '--data-binary=', '--data-urlencode '):
        commands.append((f'curl "https://api.github.com/repos/o/r/issues/1" {flag}"body=hello"', True))
    commands += [
        ('gh api graphql -f query="mutation { addComment(input: {}) { id } }"', True),
        ('gh api graphql --method=POST -f query="query { viewer { login } }"', False),
        ('gh api graphql -f query="{ viewer { login } }"', False),
        ('gh api graphql -f query="query { viewer { login } }" -F owner=o', False),
        ('gh pr view 2 --comments', False), ('gh issue view 2 --comments', False),
        ('gh api "repos/o/r/comments" --paginate', False),
        ('gh api repos/o/r/issues -X GET --paginate', False),
        ('curl https://api.github.com/repos/o/r/issues -X GET', False),
        ('curl https://example.test -X POST -d hello', False),
        ('git push origin HEAD', True), ('git status', False)]
    for command, expected in commands:
        for shape, event in (
            ('flat', {'type':'tool_call','command':command}),
            ('cursor', {'type':'tool_call','tool_call':{'shellToolCall':{'args':{'command':command}}}}),
            ('list', {'type':'tool_use','args':[{'command':command}]})):
            check(bool(mod.posted_hits([event])) == expected, f'item 17 {shape} {command}: {expected}')
    for name in ('share_issue','merge_diff','submit_diff_review','resolve_diff_thread','notion-upload-skill',
                 'save_comment','create_page','update_page','delete_page','move_page','duplicate_page'):
        for server in ('linear','notion'):
            for event in (
                {'type':'tool_use','name':f'mcp__{server}__{name}'},
                {'type':'tool_call','tool_call':{'mcpToolCall':{'args':{'providerIdentifier':server,'toolName':name}}}}):
                check(bool(mod.posted_hits([event])), f'item 17 MCP {server} {name} write {event["type"]}')
    for prefix in ('get','list','search','query','fetch','read','extract','check','guide'):
        for name in (f'{prefix}_status_updates', f'notion-{prefix}-pages'):
            for key in ('name','toolName','tool'):
                for event in (
                    {'type':'tool_use',key:f'mcp__linear__{name}'},
                    {'type':'tool_call','tool_call':{'mcpToolCall':{'args':{'providerIdentifier':'notion',key:name}}}}):
                    check(not mod.posted_hits([event]), f'item 17 MCP {key} {name} read {event["type"]}')
    check(not mod.posted_hits([{'type':'tool_call','server':'other','name':'save_comment'}]), 'item 17 other MCP server is not a hit')
    for kind in ('result','assistant'):
        check(not mod.posted_hits([{'type':kind,'command':'gh pr comment 1; git push',
                                   'name':'mcp__linear__save_comment'}]), f'item 17 {kind} prose ignored')
    check(not mod.posted_hits([{'type':'tool_call','server':'linear'},
                               {'type':'tool_call','name':'save_comment'}]), 'item 17 MCP server and write must share event')
    # Shapes from a real stack run. The strings below were flagged while no
    # branch moved and no review was posted. Each fixture is synthetic.
    def read_text(content: str) -> dict:
        return {'type': 'tool_call', 'tool_call': {'readToolCall': {
            'args': {'path': 'notes.md'}, 'result': {'success': {'content': content}}}}}

    def shell(name: str, command: str) -> dict:
        return {'type': 'tool_call', 'tool_call': {'shellToolCall': {'args': {
            'command': command, 'simpleCommands': [name],
            'parsingResult': {'executableCommands': [{'name': name, 'fullText': command}]}}}},
            'note': 'read linear'}

    def mcp(server: str, qualified: str, tool: str) -> dict:
        return {'type': 'tool_call', 'tool_call': {'mcpToolCall': {'args': {
            'name': qualified, 'toolName': tool, 'providerIdentifier': server,
            'serverIdentifier': server}}}}

    quiet = {
        'git push': read_text('Do not git push. Leave the branch where it is.'),
        'gh pr comment': read_text('Do not gh pr comment. Drafts stay local.'),
        'mcp write: gh': shell('gh', 'gh pr view 1 --json title'),
        'mcp write: echo': shell('echo', 'echo read the linear ticket'),
        'mcp write: python3': shell('python3', 'python3 -c "print(1)"'),
        'mcp write: rg': shell('rg', 'rg linear notes.md'),
        'mcp write: head': shell('head', 'head notes.md'),
        'mcp write: mkdir': shell('mkdir', 'mkdir -p scratch'),
        'mcp write: base64': shell('base64', 'base64 notes.md'),
        'mcp write: nl': shell('nl', 'nl notes.md'),
        'mcp write: ls': shell('ls', 'ls docs'),
        'mcp write: find': shell('find', 'find . -name notes.md'),
        'mcp write: linear-get_issue': mcp('linear', 'linear-get_issue', 'get_issue'),
        'mcp write: linear-list_documents': mcp('linear', 'linear-list_documents', 'list_documents'),
        'mcp write: linear-get_document': mcp('linear', 'linear-get_document', 'get_document'),
        'mcp write: linear-list_comments': mcp('linear', 'linear-list_comments', 'list_comments'),
        'mcp write: linear-list_issues': mcp('linear', 'linear-list_issues', 'list_issues'),
        'mcp write: notion-notion-get-tool-access': mcp('notion', 'notion-notion-get-tool-access', 'notion-get-tool-access'),
        'mcp write: notion-notion-fetch': mcp('notion', 'notion-notion-fetch', 'notion-fetch'),
        'mcp write: notion-notion-ai-search': mcp('notion', 'notion-notion-ai-search', 'notion-ai-search'),
        'mcp write: notion-notion-search': mcp('notion', 'notion-notion-search', 'notion-search'),
        'mcp write: mcp_auth': {'type': 'tool_call', 'tool_call': {'getMcpToolsToolCall': {
            'args': {'server': 'linear', 'toolName': 'mcp_auth'}}}},
    }
    lookup = {'type': 'tool_call', 'tool_call': {'getMcpToolsToolCall': {
        'args': {'server': 'notion', 'toolName': 'notion-ai-search'}}}}
    for label, event in quiet.items():
        check(not mod.posted_hits([event]), f'fix 2 {label} is not a write')
    check(not mod.posted_hits([quiet['mcp write: notion-notion-ai-search'], lookup]),
          'fix 2 mcp write: notion-ai-search is not a write')

    # f9: git -C <dir> push and curl --json are writes. A plain read is not.
    git_c = 'git -C /tmp/repo push origin HEAD'
    curl_json = ('curl --json \'{"body":"x"}\' '
                 'https://api.github.com/repos/acme/widget/pulls/7/reviews')
    curl_elsewhere = 'curl --json \'{"body":"x"}\' https://example.test/reviews'
    check(any('push' in hit for hit in mod.posted_hits([{'type': 'tool_call', 'command': git_c}])),
          f'f9 git -C push is a write ({mod.posted_hits([{"type": "tool_call", "command": git_c}])})')
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': 'git push origin HEAD'}])),
          'f9 plain git push stays a write')
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': curl_json}])),
          f'f9 curl --json to the reviews URL is a write ({mod.posted_hits([{"type": "tool_call", "command": curl_json}])})')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': curl_elsewhere}]),
          'f9 curl --json off GitHub is not a write')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': 'git -C /tmp/repo status'}]),
          'f9 git -C status is not a write')

    # f8: bash post.sh is a write when that local file runs gh pr comment.
    script = tmp / 'post.sh'
    script.write_text('gh pr comment 7 --body "posted from a script"\n')
    bash_cmd = f'bash {script}'
    check(any('gh pr comment' in hit for hit in mod.posted_hits([{'type': 'tool_call', 'command': bash_cmd}])),
          f'f8 bash script containing gh pr comment is a write ({mod.posted_hits([{"type": "tool_call", "command": bash_cmd}])})')
    reader = tmp / 'view.sh'
    reader.write_text('gh pr view 7 --json title\n')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': f'sh {reader}'}]),
          'f8 a local script that only reads is not a write')
    missing = tmp / 'missing-post.sh'
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': f'bash {missing}'}])),
          'f8 bash of a missing script is flagged')
    check(not mod.posted_hits([{'type': 'tool_call', 'tool_call': {'readToolCall': {
        'args': {'path': str(script)},
        'result': {'success': {'content': script.read_text()}}}}}]),
          'f8 reading the script file is not a write')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': "bash -lc 'gh pr view 1'"}]),
          'f8 bash -c of a read is not a write')
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': "bash -lc 'gh pr comment 1 --body x'"}])),
          'f8 bash -c of a write is still a write')

    # f16: git global options other than -C, a method glued to -X, and a mutation file.
    for command in (
        'git --no-pager push origin main',
        'git -c http.extraHeader=x push origin main',
        'git -C /tmp/r push origin HEAD',
        'gh api -XPOST repos/o/r/pulls/1/merge',
        'gh api -X POST repos/o/r/pulls/1/merge',
    ):
        check(bool(mod.posted_hits([{'type': 'tool_call', 'command': command}])),
              f'f16 {command} is a write ({mod.posted_hits([{"type": "tool_call", "command": command}])})')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': 'git --no-pager status'}]),
          'f16 git --no-pager status is not a write')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': 'git -c a.b=c status'}]),
          'f16 git -c status is not a write')
    mutation = tmp / 'review.graphql'
    mutation.write_text('mutation { addComment(input: {}) { id } }\n', encoding='utf-8')
    query = tmp / 'read.graphql'
    query.write_text('query { viewer { login } }\n', encoding='utf-8')
    mut_cmd = f'gh api graphql -f query=@{mutation}'
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': mut_cmd}])),
          f'f16 graphql mutation file is a write ({mod.posted_hits([{"type": "tool_call", "command": mut_cmd}])})')
    check(not mod.posted_hits([{'type': 'tool_call', 'command': f'gh api graphql -f query=@{query}'}]),
          'f16 graphql query file is not a write')
    missing_q = tmp / 'missing.graphql'
    check(bool(mod.posted_hits([{'type': 'tool_call', 'command': f'gh api graphql -f query=@{missing_q}'}])),
          'f16 unreadable graphql file is flagged')


def test_cursor_leg(tmp: Path) -> None:
    bindir = tmp / 'leg-bin'
    write_exe(bindir / 'cursor-agent', '''\
        #!/usr/bin/env python3
        import json, os
        print(json.dumps({'type':'system','model':os.environ.get('CURSOR_DISPLAY','Fable Thinking'), 'session_id':'seat-session'}))
        print(json.dumps({'type':'result','is_error':False,'result':os.environ.get('CURSOR_RESULT','Review report')}, ensure_ascii=False))
        ''')
    write_exe(bindir / 'claude', '''\
        #!/usr/bin/env python3
        import json, os, pathlib, sys
        pathlib.Path('max-touched').write_text('dirty')
        print(json.dumps({'is_error':os.environ.get('MAX_IS_ERROR','true') == 'true',
                          'result':os.environ.get('MAX_MESSAGE','Claude usage limit reached')}))
        print(os.environ.get('MAX_STDERR',''), file=sys.stderr)
        sys.exit(int(os.environ.get('MAX_EXIT','1')))
        ''')
    wt = tmp / 'leg-repo'
    wt.mkdir()
    run(['git','init',str(wt)])
    # The overflow seam reads the limit phrase from JSON with jq.
    # The stub PATH has to include jq. A machine whose jq lives outside
    # /usr/bin would otherwise miss the limit and skip the Cursor rerun.
    jq = shutil.which('jq')
    if not jq:
        raise SystemExit('FAIL jq is not on PATH')
    jqdir = tmp / 'jq-bin'
    jqdir.mkdir()
    os.symlink(jq, jqdir / 'jq')
    env = dict(os.environ, PATH=f'{bindir}:{jqdir}:/usr/bin:/bin', COUNCIL_CURSOR_AGENT=str(bindir / 'cursor-agent'))
    shell = '''\
set -eu
export COUNCIL_SOURCE_ONLY=1
. "$1"
DIR="$2" ERRORS_FILE="$2/errors.md" CLAUDE_SKU="$4" FABLE_OVERFLOW="$5" CURSOR_FABLE="claude-fable-test"
: >"$ERRORS_FILE"
printf 'Review this change.\\n' >"$DIR/voter-claude.brief.md"
rc=0
review_run_claude "$3" || rc=$?
printf 'rc=%s\\n' "$rc"
review_voter_header_value claude
'''
    def invoke(name: str, sku: str, overflow: str = '', extra: dict | None = None):
        dest = tmp / name
        dest.mkdir()
        (wt / 'max-touched').unlink(missing_ok=True)
        proc = run(['bash','-c',shell,'bash',str(SCRIPTS / 'council.sh'),str(dest),str(wt),sku,overflow], env=dict(env, **(extra or {})))
        check(proc.returncode == 0, f'{name} seam exits normally ({proc.stderr})')
        return dest, proc
    d, p = invoke('cursor-ok','cursor-fable')
    data = json.loads((d / 'voter-claude.json').read_text())
    check(data == {'result':'Review report','via':'cursor','model':'claude-fable-test','init_model':'Fable Thinking','session_id':'seat-session'}, 'Cursor result JSON shape')
    check((d / 'voter-claude.model.txt').read_text() == 'claude: ok via=cursor model=claude-fable-test display=Fable_Thinking\n', 'Cursor model proof ok line')
    check(p.stdout == 'rc=0\nclaude-fable-test (via cursor)\n', 'Cursor header value')
    d, p = invoke('cursor-wrong','cursor-fable',extra={'CURSOR_DISPLAY':'Wrong Model'})
    check((d / 'voter-claude.fail').exists() and (d / 'voter-claude.model.txt').read_text() == 'claude: FAIL via=cursor ran=Wrong_Model (lineup said claude-fable-test)\n', 'wrong init model fails with proof line')
    check('Wrong_Model (lineup said claude-fable-test)' in p.stdout, 'failed Cursor header reports model that ran')
    d, p = invoke('overflow','fable','cursor')
    check('rc=0' in p.stdout and (d / 'voter-claude.via').read_text() == 'cursor\n', 'usage limit reruns on Cursor')
    check(all((d / f'voter-claude.max.{ext}').exists() for ext in ('json','err','fail')), 'subscription attempt preserved')
    check('note: fable overflow:' in (d / 'errors.md').read_text() and 'note: the Claude subscription leg changed its worktree before the overflow rerun' in (d / 'errors.md').read_text(), 'overflow and dirty-worktree notes recorded')
    d, p = invoke('no-overflow','fable')
    check('rc=1' in p.stdout and not (d / 'voter-claude.stream.jsonl').exists(), 'no rerun without overflow flag')
    d, p = invoke('other-error','fable','cursor',{'MAX_MESSAGE':'Network unavailable'})
    check('rc=1' in p.stdout and (d / 'voter-claude.via').read_text() == 'max\n' and not (d / 'voter-claude.stream.jsonl').exists(), 'unrelated failure never overflows')
    for name, extra in (
        ('model-only', {'MAX_IS_ERROR':'false','MAX_EXIT':'0','MAX_MESSAGE':'Complete review: rate limit handling and usage limit display are correct.'}),
        ('model-only-stderr', {'MAX_IS_ERROR':'false','MAX_EXIT':'0','MAX_MESSAGE':'Complete review.', 'MAX_STDERR':'usage limit reached'}),
        ('retry-500', {'MAX_MESSAGE':'HTTP 500','MAX_STDERR':'retrying after rate limit backoff'})):
        d, p = invoke(name,'fable','cursor',extra)
        check('rc=1' in p.stdout and (d / 'voter-claude.via').read_text() == 'max\n'
              and not (d / 'voter-claude.stream.jsonl').exists(), f'item 4 {name} stays on the Claude subscription')
    d, p = invoke('real-cap-zero','fable','cursor', {'MAX_EXIT':'0'})
    check('rc=0' in p.stdout and (d / 'voter-claude.via').read_text() == 'cursor\n',
          'item 4 is_error true usage cap overflows even with zero process exit')
    d, p = invoke('cursor-unicode','cursor-fable',extra={'CURSOR_RESULT':'Review\u2028text\u0085done'})
    check('rc=0' in p.stdout and json.loads((d / 'voter-claude.json').read_text())['result'] == 'Review\u2028text\u0085done',
          'item 11 Cursor leg accepts literal U+2028 and U+0085')
    # Routing uses the source seam and a local resolver stub; no installed CLI is consulted.
    routing = '''\
set -eu
export COUNCIL_SOURCE_ONLY=1
. "$1"
DIR="$2" ERRORS_FILE="$2/errors.md" BRIEF="$2/voter-claude.brief.md" FLAG_KIND=design FLAG_DIFFICULTY=high FLAG_CLAUDE="$3" FABLE_OVERFLOW="$4"
load_models() { CODEX_REVIEW_MODEL=test; }
resolve_routing
'''
    # Design accepts cursor-fable. The id is a fixture, so a host CURSOR_FABLE
    # cannot change the result. Overflow on a non-review kind stays rejected.
    routed = {key: value for key, value in env.items() if key != 'CURSOR_FABLE'}
    with_id = dict(routed, CURSOR_FABLE='claude-fable-test')
    p = run(['bash','-c',routing,'bash',str(SCRIPTS / 'council.sh'),str(d),'cursor-fable',''], env=with_id)
    check(p.returncode == 0 and 'kind review only' not in p.stderr,
          f'design accepts cursor-fable when the id is set ({p.stderr.strip()})')
    p = run(['bash','-c',routing,'bash',str(SCRIPTS / 'council.sh'),str(d),'cursor-fable',''], env=routed)
    check(p.returncode != 0 and 'Cursor Fable id is empty' in p.stderr,
          f'explicit cursor-fable with an empty id is rejected ({p.stderr.strip()})')
    p = run(['bash','-c',routing,'bash',str(SCRIPTS / 'council.sh'),str(d),'fable','cursor'], env=routed)
    check(p.returncode != 0 and 'kind review only' in p.stderr,
          f'non-review kind rejects fable overflow=cursor ({p.stderr.strip()})')


def test_fetch_race(tmp: Path, mod) -> None:
    for name in ('stack-run.py', 'review-kind.sh'):
        text = (SCRIPTS / name).read_text()
        check('rev-parse FETCH_HEAD' not in text and "rev-parse', 'FETCH_HEAD'" not in text,
              f'fix 1 {name} does not read FETCH_HEAD')
    repo = tmp / 'fetch-race'
    repo.mkdir()

    def git(*args: str) -> str:
        proc = run(['git', '-C', str(repo), *args])
        if proc.returncode:
            raise SystemExit(f'FAIL git fixture {args}: {proc.stderr}')
        return proc.stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.test')
    git('config', 'user.name', 'Test')
    git('config', 'commit.gpgsign', 'false')
    (repo / 'a.txt').write_text('one\n')
    git('add', '.')
    git('commit', '-m', 'one')
    oid1 = git('rev-parse', 'HEAD')
    (repo / 'a.txt').write_text('two\n')
    git('add', '.')
    git('commit', '-m', 'two')
    oid2 = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/pull/1/head', oid1)
    git('update-ref', 'refs/pull/2/head', oid2)
    git('remote', 'add', 'origin', str(repo))
    for number in (1, 2):
        proc = run(['git', '-C', str(repo), 'fetch', 'origin', f'pull/{number}/head'])
        if proc.returncode:
            raise SystemExit(f'FAIL fix 1 fetch pull/{number}/head: {proc.stderr.strip()}')
        check(True, f'fix 1 fetch pull/{number}/head')
    fetched = git('rev-parse', 'FETCH_HEAD')
    check(fetched == oid2 and fetched != oid1, 'fix 1 FETCH_HEAD keeps only the latest fetch')
    check(mod.commit_exists(str(repo), oid1) and mod.commit_exists(str(repo), oid2),
          'fix 1 both fetched commits still exist')
    script = '''\
set -eu
export COUNCIL_SOURCE_ONLY=1
. "$1"
REPO="$2"
PR_BASE=main
FLAG_PR="$3"
PR_OID="$4"
review_fetch_pr
'''
    procs = [
        subprocess.Popen(
            ['bash', '-c', script, 'bash', str(SCRIPTS / 'council.sh'), str(repo), str(number), oid],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for number, oid in ((1, oid1), (2, oid2))]
    failed = []
    for number, proc in zip((1, 2), procs):
        _out, err = proc.communicate(timeout=30)
        if proc.returncode != 0:
            failed.append(f'pr {number} rc={proc.returncode} {err.strip()}')
    detail = '; '.join(failed)
    check(not failed, 'fix 1 two review_fetch_pr checks pass' + (f' ({detail})' if detail else ''))


def test_opus_session_limit(tmp: Path, mod) -> None:
    exact = {
        'is_error': False,
        'subtype': 'success',
        'result': "You've hit your session limit · resets 4am (America/Los_Angeles)",
    }
    ballot = tmp / 'opus-session'
    ballot.mkdir()
    (ballot / 'voter-opus.json').write_text(json.dumps(exact))
    check(mod.opus_capped(ballot), 'fix 3 session limit with empty modelUsage caps without a fail file')
    (ballot / 'voter-opus.json').write_text(json.dumps(dict(exact, modelUsage={})))
    check(mod.opus_capped(ballot), 'fix 3 empty modelUsage object caps')
    (ballot / 'voter-opus.json').write_text(json.dumps(dict(exact, modelUsage={'claude-opus-5': {'inputTokens': 3}})))
    check(not mod.opus_capped(ballot), 'fix 3 limit phrase with modelUsage does not cap')
    jq = shutil.which('jq')
    if not jq:
        raise SystemExit('FAIL jq is not on PATH')
    bindir = tmp / 'opus-bin'
    bindir.mkdir()
    os.symlink(jq, bindir / 'jq')
    write_exe(bindir / 'claude', '''\
        #!/usr/bin/env python3
        import os, sys
        print(os.environ['OPUS_JSON'])
        sys.exit(0)
        ''')
    wt = tmp / 'opus-wt'
    wt.mkdir()
    shell = '''\
set -eu
export COUNCIL_SOURCE_ONLY=1
. "$1"
DIR="$2"
rc=0
review_run_opus_native "$3" opus || rc=$?
printf 'rc=%s\\n' "$rc"
'''
    def invoke(name: str, payload: dict) -> tuple[Path, subprocess.CompletedProcess]:
        dest = tmp / name
        dest.mkdir()
        env = dict(os.environ, PATH=f'{bindir}:/usr/bin:/bin', OPUS_JSON=json.dumps(payload))
        proc = run(['bash', '-c', shell, 'bash', str(SCRIPTS / 'council.sh'), str(dest), str(wt)], env=env)
        check(proc.returncode == 0, f'{name} seam exits ({proc.stderr})')
        return dest, proc
    dest, proc = invoke('opus-leg', exact)
    check('rc=1' in proc.stdout and (dest / 'voter-opus.fail').read_text().strip() == 'opus: usage limit',
          f'fix 3 review_run_opus_native rejects the session-limit JSON ({proc.stdout.strip()} {proc.stderr.strip()})')
    kept = json.loads((dest / 'voter-opus.json').read_text())
    check(kept.get('result') == exact['result'] and 'modelUsage' not in kept, 'fix 3 session-limit JSON is kept')
    dest, proc = invoke('opus-leg-usage', dict(exact, modelUsage={'claude-opus-5': {'inputTokens': 3}}))
    check('rc=0' in proc.stdout and not (dest / 'voter-opus.fail').exists(),
          f'fix 3 same phrase with modelUsage is not a usage-limit failure ({proc.stdout.strip()} {proc.stderr.strip()})')


def test_prompt_words_ignore_repo_path(mod) -> None:
    repo = '/tmp/work/.claude/worktrees/council'
    prompt = f'Round A. Write only {repo}/tmp/council/p-1/brief.md and stop.'
    check(not mod.FORBIDDEN.search(prompt_outside_repo(prompt, repo)),
          'chair prompt check ignores claude in the repo path')
    check(bool(mod.FORBIDDEN.search(prompt_outside_repo(prompt + ' claude', repo))),
          'chair prompt check still flags a vendor word outside the repo path')


def test_shallow_repo(tmp: Path) -> None:
    body = (SCRIPTS / 'review-kind.sh').read_text().split('review_run() {', 1)[1]
    check('\n  review_reject_shallow\n' in body, 'fix 4 phase 1 checks for a shallow repository')
    script = '''\
set -eu
export COUNCIL_SOURCE_ONLY=1
. "$1"
REPO="$2"
review_reject_shallow
printf 'ok\\n'
'''
    def git(repo: Path, *args: str) -> str:
        proc = run(['git', '-C', str(repo), *args])
        if proc.returncode:
            raise SystemExit(f'FAIL git fixture {args}: {proc.stderr}')
        return proc.stdout.strip()

    def make(name: str) -> Path:
        repo = tmp / name
        repo.mkdir()
        git(repo, 'init', '-b', 'main')
        git(repo, 'config', 'user.email', 'test@example.test')
        git(repo, 'config', 'user.name', 'Test')
        git(repo, 'config', 'commit.gpgsign', 'false')
        git(repo, 'commit', '--allow-empty', '-m', 'init')
        return repo

    plain = make('not-shallow')
    proc = run(['bash', '-c', script, 'bash', str(SCRIPTS / 'council.sh'), str(plain)])
    check(proc.returncode == 0 and proc.stdout.strip() == 'ok',
          f'fix 4 a full repository passes the shallow check ({proc.stderr.strip()})')
    shallow = make('shallow')
    # A shallow file is enough. Do not fetch with --depth.
    (shallow / '.git' / 'shallow').write_text(git(shallow, 'rev-parse', 'HEAD') + '\n')
    flagged = git(shallow, 'rev-parse', '--is-shallow-repository')
    check(flagged == 'true', f'fix 4 fixture is shallow ({flagged})')
    proc = run(['bash', '-c', script, 'bash', str(SCRIPTS / 'council.sh'), str(shallow)])
    check(proc.returncode == 1 and 'shallow repository' in proc.stderr
          and '--depth' in proc.stderr and '--unshallow' in proc.stderr,
          f'fix 4 phase 1 stops on a shallow repository ({proc.stderr.strip()})')


def test_whole_stack(tmp: Path, mod) -> None:
    check(mod.origin_ref('main') == 'origin/main', 'fix 5 origin/ prefix on the stack base')
    check(mod.origin_ref('origin/main') == 'origin/main', 'fix 5 keeps an origin/ ref')
    check(mod.origin_ref('owner/branch-name') == 'origin/owner/branch-name', 'fix 5 prefixes a head branch')
    for bad in ('', '-main', 'origin/../main', 'has space', 'branch\nname'):
        try:
            mod.origin_ref(bad)
            raised = False
        except RuntimeError:
            raised = True
        check(raised, f'fix 5 rejects unsafe ref {bad!r}')
    for good in ('fix+tests', 'ryan@box', 'weird#name'):
        try:
            got = mod.origin_ref(good)
        except RuntimeError as exc:
            got = f'RAISED {exc}'
        check(got == f'origin/{good}', f'f10 accepts branch {good!r} ({got})')

    f = Fixture(tmp, 'whole-stack')
    f.env.pop('COUNCIL_CHAIR_MODEL', None)
    oid = f.reconcile_repo()
    name = 'p-stack-stack'
    dry = f.invoke('--dry-run')
    check(dry.returncode == 0 and not f.calls(), f'fix 5 dry run launches nothing ({dry.stderr})')
    check(f'{name}: new;' in dry.stdout and '"--base", "origin/main"' in dry.stdout
          and f'"--head", "{oid}"' in dry.stdout and f'"--repo", "{f.repo}"' in dry.stdout,
          'fix 5 dry run shows the whole-stack council.sh command')
    off = f.invoke('--dry-run', '--no-whole-stack')
    check(off.returncode == 0 and name not in off.stdout and 'After ballots: reconcile stack top' in off.stdout,
          'fix 5 --no-whole-stack hides the whole-stack ballot')
    skipped = f.invoke('--dry-run', '--no-reconcile')
    check(name not in skipped.stdout, 'fix 5 --no-reconcile dry run has no whole-stack ballot')

    got = f.invoke()
    check(got.returncode == 0, f'fix 5 full run includes the whole-stack ballot ({got.stderr})')
    check(f.record()['reconcile'] == 'ok' and f.record()['whole_stack'] == 'ok',
          f"fix 5 reconcile then whole stack ({f.record().get('whole_stack')})")
    rest = ['--base', 'origin/main', '--head', oid, '--repo', str(f.repo), '--fable-overflow', 'cursor']
    stack_calls = [c for c in f.calls() if c[0] == 'council' and c[1][0] == name]
    check([c[1] for c in stack_calls] == [[name, '--dry-run', *rest], [name, *rest], [name, '--findings']],
          f'fix 5 whole-stack council.sh arguments ({[c[1] for c in stack_calls]})')
    check(all(c[2] == 'test-chair' for c in stack_calls), 'fix 5 whole-stack council.sh sees COUNCIL_CHAIR_MODEL')
    earlier = [c for c in f.calls() if c[0] == 'council' and c[1][0] != name]
    check(earlier and all(c[2] is None for c in earlier), 'fix 5 per-PR council.sh calls do not gain a chair model')
    chairs = [c[1][-1] for c in f.calls() if c[0] == 'cursor' and name in c[1][-1]]
    check(len(chairs) == 2, f'fix 5 whole-stack uses round A and round B ({len(chairs)} prompts)')
    brief, decide = chairs
    for phrase in ('problems that span PRs', 'a contract one PR sets and a later PR breaks',
                   'data or migrations that disagree across PRs',
                   'permission or validation gaps that only appear in the combined change',
                   'dead code left by later PRs', 'missing tests for the combined flow',
                   'Do not ask for per-line style', '--base origin/main', f'--head {oid}',
                   'Ballot id: p-stack-stack'):
        check(phrase in brief, f'fix 5 round A brief instruction has {phrase}')
    for phrase in ('name the PR that introduced the line', 'conflicted legs from the builder check',
                   'Address each drafted comment to its PR', 'Jev gate'):
        check(phrase in decide, f'fix 5 round B instruction has {phrase}')
    check('Round A.' in brief and 'Write only' in brief and 'brief.md' in brief and 'do not run Conclave' in brief,
          'fix 5 round A stays on the per-PR brief path')
    check('Ballot directory:' in decide and 'Round B.' in decide, 'fix 5 round B stays on the per-PR decide path')
    check(all(not mod.FORBIDDEN.search(prompt_outside_repo(text, str(ROOT), str(f.repo))) for text in chairs),
          'fix 5 whole-stack prompts contain no model or vendor words')
    check('Whole stack p-stack-stack: ok' in (f.root / 'tmp/council/stack/run.md').read_text(),
          'fix 5 run.md records the whole-stack ballot')
    count = len([c for c in f.calls() if c[0] == 'council' and c[1][0] == name])
    check(f.invoke().returncode == 0 and f.record()['whole_stack'] == 'reused'
          and len([c for c in f.calls() if c[0] == 'council' and c[1][0] == name]) == count,
          'fix 5 a finished whole-stack ballot is not redone')

    f = Fixture(tmp, 'whole-stack-off')
    f.reconcile_repo()
    check(f.invoke('--no-whole-stack').returncode == 0 and f.record()['reconcile'] == 'ok'
          and f.record()['whole_stack'] == 'skipped'
          and not any(c[0] == 'council' and c[1][0].endswith('-stack') for c in f.calls()),
          'fix 5 --no-whole-stack still reconciles and launches no stack ballot')

    f = Fixture(tmp, 'whole-stack-reconcile-fail')
    f.reconcile_repo()
    f.env['RECONCILE_FAIL'] = '1'
    check(f.invoke().returncode == 1 and f.record()['whole_stack'] == 'not started'
          and not any(c[0] == 'cursor' and 'whole stack as one diff' in c[1][-1] for c in f.calls())
          and not any(c[0] == 'council' and c[1][0].endswith('-stack') for c in f.calls()),
          'fix 5 a failed reconcile does not start the whole-stack ballot')

    f = Fixture(tmp, 'whole-stack-fail')
    f.reconcile_repo()
    f.env['NO_RESULT_PR'] = '-stack'
    check(f.invoke().returncode == 1 and str(f.record()['whole_stack']).startswith('failed:')
          and f.record()['reconcile'] == 'ok'
          and not any(c[0] == 'council' and c[1][0].endswith('-stack') for c in f.calls()),
          f"fix 5 whole-stack chair failure fails the run ({f.record().get('whole_stack')})")


def test_stale_head(tmp: Path) -> None:
    """f2: the whole-stack head is the fetched PR oid, not origin/<branch>."""
    f = Fixture(tmp, 'stale-head')
    old = f.reconcile_repo()
    (f.repo / 'a.rb').write_text('two\n')
    f.git('add', 'a.rb')
    f.git('commit', '-m', 'new tip')
    new = f.git('rev-parse', 'HEAD')
    f.git('update-ref', 'refs/pull/3/head', new)
    f.git('update-ref', 'refs/remotes/origin/branch-3', old)
    f.env['TOP_OID'] = new
    dry = f.invoke('--dry-run')
    check(dry.returncode == 0, f'f2 dry run exits zero ({dry.stderr})')
    check(f'"--head", "{new}"' in dry.stdout, f'f2 dry run head is the PR oid not the branch ({dry.stdout})')
    check('"--head", "origin/branch-3"' not in dry.stdout, 'f2 dry run does not point at origin/branch-3')
    check(f.git('rev-parse', 'refs/remotes/origin/branch-3') == old and old != new,
          'f2 the tracking branch stays on the previous commit')

    f = Fixture(tmp, 'fork-head')
    oid = f.reconcile_repo()
    missing = run(['git', '-C', str(f.repo), 'rev-parse', 'origin/branch-3'], env=f.env)
    check(missing.returncode == 128, f'f2 fixture has no origin/branch-3 ({missing.stderr.strip()})')
    dry = f.invoke('--dry-run')
    check(dry.returncode == 0 and f'"--head", "{oid}"' in dry.stdout,
          f'f2 a missing tracking branch still uses the PR oid ({dry.returncode} {dry.stdout} {dry.stderr})')


def test_stack_ballot_name(tmp: Path) -> None:
    """f3: a second stack must not reuse the first stack's whole-stack ballot."""
    f = Fixture(tmp, 'two-stacks')
    f.reconcile_repo()

    def invoke(stack_id: str, *extra: str) -> subprocess.CompletedProcess:
        args = [sys.executable, str(f.root / 'scripts/stack-run.py'), '--repo', str(f.repo),
                '--pr', '2', '--stack-id', stack_id, '--prefix', 'widget', '--chair-model', 'test-chair',
                '--context', str(f.context), *extra]
        return run(args, env=f.env)

    first = invoke('stack-one')
    check(first.returncode == 0, f'f3 first stack finishes ({first.stderr})')
    second = invoke('stack-two', '--dry-run')
    line = next((row for row in second.stdout.splitlines() if 'base:' in row and 'head:' in row), '')
    check('stack-two' in line.split(':', 1)[0] and ': new;' in line,
          f'f3 second stack ballot is its own new ballot ({line or second.stdout})')


def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_detach(tmp: Path) -> None:
    setsid = shutil.which('setsid')
    nohup = shutil.which('nohup')
    ps = shutil.which('ps')
    check(bool(setsid and nohup and ps), f'fix 5 setsid, nohup, and ps are installed ({setsid}, {nohup}, {ps})')

    def arm(f: Fixture) -> None:
        f.env['PATH'] = f'{f.bin}:{Path(setsid).parent}:{Path(nohup).parent}:/usr/bin:/bin'
        f.env.pop('COUNCIL_STACK_DETACHED', None)

    f = Fixture(tmp, 'detach-ok')
    arm(f)
    pidfile = f.base / 'chair.pid'
    f.env.update(DETACH_HOLD='1', CHILD_PID=str(pidfile))
    dest = f.root / 'tmp/council/stack'
    pid = None
    try:
        proc = subprocess.run(f.args('--detach', '--no-reconcile', '--prs', '1'), env=f.env,
                              text=True, capture_output=True, timeout=20)
        check(proc.returncode == 0, f'fix 5 --detach exits 0 once a step starts ({proc.stderr})')
        meta = json.loads((dest / 'detach.json').read_text())
        pid = int(meta['pid'])
        log = meta['log']
        check(f'pid: {pid}' in proc.stdout and f'log: {log}' in proc.stdout and 'started: p-1 chair A' in proc.stdout,
              f'fix 5 --detach prints pid, log path, and the first step ({proc.stdout})')
        check(Path(log) == dest / 'detach.log' and (dest / 'detach.log').is_file(),
              'fix 5 pid and log path are recorded in the stack dir')
        os.kill(pid, 0)
        check(True, 'fix 5 detached child is still running')
        sid = subprocess.run([ps, '-o', 'sid=', '-p', str(pid)], text=True, capture_output=True)
        check(sid.returncode == 0 and sid.stdout.strip() == str(pid),
              f'fix 5 child session is its own pid ({sid.stdout.strip()} {sid.stderr.strip()})')
        os.kill(pid, signal.SIGHUP)
        time.sleep(0.2)
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        check(alive, 'fix 5 SIGHUP does not stop the detached child')
    finally:
        if pid is None and (dest / 'detach.json').is_file():
            try:
                pid = int(json.loads((dest / 'detach.json').read_text())['pid'])
            except (OSError, ValueError, KeyError):
                pid = None
        if pid is not None:
            kill_pid(pid)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not pidfile.exists():
            time.sleep(0.05)
        if pidfile.exists():
            try:
                kill_pid(int(pidfile.read_text()))
            except ValueError:
                pass

    f = Fixture(tmp, 'detach-die')
    arm(f)
    proc = subprocess.run(f.args('--detach', '--chair-model', 'auto', '--no-reconcile', '--prs', '1'),
                          env=f.env, text=True, capture_output=True, timeout=20)
    check(proc.returncode != 0 and 'before the first ballot step' in proc.stderr
          and 'chair model must be explicit' in proc.stderr,
          f'fix 5 child death before a step exits nonzero with the log tail ({proc.returncode} {proc.stderr})')
    check((f.root / 'tmp/council/stack/detach.json').is_file(), 'fix 5 a failed start still records pid and log path')

    f = Fixture(tmp, 'detach-dry')
    arm(f)
    proc = subprocess.run(f.args('--detach', '--dry-run'), env=f.env, text=True, capture_output=True, timeout=20)
    check(proc.returncode == 0 and 'exited 0 before the first ballot step' not in proc.stderr,
          f'f7 a detached dry run that exits 0 is success ({proc.returncode} {proc.stderr} {proc.stdout})')


def test_detach_abbrev(tmp: Path) -> None:
    """f12: an abbreviated detach flag is not detach, and it is not passed to a child."""
    mod = load('stack-run')
    mod.ROOT = tmp / 'abbrev-root'
    spawned: list[list[str]] = []

    class Proc:
        pid = 4242

        def poll(self):
            return 0

    def fake_popen(args, **kwargs):
        spawned.append(list(args))
        return Proc()

    original = mod.subprocess.Popen
    mod.subprocess.Popen = fake_popen
    old = sys.argv
    sys.argv = ['stack-run.py', '--repo', 'r', '--pr', '7', '--stack-id', 'abbrev', '--deta']
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            try:
                rc = mod.main()
            except SystemExit as exc:
                rc = exc.code if isinstance(exc.code, int) else 1
    finally:
        mod.subprocess.Popen = original
        sys.argv = old
    rejected = rc != 0 and not spawned and 'unrecognized arguments' in err.getvalue()
    check(rejected, 'f12 abbreviated --deta is rejected'
          + ('' if rejected else f' ({rc}, {spawned}, {err.getvalue().strip()})'))


def test_detach_resume(tmp: Path) -> None:
    """f6: a resumed detach that fails at startup must not report the previous finished step."""
    setsid = shutil.which('setsid')
    nohup = shutil.which('nohup')
    check(bool(setsid and nohup), f'f6 setsid and nohup are installed ({setsid}, {nohup})')
    f = Fixture(tmp, 'detach-resume')
    f.env['PATH'] = f'{f.bin}:{Path(setsid).parent}:{Path(nohup).parent}:/usr/bin:/bin'
    f.env.pop('COUNCIL_STACK_DETACHED', None)
    write_exe(f.bin / 'gh', '#!/bin/sh\necho "gh unavailable" >&2\nexit 1\n')
    dest = f.root / 'tmp/council/stack'
    dest.mkdir(parents=True)
    (dest / 'logs').mkdir()
    row = {'pr': 7, 'ballot': 'p-7', 'state': 'done', 'outcome': 'ok', 'step': 'finished',
           'chair_a_seconds': 0, 'phase1_seconds': 0, 'chair_b_seconds': 0,
           'claude_flags': [], 'fable_weekly_percent': None, 'POSTED?': []}
    (dest / 'run.json').write_text(json.dumps({
        'prs': [row], 'reconcile': 'ok', 'context': 'ready', 'whole_stack': 'ok', 'POSTED?': [],
    }))
    proc = subprocess.run(f.args('--detach', '--no-reconcile', '--prs', '7', '--pr', '7'),
                          env=f.env, text=True, capture_output=True, timeout=20)
    check(proc.returncode != 0 and 'started: p-7 finished' not in proc.stdout,
          f'f6 a failed resume does not report the old finished step ({proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r})')
    marker = dest / 'step-started'
    check(not marker.is_file(), f'f6 no step marker is written for the inherited row ({marker.read_text() if marker.is_file() else ""})')


def main() -> None:
    (ROOT / 'tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stack-tests-', dir=ROOT / 'tmp') as raw:
        tmp = Path(raw)
        mod = load('stack-run')
        test_runner(tmp, mod)
        test_reconcile(tmp, mod)
        test_fetch_race(tmp, mod)
        for test in (test_blocked_resume, test_linear_stack, test_sticky_resume, test_seat_age,
                     test_posted_chairs, test_posted_resume, test_receipts, test_streams,
                     test_reconcile_types, test_reconcile_no_output, test_max_cap, test_fold_edges,
                     test_signals, test_posted_matcher):
            test(tmp, mod)
        test_opus_session_limit(tmp, mod)
        test_shallow_repo(tmp)
        test_whole_stack(tmp, mod)
        test_stale_head(tmp)
        test_stack_ballot_name(tmp)
        test_detach(tmp)
        test_detach_resume(tmp)
        test_detach_abbrev(tmp)
        test_prompt_words_ignore_repo_path(mod)
        test_cursor_leg(tmp)
    print('ok')


if __name__ == '__main__':
    main()
