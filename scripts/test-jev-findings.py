#!/usr/bin/env python3
"""Jev findings gate: cited-code expansion, verification-aware state, executed posts.

No API call. `post_systemone` is replaced by a stub that answers the way the
live judge does on the 2026-10 run: hold when all it has is one line and "no
run was made", post when it has the code and the command output.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
FAILED = 0


def check(ok: bool, name: str) -> None:
    global FAILED
    if ok:
        print(f"ok {name}")
    else:
        FAILED += 1
        print(f"FAIL {name}")


def load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(mod)
    return mod


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"git {args}: {p.stderr}")
    return p.stdout.strip()


RUBY = """\
class Payments
  def refund(order)
    amount = order.total
    fee = order.fee
    if order.partial?
      amount = amount - order.refunded
    end
    ledger.credit(order.account, amount - fee)
    order.update(state: :refunded)
    notify(order)
    amount
  end

  def other
    1
  end
end
"""
CALLER = """\
class Settle
  def run(orders)
    orders.each do |order|
      next unless order.cancelled?
      payments.refund(order)
    end
  end
end
"""
SPEC = """\
describe Payments do
  it 'refunds' do
    Payments.new.refund(order)
  end
end
"""
PY_SRC = """\
def helper():
    return 1


def build(
    name: str,
    size: int,
) -> dict:
    out = {}
    out["name"] = name
    out["size"] = size // 0
    return out
