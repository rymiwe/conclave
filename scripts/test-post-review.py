#!/usr/bin/env python3
"""Offline checks for post-review.py and evidence-index.py.

gh is a PATH stub that logs every call and never reaches the network. The
fixture ballots under scripts/fixtures/post-review are synthetic.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / 'scripts'
FIXTURES = SCRIPTS / 'fixtures' / 'post-review'
HEAD = '1' * 40


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f'FAIL {label}')
    print(f'ok {label}')


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / f'{name}.py')
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GH = '''\
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
state = Path(os.environ['GH_STATE'])
args = sys.argv[1:]
with (state / 'calls.jsonl').open('a') as log:
    log.write(json.dumps(args) + '\\n')
repo = 'repos/example/widgets/pulls/7'
path = next((a for a in args if a.startswith('repos/') or a == 'user'), '')
base = path.split('?')[0]
from urllib.parse import parse_qs, urlsplit
page = int(parse_qs(urlsplit(path).query).get('page', ['1'])[0])
if '-X' in args:
    if base.endswith('/replies'):
        fail = os.environ.get('GH_REPLY_FAIL', '')
        if fail == '1' or (fail and f'/comments/{fail}/replies' in base):
            sys.stderr.write('HTTP 502 Bad Gateway\\n')
            sys.exit(1)
        req = json.loads(sys.stdin.read())
        (state / 'posted-reply.json').write_text(json.dumps({'url': base, 'body': req}))
        print(json.dumps({'id': 77, 'html_url': 'https://github.com/example/widgets/pull/7#discussion_r77'}))
    elif base.endswith('/comments') and '/reviews/' not in base:
        req = json.loads(sys.stdin.read())
        nth = os.environ.get('GH_FILE_FAIL_NTH')
        sent = (state / 'file-comments.jsonl')
        if nth and (len(sent.read_text().splitlines()) if sent.exists() else 0) == int(nth) - 1:
            sys.stderr.write('HTTP 502 Bad Gateway\\n')
            sys.exit(1)
        with (state / 'file-comments.jsonl').open('a') as out:
            out.write(json.dumps(req) + '\\n')
        print(json.dumps({'id': 8100, 'html_url': 'https://github.com/example/widgets/pull/7#discussion_r8100'}))
    elif base != repo + '/reviews':
        sys.exit('unexpected POST ' + base)
    else:
        req = json.loads(sys.stdin.read())
        if not (req.get('body') or '').strip() and not req.get('comments'):
            sys.stderr.write('HTTP 422 Unprocessable Entity: review has no body and no comments\\n')
            sys.exit(1)
        (state / 'posted-request.json').write_text(json.dumps(req))
        print(json.dumps({'id': 900, 'state': 'COMMENTED',
                          'html_url': 'https://github.com/example/widgets/pull/7#pullrequestreview-900'}))
elif base == repo:
    pr = {'head': {'sha': os.environ.get('GH_HEAD', '1' * 40)}}
    if os.environ.get('GH_PR_AUTHOR'):
        pr['user'] = {'login': os.environ['GH_PR_AUTHOR']}
    print(json.dumps(pr))
elif base == repo + '/files':
    print(json.dumps(json.loads(Path(os.environ['GH_FILES']).read_text()) if page == 1 else []))
elif '/contents/' in base:
    import base64
    from urllib.parse import unquote
    rel = unquote(base.split('/contents/', 1)[1])
    blobs = json.loads(Path(os.environ['GH_BLOBS']).read_text()) if os.environ.get('GH_BLOBS') else {}
    if rel not in blobs:
        sys.exit('no blob ' + rel)
    print(json.dumps({'type': 'file', 'encoding': 'base64',
                      'content': base64.b64encode(blobs[rel].encode()).decode()}))
elif base == repo + '/reviews/900/comments':
    if os.environ.get('GH_COMMENTS_FAIL'):
        sys.stderr.write('HTTP 502 Bad Gateway\\n')
        sys.exit(1)
    req = json.loads((state / 'posted-request.json').read_text())
    rows = [] if page > 1 else [
        {'id': 5000 + i, 'path': c['path'], 'line': c['line'], 'body': c['body'],
         'html_url': f'https://github.com/example/widgets/pull/7#discussion_r{5000 + i}'}
        for i, c in enumerate(req['comments'])]
    print(json.dumps(rows))
elif base == repo + '/reviews/900':
    if os.environ.get('GH_REVIEW_GONE'):
        sys.stderr.write('HTTP 404 Not Found\\n')
        sys.exit(1)
    req = json.loads((state / 'posted-request.json').read_text())
    print(json.dumps({'id': 900, 'state': 'COMMENTED', 'commit_id': req['commit_id'],
                      'body': req['body'],
                      'html_url': 'https://github.com/example/widgets/pull/7#pullrequestreview-900'}))
elif base == repo + '/reviews':
    rows = json.loads(Path(os.environ['GH_REVIEWS']).read_text()) if os.environ.get('GH_REVIEWS') else []
    if (state / 'posted-request.json').exists():
        req = json.loads((state / 'posted-request.json').read_text())
        rows.append({'id': 900, 'state': 'COMMENTED', 'commit_id': req['commit_id'],
                     'body': req['body'], 'user': {'login': 'me'},
                     'html_url': 'https://github.com/example/widgets/pull/7#pullrequestreview-900'})
    print(json.dumps([] if page > 1 else rows))
elif path == 'user':
    print(json.dumps({'login': 'me'}))
else:
    sys.exit('unexpected gh call ' + ' '.join(args))
'''


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.council = root / 'council'
        shutil.copytree(FIXTURES, self.council)
        self.state = root / 'state'
        self.state.mkdir()
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(textwrap.dedent(GH), encoding='utf-8')
        gh.chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                    'GH_STATE': str(self.state), 'GH_FILES': str(self.council / 'files-7.json')}
        self.env.pop('COUNCIL_GH', None)

    def post(self, *extra: str, env: dict | None = None, bare: bool = False) -> subprocess.CompletedProcess:
        """A real post, unless --dry-run is given. bare: no --post, the way the script runs by default."""
        flags = list(extra) if bare or '--dry-run' in extra else ['--post', *extra]
        return subprocess.run(['python3', str(SCRIPTS / 'post-review.py'), 'demo-1', '7',
                               '--council-dir', str(self.council), *flags],
                              env={**self.env, **(env or {})}, text=True, capture_output=True)

    def index(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(['python3', str(SCRIPTS / 'evidence-index.py'), 'demo-1',
                               '--council-dir', str(self.council), *extra],
                              env=self.env, text=True, capture_output=True)

    def calls(self) -> list[list[str]]:
        p = self.state / 'calls.jsonl'
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def posts(self) -> int:
        return sum(1 for c in self.calls() if '-X' in c)

    def payload(self) -> dict:
        return json.loads((self.council / 'demo-1/post-7.json').read_text())

    def set_payload(self, data: dict) -> None:
        (self.council / 'demo-1/post-7.json').write_text(json.dumps(data))


def test_right_lines() -> None:
    mod = load('post-review')
    patch = json.loads((FIXTURES / 'files-7.json').read_text())[0]['patch']
    lines = mod.right_lines(patch)
    check(sorted(lines) == [1, 2, 3, 4, 5, 12, 13, 14], 'right_lines counts context and added lines per hunk')
    check(lines[3] == '    items.count' and lines[13] == '    @cache = nil', 'right_lines keeps the line text')
    check(mod.right_lines('') == {}, 'a file with no patch has no commentable line')


def test_expect_outside_diff(root: Path) -> None:
    """f2: expect is checked on a line outside the diff, using the file at the head.

    The fixture moved f2 to app/label.rb, a file that is not in this PR's diff,
    so the comment still moves into the review body. A line outside the diff
    in a file that is in the diff is a file-level comment now.
    """
    f = Fixture(root / 'outside-expect')
    blob = '\n'.join(f'line {i}' for i in range(1, 40)) + '\nlabel = I18n.locale\n'
    blobs = root / 'blobs.json'
    blobs.write_text(json.dumps({'app/label.rb': blob}))
    env = {'GH_BLOBS': str(blobs)}
    payload = f.payload()
    payload['comments'] = [c for c in payload['comments'] if c['path'] == 'app/label.rb' and c['line'] == 40]
    payload['comments'][0]['expect'] = 'not-on-this-line'
    payload['body_findings'] = []
    f.set_payload(payload)
    missed = f.post('--dry-run', env=env)
    check(missed.returncode != 0 and "expected 'not-on-this-line'" in missed.stderr and f.posts() == 0,
          f'f4 absent expect outside the diff is refused ({missed.returncode} {missed.stderr.strip()} {missed.stdout.strip()})')
    check('nothing posted' not in missed.stdout and '#L40' not in missed.stdout,
          'f4 a failed expect does not publish the permalink')
    payload['comments'][0]['expect'] = 'I18n.locale'
    f.set_payload(payload)
    matched = f.post('--dry-run', env=env)
    check(matched.returncode == 0 and f'blob/{HEAD}/app/label.rb#L40' in
          (f.council / 'demo-1/post-7.request.json').read_text(),
          f'f2 a matching expect outside the diff still moves the comment ({matched.returncode} {matched.stderr.strip()})')


def test_form_feed_hunk(root: Path) -> None:
    """f5: a form feed inside a source line is not a hunk break."""
    mod = load('post-review')
    patch = "@@ -0,0 +1,3 @@\n+a = 1\n+\x0cb = 2\n+c = 3\n"
    lines = mod.right_lines(patch)
    check(lines == {1: 'a = 1', 2: '\x0cb = 2', 3: 'c = 3'},
          f'f5 form feed stays inside the line ({lines})')
    f = Fixture(root / 'form-feed')
    files = root / 'form-feed-files.json'
    files.write_text(json.dumps([{'filename': 'app/widget.rb', 'patch': patch}]))
    payload = f.payload()
    payload['comments'] = [{'finding': 'f1', 'path': 'app/widget.rb', 'line': 3,
                            'expect': 'b = 2', 'body': '**Requested:** this is the next line.'}]
    payload['body_findings'] = []
    f.set_payload(payload)
    proc = f.post('--dry-run', env={'GH_FILES': str(files)})
    check(proc.returncode != 0 and "expected 'b = 2'" in proc.stderr,
          f'f5 expect for the shifted line is refused ({proc.returncode} {proc.stderr.strip()} {proc.stdout.strip()})')


def test_dry_run(f: Fixture) -> None:
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'dry run exits zero ({proc.stderr.strip()})')
    check(f.posts() == 0, 'dry run never POSTs')
    check(not (f.council / 'demo-1/posted.json').exists(), 'dry run writes no posted.json')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    # f1 is Requested, and the payload no longer carries an event. The event
    # follows that label. APPROVE is not selected.
    check(req['commit_id'] == HEAD and req['event'] == 'REQUEST_CHANGES',
          'request carries commit_id and REQUEST_CHANGES')
    check([(c['path'], c['line'], c['side']) for c in req['comments']] ==
          [('app/widget.rb', 3, 'RIGHT'), ('app/widget.rb', 13, 'RIGHT')], 'only in-diff lines stay inline, side RIGHT')
    check(all('finding' not in c and 'expect' not in c for c in req['comments']), 'request drops payload-only keys')
    body = req['body']
    check('**Outside this diff**' in body, 'outside-diff comments move into the body')
    # f2's file is not in the diff, so it still moves. See test_expect_outside_diff.
    check(f'https://github.com/example/widgets/blob/{HEAD}/app/label.rb#L40' in body
          and f'https://github.com/example/widgets/blob/{HEAD}/lib/other.rb#L1' in body,
          'moved comments carry a permalink at the head')
    check('moved: app/label.rb:40 (f2)' in proc.stdout and 'nothing posted' in proc.stdout, 'dry run prints the plan')


def test_post(f: Fixture) -> None:
    proc = f.post()
    check(proc.returncode == 0, f'post exits zero ({proc.stderr.strip()})')
    check(f.posts() == 1, 'post makes exactly one POST')
    post_call = next(c for c in f.calls() if '-X' in c)
    check(post_call[:4] == ['api', '-X', 'POST', 'repos/example/widgets/pulls/7/reviews'], 'POST goes to the reviews endpoint')
    posted = json.loads((f.council / 'demo-1/posted.json').read_text())
    review = posted['reviews']['7']
    check(posted['repo'] == 'example/widgets' and review['review_id'] == 900, 'posted.json records repo and review id')
    check(review['review_url'].endswith('#pullrequestreview-900'), 'posted.json records review url')
    fs = review['findings']
    check(set(fs) == {'demo-1:f1', 'demo-1:f2', 'demo-1:f3', 'demo-1:f4', 'demo-2:f1'}, 'findings keyed by ballot and id')
    check(fs['demo-1:f1']['url'].endswith('#discussion_r5000') and fs['demo-1:f1']['posted_as'] == 'inline',
          'inline finding gets its comment url')
    check(fs['demo-2:f1']['url'].endswith('#discussion_r5001') and fs['demo-2:f1']['severity'] == 'Medium',
          'a finding from another ballot keeps its ballot and severity')
    check(fs['demo-1:f2']['posted_as'] == 'review body' and fs['demo-1:f2']['url'] == review['review_url']
          and fs['demo-1:f2']['reason'] == 'outside the diff', 'moved finding points at the review')
    check(fs['demo-1:f4']['posted_as'] == 'review body', 'body finding recorded')
    check(all('_body' not in v for v in fs.values()), 'posted.json carries no comment bodies')

    again = f.post()
    check(again.returncode != 0 and 'already in' in again.stderr and f.posts() == 1, 'a second post of the same PR is refused')


def refused(f: Fixture, label: str, needle: str, env: dict | None = None) -> None:
    before = f.posts()
    proc = f.post(env=env)
    check(proc.returncode != 0 and needle in proc.stderr + proc.stdout and f.posts() == before
          and not (f.council / 'demo-1/posted.json').exists(), label)


def test_readback_does_not_double_post(root: Path) -> None:
    """f1: a failed comment read-back must not leave a second run free to POST again."""
    f = Fixture(root / 'readback')
    env = {'GH_COMMENTS_FAIL': '1'}
    first = f.post(env=env)
    pending = f.council / 'demo-1/post-7.pending.json'
    check(first.returncode != 0 and '502' in first.stderr, f'f1 read-back failure exits nonzero ({first.stderr.strip()})')
    check(f.posts() == 1, f'f1 first run posts once ({f.posts()})')
    second = f.post(env=env)
    check(f.posts() == 1, f'f1 rerun does not post again ({f.posts()} posts)')
    check(pending.is_file(), 'f1 pending receipt exists before a failed read-back can finish')
    check(second.returncode != 0 and 'already exists on GitHub' in second.stderr,
          f'f1 rerun refuses because the review exists ({second.stderr.strip()})')
    pending.unlink()
    third = f.post(env=env)
    check(f.posts() == 1 and third.returncode != 0 and 'already exists on GitHub' in third.stderr,
          f'f1 a lost pending file still refuses the review GitHub already has ({third.stderr.strip()})')


def test_other_reviewer_same_body(root: Path) -> None:
    """Another reviewer's review with the same commit and body is not a duplicate."""
    f = Fixture(root / 'other-reviewer')
    payload = f.payload()
    payload['body'] = ''
    payload['comments'] = [c for c in payload['comments'] if c['path'] == 'app/widget.rb' and c['line'] == 3]
    payload['body_findings'] = []
    f.set_payload(payload)
    reviews = root / 'other-reviews.json'
    reviews.write_text(json.dumps([{'id': 41, 'state': 'COMMENTED', 'commit_id': HEAD, 'body': '',
                                    'user': {'login': 'someone-else'}}]))
    proc = f.post(env={'GH_REVIEWS': str(reviews)})
    check(proc.returncode == 0 and f.posts() == 1,
          f'another login\'s empty-body review on the same commit does not block ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'own-reviewer')
    f.set_payload(payload)
    reviews.write_text(json.dumps([{'id': 42, 'state': 'COMMENTED', 'commit_id': HEAD, 'body': '',
                                    'user': {'login': 'me'}}]))
    proc = f.post(env={'GH_REVIEWS': str(reviews)})
    check(proc.returncode != 0 and 'review 42 already exists on GitHub' in proc.stderr and f.posts() == 0,
          f'your own identical review on the same commit still blocks ({proc.returncode} {proc.stderr.strip()})')
    before = len(f.calls())
    dry = f.post('--dry-run', env={'GH_REVIEWS': str(reviews)})
    check(dry.returncode == 0 and ['api', 'user'] not in f.calls()[before:], 'dry run does not read the login')


def test_refusals(root: Path) -> None:
    f = Fixture(root / 'head')
    refused(f, 'stale head refused before any POST', 'PR head is', {'GH_HEAD': '2' * 40})

    f = Fixture(root / 'expect')
    p = f.payload()
    p['comments'][0]['expect'] = 'items.size'
    f.set_payload(p)
    refused(f, 'expect mismatch refused', "expected 'items.size'")

    f = Fixture(root / 'links')
    p = f.payload()
    p['body'] = 'See #6 and app/widget.rb:3.'
    f.set_payload(p)
    refused(f, 'unlinked citation refused', 'outside a markdown link')

    f = Fixture(root / 'left')
    p = f.payload()
    p['comments'][0]['side'] = 'LEFT'
    f.set_payload(p)
    refused(f, 'LEFT side refused', 'side must be RIGHT')

    f = Fixture(root / 'dup')
    p = f.payload()
    p['body_findings'] = ['f1']
    f.set_payload(p)
    refused(f, 'a finding both inline and in the body is refused', 'also an inline comment')

    f = Fixture(root / 'norepo')
    (f.council / 'demo-1/judgment.md').unlink()
    refused(f, 'no repo source refused', 'no repo')
    proc = f.post('--repo', 'example/widgets', '--dry-run')
    check(proc.returncode == 0, '--repo supplies the repo')


def test_index(f: Fixture) -> None:
    proc = f.index()
    check(proc.returncode == 0, f'index exits zero ({proc.stderr.strip()})')
    md = (f.council / 'demo-1/evidence-index.md').read_text()
    check('5 posted findings, 4 with an executed repro, 0 gap(s)' in md, 'index summary line')
    check('| 7 | demo-1 | f1 | High | inline | https://github.com/example/widgets/pull/7#discussion_r5000 |' in md,
          'row carries severity from findings.json and the comment link')
    check('| 7 | demo-2 | f1 | Medium | inline | https://github.com/example/widgets/pull/7#discussion_r5001 |' in md,
          'row carries severity from the payload')
    check('| 7 | demo-1 | f2 | - | review body | https://github.com/example/widgets/pull/7#pullrequestreview-900 |' in md,
          'moved finding links the review')
    check("$ ruby -Ilib -e 'w = Widget.new" in md, 'evidence starts with the command')
    check('````\n$ bin/check-label\n```\nlabel=Widget locale=fr\n```\n````' in md, 'evidence with a fence gets a longer fence')
    check('- transcripts: demo-stack/logs/demo-1-chair-a.jsonl, demo-stack/logs/demo-1-chair-b.jsonl' in md,
          'transcript paths come from the stack logs')
    check('- transcripts: none found' in md, 'a ballot with no transcripts says so')
    check('- repro: not run' in md and '- code: `lib/other.rb:1`' in md, 'repro state and code location')

    findings = json.loads((f.council / 'demo-2/findings.json').read_text())
    findings['findings'] = []
    (f.council / 'demo-2/findings.json').write_text(json.dumps(findings))
    out = f.root / 'index.md'
    proc = f.index('--out', str(out))
    check(proc.returncode == 1 and '- gap: f1 not found in demo-2/findings.json' in out.read_text(),
          'a missing finding is a named gap and exit 1')

    proc = subprocess.run(['python3', str(SCRIPTS / 'evidence-index.py'), 'demo-2', '--council-dir', str(f.council)],
                          text=True, capture_output=True)
    check(proc.returncode != 0 and 'no ' in proc.stderr and 'posted.json' in proc.stderr, 'no posted.json is an error')


def test_findings_shape(root: Path) -> None:
    """f11: a findings file that is not a JSON object is a named gap, not a crash."""
    f = Fixture(root / 'shape')
    home = f.council / 'demo-1'
    (home / 'posted.json').write_text(json.dumps({
        'repo': 'example/widgets',
        'reviews': {'7': {
            'review_url': 'https://github.com/example/widgets/pull/7#pullrequestreview-900',
            'findings': {'demo-1:f1': {
                'ballot': 'demo-1', 'id': 'f1', 'posted_as': 'inline',
                'url': 'https://github.com/example/widgets/pull/7#discussion_r1'}}}},
    }))
    (home / 'findings.json').write_text('["not", "an", "object"]\n')
    out = root / 'shape-index.md'
    proc = f.index('--out', str(out))
    text = out.read_text() if out.is_file() else ''
    check(proc.returncode != 0 and 'AttributeError' not in proc.stderr
          and 'not a JSON object' in proc.stderr + text and out.is_file(),
          f'f11 a list findings file is a named gap ({proc.returncode} {proc.stderr.strip()} index={out.is_file()})')


def test_repro_and_labels(root: Path) -> None:
    """Repro files are attached. A labeled reply and an unlabeled new inline comment are refused."""
    f = Fixture(root / 'repro')
    script = "```\n# council\nputs 'abc1234'\n# app/widget.rb:3\n```\n"
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f1.rb').write_text(script, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'repro comment passes the link check ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['comments'][0]['body']
    check('<summary>f1.rb</summary>' in body and '````ruby\n' in body and script in body,
          'in-diff comment carries the ruby repro block')
    check('Truncated to' not in body, 'a short script is not truncated')
    check(all('<summary>' not in c['body'] for c in req['comments'][1:]),
          'a finding with no repro file gets no block')
    check('council' not in proc.stderr and 'outside a markdown link' not in proc.stderr,
          'the fenced script does not trip the citation or word lint')

    f = Fixture(root / 'labeled-reply')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': '**Requested:** still broken.'})
    f.set_payload(payload)
    refused(f, 'a labeled reply is refused before POST', 'labeled reply')

    f = Fixture(root / 'summary-label')
    payload = f.payload()
    payload['body'] = '\n\n**Requested:** four notes on the widget change.\n'
    f.set_payload(payload)
    refused(f, 'a summary that opens with a label is refused before POST', 'summary body opens with a label')

    f = Fixture(root / 'summary-bullets')
    payload = f.payload()
    payload['body'] = (
        'Four notes on the widget change. The earlier fix is '
        '[#6](https://github.com/example/widgets/pull/6).\n\n'
        '- **Advisory:** the helper drops a row.\n'
    )
    f.set_payload(payload)
    refused(f, 'a labeled summary bullet is refused before POST', 'labeled bullet')

    f = Fixture(root / 'summary-plain-bullets')
    payload = f.payload()
    payload['body'] = (
        'Four notes on the widget change. The earlier fix is '
        '[#6](https://github.com/example/widgets/pull/6).\n\n'
        '- the helper drops a row.\n'
    )
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and f.posts() == 0,
          f'an unlabeled summary bullet and a moved comment label are accepted ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'unlabeled-inline')
    payload = f.payload()
    payload['pr_author'] = 'author'
    payload['reviewer'] = 'me'
    payload['comments'][0]['body'] = 'size counts deleted items.'
    f.set_payload(payload)
    refused(f, 'an unlabeled new inline comment on someone else\'s PR is refused', 'unlabeled new inline comment')

    f = Fixture(root / 'buried-label')
    payload = f.payload()
    payload['pr_author'] = 'author'
    payload['comments'][0]['body'] = 'size counts deleted items. as **Requested:** above'
    f.set_payload(payload)
    proc = f.post()
    check(proc.returncode != 0 and 'unlabeled new inline comment' in proc.stderr
          and 'cannot tell' not in proc.stderr and f.posts() == 0,
          'a new inline comment whose label is not at the start is refused '
          f'({proc.returncode} {proc.stderr.strip()} posts={f.posts()})')

    f = Fixture(root / 'labeled-inline')
    payload = f.payload()
    payload['pr_author'] = 'author'
    payload['reviewer'] = 'me'
    payload['comments'][0]['body'] = '**Informational:** size counts deleted items.'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and f.posts() == 0, f'a labeled new inline comment is accepted ({proc.stderr.strip()})')

    f = Fixture(root / 'plain-reply')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': 'The next build picks this up.'})
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'an unlabeled reply is accepted ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(req['replies'] == [{'in_reply_to': 42, 'body': 'The next build picks this up.'}],
          'a reply is recorded separately')
    check(all('in_reply_to' not in c for c in req['comments']), 'a reply is not a new line comment')
    check(f.posts() == 0, 'dry run does not post the reply')

    f = Fixture(root / 'post-reply')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': 'The next build picks this up.'})
    f.set_payload(payload)
    proc = f.post()
    check(proc.returncode == 0, f'posting a reply exits zero ({proc.stderr.strip()})')
    check(f.posts() == 2, f'review and reply are two POSTs ({f.posts()})')
    reply = json.loads((f.state / 'posted-reply.json').read_text())
    check(reply['url'].endswith('/comments/42/replies') and reply['body']['body'] == 'The next build picks this up.',
          'the reply goes to the reply endpoint')
    review = json.loads((f.state / 'posted-request.json').read_text())
    check(all(c.get('body') != 'The next build picks this up.' for c in review['comments']),
          'the review POST does not include the reply')

    f = Fixture(root / 'two-ballots')
    payload = f.payload()
    payload['body_findings'] = ['f4', 'demo-2:f4']
    f.set_payload(payload)
    (f.council / 'demo-1' / 'repro').mkdir()
    (f.council / 'demo-2' / 'repro').mkdir()
    (f.council / 'demo-1' / 'repro' / 'f4.rb').write_text("from demo-1\n", encoding='utf-8')
    (f.council / 'demo-2' / 'repro' / 'f4.rb').write_text("from demo-2\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'two ballots with the same filename attach ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(req['body'].count('<summary>f4.rb</summary>') == 2, 'each ballot keeps its own summary')
    check('from demo-1' in req['body'] and 'from demo-2' in req['body'], 'both scripts are in the review body')

    f = Fixture(root / 'moved-huge')
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text('y' * 70000, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a moved huge repro still dry-runs ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(len(req['body']) <= 65536, f'moved repro stays within the review body ({len(req["body"])})')
    check('Truncated to' in req['body'] and '<summary>f2.rb</summary>' in req['body'],
          'the moved script is truncated inside the review body')

    f = Fixture(root / 'reply-retry')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': 'The next build picks this up.'})
    f.set_payload(payload)
    first = f.post(env={'GH_REPLY_FAIL': '1'})
    pending = f.council / 'demo-1/post-7.pending.json'
    first_posts = [c for c in f.calls() if '-X' in c]
    review_posts = [c for c in first_posts if any(str(a).endswith('/reviews') for a in c)]
    reply_posts = [c for c in first_posts if any(str(a).endswith('/replies') for a in c)]
    # The reply POST is attempted and the stub records it before exiting 502.
    # The review must not be recorded as posted, so a rerun can send the reply.
    check(first.returncode != 0 and pending.is_file() and not (f.council / 'demo-1/posted.json').exists()
          and len(review_posts) == 1 and len(reply_posts) == 1,
          'a failed reply leaves the review pending and does not record it posted '
          f'({first.returncode} {first.stderr.strip()} reviews={len(review_posts)} replies={len(reply_posts)})')
    before = len(f.calls())
    second = f.post()
    new_posts = [c for c in f.calls()[before:] if '-X' in c]
    check(second.returncode == 0 and len(new_posts) == 1
          and any(str(arg).endswith('/comments/42/replies') for arg in new_posts[0]),
          f'a rerun posts the reply and not the review again ({second.returncode} {new_posts} {second.stderr.strip()})')
    check((f.council / 'demo-1/posted.json').is_file() and not pending.exists(),
          'the retry records the review and clears the pending receipt')

    f = Fixture(root / 'later-huge')
    draft = load('comment_draft')
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    script = repro / 'f3.rb'
    script.write_text('z' * 70000, encoding='utf-8')
    payload = f.payload()
    for c in payload['comments']:
        if c.get('finding') == 'f3':
            c['body'] = draft.attach_repro(c['body'], script, key='demo-1/f3.rb')
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a later preattached script still dry-runs ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(len(req['body']) <= 65536, f'a later preattached script stays within the review body ({len(req["body"])})')
    check('should the label follow the locale' in req['body'] and '<summary>f3.rb</summary>' in req['body'],
          'the earlier prose stays and the later script is still attached')

    f = Fixture(root / 'two-body-scripts')
    payload = f.payload()
    payload['body_findings'] = ['f4', 'demo-2:f4']
    f.set_payload(payload)
    (f.council / 'demo-1' / 'repro').mkdir()
    (f.council / 'demo-2' / 'repro').mkdir()
    (f.council / 'demo-1' / 'repro' / 'f4.rb').write_text('a' * 70000, encoding='utf-8')
    (f.council / 'demo-2' / 'repro' / 'f4.rb').write_text("puts :second\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'two body scripts still dry-run ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(len(req['body']) <= 65536, f'two body scripts stay within the review body ({len(req["body"])})')
    check('<!-- repro:demo-1/f4.rb -->' in req['body'] and '<!-- repro:demo-2/f4.rb -->' in req['body'],
          'the second body script is not dropped')
    check('puts :second' in req['body'], 'the short second script is whole')

    f = Fixture(root / 'two-replies')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 81, 'body': 'First reply.'})
    payload['comments'].append({'in_reply_to': 81, 'body': 'Second reply.'})
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'two replies to comment 81' in proc.stderr and f.posts() == 0,
          f'two replies to one comment are refused before POST ({proc.returncode} {proc.stderr.strip()} posts={f.posts()})')

    f = Fixture(root / 'preattached')
    draft = load('comment_draft')
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    script = repro / 'f2.rb'
    script.write_text('y' * 70000, encoding='utf-8')
    payload = f.payload()
    for c in payload['comments']:
        if c.get('finding') == 'f2':
            c['body'] = draft.attach_repro(c['body'], script, key='demo-1/f2.rb')
            check(len(c['body']) <= 65536 and '<!-- repro:demo-1/f2.rb -->' in c['body'],
                  f'the preattached draft fits a line comment ({len(c["body"])})')
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a preattached moved repro still dry-runs ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(len(req['body']) <= 65536, f'a preattached moved repro stays within the review body ({len(req["body"])})')
    check(req['body'].count('<summary>f2.rb</summary>') == 1 and 'Truncated to' in req['body'],
          'the preattached block is resized, not duplicated')

    f = Fixture(root / 'changed-payload')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': 'The next build picks this up.'})
    f.set_payload(payload)
    first = f.post(env={'GH_REPLY_FAIL': '1'})
    check(first.returncode != 0 and (f.council / 'demo-1/post-7.pending.json').is_file(),
          f'changed-payload setup leaves a pending review ({first.returncode} {first.stderr.strip()})')
    payload['body'] = 'A different summary, written after the review was posted.'
    f.set_payload(payload)
    before = f.posts()
    second = f.post()
    check(second.returncode != 0 and 'different payload' in second.stderr
          and not (f.council / 'demo-1/posted.json').exists() and f.posts() == before,
          'a changed payload is not recorded as posted '
          f'({second.returncode} {second.stderr.strip()} posts={f.posts()} before={before})')

    f = Fixture(root / 'moved-and-body')
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text('y' * 70000, encoding='utf-8')
    (repro / 'f4.rb').write_text("puts :body\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a moved script leaves room for a body script ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    check(len(req['body']) <= 65536, f'moved and body scripts stay within the review body ({len(req["body"])})')
    check('<summary>f2.rb</summary>' in req['body'] and 'puts :body' in req['body'],
          'the body script is attached after the moved script')

    f = Fixture(root / 'reply-order')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 51, 'body': 'Posted first.'})
    payload['comments'].append({'in_reply_to': 52, 'body': 'Fails second.'})
    f.set_payload(payload)
    first = f.post(env={'GH_REPLY_FAIL': '52'})
    check(first.returncode != 0 and (f.council / 'demo-1/post-7.pending.json').is_file(),
          f'reply-order setup posts one reply and leaves the next pending ({first.returncode} {first.stderr.strip()})')
    payload['comments'] = [c for c in payload['comments'] if 'in_reply_to' not in c]
    payload['comments'].append({'in_reply_to': 52, 'body': 'Fails second.'})
    payload['comments'].append({'in_reply_to': 51, 'body': 'Changed after it was posted.'})
    f.set_payload(payload)
    before = f.posts()
    dry = f.post('--dry-run')
    check(dry.returncode != 0 and 'different text' in dry.stderr and f.posts() == before,
          f'a dry run rejects a changed reply before posting ({dry.returncode} {dry.stderr.strip()} posts={f.posts()})')
    second = f.post()
    check(second.returncode != 0 and 'different text' in second.stderr and f.posts() == before,
          'a changed reply stops the rerun before the unsent reply is posted '
          f'({second.returncode} {second.stderr.strip()} posts={f.posts()} before={before})')

    # An unlabeled new inline comment is refused on your own PR too.
    f = Fixture(root / 'own-pr')
    payload = f.payload()
    payload['pr_author'] = 'me'
    payload['reviewer'] = 'me'
    payload['comments'][0]['body'] = 'size counts deleted items.'
    payload['comments'][0].pop('severity', None)
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'no label' in proc.stderr and f.posts() == 0
          and not (f.council / 'demo-1/post-7.request.json').exists(),
          'an unlabeled inline comment on your own PR is refused and writes no request file '
          f'({proc.returncode} {proc.stderr.strip()})')


