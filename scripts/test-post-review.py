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
        if os.environ.get('GH_REPLY_FAIL'):
            sys.stderr.write('HTTP 502 Bad Gateway\\n')
            sys.exit(1)
        req = json.loads(sys.stdin.read())
        (state / 'posted-reply.json').write_text(json.dumps({'url': base, 'body': req}))
        print(json.dumps({'id': 77, 'html_url': 'https://github.com/example/widgets/pull/7#discussion_r77'}))
    elif base != repo + '/reviews':
        sys.exit('unexpected POST ' + base)
    else:
        req = json.loads(sys.stdin.read())
        (state / 'posted-request.json').write_text(json.dumps(req))
        print(json.dumps({'id': 900, 'state': 'COMMENTED',
                          'html_url': 'https://github.com/example/widgets/pull/7#pullrequestreview-900'}))
elif base == repo:
    print(json.dumps({'head': {'sha': os.environ.get('GH_HEAD', '1' * 40)}}))
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

    def post(self, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(['python3', str(SCRIPTS / 'post-review.py'), 'demo-1', '7',
                               '--council-dir', str(self.council), *extra],
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
    """f4: expect is checked on a line outside the diff, using the file at the head."""
    f = Fixture(root / 'outside-expect')
    blob = '\n'.join(f'line {i}' for i in range(1, 40)) + '\nlabel = I18n.locale\n'
    blobs = root / 'blobs.json'
    blobs.write_text(json.dumps({'app/widget.rb': blob}))
    env = {'GH_BLOBS': str(blobs)}
    payload = f.payload()
    payload['comments'] = [c for c in payload['comments'] if c['path'] == 'app/widget.rb' and c['line'] == 40]
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
    check(matched.returncode == 0 and f'blob/{HEAD}/app/widget.rb#L40' in
          (f.council / 'demo-1/post-7.request.json').read_text(),
          f'f4 a matching expect outside the diff still moves the comment ({matched.returncode} {matched.stderr.strip()})')


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
    check(req['commit_id'] == HEAD and req['event'] == 'COMMENT', 'request carries commit_id and event')
    check([(c['path'], c['line'], c['side']) for c in req['comments']] ==
          [('app/widget.rb', 3, 'RIGHT'), ('app/widget.rb', 13, 'RIGHT')], 'only in-diff lines stay inline, side RIGHT')
    check(all('finding' not in c and 'expect' not in c for c in req['comments']), 'request drops payload-only keys')
    body = req['body']
    check('**Outside this diff**' in body, 'outside-diff comments move into the body')
    check(f'https://github.com/example/widgets/blob/{HEAD}/app/widget.rb#L40' in body
          and f'https://github.com/example/widgets/blob/{HEAD}/lib/other.rb#L1' in body,
          'moved comments carry a permalink at the head')
    check('moved: app/widget.rb:40 (f2)' in proc.stdout and 'nothing posted' in proc.stdout, 'dry run prints the plan')


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

    f = Fixture(root / 'unlabeled-inline')
    payload = f.payload()
    payload['pr_author'] = 'author'
    payload['reviewer'] = 'me'
    payload['comments'][0]['body'] = 'size counts deleted items.'
    f.set_payload(payload)
    refused(f, 'an unlabeled new inline comment on someone else\'s PR is refused', 'unlabeled new inline comment')

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

    f = Fixture(root / 'own-pr')
    payload = f.payload()
    payload['pr_author'] = 'me'
    payload['reviewer'] = 'me'
    payload['comments'][0]['body'] = 'size counts deleted items.'
    f.set_payload(payload)
    proc = f.post('--dry-run')
    check(proc.returncode == 0, f'an unlabeled inline comment on your own PR is not flagged ({proc.stderr.strip()})')


def main() -> None:
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
    print('all post-review checks passed')


if __name__ == '__main__':
    main()