"""
LONG = "def long_one\n" + "".join(f"  step_{i} = {i}\n" for i in range(150)) + "end\n"


def fixture_repo(tmp: Path) -> tuple[Path, str]:
    repo = tmp / "repo"
    (repo / "app").mkdir(parents=True)
    (repo / "spec").mkdir()
    (repo / "app/payments.rb").write_text(RUBY)
    (repo / "app/settle.rb").write_text(CALLER)
    (repo / "spec/payments_spec.rb").write_text(SPEC)
    (repo / "app/build.py").write_text(PY_SRC)
    (repo / "app/long.rb").write_text(LONG)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.test")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo, git(repo, "rev-parse", "HEAD")


def finding(**kw) -> dict:
    base = {
        "id": "f1",
        "claim": "refund of a partial order double-subtracts the fee",
        "file": "app/payments.rb",
        "line": 8,
        "cited_code": "    ledger.credit(order.account, amount - fee)",
        "evidence": "no run was made",
        "repro_executed": False,
        "legs": {"A": "found", "B": "missed"},
    }
    base.update(kw)
    return base


def run_gate(judge, d: Path, repo: Path | None) -> None:
    import contextlib, io
    argv = [str(d)] + (["--repo", str(repo)] if repo else [])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            judge.findings_main(argv)
    except SystemExit as exc:
        check(False, f"findings_main accepts --repo ({exc})")
        with contextlib.redirect_stdout(io.StringIO()):
            judge.findings_main([str(d)])


def test_expansion(tmp: Path) -> None:
    try:
        fc = load("finding_context", "finding_context.py")
    except FileNotFoundError:
        check(False, "scripts/finding_context.py exists")
        return
    repo, head = fixture_repo(tmp)
    doc = {"findings": [finding()]}
    notes = fc.expand_findings(doc, str(repo), head)
    text = doc["findings"][0]["cited_code"]
    lines = text.splitlines()
    check(notes == ["f1: expanded to %d lines" % len(lines)], f"expansion note ({notes})")
    check(len(lines) >= 20, f"cited_code grew to at least 20 lines ({len(lines)})")
    check("def refund(order)" in text and "notify(order)" in text and "amount\n" in text + "\n",
          "cited_code has the whole enclosing method")
    check(re.search(r"^> *8\| ", text, re.M) is not None, "the cited line is marked")
    check("caller of `refund`: app/settle.rb" in text and "payments.refund(order)" in text,
          "one caller is included and a non-test caller is preferred")
    check("spec/payments_spec.rb" not in text, "only one caller is shown")
    check("quoted by the chair:" in text and "ledger.credit(order.account, amount - fee)" in text.split("read from the repo:")[0],
          "the chair's quote is kept")
    check(len(text) <= fc.MAX_CHARS + 400, f"cited_code stays under the size cap ({len(text)})")

    d2 = {"findings": [finding(file="app/build.py", line=11, cited_code='out["size"] = size // 0')]}
    fc.expand_findings(d2, str(repo), head)
    t2 = d2["findings"][0]["cited_code"]
    check("def build(" in t2 and "return out" in t2 and ") -> dict:" in t2, "python function with a multi-line signature")
    check("caller: none found for `build`" in t2, "no caller is reported as such")

    d3 = {"findings": [finding(file="app/long.rb", line=100, cited_code="step_97 = 97")]}
    fc.expand_findings(d3, str(repo), head)
    t3 = d3["findings"][0]["cited_code"]
    check(len(t3.splitlines()) < 110 and "lines not shown" in t3 and "step_97" in t3,
          f"a long function is windowed around the cited line ({len(t3.splitlines())} lines)")

    quoted = "x\n" * 25
    d4 = {"findings": [finding(cited_code=quoted)]}
    notes = fc.expand_findings(d4, str(repo), head)
    check(d4["findings"][0]["cited_code"] == quoted and "kept" in notes[0], "a quote of 20 lines or more is left alone")

    d5 = {"findings": [finding()]}
    notes = fc.expand_findings(d5, str(repo), "0" * 40)
    check(d5["findings"][0]["cited_code"] == finding()["cited_code"] and "not in repo" in notes[0],
          f"unknown head leaves the quote and says why ({notes})")
    d6 = {"findings": [finding(file="app/missing.rb")]}
    notes = fc.expand_findings(d6, str(repo), head)
    check("not readable" in notes[0], f"missing file is a note, not a crash ({notes})")


def stub_jev(judge, seen: list):
    """A judge that holds a thin quote and posts what it can verify from code or output."""

    def post(payload: dict) -> dict:
        seen.append(payload)
        state = payload["state"]
        answers = {}
        for fid, q in payload["questions"].items():
            section = state.split(f"## {fid}\n", 1)[1].split("\n## ", 1)[0]
            executed = "verification: executed" in section
            thin = len(section.split("cited_code:\n", 1)[1].split("\nevidence:", 1)[0].splitlines()) < 5
            exercised_hold = "nobody exercised" in q["criteria"]["hold"]
            if executed and "boom" in section and not exercised_hold:
                answers[fid] = {"choice": "post", "confidence": 0.91}
            elif thin or exercised_hold:
                answers[fid] = {"choice": "hold", "confidence": 0.08}
            else:
                answers[fid] = {"choice": "post", "confidence": 0.8}
        return {"model": "stub", "answers": answers}

    judge.post_systemone = post


def ballot(tmp: Path, name: str, head: str, findings: list, verified_at: str | None) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "header.json").write_text(json.dumps({
        "kind": "review", "branch": "none", "pr": "1", "voter_a_model": "a", "voter_b_model": "b",
        "builder_model": "none", "head_oid": head, "stack": "none"}))
    doc = {"target": "fixture", "findings": findings}
    if verified_at:
        doc["verified_at"] = verified_at
    (d / "findings.json").write_text(json.dumps(doc))
    return d


def test_state_and_gate(tmp: Path) -> None:
    judge = load("jev_judge", "jev-judge.py")
    repo, head = fixture_repo(tmp)
    seen: list = []
    stub_jev(judge, seen)
    executed = finding(
        id="f1",
        evidence="$ ruby repro/f1.rb\nboom: refunded 90, expected 100",
        repro_executed=True,
    )
    read_only = finding(id="f2", line=10, claim="notify runs before the ledger commits")
    d = ballot(tmp, "gate", head, [executed, read_only], head)
    run_gate(judge, d, repo)
    state = seen[0]["state"]
    q = seen[0]["questions"]
    s1 = state.split("## f1\n", 1)[1].split("\n## ", 1)[0]
    s2 = state.split("## f2\n", 1)[1]
    check("verification: executed at head " + head[:8] in s1, "executed finding says it was executed at head")
    check("boom: refunded 90, expected 100" in s1 and "$ ruby repro/f1.rb" in s1, "state carries the command and its output")
    check("no run was made" not in s1, "an executed finding is never described as not run")
    check("verification: checked at head " + head[:8] in s2 and "no runnable repro" in s2, "a read finding says it was checked by reading")
    check(len(s1.split("cited_code:\n", 1)[1].split("\nevidence:", 1)[0].splitlines()) >= 20,
          "cited_code in state is the expanded function and caller")
    check("caller of `refund`" in s1, "the caller is in the state")
    check("nobody exercised" not in json.dumps(q), "no question says nobody exercised the scenario")
    check("was executed" in q["f1"]["instructions"] and "executed" not in q["f2"]["criteria"]["hold"],
          "the executed finding gets the executed question and the read one does not")
    check("output" in q["f1"]["criteria"]["post"] and "not shown in the cited code" in q["f2"]["criteria"]["hold"],
          "criteria match what was done")
    check('"A"' not in state and "missed" not in state and "legs" not in state, "state still carries no leg data")
    text = (d / "judgment.md").read_text()
    check("comments to post (2 post, 0 hold, 0 second look)" in text, f"both reach post ({text.splitlines()[:6]})")
    check((d / "cited-code-notes.txt").is_file() and (d / "cited-code-notes.txt").read_text().count("expanded") == 2, "cited-code-notes.txt lists each finding")
    decide = (d / "decide.md").read_text()
    check("proven by running it" in decide and "read, not run" in decide, "decide.md keeps how-sure per finding")

    # Without --repo nothing is expanded and the note says so.
    seen.clear()
    d = ballot(tmp, "norepo", head, [executed], head)
    run_gate(judge, d, None)
    check((d / "cited-code-notes.txt").is_file() and "no --repo" in (d / "cited-code-notes.txt").read_text(), "no --repo is a note")
    check(seen[0]["state"].count("ledger.credit") == 1, "no --repo leaves the chair's quote as it was")

    # An executed finding with no command or output is rejected.
    bad = ballot(tmp, "bad", head, [finding(repro_executed=True, evidence="no run was made")], head)
    try:
        judge.load_findings(bad / "findings.json")
        check(False, "repro_executed with 'no run was made' is rejected")
    except SystemExit as exc:
        check("holds no command or output" in str(exc), "repro_executed with 'no run was made' is rejected")
    odd = ballot(tmp, "odd", head, [finding()], "not-a-sha")
    try:
        judge.load_findings(odd / "findings.json")
        check(False, "a bad verified_at is rejected")
    except SystemExit as exc:
        check("verified_at" in str(exc), "a bad verified_at is rejected")


def test_old_shape(tmp: Path) -> None:
    """A findings.json without verified_at still works: nothing new is required."""
    judge = load("jev_judge2", "jev-judge.py")
    repo, head = fixture_repo(tmp)
    seen: list = []
    stub_jev(judge, seen)
    d = ballot(tmp, "old", head, [finding()], None)
    run_gate(judge, d, repo)
    s = seen[0]["state"]
    check("verification: none: read-only, no run was made." in s, "no verified_at: the state says so plainly")
    check("def refund(order)" in s, "and the code is still expanded")


TS_METHOD = (
    "export class Payments {\n  refund(order: Order) {\n    if (order.locked) return 0\n"
    + "".join(f"    const pad_{i} = {i}\n" for i in range(25))
    + "    ledger.credit(order.account, amount - fee)\n    return amount\n  }\n}\n"
)


def section_of(state: str, fid: str) -> str:
    return state.split(f"## {fid}\n", 1)[1].split("\n## ", 1)[0]


def test_how_matches_expansion(tmp: Path) -> None:
    """The question and verification line say what cited_code really holds."""
    judge = load("jev_judge3", "jev-judge.py")
    repo, head = fixture_repo(tmp)
    (repo / "app/payments.ts").write_text(TS_METHOD)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "ts")
    head = git(repo, "rev-parse", "HEAD")
    credit = TS_METHOD.splitlines().index("    ledger.credit(order.account, amount - fee)") + 1
    findings = [
        finding(id="fn"),
        finding(id="win", file="app/payments.ts", line=credit, cited_code="ledger.credit(order.account, amount - fee)"),
        finding(id="gone", file="app/missing.rb"),
        finding(id="kept", cited_code="x\n" * 25),
    ]
    for verified_at in (head, None):
        seen: list = []
        stub_jev(judge, seen)
        d = ballot(tmp, f"how-{bool(verified_at)}", head, [dict(f) for f in findings], verified_at)
        run_gate(judge, d, repo)
        q, state = seen[0]["questions"], seen[0]["state"]
        fn, win, gone, kept = (q[k]["instructions"] for k in ("fn", "win", "gone", "kept"))
        check("enclosing function and one caller" in fn, f"function plus caller is named when both were read ({bool(verified_at)})")
        check("enclosing function and one caller" not in win and "no enclosing function was found" in win
              and "enclosing function beyond these lines" in q["win"]["criteria"]["hold"],
              f"a +-10 line window is called a window and the hold names the missing function ({bool(verified_at)})")
        for fid, text in (("gone", gone), ("kept", kept)):
            check("read from the repo" not in text.replace("not read from the repo", "")
                  and "enclosing function" not in text and "repo code around the quote" in q[fid]["criteria"]["hold"],
                  f"{fid}: a chair quote is not described as code read from the repo ({bool(verified_at)})")
        for fid in ("win", "gone", "kept"):
            line = section_of(state, fid).split("verification: ", 1)[1].splitlines()[0]
            check("enclosing function and one caller" not in line
                  and "read from the repo at that commit" not in line,
                  f"{fid}: the verification line follows the expansion result ({line[:90]!r})")
        if verified_at:
            check(fn.startswith("Should") and f"checked at head {head[:8]} by reading" in fn,
                  "a verified read finding says where it was checked")
        else:
            check(all("checked" not in q[k]["instructions"] for k in q),
                  "no verified_at: no question claims the finding was checked")


def test_caller_grep_is_plain(tmp: Path) -> None:
    """color.ui=always, an uppercase head, and a non-ASCII caller path still find the caller."""
    fc = load("finding_context2", "finding_context.py")
    repo, head = fixture_repo(tmp)
    git(repo, "config", "color.ui", "always")
    git(repo, "config", "color.grep", "always")
    doc = {"findings": [finding()]}
    fc.expand_findings(doc, str(repo), head)
    check("caller of `refund`: app/settle.rb" in doc["findings"][0]["cited_code"],
          "color.ui=always does not hide the caller")
    doc = {"findings": [finding()]}
    fc.expand_findings(doc, str(repo), head.upper())
    check("caller of `refund`: app/settle.rb" in doc["findings"][0]["cited_code"],
          "an uppercase head still finds the caller")
    git(repo, "mv", "app/settle.rb", "app/caf\u00e9_caller.rb")
    git(repo, "commit", "-q", "-m", "rename")
    head = git(repo, "rev-parse", "HEAD")
    doc = {"findings": [finding()]}
    fc.expand_findings(doc, str(repo), head)
    text = doc["findings"][0]["cited_code"]
    check("caller of `refund`: app/caf\u00e9_caller.rb" in text and "payments.refund(order)" in text
          and "not readable" not in text, "a non-ASCII caller path is read back")


def test_signature_walk_stops_at_definition() -> None:
    fc = load("finding_context3", "finding_context.py")
    src = 'def format_token(prefix="("):\n    return prefix + "x"\n\ndef innocent():\n    return 1\n'.splitlines()
    got = fc.enclosing_def(src, 2)
    check(got is not None and got[2] == "format_token" and got[1] < 3,
          f"a paren inside a string does not swallow the next function ({got})")
    check(fc.enclosing_def(src, 5) is not None and fc.enclosing_def(src, 5)[2] == "innocent",
          "the next function is its own definition")


def test_pytest_caller_ranks_last(tmp: Path) -> None:
    fc = load("finding_context4", "finding_context.py")
    repo = tmp / "py"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/refunds.py").write_text("def refund(order):\n    return order\n")
    (repo / "pkg/test_refund.py").write_text("from pkg.refunds import x\nrefund(1)\n")
    (repo / "pkg/util.py").write_text("import x\n\n\nrefund(2)\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "-c", "user.email=t@e", "-c", "user.name=T", "add", ".")
    git(repo, "-c", "user.email=t@e", "-c", "user.name=T", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "f")
    head = git(repo, "rev-parse", "HEAD")
    doc = {"findings": [finding(file="pkg/refunds.py", line=2, cited_code="return order")]}
    fc.expand_findings(doc, str(repo), head)
    text = doc["findings"][0]["cited_code"]
    check("caller of `refund`: pkg/util.py" in text, f"a pytest test_*.py caller ranks after product code ({re.findall(r'caller of .*', text)})")


def test_line_outside_file(tmp: Path) -> None:
    fc = load("finding_context5", "finding_context.py")
    repo, head = fixture_repo(tmp)
    doc = {"findings": [finding(line=500)]}
    notes = fc.expand_findings(doc, str(repo), head)
    n = len(RUBY.splitlines())
    check(doc["findings"][0]["cited_code"] == finding()["cited_code"]
          and notes == [f"f1: not expanded (line 500 is outside the file ({n} lines))"],
          f"a cited line past the end keeps the chair quote and says why ({notes})")
    doc = {"findings": [finding(line=0)]}
    notes = fc.expand_findings(doc, str(repo), head)
    check("line 0 is outside the file" in notes[0], f"line 0 is outside the file too ({notes})")


def test_cap_after_quote(tmp: Path) -> None:
    fc = load("finding_context6", "finding_context.py")
    repo, head = fixture_repo(tmp)
    (repo / "app/wide.rb").write_text("def wide\n" + "".join(f"  v{i} = '{'w' * 90}'\n" for i in range(75)) + "end\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "wide")
    head = git(repo, "rev-parse", "HEAD")
    quote = "".join(f"  q{i} {'q' * 76}\n" for i in range(19))
    doc = {"findings": [finding(file="app/wide.rb", line=40, cited_code=quote)]}
    fc.expand_findings(doc, str(repo), head)
    got = len(doc["findings"][0]["cited_code"])
    check(got <= fc.MAX_CHARS, f"the cap holds after the chair quote is prepended ({got} chars)")
    doc = {"findings": [finding(cited_code=("k" * 400 + "\n") * 25)]}
    fc.expand_findings(doc, str(repo), head)
    got = len(doc["findings"][0]["cited_code"])
    check(got <= fc.MAX_CHARS, f"a kept quote of 20+ lines is capped too ({got} chars)")


def small_repo(repo: Path, files: dict[str, str]) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=t@e", "-c", "user.name=T", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "f")
    return git(repo, "rev-parse", "HEAD")


def test_caller_suffix_is_another_method(tmp: Path) -> None:
    """save! and save? are other methods than save; a ?. optional call is still a call."""
    fc = load("finding_context7", "finding_context.py")
    head = small_repo(tmp, {
        "app/orders.rb": "class Orders\n  def save(order)\n    order\n  end\nend\n",
        "app/a_bang.rb": "orders.save!(order)\n",
        "app/b_pred.rb": "orders.save?(order)\n",
        "app/c_set.rb": "orders.save=(order)\n",
        "app/d_call.rb": "orders.save(order)\n",
    })
    doc = {"findings": [finding(file="app/orders.rb", line=3, cited_code="order")]}
    fc.expand_findings(doc, str(tmp), head)
    callers = re.findall(r"caller of .*", doc["findings"][0]["cited_code"])
    check(callers == ["caller of `save`: app/d_call.rb:1-1"],
          f"save!, save? and save= are not callers of save ({callers})")
    other = tmp / "other"
    other.mkdir()
    head = small_repo(other, {
        "lib/defs.rb": "def flush!(x)\n  x\nend\ndef empty?(x)\n  x\nend\ndef refund(x)\n  x\nend\n",
        "lib/a.rb": "q.flush(x)\nitems.empty(x)\n",
        "lib/b.rb": "q.flush!(x)\nitems.empty?(x)\npayments.refund?.(x)\n",
    })
    got = []
    for line in (2, 5, 8):
        doc = {"findings": [finding(file="lib/defs.rb", line=line, cited_code="x")]}
        fc.expand_findings(doc, str(other), head)
        got += re.findall(r"caller of `[^`]+`: \S+", doc["findings"][0]["cited_code"])
    check(got == ["caller of `flush!`: lib/b.rb:1-3", "caller of `empty?`: lib/b.rb:1-3",
                  "caller of `refund`: lib/b.rb:1-3"],
          f"a name ending in ! or ? matches itself only, and ?. is a call ({got})")


def test_signature_walk_stops_at_dedent() -> None:
    fc = load("finding_context8", "finding_context.py")
    src = 'def format_token(prefix="("):\n    return prefix + "x"\nLIMIT = 2\ndef innocent():\n    return 1\n'.splitlines()
    check(fc.enclosing_def(src, 3) is None, f"a top-level line after an unbalanced signature is outside ({fc.enclosing_def(src, 3)})")
    got = fc.enclosing_def(src, 2)
    check(got is not None and got[2] == "format_token" and got[1] == 1, f"the return line is still in format_token ({got})")
    cls = 'class C:\n    def format_token(self, prefix="("):\n        return prefix\n    LIMIT = 2\n'.splitlines()
    check(fc.enclosing_def(cls, 4) is None, f"a class attribute after the method is not in it ({fc.enclosing_def(cls, 4)})")
    black = "def build(\n    name: str,\n) -> dict:\n    return {}\nKEEP = 1\n".splitlines()
    check(fc.enclosing_def(black, 4)[2] == "build" and fc.enclosing_def(black, 5) is None,
          "a Black-style signature closing with ) still continues")
    body = 'def f(prefix="("):\n    return (\n        1\n    )\n'.splitlines()
    check(fc.enclosing_def(body, 3) is not None and fc.enclosing_def(body, 3)[2] == "f", "an indented body stays in f")


def long_with_caller(tmp: Path, payload: int) -> tuple[Path, str]:
    body = "".join(f"  step_{i} = {'w' * payload}\n" for i in range(150))
    head = small_repo(tmp, {
        "app/long.rb": "def long_one\n" + body + "end\n",
        "app/use.rb": "".join(f"x{i} = {i}\n" for i in range(6)) + "long_one\n" + "".join(f"y{i} = {i}\n" for i in range(6)),
    })
    return tmp, head


def test_cap_keeps_mark_and_says_caller_dropped(tmp: Path) -> None:
    fc = load("finding_context9", "finding_context.py")
    for payload in (72, 60):
        repo, head = long_with_caller(tmp / str(payload), payload)
        doc = {"findings": [finding(file="app/long.rb", line=141, cited_code="  step_139 = w")]}
        fc.expand_findings(doc, str(repo), head)
        f = doc["findings"][0]
        text = f["cited_code"]
        has_caller = "caller of `long_one`" in text
        check(len(text) <= fc.MAX_CHARS and ">141| " in text,
              f"payload {payload}: the cited line keeps its > mark under the cap ({len(text)} chars)")
        check(has_caller == (f["cited_context"] == fc.FUNCTION_AND_CALLER) and f["cited_context"] == fc.FUNCTION_ONLY,
              f"payload {payload}: a caller cut at the cap is not claimed ({f['cited_context']}, caller shown {has_caller})")
    quote = "".join(f"  q{i} {'q' * 400}\n" for i in range(19))
    doc = {"findings": [finding(file="app/long.rb", line=141, cited_code=quote)]}
    fc.expand_findings(doc, str(repo), head)
    text = doc["findings"][0]["cited_code"]
    check(len(text) <= fc.MAX_CHARS and ">141| " in text and "quoted by the chair" in text,
          f"a long chair quote is shortened before the marked line is lost ({len(text)} chars)")


def test_def_any_receiver() -> None:
    fc = load("finding_context10", "finding_context.py")
    got = [fc._def_name(l) for l in ("def Payments.refund(order)", "def self.refund(order)", "def refund(order)",
                                     "async def refund(order)", "def refund?(order)", "def save!(order)")]
    check(got == ["refund", "refund", "refund", "refund", "refund?", "save!"], f"a constant receiver is not the name ({got})")


def test_head_and_verified_at_must_agree(tmp: Path) -> None:
    judge = load("jev_judge_heads", "jev-judge.py")
    repo, head = fixture_repo(tmp)
    seen: list = []
    stub_jev(judge, seen)
    d = ballot(tmp, "mismatch", "a" * 40, [finding()], "b" * 40)
    import contextlib, io
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            judge.findings_main([str(d), "--repo", str(repo)])
        refused = ""
    except SystemExit as exc:
        refused = str(exc.code)
    check("aaaaaaaa" in refused and "bbbbbbbb" in refused and not seen
          and not (d / "jev-findings-request.json").exists(),
          f"head_oid and verified_at that differ are refused before the API call ({refused!r})")
    d = ballot(tmp, "case", head.upper(), [finding()], head)
    run_gate(judge, d, repo)
    state = seen[-1]["state"] if seen else ""
    check(f"checked at head {head[:8]} by reading" in state and f"at head {head[:8]}" in state,
          "a head_oid that differs only in case is the same commit, and the line names it")


def main() -> None:
    os.environ.setdefault("CONCLAVE_JEV_OP", "/bin/false")
    with tempfile.TemporaryDirectory(prefix="jev-findings-") as raw:
        tmp = Path(raw)
        (tmp / "a").mkdir(); (tmp / "b").mkdir(); (tmp / "c").mkdir()
        test_expansion(tmp / "a")
        test_state_and_gate(tmp / "b")
        test_old_shape(tmp / "c")
        for name in ("d", "e", "f", "g", "h"):
            (tmp / name).mkdir()
        test_how_matches_expansion(tmp / "d")
        test_caller_grep_is_plain(tmp / "e")
        test_signature_walk_stops_at_definition()
        test_pytest_caller_ranks_last(tmp / "f")
        test_line_outside_file(tmp / "g")
        test_cap_after_quote(tmp / "h")
        for name in ("i", "j", "k"):
            (tmp / name).mkdir()
        test_caller_suffix_is_another_method(tmp / "i")
        test_signature_walk_stops_at_dedent()
        test_cap_keeps_mark_and_says_caller_dropped(tmp / "j")
        test_def_any_receiver()
        test_head_and_verified_at_must_agree(tmp / "k")
    print(f"{'FAILED' if FAILED else 'all passed'}: {FAILED} failed")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