def test_moved_repro_keeps_its_block(root: Path) -> None:
    """An 8-digit script used to be reserved with a 6-digit note, and the moved loop dropped it."""
    f = Fixture(root / 'eight-digit')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') in ('f2', 'f3')]
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text('y' * 10_000_000, encoding='utf-8')
    (repro / 'f3.rb').write_text('z' * 10_000_000, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'two 8-digit moved scripts still dry-run ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['body']
    check(len(body) <= 65536, f'8-digit moved scripts stay within the review body ({len(body)})')
    check('<!-- repro:demo-1/f2.rb -->' in body and '<!-- repro:demo-1/f3.rb -->' in body,
          'the second moved script is not dropped')
    check('of 10000000 bytes' in body, 'the truncation note names the real byte length')


def test_hand_written_repro_is_adopted(root: Path) -> None:
    """A documented hand-written block has no marker. Posting adopts it."""
    block = (
        "<details><summary>{name}</summary>\n\n"
        "```ruby\n"
        "{script}"
        "```\n\n"
        "</details>\n"
    )

    f = Fixture(root / 'hand-moved')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f2']
    script = "puts :hand\n"
    payload['comments'][0]['body'] = (
        "**Question:** should the label follow the locale?\n\n"
        + block.format(name='f2.rb', script=script)
    )
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text(script, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and 'no room for repro' not in proc.stderr,
          f'a hand-written moved repro is not a false no-room error '
          f'({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['body']
    check('<!-- repro:demo-1/f2.rb -->' in body and body.count('<summary>f2.rb</summary>') == 1
          and 'puts :hand' in body,
          'posting adopts the hand-written moved block by inserting the marker')

    f = Fixture(root / 'hand-body')
    payload = f.payload()
    payload['body_findings'] = ['f4']
    script = "puts :bodyhand\n"
    payload['body'] = payload['body'] + "\n\n" + block.format(name='f4.rb', script=script)
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f4.rb').write_text("puts :fromfile\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and 'no room for repro' not in proc.stderr,
          f'a hand-written body repro is not a false no-room error '
          f'({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['body']
    # This fixture also moves f2 into the body, so the adopted f4 block is
    # rendered again from repro/f4.rb into the room the moved comment leaves.
    check('<!-- repro:demo-1/f4.rb -->' in body and body.count('<summary>f4.rb</summary>') == 1
          and 'puts :fromfile' in body and 'puts :bodyhand' not in body,
          'posting adopts the hand-written body block once, rendered from the file beside a moved comment')

    f = Fixture(root / 'hand-body-alone')
    payload = f.payload()
    payload['body_findings'] = ['f4']
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    payload['body'] = payload['body'] + "\n\n" + block.format(name='f4.rb', script="puts :bodyhand\n")
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f4.rb').write_text("puts :fromfile\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a hand-written body repro with no moved comment posts ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['body']
    check('<!-- repro:demo-1/f4.rb -->' in body and body.count('<summary>f4.rb</summary>') == 1
          and 'puts :bodyhand' in body and 'puts :fromfile' not in body,
          'with no moved comment, the hand-written body block that fits is kept as written')

    f = Fixture(root / 'hand-body-after-moved')
    payload = f.payload()
    payload['body_findings'] = ['f4']
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f2']
    payload['body'] = payload['body'] + "\n\n" + block.format(name='f4.rb', script="puts :bodyhand\n")
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text("puts :moved\n", encoding='utf-8')
    (repro / 'f4.rb').write_text("puts :fromfile\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a moved repro plus a hand-written body repro posts ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    body = req['body']
    check(body.count('<summary>f4.rb</summary>') == 1 and '<!-- repro:demo-1/f4.rb -->' in body
          and 'puts :fromfile' in body and 'puts :bodyhand' not in body
          and '<!-- repro:demo-1/f2.rb -->' in body,
          'a hand-written body block is adopted after another marker is already in the body')

    f = Fixture(root / 'big-hand-body-with-moved')
    payload = f.payload()
    payload['body_findings'] = ['f4']
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f2']
    payload['body'] = payload['body'] + "\n\n" + block.format(name='f4.rb', script='# pad\n' * 12000)
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text("puts :moved\n", encoding='utf-8')
    (repro / 'f4.rb').write_text('# pad\n' * 12000, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0,
          f'a large hand-written body block with a moved comment is resized, not refused ({proc.returncode} {proc.stderr.strip()})')
    if proc.returncode == 0:
        req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
        body = req['body']
        check(len(body) <= 65536 and body.count('<summary>f4.rb</summary>') == 1
              and '<!-- repro:demo-1/f4.rb -->' in body and '<!-- repro:demo-1/f2.rb -->' in body
              and 'puts :moved' in body,
              f'both scripts land in the review body within the limit ({len(body)})')

    f = Fixture(root / 'hand-inline')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    payload['comments'][0]['body'] = (
        payload['comments'][0]['body'].rstrip() + "\n\n" + block.format(name='f1.rb', script="puts :inlinehand\n")
    )
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f1.rb').write_text("puts :fromfile\n", encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a hand-written inline repro posts ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    inline_body = req['comments'][0]['body']
    check(inline_body.count('<summary>f1.rb</summary>') == 1 and '<!-- repro:demo-1/f1.rb -->' in inline_body
          and 'puts :inlinehand' in inline_body and 'puts :fromfile' not in inline_body,
          'posting adopts a hand-written inline block')


def test_inline_repro_not_dropped(root: Path) -> None:
    """An inline repro that does not fit is refused, and an over-limit body is refused."""
    f = Fixture(root / 'inline-no-room')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    label = '**Requested:** '
    payload['comments'][0]['body'] = label + ('x' * (65536 - len(label)))
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f1.rb').write_text('puts :hi\n', encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and f.posts() == 0
          and 'inline comment demo-1:f1 has no room for repro demo-1/f1.rb' in proc.stderr
          and not (f.council / 'demo-1/post-7.request.json').exists(),
          'an inline repro that does not fit is refused before POST '
          f'({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'inline-over-limit')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    label = '**Requested:** '
    payload['comments'][0]['body'] = label + ('y' * (65537 - len(label)))
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and f.posts() == 0
          and 'inline comment demo-1:f1 prose is 65537 characters' in proc.stderr
          and "GitHub's limit is 65536" in proc.stderr
          and 'no room for repro' not in proc.stderr,
          f'an over-limit inline body is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'inline-over-limit-repro')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    payload['comments'][0]['body'] = label + ('y' * (65537 - len(label)))
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f1.rb').write_text('puts :hi\n', encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and f.posts() == 0
          and 'inline comment demo-1:f1 prose is 65537 characters' in proc.stderr
          and 'no room for repro' not in proc.stderr,
          f'over-limit prose with a repro names the size, not the repro ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'inline-big-hand')
    payload = f.payload()
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') == 'f1']
    payload['comments'][0]['body'] = (
        label + 'the count is wrong.\n\n<details><summary>f1.rb</summary>\n\n```ruby\n'
        + ('# pad\n' * 12000) + '```\n\n</details>\n'
    )
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f1.rb').write_text('# pad\n' * 12000, encoding='utf-8')
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a large hand-written inline block is resized, not refused ({proc.returncode} {proc.stderr.strip()})')
    req = json.loads((f.council / 'demo-1/post-7.request.json').read_text())
    inline_body = req['comments'][0]['body']
    check(len(inline_body) <= 65536 and '<!-- repro:demo-1/f1.rb -->' in inline_body
          and inline_body.count('<summary>f1.rb</summary>') == 1 and 'bytes' in inline_body,
          f'the large hand-written inline block is truncated with a note ({len(inline_body)})')

    f = Fixture(root / 'reply-over-limit')
    payload = f.payload()
    payload['comments'].append({'in_reply_to': 42, 'body': 'z' * 65537})
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and f.posts() == 0
          and 'reply to comment 42 is 65537 characters' in proc.stderr
          and "GitHub's limit is 65536" in proc.stderr,
          f'an over-limit reply is refused ({proc.returncode} {proc.stderr.strip()})')


def test_moved_repro_missing_block_dies(root: Path) -> None:
    """A moved comment with no room for its script stops, instead of posting without it.

    The summary is a short opener, so the bulk that uses up the review body
    is a later moved comment, not a 65300-character summary.
    """
    f = Fixture(root / 'no-room')
    payload = f.payload()
    payload['body'] = 'Four notes on the widget change.'
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') in ('f2', 'f3')]
    for comment in payload['comments']:
        if comment.get('finding') == 'f3':
            comment['body'] = 'z' * 70000
    f.set_payload(payload)
    repro = f.council / 'demo-1' / 'repro'
    repro.mkdir()
    (repro / 'f2.rb').write_text('puts :hi\n', encoding='utf-8')
    refused(f, 'a moved repro that does not fit is refused before POST',
            'no room for repro demo-1/f2.rb')


OFFER = 'verdict: pending human — APPROVE offered'
NO_OFFER = 'verdict: pending human — no approve offer'
SEVERITY_LABELS = {
    'blocking': '**Requested:**',
    'should-fix': '**Requested:**',
    'nit': '**Advisory:**',
    'usability': '**Advisory:**',
    'fyi': '**Informational:**',
}
PR1_SVC = '@@ -1,2 +1,4 @@\n class Svc\n+  def a\n+  end\n end\n'
PR2_X = '@@ -1,1 +1,2 @@\n module X\n+  LIMIT = 3\n'
PR3_SVC = (
    '@@ -124,2 +124,5 @@\n   def b\n   end\n+  def stale_total\n+    rows.sum\n+  end\n'
)
STACK_GH = '''\
#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
args = sys.argv[1:]
log = Path(os.environ['GH_LOG'])
with log.open('a') as handle:
    handle.write(json.dumps(args) + '\\n')
if '-X' in args:
    sys.stderr.write('unexpected POST\\n')
    sys.exit(1)
path = next((a for a in args if a.startswith('repos/') or a == 'user'), '')
base = path.split('?')[0]
page = int(parse_qs(urlsplit(path).query).get('page', ['1'])[0])
if path == 'user':
    print(json.dumps({'login': 'me'}))
    raise SystemExit
if '/contents/' in base:
    sys.exit(1)
marker = '/pulls/'
if marker not in base:
    sys.exit('unexpected gh call ' + ' '.join(args))
number, tail = base.split(marker, 1)[1].split('/', 1) if '/' in base.split(marker, 1)[1] else (base.split(marker, 1)[1], '')
if tail.startswith('files'):
    print('[]' if page > 1 else Path(os.environ['GH_PATCHES'], number + '.json').read_text())
elif tail == '':
    print(json.dumps({'head': {'sha': os.environ['GH_HEAD']}}))
else:
    sys.exit('unexpected gh call ' + ' '.join(args))
'''


def verdict_line(proc: subprocess.CompletedProcess) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith('verdict:'):
            return line
    return ''


def request_of(f: Fixture) -> dict:
    return json.loads((f.council / 'demo-1/post-7.request.json').read_text())


def write_voters(f: Fixture, count: int) -> None:
    blind = f.council / 'demo-1' / 'blind'
    blind.mkdir(parents=True)
    rows = ''.join(f'{letter}=/tmp/voter-{letter}.json\n' for letter in 'ABCDEFGH'[:count])
    (blind / 'key.txt').write_text(rows)


def inline_only(f: Fixture, comments: list, body: str = 'One note on the widget change.') -> dict:
    payload = f.payload()
    payload.pop('event', None)
    payload.pop('voters_finished', None)
    payload['body'] = body
    payload['body_findings'] = []
    payload['comments'] = comments
    f.set_payload(payload)
    return payload


def one_inline(body: str, **extra: object) -> dict:
    comment = {
        'finding': 'f1', 'path': 'app/widget.rb', 'line': 3,
        'expect': 'items.count', 'body': body,
    }
    comment.update(extra)
    return comment


def test_summary_opener(root: Path) -> None:
    f = Fixture(root / 'opener-short')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')], body='x' * 600)
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'a 600-character summary opener passes ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'opener-chars')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')], body='x' * 601)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'summary opener is 601 characters' in proc.stderr and f.posts() == 0
          and not (f.council / 'demo-1/post-7.request.json').exists(),
          f'a summary opener over 600 characters is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'opener-lines')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')],
                body='\n'.join(f'line {i}' for i in range(1, 8)))
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'summary opener is 7 non-empty lines' in proc.stderr and f.posts() == 0,
          f'a summary opener over 6 lines is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'opener-table')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')],
                body='Notes.\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n')
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'summary opener contains a markdown table' in proc.stderr and f.posts() == 0,
          f'a summary opener with a table is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'opener-table-one-col')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')],
                body='Notes.\n\n| Note |\n| --- |\n| counts deleted items |\n')
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'summary opener contains a markdown table' in proc.stderr and f.posts() == 0,
          f'a summary opener with a one-column table is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'opener-ok')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')], body='One note on the widget change.')
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and f.posts() == 0, f'a short summary opener passes ({proc.returncode} {proc.stderr.strip()})')


