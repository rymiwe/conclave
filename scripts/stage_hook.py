#!/usr/bin/env python3
# Usage: scripts/stage_hook.py emit EVENT [--dir DIR] [--json PAYLOAD]
#        scripts/stage_hook.py progress run {started,completed,failed} --dir DIR
"""Run the operator's stage hook on a stage event. Never breaks the run.

CONCLAVE_STAGE_HOOK (COUNCIL_STAGE_HOOK is the alias) is a command line,
split with shlex, never run through a shell. Unset or empty: no hook runs.
The hook gets the event name and one JSON payload as its last two arguments,
and the same two values in CONCLAVE_STAGE_EVENT and CONCLAVE_STAGE_PAYLOAD.
stdin is /dev/null. Its output is discarded. It has
CONCLAVE_STAGE_HOOK_TIMEOUT seconds (default 30).

A hook that is missing, not executable, exits non-zero, or times out logs one
line to stderr and to stage-hook.log in the run dir. The hook command itself
is not logged, but it must never hold a secret: chairs inherit it and a
chair's model can read its environment. A hook that needs a credential
fetches it itself at run time. Payloads carry paths and counts only.

Every known event with a dir also appends one line to progress.md in that dir,
hook or not: "<local time with offset> <stage> <status>". The run stage
(started, completed, failed) comes from conclave.sh through the progress
subcommand. _progress is the only writer. See docs/preflight.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from conclave_env import setting  # noqa: E402

# repros_done is reserved for a later repro stage. Nothing emits it yet.
EVENTS = ('preflight_failed', 'holistic_done', 'ballot_started', 'ballot_done',
          'repros_done', 'post_plan_ready', 'stalled')
DEFAULT_TIMEOUT = 30.0
LOG_NAME = 'stage-hook.log'
PROGRESS_NAME = 'progress.md'
RUN_STATUSES = ('started', 'completed', 'failed')


def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def progress_now() -> str:
    """Local time, to the second, with a numeric offset. Payload ts stays now()."""
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _status(payload: dict | None) -> str:
    """The payload's outcome class with whitespace as '-', else 'reached'."""
    outcome = (payload or {}).get('outcome')
    if outcome in (None, ''):
        return 'reached'
    text = re.sub(r'\s+', '-', str(outcome).strip())
    return text or 'reached'


def _progress(dir: str | Path, stage: str, status: str) -> None:
    """Append one progress line. The only writer. Never raises and never mkdirs.

    One O_APPEND write keeps a line intact when stack-run threads append together.
    """
    try:
        data = f'{progress_now()} {stage} {status}\n'.encode('utf-8')
        fd = os.open(Path(dir) / PROGRESS_NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception:
        try:
            print(f'progress: could not append to {dir}/{PROGRESS_NAME}', file=sys.stderr)
        except Exception:
            pass


def hook_timeout() -> float:
    raw = setting('STAGE_HOOK_TIMEOUT', '')
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT
    return value if 0 < value < 86400 else DEFAULT_TIMEOUT


def _log(event: str, what: str, dir: str | Path | None) -> None:
    line = f'stage-hook: {event} {what}'
    print(line, file=sys.stderr)
    if dir is None:
        return
    try:
        with (Path(dir) / LOG_NAME).open('a', encoding='utf-8') as out:
            out.write(f'{now()} {line}\n')
    except OSError:
        pass


def _run(argv: list[str], env: dict[str, str], timeout: float) -> str:
    """Run the hook. Return '' on success, or a short failure class."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, start_new_session=True)
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


def emit(event: str, payload: dict | None = None, dir: str | Path | None = None) -> bool:
    """Run the hook for one event. True when it ran and exited 0. Never raises."""
    try:
        if dir is not None and event in EVENTS:
            _progress(dir, event, _status(payload))
        command = setting('STAGE_HOOK', '')
        if not command or not command.strip():
            return False
        if event not in EVENTS:
            _log(event, 'ignored: unknown event', dir)
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = []
        if not argv:
            _log(event, 'failed: bad hook command', dir)
            return False
        body = {k: v for k, v in (payload or {}).items() if k not in ('event', 'ts')}
        doc = {'event': event, 'ts': now(), **body}
        if dir is not None:
            doc.setdefault('dir', str(dir))
        text = json.dumps(doc, sort_keys=True)
        env = dict(os.environ, CONCLAVE_STAGE_EVENT=event, CONCLAVE_STAGE_PAYLOAD=text)
        failure = _run([*argv, event, text], env, hook_timeout())
        if failure:
            _log(event, f'failed: {failure}', dir)
            return False
        return True
    except Exception as exc:  # A hook must never break the run.
        _log(event, f'failed: {type(exc).__name__}', dir)
        return False


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    one = sub.add_parser('emit')
    one.add_argument('event')
    one.add_argument('--dir')
    one.add_argument('--json', default='{}')
    prog = sub.add_parser('progress')
    prog.add_argument('stage', choices=('run',))
    prog.add_argument('status', choices=RUN_STATUSES)
    prog.add_argument('--dir', required=True)
    args = parser.parse_args(argv)
    if args.cmd == 'progress':
        _progress(args.dir, args.stage, args.status)
        return 0
    try:
        payload = json.loads(args.json)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        _log(args.event, 'failed: payload is not a JSON object', args.dir)
        return 0
    emit(args.event, payload, args.dir)
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
