#!/usr/bin/env python3
# Usage: scripts/stack-run.py --repo DIR --pr N --stack-id ID [--prs N,N,...]
"""Run resumable per-PR review ballots, reconcile findings at the stack top, then one whole-stack ballot.

Only chairs write review prose. The runner records each step, validates their
receipts, and folds stack verdicts into existing decisions without posting.
Fable at or above 95% weekly usage starts on Opus. An observed Fable overflow still moves later ballots to Cursor.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from conclave_env import publish, setting  # noqa: E402
# Prompts name where the word list lives instead of carrying the words.
WORDS = (f"the vendor and family words matched by review_forbidden_word in "
         f"{ROOT / 'scripts/review-kind.sh'} (read that one line)")


def _forbidden() -> re.Pattern[str]:
    """The same whole-word list review_forbidden_word greps for."""
    text = (ROOT / 'scripts/review-kind.sh').read_text(encoding='utf-8')
    anycase = re.search(r"grep -Eio '\\b\(([^']+)\)\\b'", text)
    names = re.search(r"grep -Eo '\\b\(([^']+)\)\\b'", text)
    if anycase is None or names is None:
        raise RuntimeError('review_forbidden_word list is missing from review-kind.sh')
    return re.compile(r'(?i:\b(?:' + anycase.group(1) + r')\b)|\b(?:' + names.group(1) + r')\b')


FORBIDDEN = _forbidden()
ANSWERS = {'post as a bug', 'post as a question', 'post as advisory', 'hold'}
POLICY = ('You are a chair. Read Linear and Notion, never write to them. '
          'Post nothing to GitHub. Do not edit the target repo. '
          'Your last message must be the full report. ')
NO_SHALLOW = ('Do not pass --depth, --shallow-*, or --unshallow to git. '
              'Those flags make the clone shallow and the next phase 1 stops. ')
BLOCK = re.compile(r'^(\S+) — \*\*what breaks', re.M)
LIMIT = re.compile(r'usage limit|limit reached|hit your .*limit|session limit|weekly limit|requires usage credits|out of extra usage', re.I)
BLOCKED = 'phase 1 recorded errors in errors.md; fix them, then remove blind/ or rerun conclave.sh'
MAX_CAPPED = 'not started; the Claude subscription is at a usage limit (coverage leg)'


def read(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.exists() else ''


def origin_ref(name: str) -> str:
    """A remote-tracking ref for a PR base or head branch. origin/ is kept.

    Any branch name git accepts is allowed. A leading dash is refused so the
    ref cannot be read as a flag.
    """
    if not isinstance(name, str) or not name or name.startswith('-') or any(c in name for c in '\n\r\x00'):
        raise RuntimeError(f'unsafe stack ref: {name!r}')
    if name.startswith('/') or name.endswith('/') or '..' in name.split('/'):
        raise RuntimeError(f'unsafe stack ref: {name!r}')
    ref = name if name.startswith('origin/') else f'origin/{name}'
    branch = ref[len('origin/'):]
    if not branch or any(not part or part.startswith('-') for part in branch.split('/')):
        raise RuntimeError(f'unsafe stack ref: {name!r}')
    proc = subprocess.run(
        ['git', 'check-ref-format', f'refs/remotes/{ref}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f'unsafe stack ref: {name!r}')
    return ref


def commit_exists(repo: str, oid: str) -> bool:
    """True when oid is a commit in repo. Do not read FETCH_HEAD."""
    proc = subprocess.run(
        ['git', '-C', repo, 'cat-file', '-e', f'{oid}^{{commit}}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0


def write_json(path: Path, data: object) -> None:
    """Replace a complete record atomically so interruption cannot truncate it."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(path)


def log_tail(path: Path, count: int = 40) -> str:
    if not path.is_file():
        return ''
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    return '\n'.join(lines[-count:])


def brief_hash(ballot: Path) -> str:
    return hashlib.sha256((ballot / 'brief.md').read_bytes()).hexdigest()


def state(ballot: Path) -> str:
    if (ballot / 'judgment.md').exists():
        decide = read(ballot / 'decide.md')
        if ((ballot / 'decide.md').exists() and 'TODO(chair)' not in decide
                and ((ballot / 'comments.md').exists() or decide.strip() == 'There is nothing to decide.')):
            return 'done'
        return 'needs decide'
    if list((ballot / 'blind').glob('leg-*.md')):
        errors = ballot / 'errors.md'
        if not errors.exists() or any(line.strip() and line.strip() != '(none)' and not line.strip().startswith('note:')
                                      for line in read(errors).split('\n')):
            return 'blocked'
        return 'needs findings'
    if (ballot / 'brief.md').exists() and read(ballot / '.stack-brief.sha256').strip() == brief_hash(ballot):
        return 'needs legs'
    return 'new'


def seat_percent(path: Path) -> float | None:
    try:
        data = json.loads(read(path))
        ts = data['ts']
        if (type(ts) not in (int, float) or not math.isfinite(ts)
                or time.time() - ts > float(setting('SEAT_MAX_AGE', '900'))):
            return None
        value = data['fable']
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def claude_flags(percent: float | None, sticky: bool) -> list[str]:
    if sticky:
        return ['--claude-model', 'cursor-fable']
    if percent is not None and percent >= 95:
        return ['--claude-model', 'opus']
    return ['--fable-overflow', 'cursor']


def opus_capped(ballot: Path) -> bool:
    failed = (ballot / 'voter-opus.fail').exists()
    if failed and LIMIT.search(read(ballot / 'voter-opus.err')):
        return True
    try:
        data = json.loads(read(ballot / 'voter-opus.json'))
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    result = data.get('result')
    if not isinstance(result, str) or not LIMIT.search(result):
        return False
    usage = data.get('modelUsage')
    # Missing or empty modelUsage is a usage-limit failure even when is_error is false.
    if not isinstance(usage, dict) or len(usage) == 0:
        return True
    return failed and data.get('is_error') is True