def test_severity_on_comments(root: Path) -> None:
    for severity, label in SEVERITY_LABELS.items():
        f = Fixture(root / f'sev-{severity}')
        inline_only(f, [one_inline('size counts deleted items.', severity=severity)])
        proc = f.post('--dry-run')
        check(proc.returncode == 0, f'severity {severity} dry-runs ({proc.returncode} {proc.stderr.strip()})')
        body = request_of(f)['comments'][0]['body']
        check(body.startswith(label + ' '), f'severity {severity} is prepended as {label} ({body})')

    for extra in ({}, {'severity': 'nit'}):
        f = Fixture(root / f'question-{len(extra)}')
        inline_only(f, [one_inline('**Question:** should the label follow the locale?', **extra)])
        proc = f.post('--dry-run')
        check(proc.returncode != 0 and 'opens with **Question:**' in proc.stderr and f.posts() == 0
              and not (f.council / 'demo-1/post-7.request.json').exists(),
              f'a new inline comment that opens with **Question:** is refused {extra} '
              f'({proc.returncode} {proc.stderr.strip()})')
        lint = subprocess.run(
            ['python3', str(SCRIPTS / 'check-comment-labels.py'), str(f.council / 'demo-1/post-7.json')],
            text=True, capture_output=True)
        check(lint.returncode != 0 and 'opens with **Question:**' in lint.stdout,
              f'the lint and post-review agree on **Question:** {extra} ({lint.returncode} {lint.stdout.strip()})')

    f = Fixture(root / 'already-labeled')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run')
    body = request_of(f)['comments'][0]['body'] if proc.returncode == 0 else ''
    check(proc.returncode == 0 and body.startswith('**Requested:**'),
          f'an already-labeled inline comment keeps its label ({proc.returncode} {body!r} {proc.stderr.strip()})')

    f = Fixture(root / 'own-severity')
    payload = inline_only(f, [one_inline('size counts deleted items.', severity='usability')])
    payload['pr_author'] = 'me'
    payload['reviewer'] = 'me'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    body = request_of(f)['comments'][0]['body'] if proc.returncode == 0 else ''
    check(proc.returncode == 0 and body.startswith('**Advisory:**'),
          f'your own PR prepends the label from severity ({proc.returncode} {body!r} {proc.stderr.strip()})')

    f = Fixture(root / 'mismatch')
    inline_only(f, [one_inline('**Advisory:** size counts deleted items.', severity='blocking')])
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'disagrees with severity' in proc.stderr and f.posts() == 0
          and not (f.council / 'demo-1/post-7.request.json').exists(),
          f'a label that disagrees with severity is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'unlabeled-other')
    payload = f.payload()
    payload['pr_author'] = 'author'
    payload['reviewer'] = 'me'
    payload['comments'] = [one_inline('size counts deleted items.')]
    payload['body_findings'] = []
    payload.pop('event', None)
    f.set_payload(payload)
    proc = f.post()
    check(proc.returncode != 0 and 'no label' in proc.stderr and f.posts() == 0
          and not (f.council / 'demo-1/post-7.request.json').exists()
          and not (f.council / 'demo-1/posted.json').exists(),
          'an unlabeled inline comment with no severity is refused and writes no request file '
          f'({proc.returncode} {proc.stderr.strip()})')


def test_file_level_and_reanchor(root: Path) -> None:
    f = Fixture(root / 'file-level')
    inline_only(f, [{
        'finding': 'f1', 'path': 'app/widget.rb', 'line': 40,
        'body': '**Advisory:** this line is not in the diff.',
    }])
    dry = f.post('--dry-run')
    req = request_of(f) if dry.returncode == 0 else {}
    plan = req.get('plan') or []
    check(dry.returncode == 0 and plan and plan[0]['post_as'] == 'file' and req.get('comments') == []
          and req.get('file_comments') and 'plan: demo-1:f1 file app/widget.rb' in dry.stdout,
          f'file-level fallback: the plan says file and the request has no inline comment '
          f'({dry.returncode} {dry.stderr.strip()} {req})')
    real = f.post('--human-verdict', 'COMMENT')
    posted_req = json.loads((f.state / 'posted-request.json').read_text()) if (f.state / 'posted-request.json').is_file() else {}
    file_rows = []
    file_log = f.state / 'file-comments.jsonl'
    if file_log.is_file():
        file_rows = [json.loads(line) for line in file_log.read_text().splitlines() if line]
    check(real.returncode == 0 and len(file_rows) == 1 and file_rows[0].get('subject_type') == 'file'
          and file_rows[0].get('path') == 'app/widget.rb' and posted_req.get('comments') == [],
          f'a real post sends subject_type file once ({real.returncode} {real.stderr.strip()} {file_rows})')
    before = f.posts()
    again = f.post('--human-verdict', 'COMMENT')
    file_rows_again = [json.loads(line) for line in file_log.read_text().splitlines() if line]
    check(again.returncode != 0 and 'already in' in again.stderr and f.posts() == before and len(file_rows_again) == 1,
          f'a rerun does not post the file-level comment again ({again.returncode} {again.stderr.strip()} posts={f.posts()})')

    def stack_run(name: str, comments: list, findings: list,
                  listed: tuple = (1, 2, 3), extra: tuple = (),
                  svc: tuple = (PR1_SVC, PR3_SVC)) -> tuple[subprocess.CompletedProcess, list]:
        council = root / name / 'council'
        ballot = council / 'reanchor'
        ballot.mkdir(parents=True)
        patches = root / name / 'patches'
        patches.mkdir()
        for number, rows in (
            ('1', [{'filename': 'app/svc.rb', 'patch': svc[0]}]),
            ('2', [{'filename': 'lib/x.rb', 'patch': PR2_X}]),
            ('3', [{'filename': 'app/svc.rb', 'patch': svc[1]}]),
        ):
            (patches / f'{number}.json').write_text(json.dumps(rows))
        (ballot / 'stack.json').write_text(json.dumps({
            'status': 'stack', 'prs': [{'number': n} for n in listed],
        }))
        (ballot / 'findings.json').write_text(json.dumps({'findings': findings}))
        (ballot / 'post-1.json').write_text(json.dumps({
            'repo': 'example/widgets', 'ballot': 'reanchor', 'commit_id': HEAD,
            'body': 'One note.', 'comments': comments,
        }))
        bin_dir = root / name / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text(textwrap.dedent(STACK_GH), encoding='utf-8')
        gh.chmod(0o755)
        log = root / name / 'calls.jsonl'
        env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}',
               'GH_LOG': str(log), 'GH_PATCHES': str(patches), 'GH_HEAD': HEAD}
        env.pop('COUNCIL_GH', None)
        env.pop('CONCLAVE_GH', None)
        proc = subprocess.run(
            ['python3', str(SCRIPTS / 'post-review.py'), 'reanchor', '1', '--council-dir', str(council), *extra],
            env=env, text=True, capture_output=True,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []
        return proc, calls

    proc, calls = stack_run('reanchor', [{
        'finding': 'f1', 'path': 'app/svc.rb', 'line': 126,
        'body': '**Requested:** the total is first added on a later pull request.',
    }], [{
        'id': 'f1', 'file': 'app/svc.rb', 'line': 126, 'cited_code': 'def stale_total',
    }])
    request = root / 'reanchor' / 'council' / 'reanchor' / 'post-1.request.json'
    check(proc.returncode != 0 and 're-anchor to #3 app/svc.rb:126' in proc.stderr
          and not any('-X' in call for call in calls) and not request.exists(),
          f'a stack-tip line is refused and nothing is posted ({proc.returncode} {proc.stderr.strip()} calls={calls})')

    proc, calls = stack_run('flag', [{
        'finding': 'f1', 'path': 'lib/x.rb', 'line': 9,
        'body': '**Advisory:** the limit is not on this pull request.',
    }], [{
        'id': 'f1', 'file': 'lib/x.rb', 'line': 9, 'cited_code': 'nothing here',
    }])
    request = root / 'flag' / 'council' / 'reanchor' / 'post-1.request.json'
    check(proc.returncode != 0 and 'flag for the chair' in proc.stderr
          and not any('-X' in call for call in calls) and not request.exists(),
          f'a line that cannot be matched is flagged and nothing is posted ({proc.returncode} {proc.stderr.strip()})')

    # stack.json that lists the later PRs but not this one used to get this PR
    # appended as the tip, so #3 became "earlier" and the line posted on #1.
    proc, calls = stack_run('unlisted', [{
        'finding': 'f1', 'path': 'app/svc.rb', 'line': 126,
        'body': '**Requested:** the total is first added on a later pull request.',
    }], [{
        'id': 'f1', 'file': 'app/svc.rb', 'line': 126, 'cited_code': 'def stale_total',
    }], listed=(2, 3), extra=('--dry-run',))
    request = root / 'unlisted' / 'council' / 'reanchor' / 'post-1.request.json'
    req = json.loads(request.read_text()) if request.exists() else {}
    plan = req.get('plan') or [{}]
    check(proc.returncode == 0 and 'stack.json lists #2, #3 but not #1' in proc.stderr
          and plan[0].get('post_as') == 'file' and req.get('comments') == []
          and not any('-X' in call for call in calls),
          f'a target stack.json does not list falls back to a file-level comment, with a warning, and posts nothing '
          f'({proc.returncode} {proc.stderr.strip()} {plan})')

    # Both PRs put different code on commentable line 2 of app/svc.rb. The
    # cited code is first added by #3, so the stack tip's line number lands on
    # unrelated code in #1 and must not post there.
    same_line = (PR1_SVC, '@@ -1,2 +1,5 @@\n class Svc\n+  def stale_total\n+    rows.sum\n+  end\n end\n')
    clash = [{'finding': 'f1', 'path': 'app/svc.rb', 'line': 2,
              'body': '**Requested:** the total is first added on a later pull request.'}]
    cited = [{'id': 'f1', 'file': 'app/svc.rb', 'line': 2, 'cited_code': 'def stale_total'}]
    proc, calls = stack_run('same-line', clash, cited, svc=same_line, extra=('--dry-run',))
    request = root / 'same-line' / 'council' / 'reanchor' / 'post-1.request.json'
    check(proc.returncode != 0 and 're-anchor to #3 app/svc.rb:2' in proc.stderr
          and not any('-X' in call for call in calls) and not request.exists(),
          f'a stack-tip line that is also commentable on #1 with other code is refused, not posted inline '
          f'({proc.returncode} {proc.stderr.strip()})')

    proc, calls = stack_run('same-line-ok', clash, [{**cited[0], 'cited_code': 'def a'}], svc=same_line,
                            extra=('--dry-run',))
    request = root / 'same-line-ok' / 'council' / 'reanchor' / 'post-1.request.json'
    plan = (json.loads(request.read_text()) if request.exists() else {}).get('plan') or [{}]
    check(proc.returncode == 0 and plan[0].get('post_as') == 'inline',
          f'cited code that matches the line on #1 still posts inline ({proc.returncode} {proc.stderr.strip()} {plan})')

    proc, calls = stack_run('same-line-nocite', clash, [{**cited[0], 'cited_code': None}], svc=same_line,
                            extra=('--dry-run',))
    request = root / 'same-line-nocite' / 'council' / 'reanchor' / 'post-1.request.json'
    plan = (json.loads(request.read_text()) if request.exists() else {}).get('plan') or [{}]
    check(proc.returncode == 0 and plan[0].get('post_as') == 'inline',
          f'a finding with no cited code and a line in the diff still posts inline ({proc.returncode} {proc.stderr.strip()} {plan})')

    proc, calls = stack_run('same-line-nowhere', clash, [{**cited[0], 'cited_code': 'def nowhere'}], svc=same_line,
                            extra=('--dry-run',))
    request = root / 'same-line-nowhere' / 'council' / 'reanchor' / 'post-1.request.json'
    plan = (json.loads(request.read_text()) if request.exists() else {}).get('plan') or [{}]
    check(proc.returncode == 0 and plan[0].get('post_as') == 'file',
          f'cited code that no PR adds falls back to a file-level comment, not inline on unrelated code '
          f'({proc.returncode} {proc.stderr.strip()} {plan})')

    # #1 has `def a` on line 2. Cited `def` is a prefix of it and #3 adds the
    # whole line `def` at line 2, so a substring check would pin it to #1.
    prefix = (PR1_SVC, '@@ -1,2 +1,4 @@\n class Svc\n+  def\n+  end\n end\n')
    proc, calls = stack_run('same-line-prefix', clash, [{**cited[0], 'cited_code': 'def'}], svc=prefix,
                            extra=('--dry-run',))
    check(proc.returncode != 0 and 're-anchor to #3 app/svc.rb:2' in proc.stderr,
          f'cited code that is only a prefix of the line on #1 is re-anchored to #3, not posted on #1 '
          f'({proc.returncode} {proc.stderr.strip()})')

    explicit = [{**clash[0], 'expect': 'def'}]
    proc, calls = stack_run('same-line-expect', explicit, [{**cited[0], 'cited_code': 'def stale_total'}],
                            svc=prefix, extra=('--dry-run',))
    check(proc.returncode == 0, f'an explicit expect still matches as a substring ({proc.returncode} {proc.stderr.strip()})')


SELF_NOTE_FOLDS = ' (posted as a comment because this is your own PR)'


def soft_checks() -> tuple[list[str], object]:
    failed: list[str] = []

    def soft(cond: bool, label: str) -> None:
        print(('ok ' if cond else 'FAIL ') + label)
        if not cond:
            failed.append(label)

    return failed, soft


def test_empty_review(root: Path) -> None:
    """A review with no summary and only file-level findings is an empty review, which GitHub rejects."""
    failed, soft = soft_checks()
    file_only = {'finding': 'f1', 'path': 'app/widget.rb', 'line': 40,
                 'body': '**Advisory:** this line is not in the diff.'}

    f = Fixture(root / 'empty-file-only')
    inline_only(f, [file_only], body='')
    proc = f.post('--dry-run')
    soft(proc.returncode != 0 and 'empty review' in proc.stderr and 'file-level' in proc.stderr
         and not (f.council / 'demo-1/post-7.request.json').exists(),
         f'no summary and only file-level findings is refused at validation with a clear error '
         f'({proc.returncode} {proc.stderr.strip()})')
    real = f.post('--human-verdict', 'COMMENT')
    soft(real.returncode != 0 and 'empty review' in real.stderr and f.posts() == 0,
         f'a real post of the same payload never reaches the reviews endpoint ({real.returncode} {real.stderr.strip()} posts={f.posts()})')

    f = Fixture(root / 'empty-with-summary')
    inline_only(f, [file_only], body='One note.')
    proc = f.post('--dry-run')
    soft(proc.returncode == 0, f'the same finding with a summary passes ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'empty-with-inline')
    inline_only(f, [file_only, {**one_inline('**Advisory:** size counts deleted items.'), 'finding': 'f2'}], body='')
    proc = f.post('--dry-run')
    soft(proc.returncode == 0, f'an inline comment carries an empty summary ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'empty-own-pr')
    inline_only(f, [{**file_only, 'body': '**Requested:** this line is not in the diff.'}], body='')
    proc = f.post('--dry-run', env={'GH_PR_AUTHOR': 'me'})
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('body', '').startswith('Verdict: request changes'),
         f'on your own PR the verdict line is the body, so a file-level-only review is not empty '
         f'({proc.returncode} {proc.stderr.strip()} {req.get("body")!r})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} empty-review check(s)')


def test_stale_cited_code(root: Path) -> None:
    """A single-PR finding whose cited code is no longer on its line at the head does not post inline."""
    failed, soft = soft_checks()

    def moved(line: int, cited: str = 'items.count') -> Fixture:
        f = Fixture(root / f'moved-{line}-{abs(hash(cited)) % 1000}')
        inline_only(f, [{'finding': 'f1', 'path': 'app/widget.rb', 'line': line,
                         'body': '**Advisory:** size counts deleted items.'}])
        doc = json.loads((f.council / 'demo-1/findings.json').read_text())
        doc['findings'][0]['cited_code'] = cited
        (f.council / 'demo-1/findings.json').write_text(json.dumps(doc))
        return f

    f = moved(4)
    proc = f.post('--dry-run')
    req = request_of(f) if proc.returncode == 0 else {}
    plan = req.get('plan') or [{}]
    soft(proc.returncode == 0 and plan[0].get('post_as') == 'file' and req.get('comments') == []
         and 'does not hold the cited code' in proc.stderr,
         f'cited code that moved off line 4 is not posted inline there; it is file-level, with a warning '
         f'({proc.returncode} {proc.stderr.strip()} {plan})')

    f = moved(3)
    proc = f.post('--dry-run')
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and (req.get('plan') or [{}])[0].get('post_as') == 'inline',
         f'cited code that is still on its line posts inline ({proc.returncode} {proc.stderr.strip()})')

    f = moved(3, cited='items.size')
    proc = f.post('--dry-run')
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and (req.get('plan') or [{}])[0].get('post_as') == 'file' and req.get('comments') == [],
         f'cited code that is gone from the line is file-level, not inline ({proc.returncode} {proc.stderr.strip()})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} stale-cited-code check(s)')