def script_paths(command: str) -> list[str | None]:
    """Local bash/sh script paths named by a command.

    None means bash or sh was given a script operand that is not a readable
    local file. The caller flags that command. -c is a command string, not a file.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        if re.search(r'(?:^|\s)(?:\S*/)?(?:bash|sh)\b', command):
            return [None]
        return []
    found: list[str | None] = []
    i = 0
    while i < len(tokens):
        base = tokens[i].rsplit('/', 1)[-1]
        if base not in ('bash', 'sh'):
            i += 1
            continue
        i += 1
        skip_script = False
        while i < len(tokens) and tokens[i].startswith('-') and tokens[i] != '-':
            flag = tokens[i]
            name, eq, _value = flag.partition('=')
            i += 1
            if name.startswith('--'):
                if name in ('--rcfile', '--init-file') and not eq and i < len(tokens):
                    i += 1
                continue
            letters = name[1:]
            if 'c' in letters:
                # -c and clusters such as -lc take the next word as a command string.
                if letters.endswith('c') and not eq and i < len(tokens):
                    i += 1
                skip_script = True
                break
            if letters == 'o' and not eq and i < len(tokens):
                i += 1
        if skip_script or i >= len(tokens) or tokens[i].startswith('-'):
            continue
        raw = tokens[i].rstrip(';|&')
        i += 1
        if not raw or '://' in raw:
            found.append(None)
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path = path.resolve()
        except OSError:
            found.append(None)
            continue
        found.append(str(path) if path.is_file() else None)
    return found


def posted_hits(events: list[dict]) -> list[str]:
    """Match executed commands and the MCP tool's own name.

    Text from a read tool is ignored. A bash or sh command that names a
    script is scanned when that file exists and is local. A missing script
    is flagged, because its body cannot be checked.
    """
    gh = r'\bgh\s+(?:(?:-R|--repo)(?:=|\s+)\S+\s+)*'
    gh_post = re.compile(gh + r'(?:pr|issue)\s+(?:review|comment|edit|merge|close|reopen|create|ready|lock|delete)\b')
    gh_api = re.compile(gh + r'api\b(.*)', re.S)
    api_write = re.compile(r'(?:^|\s)(?:-X|--method)(?:=|\s*)(?:POST|PATCH|PUT|DELETE)\b'
                           r'|(?:^|\s)(?:-[fF]|--field|--raw-field|--input)(?==|\s|$)', re.I)
    curl_write = re.compile(r'(?:^|\s)(?:-X|--request)(?:=|\s+)(?:POST|PATCH|PUT|DELETE)\b'
                            r'|(?:^|\s)(?:-d|--data[\w-]*)(?==|\s|$)'
                            r'|(?:^|\s)--json(?==|\s|$)', re.I)
    # Global options before the subcommand, not only -C. -c and -C take a value.
    git_global = (
        r'-C(?:\s+\S+|\S+)'
        r'|-c(?:\s+\S+|\S+)'
        r'|--no-pager|--bare|--no-replace-objects|-p|--paginate'
        r'|--(?:html-path|man-path|info-path|version|help)'
        r'|--exec-path(?:=\S+)?'
        r'|--(?:git-dir|work-tree|namespace|super-prefix|config-env|list-cmds)(?:=\S+|\s+\S+)'
    )
    git_push = re.compile(rf'\bgit(?:\s+(?:{git_global}))*\s+push\b')
    mcp_server = re.compile(r'(?i)linear|notion')
    # A server prefix (linear-, notion-, notion-notion-) may sit in front of the verb.
    # ai-search is a read. Shell executable names are not tool names.
    mcp_read = re.compile(
        r'^(?:mcp__.*?__)?(?:[A-Za-z0-9]+-)*?(?:ai-search|get|list|search|query|fetch|read|extract|check|guide)',
        re.I)

    def commands(value: object, key: str = ''):
        if isinstance(value, str):
            if key == 'command':
                yield value
        elif isinstance(value, dict):
            for k, v in value.items():
                yield from commands(v, k)
        elif isinstance(value, list):
            for item in value:
                yield from commands(item, key)

    def own_tool(container: object) -> tuple[str, str] | None:
        """The tool name on this object, plus a linear/notion server field if one is set."""
        if not isinstance(container, dict):
            return None
        name = ''
        for key in ('toolName', 'name', 'tool'):
            value = container.get(key)
            if isinstance(value, str) and value:
                name = value
                break
        if not name:
            return None
        server = ''
        for key in ('providerIdentifier', 'server', 'serverIdentifier'):
            value = container.get(key)
            if isinstance(value, str) and mcp_server.search(value):
                server = value
                break
        return server, name

    hits: list[str] = []

    def graphql_file_mutation(rest: str) -> bool:
        """A graphql -f query=@file whose body is a mutation, or cannot be read."""
        for match in re.finditer(
            r'(?:^|\s)(?:-[fF]|--field|--raw-field)(?:=|\s+)(\S+)', rest
        ):
            raw = match.group(1).strip('"').strip("'")
            if '=' in raw:
                raw = raw.split('=', 1)[1].strip('"').strip("'")
            if not raw.startswith('@') or raw == '@':
                continue
            path = Path(raw[1:]).expanduser()
            if not path.is_absolute():
                path = Path.cwd() / path
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
            except OSError:
                return True
            if re.search(r'\bmutation\b', text):
                return True
        return False

    def scan(value: str) -> None:
        hits.extend(match.group(0) for match in gh_post.finditer(value))
        for api in gh_api.finditer(value):
            rest = api.group(1)
            graphql = re.search(r'\bgraphql\b', rest)
            wrote = (
                graphql and (re.search(r'\bmutation\b', rest) or graphql_file_mutation(rest))
            ) or (not graphql and api_write.search(rest))
            if wrote:
                hits.append(api.group(0))
        if re.search(r'\bcurl\s', value) and 'api.github.com' in value and curl_write.search(value):
            hits.append(value)
        hits.extend(match.group(0) for match in git_push.finditer(value))

    for event in events:
        if not isinstance(event, dict) or event.get('type') not in ('tool_call', 'tool_use'):
            continue
        for value in commands(event):
            scan(value)
            for script in script_paths(value):
                if script is None:
                    hits.append(value)
                    continue
                try:
                    text = Path(script).read_text(encoding='utf-8', errors='replace')
                except OSError:
                    hits.append(value)
                    continue
                scan(text)
        identities = []
        found = own_tool(event)
        if found:
            identities.append(found)
        call = event.get('tool_call')
        if isinstance(call, dict):
            mcp = call.get('mcpToolCall')
            if isinstance(mcp, dict):
                args = mcp.get('args')
                found = own_tool(args if isinstance(args, dict) else mcp)
                if found:
                    identities.append(found)
        for server, name in identities:
            if not server and not mcp_server.search(name):
                continue
            if mcp_read.match(name):
                continue
            hits.append(f'mcp write: {name}')
    return list(dict.fromkeys(hits))


def verdict_text(entry: dict) -> str:
    return {'present': 'still present at the top', 'fixed': f"fixed in #{entry.get('pr')}",
            'changed': f"changed in #{entry.get('pr')}", 'unknown': 'not checked'}[entry['verdict']]


def validate_reconcile(raw: object, inputs: list[dict], prs: list[dict],
                       top_oid: str) -> tuple[dict, list[str]]:
    """Normalize untrusted chair output; omissions and invalid rows stay visible."""
    warnings: list[str] = []
    order = {p['number']: i for i, p in enumerate(prs)}
    expected = {(f['ballot'], f['id']): f for f in inputs}
    entries: dict[tuple[str, str], list[dict]] = {}
    rows = raw.get('findings', []) if isinstance(raw, dict) else []
    if not isinstance(rows, list):
        rows = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('ballot'), str) or not isinstance(row.get('id'), str):
            warnings.append('invalid reconcile entry without ballot/id')
            continue
        key = (row['ballot'], row['id'])
        if key not in expected:
            warnings.append(f'unexpected finding {key}')
            continue
        entries.setdefault(key, []).append(row)
    result = []
    wrong_top = not isinstance(raw, dict) or raw.get('top_pr') != prs[-1]['number'] or raw.get('top_oid') != top_oid
    for key, finding in expected.items():
        candidates = entries.get(key, [])
        reason = ''
        row = candidates[0] if candidates else {}
        if not candidates:
            reason = 'not returned by the reconcile chair'
        elif len(candidates) != 1:
            reason = 'duplicate finding returned by the reconcile chair'
        elif wrong_top:
            reason = 'reconcile chair returned a different stack top'
        elif not isinstance(row.get('verdict'), str) or row['verdict'] not in ('present', 'fixed', 'changed', 'unknown'):
            reason = 'invalid verdict'
        elif not isinstance(row.get('recommend'), str) or row['recommend'] not in ANSWERS:
            reason = 'invalid recommendation'
        elif not isinstance(row.get('evidence'), str) or not row['evidence'].strip():
            reason = 'missing evidence (unknown requires a reason)'
        elif not isinstance(row.get('why'), str) or not row['why'].strip() or not isinstance(row.get('repro_run'), bool):
            reason = 'missing why or boolean repro_run'
        elif row['verdict'] in ('fixed', 'changed'):
            pr = row.get('pr')
            if type(pr) is not int or pr not in order or order[pr] <= order[finding['pr']]:
                reason = 'fixed/changed requires a later PR in the stack'
        elif 'pr' not in row or row['pr'] is not None:
            reason = 'present/unknown requires pr null'
        if reason:
            warnings.append(f'{key[0]} {key[1]}: {reason}')
            row = {'ballot': key[0], 'id': key[1], 'verdict': 'unknown', 'pr': None,
                   'evidence': reason, 'repro_run': False, 'recommend': 'hold', 'why': reason}
        result.append(row)
    return {'top_pr': prs[-1]['number'], 'top_oid': top_oid, 'findings': result}, warnings


def cell(value: object) -> str:
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', '<br>')


def render_reconcile(doc: dict) -> str:
    rows = doc['findings']
    counts = ', '.join(f"{v}: {sum(r['verdict'] == v for r in rows)}" for v in ('present', 'fixed', 'changed', 'unknown'))
    lines = [f"# Stack top #{doc['top_pr']}", f"\nTop oid: {doc['top_oid']}\n\n{counts}\n",
             '| Ballot | Id | Verdict | Recommend | Why | Evidence |', '|---|---|---|---|---|---|']
    for row in rows:
        lines.append('| ' + ' | '.join(cell(v) for v in (row['ballot'], row['id'], verdict_text(row),
                                                        row['recommend'], row['why'], row['evidence'])) + ' |')
    return '\n'.join(lines) + '\n'


def fold_decide(text: str, verdicts: dict[str, dict], top_pr: int, top_oid: str,
                folded: set[str] | None = None) -> str:
    """Insert or replace only stack-top lines in matching finding blocks."""
    current = None
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        match = BLOCK.match(line)
        if match:
            current = match.group(1)
        row = verdicts.get(current) if current else None
        if row and line.startswith('**at the stack top'):
            continue
        output.append(line)
        if row and line.startswith('**later PRs that change this file**:'):
            ending = '\r\n' if line.endswith('\r\n') else '\n'
            if not line.endswith('\n'):
                output.append(ending)
            why = ' '.join(row['why'].splitlines()).replace('TODO(chair)', 'TODO (chair)')
            output.append(f"**at the stack top (#{top_pr} {top_oid[:8]})**: {verdict_text(row)}; "
                          f"recommend {row['recommend']}: {why}{ending}")
            if folded is not None:
                folded.add(current)
    return ''.join(output)


class Runner:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo = Path(args.repo).expanduser().resolve()
        self.dest = ROOT / 'tmp/council' / args.stack_id
        self.prefix = args.prefix or self.repo.name
        for value in (args.stack_id, self.prefix):
            if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', value):
                raise RuntimeError(f'unsafe stack id or prefix: {value!r}')
        self.dest.mkdir(parents=True, exist_ok=True)
        self.context = self.dest / 'context.md'
        self.logs = self.dest / 'logs'
        self.council = setting('SCRIPT', str(ROOT / 'scripts/conclave.sh'))
        self.cursor = setting('CURSOR_AGENT', 'cursor-agent')
        self.seat = Path(setting('SEAT_JSON', '~/.cache/seat-usage/latest.json')).expanduser()
        self.lock = threading.RLock()
        self.children: set[subprocess.Popen] = set()
        self.interrupted: int | None = None
        self.launching_on_main = False
        self.deferred_signal: int | None = None
        self.sticky = False
        self.max_capped = False
        self.previous = json.loads(read(self.dest / 'run.json') or '{}')
        self.record: dict = {'prs': self.previous.get('prs', []), 'reconcile': 'pending', 'context': 'pending',
                             'whole_stack': 'pending',
                             'POSTED?': list(dict.fromkeys(self.previous.get('POSTED?', [])))}
        self.model = args.chair_model
        self.detach_step = ''
        if self.model:
            self.check_model()

    def check_model(self) -> None:
        if not self.model or self.model.lower() == 'auto' or 'composer' in self.model.lower():
            raise RuntimeError('chair model must be explicit, non-auto, and contain no composer')

    def ballot(self, number: int) -> Path:
        return ROOT / 'tmp/council' / f'{self.prefix}-{number}'

    def start_child(self, args: list[str], *, cleanup: bool = False, **kwargs) -> subprocess.Popen:
        with self.lock:
            if self.interrupted and not cleanup:
                raise RuntimeError(f'interrupted by signal {self.interrupted}')
            # Defer a main-thread handler until its new child is registered.
            # Worker launches hold the lock, so a handler waits for registration.
            on_main = threading.current_thread() is threading.main_thread()
            if on_main:
                self.launching_on_main = True
            try:
                proc = subprocess.Popen(args, start_new_session=True, **kwargs)
                self.children.add(proc)
            finally:
                if on_main:
                    self.launching_on_main = False
                    if self.deferred_signal:
                        signum, self.deferred_signal = self.deferred_signal, None
                        self.handle_signal(signum)
            return proc

    def handle_signal(self, signum: int, _frame=None) -> None:
        if self.launching_on_main:
            self.deferred_signal = signum
            return
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        self.interrupt(signum)

    @staticmethod
    def signal_child(proc: subprocess.Popen, signum: int) -> None:
        try:
            os.killpg(proc.pid, signum)
        except ProcessLookupError:
            pass

    def process(self, args: list[str], *, timeout: float | None = None, cleanup: bool = False,
                **kwargs) -> subprocess.CompletedProcess:
        proc = self.start_child(args, cleanup=cleanup, **kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            self.signal_child(proc, signal.SIGKILL)
            proc.communicate()
            raise
        finally:
            with self.lock:
                self.children.discard(proc)

    def command(self, args: list[str], cwd: Path | None = None, *, cleanup: bool = False) -> str:
        proc = self.process(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cleanup=cleanup)
        if proc.returncode:
            raise RuntimeError(f'{shlex.join(args)}: {proc.stderr.strip() or proc.returncode}')
        return proc.stdout

    def interrupt(self, signum: int) -> None:
        with self.lock:
            self.interrupted = signum
            children = set(self.children)
            message = f'interrupted by signal {signum}'
            self.record['error'] = message
            for step in ('context', 'reconcile', 'whole_stack'):
                if self.record.get(step) == 'running':
                    self.record[step] = f'failed: {message}'
        for proc in children:
            self.signal_child(proc, signal.SIGTERM)
        deadline = time.monotonic() + 10
        pending = set(children)
        while pending and time.monotonic() < deadline:
            for proc in list(pending):
                proc.poll()  # Reap leaders before checking the whole process group.
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    pending.remove(proc)
            if pending:
                time.sleep(.05)
        for proc in pending:
            self.signal_child(proc, signal.SIGKILL)
        for proc in children:
            proc.wait()
        try:
            self.save()
        finally:
            raise SystemExit(128 + signum)

    def save(self) -> None:
        with self.lock:
            write_json(self.dest / 'run.json', self.record)
            lines = ['| PR | Ballot | Start | Outcome | Chair A seconds | Phase 1 seconds | Chair B seconds | Claude flags | Fable weekly % | POSTED? |',
                     '|---|---|---|---|---|---|---|---|---|---|']
            for row in self.record['prs']:
                lines.append('| ' + ' | '.join(cell(row[k]) for k in
                    ('pr', 'ballot', 'state', 'outcome', 'chair_a_seconds', 'phase1_seconds', 'chair_b_seconds',
                     'claude_flags', 'fable_weekly_percent', 'POSTED?')) + ' |')
            lines.append('\nContext and reconcile chairs POSTED?: ' +
                         (', '.join(self.record['POSTED?']) or 'none'))
            lines.append(f"\nReconcile: {self.record['reconcile']}")
            whole = self.record.get('whole_stack_row') or {}
            ballot = whole.get('ballot')
            status = self.record.get('whole_stack', 'pending')
            hits = whole.get('POSTED?') or []
            posted = f"; POSTED?: {', '.join(hits)}" if hits else ''
            lines.append(f"\nWhole stack{(' ' + ballot) if ballot else ''}: {status}{posted}\n")
            (self.dest / 'run.md').write_text('\n'.join(lines), encoding='utf-8')
            self.note_first_step()

    def mark_step(self, row: dict, step: str) -> None:
        """Record a step this process started. Inherited rows do not count."""
        row['step'] = step
        if step and step != 'pending' and not self.detach_step:
            self.detach_step = f"{row.get('ballot', '')} {step}"

    def note_first_step(self) -> None:
        """Let a --detach parent see that this process reached a ballot step."""
        token_path = self.dest / 'detach.token'
        marker = self.dest / 'step-started'
        if marker.exists() or not token_path.is_file() or not self.detach_step:
            return
        token = token_path.read_text(encoding='utf-8').strip().splitlines()
        if not token:
            return
        write_text_atomic(marker, token[0] + '\n' + self.detach_step + '\n')

    def stack(self) -> list[dict]:
        spec = importlib.util.spec_from_file_location('review_stack', ROOT / 'scripts/review-stack.py')
        assert spec and spec.loader
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.gh_json = lambda repo, args: json.loads(self.command(['gh', *args], Path(repo)))
        if self.args.prs:
            try:
                numbers = [int(n) for n in self.args.prs.split(',')]
            except ValueError as exc:
                raise RuntimeError('--prs must be a comma-separated list of positive integers') from exc
            if not numbers or any(n <= 0 for n in numbers) or len(set(numbers)) != len(numbers):
                raise RuntimeError('--prs must contain distinct positive PR numbers')
            prs = []
            for n in numbers:
                row = helper.pr_view(str(self.repo), n)
                row['files'], row['changedFiles'] = helper.pr_file_info(str(self.repo), n)
                row['target'] = n == self.args.pr
                prs.append(row)
            helper.write_stack_json(self.dest, prs, self.args.pr, helper.header_value(prs, self.args.pr), False)
            helper.write_stack_md(self.dest, prs, self.args.pr, False)
        else:
            self.command([sys.executable, str(ROOT / 'scripts/review-stack.py'), str(self.repo), str(self.args.pr), str(self.dest)])
            doc = json.loads(read(self.dest / 'stack.json'))
            if doc['status'] == 'unknown' or doc.get('truncated'):
                raise RuntimeError(f"stack walk incomplete: {doc.get('error', 'truncated at 20 PRs')}; use --prs")
            prs = doc['prs']
            if doc['status'] == 'none':
                row = helper.pr_view(str(self.repo), self.args.pr)
                row['files'], row['changedFiles'] = helper.pr_file_info(str(self.repo), self.args.pr)
                prs = [row]
        for previous, current in zip(prs, prs[1:]):
            if current['base'] != previous['head']:
                raise RuntimeError(f"stack breaks at #{current['number']}: base {current['base']!r} "
                                   f"does not match #{previous['number']} head {previous['head']!r}; pass a linear --prs")
        if any(self.ballot(p['number']) == self.dest for p in prs):
            raise RuntimeError('--stack-id must differ from every ballot id')
        if ROOT / 'tmp/council' / self.whole_ballot_name() == self.dest:
            raise RuntimeError('--stack-id must differ from the whole-stack ballot id')
        return prs

    def flags(self, refresh: bool = True) -> tuple[list[str], float | None]:
        if refresh:
            seat_cli = os.path.expanduser(setting('SEAT_USAGE', '~/.local/bin/seat-usage'))
            try:
                self.process([seat_cli], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
            except (OSError, subprocess.TimeoutExpired):
                pass
        with self.lock:
            percent = seat_percent(self.seat)
            return claude_flags(percent, self.sticky), percent

    def phase_commands(self, ballot: str, pr: int, flags: list[str],
                       target: list[str] | None = None) -> list[list[str]]:
        # Per-PR ballots pass --pr. The whole-stack ballot passes --base and --head.
        rest = [*(target if target is not None else ['--pr', str(pr)]), '--repo', str(self.repo), *flags]
        return [[self.council, ballot, '--dry-run', *rest], [self.council, ballot, *rest]]

    def preview_steps(self, ballot: str, status: str, pr: int, flags: list[str],
                      target: list[str] | None = None) -> list[str]:
        steps = []
        if status == 'new':
            steps.append('chair A')
        if status in ('new', 'needs legs'):
            steps.extend(json.dumps(a) for a in self.phase_commands(ballot, pr, flags, target))
        if status not in ('done', 'blocked'):
            steps.append('chair B (phase 4 only)' if status == 'needs decide' else 'chair B (phases 2-4)')
        return steps

    def whole_ballot_name(self) -> str:
        """Ballot id for the combined diff. The stack id keeps two stacks apart."""
        return f'{self.prefix}-{self.args.stack_id}-stack'

    def top_head_oid(self, prs: list[dict]) -> str:
        """Head commit GitHub reports for the top PR. Not origin/<branch>."""
        top = prs[-1]['number']
        meta = json.loads(self.command(
            ['gh', 'pr', 'view', str(top), '--json', 'headRefOid'], self.repo))
        oid = str(meta.get('headRefOid') or '')
        if not re.fullmatch(r'[0-9a-fA-F]{40}', oid):
            raise RuntimeError(f'top PR #{top} returned no head oid')
        return oid.lower()

    def fetch_top_head(self, prs: list[dict], oid: str) -> None:
        """Fetch pull/<n>/head and require the GitHub oid to be present."""
        top = prs[-1]['number']
        repo = str(self.repo)
        self.command(['git', '-C', repo, 'fetch', 'origin', f'pull/{top}/head'])
        if not commit_exists(repo, oid):
            raise RuntimeError(f'top commit {oid} is not in the repo after fetch')

    def stack_refs(self, prs: list[dict]) -> tuple[str, str]:
        """Bottom PR base as origin/<branch>, top PR head as the GitHub oid."""
        return origin_ref(str(prs[0].get('base', ''))), self.top_head_oid(prs)

    def chair(self, label: str, prompt: str, workspace: Path, row: dict,
              step: str, resume: str | None = None) -> str | None:
        args = [self.cursor, '-p', '--model', self.model, '--output-format', 'stream-json', '-f', '--trust',
                '--approve-mcps', '--workspace', str(workspace)]
        if resume:
            args += ['--resume', resume]
        args.append(POLICY + prompt)
        log = self.logs / f'{label}.jsonl'
        start = time.monotonic()
        error = ''
        # Chair marker on this subprocess only. Do not publish it on the runner:
        # phase 1 and the runner's other children must not receive it from here.
        # CONCLAVE_CHAIR_MODEL is the model id, not this marker.
        chair_env = os.environ.copy()
        chair_env['CONCLAVE_CHAIR'] = '1'
        try:
            with log.open('w') as out, log.with_suffix('.err').open('w') as err:
                try:
                    rc = self.process(args, stdout=out, stderr=err, cwd=workspace,
                                      timeout=self.args.chair_timeout, env=chair_env).returncode
                    if rc:
                        error = f'chair exit {rc}'
                except subprocess.TimeoutExpired:
                    error = f'chair timeout after {self.args.chair_timeout}s'
        finally:
            row[step + '_seconds'] = round(time.monotonic() - start, 3)
            self.save()
        events = []
        malformed_last = False
        lines = read(log).split('\n')
        if lines[-1] == '':
            lines.pop()
        for line in lines:
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('not an object')
                events.append(event)
                malformed_last = False
            except ValueError:
                malformed_last = True
        row['POSTED?'] = list(dict.fromkeys([*row.get('POSTED?', []), *posted_hits(events)]))
        self.save()
        if error:
            raise RuntimeError(error)
        if malformed_last or not events or events[-1].get('type') != 'result' or events[-1].get('is_error') is not False:
            raise RuntimeError('chair has no successful final result line')
        return next((e.get('session_id') for e in events if e.get('type') == 'system'), None)

    def brief_prompt(self, row: dict, ballot: Path) -> str:
        target = row.get('target')
        if target:
            base = target[target.index('--base') + 1]
            head = target[target.index('--head') + 1]
            return (f"Round A. This ballot is the whole stack as one diff. Ballot id: {ballot.name}. "
                    f"Target: --repo {self.repo} --base {base} --head {head} "
                    f"(the top of the stack, PR {row['pr']}). "
                    f"Read {self.context} and {self.dest / 'stack.md'}. "
                    f"Write only {ballot / 'brief.md'} and stop; do not run Conclave. "
                    'First lines: kind: review and difficulty: high. '
                    'Then # Conclave brief: review of the whole stack. '
                    'Ask reviewers for problems that span PRs: a contract one PR sets and a later PR breaks, '
                    'data or migrations that disagree across PRs, '
                    'permission or validation gaps that only appear in the combined change, '
                    'dead code left by later PRs, and missing tests for the combined flow. '
                    'Do not ask for per-line style. '
                    f'Do not use these words anywhere in the brief, {WORDS}. The script rejects a brief that contains one. '
                    + NO_SHALLOW)
        return (f"Round A. Read gh pr view {row['pr']} and gh pr diff {row['pr']} read-only, "
                f"{self.context}, and {self.dest / 'stack.md'}. Write only {ballot / 'brief.md'} and stop; do not run Conclave. "
                'First lines: kind: review and difficulty: high. Then # Conclave brief: review of '
                f"#{row['pr']}, <title>. Sections: ## Goal (domain purpose, worst outcome, where to look hardest); "
                '## Settled decisions (do not relitigate: only checked facts and recorded human decisions, never a mechanism this diff introduces; '
                'a finding that the design lets code skip CI, auth, or validation is still reported); ## Specification (ticket and shared context); '
                '## In / out of scope (in: files this PR changes; out: code later PRs add; never review against the stack top). '
                'Include relevant ticket and domain facts, not a blind paste of the context. '
                f'Do not use these words anywhere in the brief, {WORDS}. The script rejects a brief that contains one. '
                + NO_SHALLOW)

    def ballot_run(self, row: dict) -> None:
        ballot = ROOT / 'tmp/council' / row['ballot']
        session = None
        try:
            with self.lock:
                if self.max_capped:
                    raise RuntimeError(MAX_CAPPED)
            if row['state'] == 'blocked':
                raise RuntimeError(BLOCKED)
            if row['state'] == 'done':
                row['outcome'] = 'reused'
                return
            ballot.mkdir(parents=True, exist_ok=True)
            if row['state'] == 'new':
                (ballot / '.stack-brief.sha256').unlink(missing_ok=True)
                if (ballot / 'brief.md').exists():
                    (ballot / 'brief.md').replace(ballot / 'brief.md.prev')
                self.mark_step(row, 'chair A')
                self.save()
                session = self.chair(row['ballot'] + '-chair-a', self.brief_prompt(row, ballot), self.repo, row, 'chair_a')
                if not (ballot / 'brief.md').is_file():
                    raise RuntimeError('chair A did not write brief.md')
            if row['state'] in ('new', 'needs legs'):
                brief = read(ballot / 'brief.md')
                if not re.match(r'\s*kind:\s*review\s*$', brief, re.M) or FORBIDDEN.search(brief):
                    raise RuntimeError('brief headers invalid or brief contains a forbidden word')
                if row['state'] == 'new':
                    (ballot / '.stack-brief.sha256').write_text(brief_hash(ballot) + '\n', encoding='utf-8')
                flags, percent = self.flags()
                row.update(claude_flags=flags, fable_weekly_percent=percent)
                self.mark_step(row, 'phase 1')
                self.save()
                start = time.monotonic()
                try:
                    with (self.logs / f"{row['ballot']}-phase1.out").open('w') as out, (self.logs / f"{row['ballot']}-phase1.err").open('w') as err:
                        for args in self.phase_commands(row['ballot'], row['pr'], flags, row.get('target')):
                            rc = self.process(args, cwd=self.repo, stdout=out, stderr=err).returncode
                            if rc:
                                raise RuntimeError(f'phase 1 exit {rc}: {shlex.join(args)}')
                finally:
                    with self.lock:
                        if read(ballot / 'voter-claude.via').strip() == 'cursor':
                            self.sticky = True
                        if opus_capped(ballot):
                            self.max_capped = True
                    row['phase1_seconds'] = round(time.monotonic() - start, 3)
                    self.save()
                if not list((ballot / 'blind').glob('leg-*.md')):
                    raise RuntimeError('phase 1 produced no blinded reports')
            self.mark_step(row, 'chair B')
            self.save()
            instructions = ('judgment.md already exists: skip findings.json and the findings command; do phase 4 only. '
                            if (ballot / 'judgment.md').exists() else
                            f'Write findings.json in the Findings file schema, then run {shlex.join([self.council, row["ballot"], "--findings"])}. ')
            span = ''
            if row.get('target'):
                span = ('For each finding, name the PR that introduced the line (git log on the stack branches). '
                        'Note in decide.md any conflicted legs from the builder check. '
                        'Address each drafted comment to its PR. '
                        'Your last message is the full report: judgment header, every gated finding with PR, '
                        'file:line, defect, legs, and Jev gate. ')
            prompt = (f"Round B. Ballot directory: {ballot}. Follow {ROOT / 'docs/review.md'} phases 2 to 4. "
                      'Read only blind/leg-*.md for reports; never voter-* files; never blind/key.txt until the convergence table exists. '
                      + instructions + f'Replace every TODO(chair) in {ballot / "decide.md"}. '
                      f'Draft comments into {ballot / "comments.md"}. '
                      'On someone else\'s pull request, label each new inline comment and each review-body item with **Requested:**, **Advisory:**, or **Informational:**. '
                      'Do not draft **Question:**. A reply to an existing comment never gets a label. '
                      f'When you verify a finding with a throwaway script, save it as {ballot / "repro"}/<finding-id>.<ext> '
                      '(for example repro/f1.rb). The drafter attaches that file to the comment. A finding with no file there gets no block. '
                      'Drafts must pass scripts/check-comment-links.py. That check rejects an internal word in a public comment and names the plain replacement. '
                      'A fenced script is not prose. A post payload must also pass scripts/check-comment-labels.py. '
                      + span + 'Write only in the ballot directory. Post nothing. ' + NO_SHALLOW)
            self.chair(row['ballot'] + '-chair-b', prompt, self.repo, row, 'chair_b', session)
            if not (ballot / 'findings.json').is_file() or state(ballot) != 'done':
                raise RuntimeError('missing findings/judgment/decide/comments or unfilled TODO(chair)')
            row['outcome'] = 'ok'
        except (OSError, ValueError, RuntimeError) as exc:
            row['outcome'] = f'failed: {exc}'
        finally:
            self.mark_step(row, 'finished')
            self.save()

    def reconcile_inputs(self, prs: list[dict], meta: dict[int, dict]) -> list[dict]:
        inputs = []
        lines = ['# Stack reconciliation input', '\n## Stack']
        for pr in prs:
            lines.append(f"#{pr['number']} {pr['title']}\nhead_oid: {meta[pr['number']]['headRefOid']}\n"
                         f"base: {meta[pr['number']]['baseRefName']}\nfiles: {json.dumps(pr['files'])}\n")
        for pr in prs:
            ballot = self.ballot(pr['number'])
            path = ballot / 'findings.json'
            if not path.exists():
                lines.append(f'## {ballot.name}: missing findings.json\n')
                continue
            judgment = read(ballot / 'judgment.md')
            match = re.search(r'^head_oid:\s*(\S+)', judgment, re.M)
            header = json.loads(read(ballot / 'header.json') or '{}')
            oid = match.group(1) if match else header.get('head_oid', 'unknown')
            decide = read(ballot / 'decide.md')
            blocks = {m.group(1): decide[m.start(): (matches[i + 1].start() if i + 1 < len(matches) else len(decide))]
                      for matches in [list(BLOCK.finditer(decide))] for i, m in enumerate(matches)}
            for finding in json.loads(read(path))['findings']:
                item = dict(finding, ballot=ballot.name, pr=pr['number'], head_oid=oid)
                recommend = re.search(r'^\*\*recommend\*\*:.*', blocks.get(finding['id'], ''), re.M)
                item['recommend'] = recommend.group(0) if recommend else 'missing'
                inputs.append(item)
                lines.append(f"## {ballot.name} {finding['id']}\n\n```json\n{json.dumps(item, indent=2)}\n```\n")
        (self.dest / 'reconcile-input.md').write_text('\n'.join(lines), encoding='utf-8')
        return inputs

    def reconcile(self, prs: list[dict]) -> None:
        repo = str(self.repo)
        meta = {p['number']: json.loads(self.command(['gh', 'pr', 'view', str(p['number']), '--json', 'headRefOid,baseRefName'], self.repo)) for p in prs}
        top = prs[-1]['number']
        oid = meta[top]['headRefOid']
        # FETCH_HEAD is one slot for the whole repo. A parallel ballot fetch
        # overwrites it. The oid came from gh. Check that commit is present.
        self.command(['git', '-C', repo, 'fetch', 'origin', f'pull/{top}/head'])
        if not commit_exists(repo, oid):
            raise RuntimeError(f'top commit {oid} is not in the repo after fetch')
        inputs = self.reconcile_inputs(prs, meta)
        scratch = Path(tempfile.mkdtemp(prefix='council-stack-'))
        wt = scratch / 'top'
        try:
            if scratch.is_relative_to(self.repo) or scratch.is_relative_to(ROOT):
                raise RuntimeError('temporary worktree must be outside both repositories; set TMPDIR')
            self.command(['git', '-C', repo, 'worktree', 'add', '--detach', str(wt), oid])
            for name in ('config/database.yml', '.env'):
                source = self.repo / name
                ignored = self.process(['git', '-C', repo, 'check-ignore', '-q', '--', name]).returncode == 0
                if source.is_file() and ignored:
                    target = wt / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_symlink():
                        target.unlink()
                    shutil.copyfile(source, target)
            output = self.dest / 'reconcile.json'
            output.unlink(missing_ok=True)
            prompt = (f'Reconcile every finding in {self.dest / "reconcile-input.md"} against the stack top #{top} at {oid} in this worktree. '
                      'Run the repro where one exists (Rails specs or runner scripts are fine); edit nothing tracked. '
                      'Find which later PR settled it using git diff between PR head oids; fetch missing PR heads read-only if needed. '
                      f'Write JSON only to {output}; the runner renders markdown. Schema: '
                      '{"top_pr": <number>, "top_oid": "<sha>", "findings": [{"ballot": "<ballot>", "id": "f1", '
                      '"verdict": "present | fixed | changed | unknown", "pr": <number or null>, "evidence": "what was run or read", '
                      '"repro_run": true, "recommend": "post as a bug | post as a question | post as advisory | hold", "why": "one line"}]}. '
                      'Every input finding exactly once. pr must name a later PR in this stack for fixed/changed, null otherwise. '
                      'unknown needs the reason in evidence. Write no other artifact. ' + NO_SHALLOW)
            self.chair('reconcile-chair', prompt, wt, self.record, 'reconcile_chair')
            try:
                raw = json.loads(read(output))
            except ValueError as exc:
                raise RuntimeError('reconcile chair wrote no valid reconcile.json') from exc
            if not isinstance(raw, dict):
                raise RuntimeError('reconcile chair wrote no valid reconcile.json')
            doc, warnings = validate_reconcile(raw, inputs, prs, oid)
            write_json(output, doc)
            (self.dest / 'reconcile.md').write_text(render_reconcile(doc), encoding='utf-8')
            for pr in prs:
                ballot = self.ballot(pr['number'])
                rows = {r['id']: r for r in doc['findings'] if r['ballot'] == ballot.name}
                if not (ballot / 'decide.md').exists():
                    warnings.append(f'{ballot.name}: missing decide.md; fold skipped')
                    continue
                # Preserve line endings and all unrelated bytes.
                path = ballot / 'decide.md'
                text = path.read_bytes().decode('utf-8')
                folded: set[str] = set()
                path.write_bytes(fold_decide(text, rows, top, oid, folded).encode('utf-8'))
                warnings.extend(f'{ballot.name} {finding_id}: no later-PRs line; stack-top line not folded'
                                for finding_id in rows if finding_id not in folded)
            self.record['reconcile_warnings'] = warnings
            for warning in warnings:
                print(warning, file=sys.stderr)
        finally:
            try:
                if wt.exists():
                    self.command(['git', '-C', repo, 'worktree', 'remove', '--force', str(wt)], cleanup=True)
            finally:
                self.command(['git', '-C', repo, 'worktree', 'prune'], cleanup=True)
                shutil.rmtree(scratch)

    def run(self) -> int:
        prs = self.stack()
        self.sticky = any(read(self.ballot(pr['number']) / 'voter-claude.via').strip() == 'cursor' for pr in prs)
        previous_hits: dict[int, list[str]] = {}
        for row in self.previous.get('prs', []):
            previous_hits.setdefault(row['pr'], []).extend(row.get('POSTED?', []))
        rows = []
        for pr in prs:
            rows.append({'pr': pr['number'], 'ballot': self.ballot(pr['number']).name,
                'state': state(self.ballot(pr['number'])), 'outcome': 'pending', 'step': 'pending',
                'chair_a_seconds': 0, 'chair_b_seconds': 0, 'phase1_seconds': 0,
                'claude_flags': [], 'fable_weekly_percent': None,
                'POSTED?': list(dict.fromkeys(previous_hits.get(pr['number'], [])))})
        self.record['prs'] = rows
        if self.args.dry_run:
            flags, _ = self.flags(refresh=False)
            lines = []
            for row in self.record['prs']:
                steps = [] if self.args.reconcile_only else self.preview_steps(row['ballot'], row['state'], row['pr'], flags)
                lines.append(f"{row['ballot']}: {row['state']}; steps: {'; '.join(steps) or 'none'}; claude flags: {json.dumps(flags)}")
            if not self.args.no_reconcile:
                lines.append('After ballots: reconcile stack top')
            if not self.args.no_whole_stack and not self.args.no_reconcile and not self.args.reconcile_only:
                base, head = self.stack_refs(prs)
                name = self.whole_ballot_name()
                target = ['--base', base, '--head', head]
                status = state(ROOT / 'tmp/council' / name)
                steps = self.preview_steps(name, status, prs[-1]['number'], flags, target)
                lines.append(f"{name}: {status}; steps: {'; '.join(steps) or 'none'}; "
                             f"base: {base}; head: {head}; claude flags: {json.dumps(flags)}")
            text = '\n'.join(lines) + '\n'
            (self.dest / 'dry-run.md').write_text(text, encoding='utf-8')
            print(text, end='')
            return 0
        if not self.args.reconcile_only and not self.args.context and not self.context.exists() and not self.args.ticket:
            raise RuntimeError('shared context missing: provide --context FILE or --ticket KEY before any ballot')
        if not self.model:
            # The resolver emits shell-quoted assignments, not executable prompts.
            resolved = self.command(['bash', '-c', 'eval "$("$1")"; printf "%s" "$CURSOR_BUILDER"',
                                'stack-run', str(ROOT / 'scripts/resolve-models.sh')])
            self.model = resolved.strip()
        self.check_model()
        self.logs.mkdir(exist_ok=True)
        self.save()
        if not self.args.reconcile_only:
            if self.args.context:
                source = Path(self.args.context).expanduser().resolve()
                if source != self.context:
                    shutil.copyfile(source, self.context)
            elif not self.context.exists():
                self.record['context'] = 'running'
                self.save()
                self.chair('context-chair', f'Read Linear ticket {self.args.ticket}, its description and EVERY comment, '
                    f'and the Notion pages it links, read-only. Write only {self.context} with what the stack is for, '
                    'domain rules reviewers need, and decisions recorded in ticket comments. '
                    f'Do not use these words anywhere in it, {WORDS}.', self.repo, self.record, 'context_chair')
            if not self.context.is_file() or not read(self.context).strip() or FORBIDDEN.search(read(self.context)):
                raise RuntimeError('context.md missing, empty, or contains a forbidden word')
            self.record['context'] = 'ready'
            self.save()
            # Submit in stack order, at most two at once. A completed wave's
            # overflow is sticky before the next wave starts.
            with ThreadPoolExecutor(max_workers=self.args.jobs) as pool:
                rows = self.record['prs']
                for i in range(0, len(rows), self.args.jobs):
                    futures = [pool.submit(self.ballot_run, row) for row in rows[i:i + self.args.jobs]]
                    for future in futures:
                        future.result()
        if self.max_capped:
            self.record['reconcile'] = f'failed: {MAX_CAPPED}'
        elif self.args.no_reconcile:
            self.record['reconcile'] = 'skipped'
        else:
            self.record['reconcile'] = 'running'
            self.save()
            try:
                self.reconcile(prs)
                self.record['reconcile'] = 'ok'
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                self.record['reconcile'] = f'failed: {exc}'
        self.finish_whole_stack(prs)
        self.save()
        whole = str(self.record.get('whole_stack', ''))
        return int(any(r['outcome'].startswith('failed:') for r in self.record['prs'])
                   or self.record['reconcile'].startswith('failed:') or whole.startswith('failed:'))

    def finish_whole_stack(self, prs: list[dict]) -> None:
        """One combined-diff ballot after a successful reconcile. Same launcher as a PR."""
        if self.args.no_whole_stack or self.args.no_reconcile or self.args.reconcile_only:
            self.record['whole_stack'] = 'skipped'
            return
        if self.record.get('reconcile') != 'ok':
            self.record['whole_stack'] = 'not started'
            return
        base, head = self.stack_refs(prs)
        self.fetch_top_head(prs, head)
        name = self.whole_ballot_name()
        ballot = ROOT / 'tmp/council' / name
        if ballot == self.dest:
            raise RuntimeError('--stack-id must differ from the whole-stack ballot id')
        previous = self.previous.get('whole_stack_row') or {}
        row = {'pr': prs[-1]['number'], 'ballot': name, 'state': state(ballot), 'outcome': 'pending',
               'step': 'pending', 'chair_a_seconds': 0, 'chair_b_seconds': 0, 'phase1_seconds': 0,
               'claude_flags': [], 'fable_weekly_percent': None,
               'POSTED?': list(dict.fromkeys(previous.get('POSTED?', []))),
               'target': ['--base', base, '--head', head]}
        self.record['whole_stack'] = 'running'
        self.record['whole_stack_row'] = row
        self.save()
        if self.model:
            publish('CHAIR_MODEL', self.model)
        self.ballot_run(row)
        self.record['whole_stack'] = row['outcome']


def detach(args: argparse.Namespace) -> int:
    """Re-exec under setsid and nohup. Return once the first ballot step starts."""
    if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9._-]*', args.stack_id):
        print(f'stack-run: unsafe stack id or prefix: {args.stack_id!r}', file=sys.stderr)
        return 1
    dest = ROOT / 'tmp/council' / args.stack_id
    dest.mkdir(parents=True, exist_ok=True)
    log_path = dest / 'detach.log'
    token = secrets.token_hex(16)
    (dest / 'detach.token').write_text(token + '\n', encoding='utf-8')
    (dest / 'step-started').unlink(missing_ok=True)
    # A short form such as --deta must not be left for the child to detach again.
    child_args = [arg for arg in sys.argv[1:]
                  if not (arg.startswith('--') and '--detach'.startswith(arg) and len(arg) > 2)]
    script = str(Path(sys.argv[0]).resolve())
    log = log_path.open('ab')
    try:
        proc = subprocess.Popen(
            ['setsid', 'nohup', sys.executable, script, *child_args],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            env=dict(os.environ, COUNCIL_STACK_DETACHED='1', CONCLAVE_STACK_DETACHED='1'), cwd=os.getcwd())
    except OSError as exc:
        print(f'stack-run: {exc}', file=sys.stderr)
        return 1
    finally:
        log.close()
    write_json(dest / 'detach.json', {'pid': proc.pid, 'log': str(log_path)})
    marker = dest / 'step-started'
    while True:
        if marker.is_file():
            lines = marker.read_text(encoding='utf-8', errors='replace').splitlines()
            if lines and lines[0] == token:
                step = lines[1] if len(lines) > 1 else 'started'
                print(f'pid: {proc.pid}')
                print(f'log: {log_path}')
                print(f'started: {step}')
                return 0
        rc = proc.poll()
        if rc is not None:
            if rc == 0:
                print(f'pid: {proc.pid}')
                print(f'log: {log_path}')
                return 0
            print(f'stack-run: detached child exited {rc} before the first ballot step', file=sys.stderr)
            tail = log_tail(log_path)
            if tail:
                print(tail, file=sys.stderr)
            return rc
        time.sleep(0.05)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--stack-id', required=True)
    parser.add_argument('--prefix')
    parser.add_argument('--prs')
    parser.add_argument('--context')
    parser.add_argument('--ticket')
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=1)
    parser.add_argument('--chair-model')
    parser.add_argument('--chair-timeout', type=int, default=3600)
    parser.add_argument('--dry-run', action='store_true')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--reconcile-only', action='store_true')
    group.add_argument('--no-reconcile', action='store_true')
    parser.add_argument('--no-whole-stack', action='store_true',
                        help='Skip the whole-stack ballot after reconcile')
    parser.add_argument('--detach', action='store_true',
                        help='Re-exec under setsid and nohup, then return after the first ballot step starts')
    args = parser.parse_args()
    if args.pr <= 0 or args.chair_timeout <= 0:
        parser.error('--pr and --chair-timeout must be positive')
    if args.detach:
        return detach(args)
    runner = None
    handlers = {}
    try:
        runner = Runner(args)
        # nohup already ignores hangup. Keep that in the detached child.
        caught = (signal.SIGINT, signal.SIGTERM)
        if setting('STACK_DETACHED') == '1':
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
        else:
            caught = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        for sig in caught:
            handlers[sig] = signal.signal(sig, runner.handle_signal)
        return runner.run()
    except (OSError, ValueError, RuntimeError) as exc:
        if runner and not args.dry_run and runner.logs.exists():
            runner.record['error'] = str(exc)
            runner.save()
        print(f'stack-run: {exc}', file=sys.stderr)
        return 1
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(main())