def test_own_pr_comment_event(root: Path) -> None:
    failed, soft = soft_checks()
    f = Fixture(root / 'own-event-comment')
    payload = inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    payload['event'] = 'COMMENT'
    f.set_payload(payload)
    proc = f.post('--dry-run', env={'GH_PR_AUTHOR': 'me'})
    req = request_of(f) if proc.returncode == 0 else {}
    body = req.get('body', '')
    print(f'info own PR, payload event COMMENT, Requested finding: event={req.get("event")!r} '
          f'first line={body.splitlines()[0] if body else ""!r} body={body!r}')
    soft(proc.returncode == 0 and req.get('event') == 'COMMENT'
         and body == 'Verdict: request changes' + SELF_NOTE_FOLDS + '\n\nOne note on the widget change.'
         and req['comments'][0]['body'].startswith('**Requested:**'),
         f'own PR, payload event COMMENT with a Requested finding: posts as COMMENT with the verdict line '
         f'({proc.returncode} {proc.stderr.strip()})')
    f = Fixture(root / 'other-event-comment')
    payload = inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    payload['event'] = 'COMMENT'
    f.set_payload(payload)
    proc = f.post('--dry-run', env={'GH_PR_AUTHOR': 'someone-else'})
    soft(proc.returncode != 0 and 'conflicts with 1 Requested' in proc.stderr,
         f"someone else's PR, payload event COMMENT with a Requested finding is still refused "
         f'({proc.returncode} {proc.stderr.strip()})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} own-PR COMMENT check(s)')


def test_default_is_preview(root: Path) -> None:
    failed, soft = soft_checks()
    f = Fixture(root / 'default-preview')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post(bare=True)
    soft(proc.returncode == 0 and f.posts() == 0 and ['api', 'user'] not in f.calls()
         and not (f.council / 'demo-1/posted.json').exists() and not (f.council / 'demo-1/post-7.pending.json').exists(),
         f'no flags: nothing is posted and no receipt is written ({proc.returncode} {proc.stderr.strip()} posts={f.posts()})')
    req = request_of(f) if (f.council / 'demo-1/post-7.request.json').exists() else {}
    preview = proc.stdout.split('review that --post would send:', 1)[-1]
    try:
        shown = json.loads(preview[preview.index('{'):preview.rindex('}') + 1])
    except ValueError:
        shown = None
    soft(shown is not None and shown.get('request') == {k: req.get(k) for k in ('commit_id', 'event', 'body', 'comments')}
         and 'preview' in proc.stdout,
         f'no flags: stdout prints the exact review it would send ({proc.returncode} {proc.stdout[-400:]!r})')
    both = f.post('--dry-run', '--post', bare=True)
    soft(both.returncode != 0 and f.posts() == 0, f'--dry-run with --post is refused ({both.returncode} {both.stderr.strip()})')
    real = f.post()
    soft(real.returncode == 0 and f.posts() >= 1 and (f.state / 'posted-request.json').is_file(),
         f'--post posts the review ({real.returncode} {real.stderr.strip()} posts={f.posts()})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} default-preview check(s)')


SELF_NOTE = ' (posted as a comment because this is your own PR)'


def test_self_authored(root: Path) -> None:
    """GitHub rejects REQUEST_CHANGES and APPROVE on your own PR; the stub's gh user is `me`."""
    failed: list[str] = []

    def soft(cond: bool, label: str) -> None:
        print(('ok ' if cond else 'FAIL ') + label)
        if not cond:
            failed.append(label)

    def first_line(text: str) -> str:
        return text.splitlines()[0] if text else ''

    mine, theirs = {'GH_PR_AUTHOR': 'me'}, {'GH_PR_AUTHOR': 'someone-else'}

    f = Fixture(root / 'self-requested')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run', env=mine)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'COMMENT'
         and first_line(req.get('body', '')) == 'Verdict: request changes' + SELF_NOTE
         and (req.get('comments') or [{}])[0].get('body', '').startswith('**Requested:**')
         and 'COMMENT' in proc.stdout and 'your own PR' in proc.stdout,
         f'own PR, Requested finding: dry run reports COMMENT, the verdict first line, and keeps the label '
         f'({proc.returncode} {proc.stderr.strip()} {req.get("event")!r} {req.get("body")!r})')
    real = f.post(env=mine)
    sent = json.loads((f.state / 'posted-request.json').read_text()) if (f.state / 'posted-request.json').is_file() else {}
    soft(real.returncode == 0 and sent.get('event') == 'COMMENT'
         and first_line(sent.get('body', '')) == 'Verdict: request changes' + SELF_NOTE
         and sent.get('comments', [{}])[0].get('body', '').startswith('**Requested:**'),
         f'own PR, Requested finding: the POST is a COMMENT with the verdict line ({real.returncode} {real.stderr.strip()} {sent.get("event")!r})')

    f = Fixture(root / 'self-requested-comment')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run', '--human-verdict', 'COMMENT', env=mine)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'COMMENT'
         and first_line(req.get('body', '')) == 'Verdict: request changes' + SELF_NOTE,
         f'own PR: --human-verdict COMMENT is allowed with a Requested finding ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'self-approve')
    inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    write_voters(f, 2)
    proc = f.post('--dry-run', '--human-verdict', 'APPROVE', env=mine)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'COMMENT'
         and first_line(req.get('body', '')) == 'Verdict: approve' + SELF_NOTE
         and (req.get('comments') or [{}])[0].get('body', '').startswith('**Advisory:**'),
         f'own PR, APPROVE chosen: posts as COMMENT with the approve verdict line and the label '
         f'({proc.returncode} {proc.stderr.strip()} {req.get("event")!r} {req.get("body")!r})')
    real = f.post('--human-verdict', 'APPROVE', env=mine)
    sent = json.loads((f.state / 'posted-request.json').read_text()) if (f.state / 'posted-request.json').is_file() else {}
    soft(real.returncode == 0 and sent.get('event') == 'COMMENT'
         and first_line(sent.get('body', '')) == 'Verdict: approve' + SELF_NOTE,
         f'own PR, APPROVE chosen: the POST is a COMMENT ({real.returncode} {real.stderr.strip()} {sent.get("event")!r})')

    f = Fixture(root / 'self-comment')
    inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    write_voters(f, 2)
    proc = f.post('--dry-run', '--human-verdict', 'COMMENT', env=mine)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'COMMENT' and req.get('body') == 'One note on the widget change.',
         f'own PR, COMMENT chosen: no verdict line is added ({proc.returncode} {proc.stderr.strip()} {req.get("body")!r})')

    f = Fixture(root / 'other-requested')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run', env=theirs)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'REQUEST_CHANGES'
         and req.get('body') == 'One note on the widget change.' and 'own PR' not in proc.stdout
         and 'own PR' not in json.dumps(req),
         f'someone else\'s PR, Requested finding: still REQUEST_CHANGES with the body unchanged '
         f'({proc.returncode} {proc.stderr.strip()} {req.get("event")!r} {req.get("body")!r})')

    f = Fixture(root / 'other-approve')
    inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    write_voters(f, 2)
    proc = f.post('--dry-run', '--human-verdict', 'APPROVE', env=theirs)
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'APPROVE' and req.get('body') == 'One note on the widget change.',
         f'someone else\'s PR, APPROVE chosen: still APPROVE with the body unchanged '
         f'({proc.returncode} {proc.stderr.strip()} {req.get("event")!r} {req.get("body")!r})')

    f = Fixture(root / 'no-author')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run')
    req = request_of(f) if proc.returncode == 0 else {}
    soft(proc.returncode == 0 and req.get('event') == 'REQUEST_CHANGES' and req.get('body') == 'One note on the widget change.',
         f'a PR response with no author is not treated as your own ({proc.returncode} {proc.stderr.strip()} {req.get("event")!r})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} self-authored check(s)')


def test_verdict(root: Path) -> None:
    f = Fixture(root / 'requested')
    inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    proc = f.post('--dry-run')
    req = request_of(f)
    check(proc.returncode == 0 and req.get('event') == 'REQUEST_CHANGES'
          and verdict_line(proc) == 'verdict: REQUEST_CHANGES (1 Requested)',
          f'Requested finding requests changes ({verdict_line(proc)!r} {proc.stderr.strip()})')

    f = Fixture(root / 'requested-and-advisory')
    inline_only(f, [
        one_inline('**Requested:** size counts deleted items.'),
        {'finding': 'f3', 'path': 'app/widget.rb', 'line': 13, 'expect': '@cache = nil',
         'body': '**Advisory:** reset leaves the cache warm.'},
    ])
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and request_of(f).get('event') == 'REQUEST_CHANGES'
          and 'REQUEST_CHANGES (1 Requested)' in verdict_line(proc),
          f'Requested plus Advisory still requests changes ({verdict_line(proc)!r} {proc.stderr.strip()})')

    for label, name in (('**Advisory:**', 'advisory'), ('**Informational:**', 'informational')):
        f = Fixture(root / f'{name}-offer')
        inline_only(f, [one_inline(f'{label} size counts deleted items.')])
        write_voters(f, 2)
        proc = f.post('--dry-run')
        req = request_of(f) if proc.returncode == 0 else {}
        check(proc.returncode == 0 and OFFER in verdict_line(proc) and req.get('event') != 'APPROVE'
              and 'event' not in req and 'APPROVE' not in json.dumps({k: req.get(k) for k in ('event',)}),
              f'{name}-only with 2 voters offers APPROVE and emits no APPROVE event '
              f'({verdict_line(proc)!r} event={req.get("event")!r} {proc.stderr.strip()})')

        f = Fixture(root / f'{name}-refuse')
        inline_only(f, [one_inline(f'{label} size counts deleted items.')])
        write_voters(f, 2)
        proc = f.post()
        check(proc.returncode != 0 and 'A real post needs --human-verdict' in proc.stderr and f.posts() == 0
              and not (f.council / 'demo-1/posted.json').exists(),
              f'{name}-only without --human-verdict posts nothing ({proc.returncode} {proc.stderr.strip()})')

        f = Fixture(root / f'{name}-approve')
        inline_only(f, [one_inline(f'{label} size counts deleted items.')])
        write_voters(f, 2)
        proc = f.post('--human-verdict', 'APPROVE')
        posted = json.loads((f.state / 'posted-request.json').read_text()) if proc.returncode == 0 else {}
        record = request_of(f) if (f.council / 'demo-1/post-7.request.json').is_file() else {}
        check(proc.returncode == 0 and posted.get('event') == 'APPROVE' and 'plan_verdict' not in posted
              and record.get('event') == 'APPROVE' and OFFER in record.get('plan_verdict', ''),
              f'{name}-only --human-verdict APPROVE posts APPROVE ({proc.returncode} {posted} {proc.stderr.strip()})')

    for count, how in ((1, '1 voter'), (0, 'unknown count')):
        f = Fixture(root / f'voters-{count}')
        inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
        if count:
            write_voters(f, count)
        proc = f.post('--dry-run')
        line = verdict_line(proc)
        check(proc.returncode == 0 and line.startswith(NO_OFFER) and f'only {count} voter(s) finished' in line
              and 'need 2' in line,
              f'advisory-only with {how} withholds the approve offer ({line!r} {proc.stderr.strip()})')
        if count == 0:
            check('the voter count is unknown and is taken as 0' in line,
                  f'an unknown voter count is stated ({line!r})')
        refused_approve = f.post('--human-verdict', 'APPROVE')
        check(refused_approve.returncode != 0 and 'approve offer was withheld' in refused_approve.stderr
              and f.posts() == 0,
              f'--human-verdict APPROVE is refused with {how} ({refused_approve.returncode} {refused_approve.stderr.strip()})')
        accepted = f.post('--human-verdict', 'REQUEST_CHANGES')
        posted = json.loads((f.state / 'posted-request.json').read_text()) if accepted.returncode == 0 else {}
        check(accepted.returncode == 0 and posted.get('event') == 'REQUEST_CHANGES',
              f'--human-verdict REQUEST_CHANGES works with {how} ({accepted.returncode} {posted} {accepted.stderr.strip()})')

    f = Fixture(root / 'payload-loses-to-key')
    payload = inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    payload['voters_finished'] = 2
    f.set_payload(payload)
    write_voters(f, 1)
    proc = f.post('--dry-run')
    check(NO_OFFER in verdict_line(proc) and 'only 1 voter(s) finished' in verdict_line(proc)
          and 'counted from demo-1/blind/key.txt' in verdict_line(proc),
          f'the ballot key wins over a higher payload count ({verdict_line(proc)!r})')

    f = Fixture(root / 'payload-count')
    payload = inline_only(f, [one_inline('**Informational:** size counts deleted items.')])
    payload['voters_finished'] = 2
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(OFFER in verdict_line(proc) and 'the payload voters_finished' in verdict_line(proc),
          f'voters_finished is used when the ballot has no key ({verdict_line(proc)!r} {proc.stderr.strip()})')

    f = Fixture(root / 'payload-approve')
    payload = inline_only(f, [])
    payload['event'] = 'APPROVE'
    payload['comments'] = []
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'APPROVE is only a --human-verdict' in proc.stderr and f.posts() == 0,
          f'a payload event APPROVE is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'comment-with-findings')
    payload = inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    payload['event'] = 'COMMENT'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'payload event COMMENT' in proc.stderr and f.posts() == 0
          and not (f.council / 'demo-1/post-7.request.json').exists(),
          f'payload event COMMENT with findings is refused ({proc.returncode} {proc.stderr.strip()})')

    f = Fixture(root / 'human-comment')
    inline_only(f, [one_inline('**Advisory:** size counts deleted items.')])
    write_voters(f, 2)
    proc = f.post('--human-verdict', 'COMMENT')
    posted = json.loads((f.state / 'posted-request.json').read_text()) if proc.returncode == 0 else {}
    check(proc.returncode == 0 and posted.get('event') == 'COMMENT',
          f'--human-verdict COMMENT is the human choosing COMMENT ({proc.returncode} {posted} {proc.stderr.strip()})')

    f = Fixture(root / 'conflict-event')
    payload = inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
    payload['event'] = 'COMMENT'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode != 0 and 'payload event COMMENT' in proc.stderr and 'REQUEST_CHANGES' in proc.stderr,
          f'a payload COMMENT conflicts with Requested ({proc.returncode} {proc.stderr.strip()})')

    for human in ('APPROVE', 'COMMENT'):
        f = Fixture(root / f'conflict-{human}')
        inline_only(f, [one_inline('**Requested:** size counts deleted items.')])
        proc = f.post('--human-verdict', human)
        check(proc.returncode != 0 and f'--human-verdict {human}' in proc.stderr and 'REQUEST_CHANGES' in proc.stderr
              and f.posts() == 0,
              f'--human-verdict {human} conflicts with Requested ({proc.returncode} {proc.stderr.strip()})')

    for path in sorted(FIXTURES.rglob('post-*.json')):
        f = Fixture(root / f'fixture-{path.stem}')
        proc = f.post('--dry-run')
        req = request_of(f) if proc.returncode == 0 else {}
        check(proc.returncode == 0 and req.get('event') != 'APPROVE' and not verdict_line(proc).startswith('verdict: APPROVE'),
              f'{path.name} does not emit APPROVE ({verdict_line(proc)!r} event={req.get("event")!r} {proc.stderr.strip()})')

    f = Fixture(root / 'reply-only')
    payload = inline_only(f, [{'in_reply_to': 42, 'body': 'The next build picks this up.'}])
    payload['comments'] = [{'in_reply_to': 42, 'body': 'The next build picks this up.'}]
    f.set_payload(payload)
    proc = f.post()
    posted = json.loads((f.state / 'posted-request.json').read_text()) if proc.returncode == 0 else {}
    check(proc.returncode == 0 and posted.get('event') == 'COMMENT' and f.posts() == 2
          and 'no labeled findings' in request_of(f).get('plan_verdict', ''),
          f'a reply-only payload posts COMMENT ({proc.returncode} event={posted.get("event")!r} posts={f.posts()} {proc.stderr.strip()})')

    f = Fixture(root / 'summary-only')
    payload = inline_only(f, [])
    payload['event'] = 'REQUEST_CHANGES'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and request_of(f).get('event') == 'REQUEST_CHANGES',
          f'zero labeled findings keep the payload event ({verdict_line(proc)!r} {proc.stderr.strip()})')

    f = Fixture(root / 'moved-only')
    payload = f.payload()
    payload.pop('event', None)
    payload['body_findings'] = []
    payload['comments'] = [c for c in payload['comments'] if c.get('finding') in ('f2', 'f3')]
    f.set_payload(payload)
    proc = f.post('--dry-run')
    req = request_of(f) if proc.returncode == 0 else {}
    labels = [row.get('label') for row in req.get('plan', [])]
    check(proc.returncode == 0 and req.get('event') == 'COMMENT' and labels and all(label is None for label in labels),
          f'findings moved outside the diff are zero labeled findings ({verdict_line(proc)!r} labels={labels} {proc.stderr.strip()})')


def test_resume_keeps_published_comments(root: Path) -> None:
    """A published file-level comment must stay in the payload, and in the record, until the run completes."""
    failed, soft = soft_checks()
    one = {'finding': 'f1', 'path': 'app/widget.rb', 'line': 40, 'body': '**Advisory:** first file note.'}
    two = {'finding': 'f4', 'path': 'app/widget.rb', 'line': 41, 'body': '**Advisory:** second file note.'}

    def file_rows(f: Fixture) -> list[dict]:
        log = f.state / 'file-comments.jsonl'
        return [json.loads(line) for line in log.read_text().splitlines() if line] if log.is_file() else []

    def partial(name: str) -> Fixture:
        f = Fixture(root / name)
        inline_only(f, [one, two])
        first = f.post('--human-verdict', 'COMMENT', env={'GH_FILE_FAIL_NTH': '2'})
        soft(first.returncode != 0 and '502' in first.stderr and len(file_rows(f)) == 1
             and (f.council / 'demo-1/post-7.pending.json').is_file()
             and not (f.council / 'demo-1/posted.json').exists(),
             f'{name}: the first file comment posts, the second fails, the receipt is left '
             f'({first.returncode} {first.stderr.strip()} file comments={len(file_rows(f))})')
        return f

    f = partial('resume-dropped')
    inline_only(f, [two])
    posts = f.posts()
    receipt = (f.council / 'demo-1/post-7.pending.json').read_text()
    for flags in (['--dry-run'], ['--human-verdict', 'COMMENT']):
        proc = f.post(*flags)
        soft(proc.returncode != 0 and 'demo-1:f1' in proc.stderr and 'missing from the payload' in proc.stderr
             and f.posts() == posts and len(file_rows(f)) == 1
             and (f.council / 'demo-1/post-7.pending.json').read_text() == receipt
             and not (f.council / 'demo-1/posted.json').exists(),
             f'resume {" ".join(flags)} with the first file comment dropped is refused, naming it, '
             f'with posts unchanged ({proc.returncode} {proc.stderr.strip()} posts={f.posts()}, was {posts})')

    f = partial('resume-changed')
    inline_only(f, [{**one, 'body': '**Advisory:** first file note, reworded.'}, two])
    posts = f.posts()
    proc = f.post('--human-verdict', 'COMMENT')
    soft(proc.returncode != 0 and 'demo-1:f1' in proc.stderr and 'different text' in proc.stderr
         and f.posts() == posts and len(file_rows(f)) == 1
         and not (f.council / 'demo-1/posted.json').exists(),
         f'resume with the first file comment reworded is refused, naming it, with posts unchanged '
         f'({proc.returncode} {proc.stderr.strip()} posts={f.posts()}, was {posts})')

    f = partial('resume-both-problems')
    inline_only(f, [{**two, 'body': '**Advisory:** second file note, reworded.'}])
    posts = f.posts()
    proc = f.post('--human-verdict', 'COMMENT')
    soft(proc.returncode != 0 and 'demo-1:f1' in proc.stderr and f.posts() == posts,
         f'a missing published comment is refused even when another comment is not yet posted '
         f'({proc.returncode} {proc.stderr.strip()})')

    f = partial('resume-unchanged')
    posts = f.posts()
    proc = f.post('--human-verdict', 'COMMENT')
    recorded = {}
    if (f.council / 'demo-1/posted.json').is_file():
        recorded = json.loads((f.council / 'demo-1/posted.json').read_text())['reviews']['7']['findings']
    soft(proc.returncode == 0 and len(file_rows(f)) == 2 and f.posts() == posts + 1
         and sorted(recorded) == ['demo-1:f1', 'demo-1:f4']
         and all(recorded[k].get('comment_id') == 8100 and recorded[k].get('url') for k in recorded)
         and not (f.council / 'demo-1/post-7.pending.json').exists(),
         f'an unchanged resume sends only the second comment and posted.json holds both '
         f'({proc.returncode} {proc.stderr.strip()} file comments={len(file_rows(f))} '
         f'posts={f.posts()}, was {posts} recorded={sorted(recorded)})')

    f = partial('resume-review-gone')
    posts = f.posts()
    receipt = (f.council / 'demo-1/post-7.pending.json').read_text()
    proc = f.post('--human-verdict', 'COMMENT', env={'GH_REVIEW_GONE': '1'})
    soft(proc.returncode != 0 and 'demo-1:f1' in proc.stderr and 'not deleting the receipt' in proc.stderr
         and f.posts() == posts and len(file_rows(f)) == 1
         and (f.council / 'demo-1/post-7.pending.json').read_text() == receipt,
         f'a receipt that lists a published comment is not deleted when its review is gone, and nothing posts '
         f'({proc.returncode} {proc.stderr.strip()} posts={f.posts()}, was {posts})')
    if failed:
        raise SystemExit(f'FAIL {len(failed)} resume check(s)')


REPRO_NOTE = 'not inlined: path is a symlink or resolves outside the run directory'


def test_repro_symlink_not_inlined(root: Path) -> None:
    """A repro that is a symlink, or sits under a symlinked repro directory, is not inlined."""
    outside = root / 'outside'
    outside.mkdir()
    (outside / 'f1.rb').write_text("puts 'OUTSIDE-ONLY-31337'\n", encoding='utf-8')

    f = Fixture(root / 'repro-plain')
    (f.council / 'demo-1' / 'repro').mkdir()
    (f.council / 'demo-1' / 'repro' / 'f1.rb').write_text("puts 'inside'\n", encoding='utf-8')
    proc = f.post('--dry-run')
    body = request_of(f)['comments'][0]['body'] if proc.returncode == 0 else ''
    check("puts 'inside'" in body and REPRO_NOTE not in proc.stderr,
          f'a plain repro file still inlines ({proc.returncode} {proc.stderr.strip()})')

    for name, link in (('repro-file-link', 'file'), ('repro-dir-link', 'dir')):
        f = Fixture(root / name)
        repro = f.council / 'demo-1' / 'repro'
        if link == 'file':
            repro.mkdir()
            (repro / 'f1.rb').symlink_to(outside / 'f1.rb')
        else:
            repro.symlink_to(outside, target_is_directory=True)
        proc = f.post('--dry-run')
        req = request_of(f) if proc.returncode == 0 else {}
        sent = json.dumps(req) + proc.stdout
        check(proc.returncode == 0 and 'OUTSIDE-ONLY-31337' not in sent,
              f'a symlinked repro {link} is not inlined ({proc.returncode} {proc.stderr.strip()})')
        check(proc.stderr.count(f'demo-1: repro for f1 {REPRO_NOTE}') == 1,
              f'a symlinked repro {link} gets one note ({proc.stderr.strip()})')


def fake_aws_key() -> str:
    """Built at run time, so no key-shaped literal lives in the repo."""
    import secrets
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
    return 'AK' + 'IA' + ''.join(secrets.choice(alphabet) for _ in range(16))


def path_without_gitleaks() -> str:
    keep = [d for d in os.environ['PATH'].split(os.pathsep) if d and not (Path(d) / 'gitleaks').exists()]
    return os.pathsep.join(keep)


def test_secret_scan_clean(root: Path) -> None:
    """gitleaks scans every outgoing body before --post and in the preview."""
    check(shutil.which('gitleaks') is not None, 'gitleaks is on PATH (a post-review dependency, see docs/review.md)')
    f = Fixture(root / 'scan-clean')
    proc = f.post('--dry-run')
    check(proc.returncode == 0 and 'secret scan: clean' in proc.stdout,
          f'a clean review passes the scan in the preview ({proc.returncode} {proc.stderr.strip()})')
    proc = f.post()
    check(proc.returncode == 0 and f.posts() == 1 and 'secret scan: clean' in proc.stdout,
          f'a clean review passes the scan and posts ({proc.returncode} {proc.stderr.strip()})')


def test_secret_scan_key(root: Path) -> None:
    key = fake_aws_key()
    for flag in ('--post', 'preview'):
        f = Fixture(root / f'scan-key-{flag.strip("-")}')
        payload = f.payload()
        payload['comments'][0]['body'] += f' The fixture logs {key} at boot.'
        f.set_payload(payload)
        proc = f.post() if flag == '--post' else f.post(bare=True)
        out = proc.stdout + proc.stderr
        check(proc.returncode != 0 and f.posts() == 0 and 'aws-access-token' in proc.stderr,
              f'a fake key in a comment is refused ({flag}, {proc.returncode} posts={f.posts()})')
        check(key not in out and key[4:] not in out, f'the refusal never prints the key ({flag})')


def test_secret_scan_missing(root: Path) -> None:
    f = Fixture(root / 'scan-missing')
    missing = {'PATH': f"{f.root / 'bin'}{os.pathsep}{path_without_gitleaks()}"}
    proc = f.post(env=missing)
    check(proc.returncode != 0 and f.posts() == 0 and 'gitleaks' in proc.stderr,
          f'--post without gitleaks on PATH is refused ({proc.returncode} posts={f.posts()} {proc.stderr.strip()})')
    proc = f.post(bare=True, env=missing)
    check(proc.returncode == 0 and f.posts() == 0 and 'secret scan: not run' in proc.stdout
          and '--post will refuse' in proc.stdout,
          f'the preview reports a missing gitleaks ({proc.returncode} {proc.stdout[-300:]})')

    f = Fixture(root / 'scan-error')
    broken = f.root / 'bin' / 'gitleaks'
    broken.write_text('#!/bin/sh\nexit 2\n', encoding='utf-8')
    broken.chmod(0o755)
    proc = f.post()
    check(proc.returncode != 0 and f.posts() == 0 and 'gitleaks' in proc.stderr,
          f'--post refuses when gitleaks errors ({proc.returncode} posts={f.posts()} {proc.stderr.strip()})')


def main() -> None:
    only = sys.argv[1:]
    if only:
        with tempfile.TemporaryDirectory(prefix='post-review-tests-') as raw:
            for name in only:
                globals()[name](Path(raw))
        return
    with tempfile.TemporaryDirectory(prefix='post-review-tests-') as raw:
        root = Path(raw)
        test_right_lines()
        test_expect_outside_diff(root)
        test_form_feed_hunk(root)
        test_dry_run(Fixture(root / 'dry'))
        f = Fixture(root / 'post')
        test_post(f)
        test_index(f)
        test_findings_shape(root)
        test_readback_does_not_double_post(root)
        test_other_reviewer_same_body(root)
        test_refusals(root)
        test_repro_and_labels(root)
        test_moved_repro_keeps_its_block(root)
        test_hand_written_repro_is_adopted(root)
        test_inline_repro_not_dropped(root)
        test_moved_repro_missing_block_dies(root)
        test_summary_opener(root)
        test_severity_on_comments(root)
        test_file_level_and_reanchor(root)
        test_verdict(root)
        test_self_authored(root)
        test_empty_review(root)
        test_stale_cited_code(root)
        test_own_pr_comment_event(root)
        test_default_is_preview(root)
        test_resume_keeps_published_comments(root)
        test_repro_symlink_not_inlined(root)
        test_secret_scan_key(root)
        test_secret_scan_clean(root)
        test_secret_scan_missing(root)
    print('all post-review checks passed')


if __name__ == '__main__':
    main()
