#!/usr/bin/env python3
"""Offline checks for finding_anchor.py: severity labels, resolve(), and the CLI.

gh is a PATH stub that logs every call and never reaches the network. The
stack fixture is synthetic: a method first added in PR 3 at line 126, and
PR 1 changes the same file with fewer lines.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import finding_anchor  # noqa: E402


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# PR 1 adds a short method near the top of app/svc.rb.
PR1_SVC = "@@ -1,2 +1,4 @@\n class Svc\n+  def a\n+  end\n end\n"
# PR 2 changes only lib/x.rb.
PR2_X = "@@ -1,1 +1,2 @@\n module X\n+  LIMIT = 3\n"
# PR 3 first adds stale_total, at line 126 of its own head.
PR3_SVC = (
    "@@ -124,2 +124,5 @@\n   def b\n   end\n+  def stale_total\n+    rows.sum\n+  end\n"
)
STACK = [
    {"number": 1, "files": {"app/svc.rb": PR1_SVC}},
    {"number": 2, "files": {"lib/x.rb": PR2_X}},
    {"number": 3, "files": {"app/svc.rb": PR3_SVC}},
]


def test_labels() -> None:
    want = {
        "blocking": "**Requested:**",
        "should-fix": "**Requested:**",
        "nit": "**Advisory:**",
        "usability": "**Advisory:**",
        "fyi": "**Informational:**",
    }
    for severity, label in want.items():
        got = finding_anchor.label_for(severity)
        check(got == label, f"severity {severity} maps to {label} ({got})")
    for severity in ("Should Fix", "should_fix", "BLOCKING", "FYI", " nit "):
        check(finding_anchor.label_for(severity) is not None,
              f"severity {severity!r} is case, space, and underscore tolerant")
    for severity in ("medium", "High", "", None, 3):
        check(finding_anchor.label_for(severity) is None, f"severity {severity!r} maps to None")


def test_right_lines_moved() -> None:
    post_review = load("post-review")
    check(post_review.right_lines is finding_anchor.right_lines,
          "post-review keeps importing right_lines from finding_anchor")
    check(finding_anchor.right_lines(PR3_SVC)[126] == "  def stale_total",
          "right_lines counts PR 3's own lines")


def test_resolve() -> None:
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 2, "cited_code": "\n  def a\n  end"}, STACK, 1
    )
    check(got["post_as"] == "inline" and got["pr"] == 1 and got["line"] == 2 and not got.get("weak"),
          f"inline when the line is in the PR's diff and the cited code matches ({got})")

    weak = finding_anchor.resolve({"file": "app/svc.rb", "line": 3}, STACK, 1)
    check(weak["post_as"] == "inline" and weak["pr"] == 1 and weak.get("weak") is True,
          f"no cited code checks only the line and is weak ({weak})")

    tip = {"file": "app/svc.rb", "line": 126, "cited_code": "def stale_total"}
    got = finding_anchor.resolve(tip, STACK, 1)
    check(got["post_as"] == "inline" and got["pr"] == 3 and got["line"] == 126
          and got["reason"] == "re-anchored: first introduced in #3",
          f"a stack-tip line is re-anchored to the PR that first adds it ({got})")
    check(got["pr"] != 1, "the stack-tip line is never pinned to the earlier PR")
    off = finding_anchor.resolve({**tip, "line": 999}, STACK, 1)
    check(off["pr"] == 3 and off["line"] == 126,
          f"the re-anchored line is PR 3's own line, not the cited one ({off})")
    on_three = finding_anchor.resolve(tip, STACK, 3)
    check(on_three["post_as"] == "inline" and on_three["pr"] == 3 and on_three["line"] == 126,
          f"on PR 3 the same finding is plain inline ({on_three})")

    got = finding_anchor.resolve({"file": "app/svc.rb", "line": 50, "cited_code": "def a"}, STACK, 1)
    check(got["post_as"] == "file" and got["pr"] == 1 and got["line"] is None
          and "not in this PR's diff" in got["reason"],
          f"file-level when the line is not in the PR's diff but the file is ({got})")
    got = finding_anchor.resolve({"file": "app/svc.rb", "line": 2, "cited_code": "def zzz"}, STACK, 1)
    check(got["post_as"] == "file" and "does not hold the cited code" in got["reason"],
          f"file-level when the diff line does not hold the cited code ({got})")

    got = finding_anchor.resolve({"file": "lib/x.rb", "line": 9, "cited_code": "nothing here"}, STACK, 1)
    check(got["post_as"] == "flag" and got["pr"] is None and got["line"] is None and "#2" in got["reason"],
          f"flag when only a later PR changes the file and the line cannot be matched ({got})")
    got = finding_anchor.resolve({"file": "lib/x.rb", "line": 9, "cited_code": "LIMIT = 3"}, STACK, 1)
    check(got["post_as"] == "inline" and got["pr"] == 2 and got["line"] == 2,
          f"a later PR's file with the cited code re-anchors there ({got})")

    got = finding_anchor.resolve({"file": "README.md", "line": 1, "cited_code": "x"}, STACK, 1)
    check(got["post_as"] == "unverified" and "no PR's diff" in got["reason"],
          f"unverified when the file is in no PR's diff ({got})")
    got = finding_anchor.resolve({"file": "app/svc.rb", "line": 2}, [], 1)
    check(got["post_as"] == "unverified" and got["reason"] == "patches unavailable",
          f"unverified when patches are unavailable ({got})")
    got = finding_anchor.resolve({"line": 2}, STACK, 1)
    check(got["post_as"] == "unverified", f"unverified with no file ({got})")


def test_how_it_posts() -> None:
    finding = {"id": "f1", "file": "app/svc.rb", "line": 126}
    plain = finding_anchor.how_it_posts(finding)
    check("inline review comment on app/svc.rb:126 by default" in plain
          and "file-level comment on app/svc.rb when that line is not in this PR's diff" in plain
          and "**Requested:**" in plain and "never the summary or a reply" in plain,
          f"default how-it-posts text ({plain})")
    moved = finding_anchor.how_it_posts(finding, finding_anchor.resolve(
        {**finding, "cited_code": "def stale_total"}, STACK, 1))
    check(moved.startswith("re-anchored to #3 app/svc.rb:126"), f"re-anchored plan text ({moved})")
    flag = finding_anchor.how_it_posts(finding, {"post_as": "flag", "pr": None, "reason": "r"})
    check(flag.startswith("flag for the chair"), f"flag plan text ({flag})")
    unv = finding_anchor.how_it_posts(finding, {"post_as": "unverified", "pr": 1, "reason": "gh: down"})
    check(unv.startswith("unverified because gh: down"), f"unverified plan text ({unv})")


GH = '''\
#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['GH_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
if os.environ.get('GH_FAIL'):
    sys.stderr.write('HTTP 502 Bad Gateway\\n')
    sys.exit(1)
m = re.fullmatch(r'repos/example/widgets/pulls/(\\d+)/files', args[1] if len(args) > 1 else '')
if not m or '--paginate' not in args:
    sys.exit('unexpected gh call ' + ' '.join(args))
print(Path(os.environ['GH_PATCHES'], m.group(1) + '.json').read_text())
'''


def test_cli(tmp: Path) -> None:
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(textwrap.dedent(GH), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    patches = tmp / "patches"
    patches.mkdir()
    for pr in STACK:
        rows = [{"filename": name, "patch": text} for name, text in pr["files"].items()]
        (patches / f"{pr['number']}.json").write_text(json.dumps(rows), encoding="utf-8")
    log = tmp / "calls.jsonl"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GH_LOG": str(log), "GH_PATCHES": str(patches)}
    env.pop("CONCLAVE_GH", None)
    env.pop("COUNCIL_GH", None)

    ballot = tmp / "ballot"
    ballot.mkdir()
    (ballot / "judgment.md").write_text(
        "kind: review\npr: https://github.com/example/widgets/pull/1\n", encoding="utf-8")
    (ballot / "findings.json").write_text(json.dumps({"findings": [
        {"id": "f1", "file": "app/svc.rb", "line": 126, "cited_code": "def stale_total"},
        {"id": "f2", "file": "app/svc.rb", "line": 2, "cited_code": "def a"},
    ]}), encoding="utf-8")
    (ballot / "stack.json").write_text(json.dumps({"status": "stack", "prs": [
        {"number": 1, "target": True}, {"number": 2}, {"number": 3}]}), encoding="utf-8")

    def run() -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPTS / "finding_anchor.py"), str(ballot)],
                              env=env, text=True, capture_output=True)

    proc = run()
    anchors = json.loads((ballot / "anchors.json").read_text()) if (ballot / "anchors.json").is_file() else {}
    check(proc.returncode == 0 and anchors.get("f1", {}).get("pr") == 3
          and anchors.get("f1", {}).get("line") == 126 and anchors.get("f2", {}).get("post_as") == "inline",
          f"cli writes anchors.json from each stack PR's own diff ({proc.returncode} {proc.stderr.strip()} {anchors})")
    lines = proc.stdout.strip().splitlines()
    check(len(lines) == 2 and lines[0].startswith("f1 inline #3 app/svc.rb:126"),
          f"cli prints one line per finding ({lines})")
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    check([c[1] for c in calls] == [f"repos/example/widgets/pulls/{n}/files" for n in (1, 2, 3)],
          f"cli fetches every stack PR bottom to top ({calls})")

    (ballot / "stack.json").unlink()
    log.unlink()
    proc = run()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    anchors = json.loads((ballot / "anchors.json").read_text())
    check(proc.returncode == 0 and [c[1] for c in calls] == ["repos/example/widgets/pulls/1/files"]
          and anchors["f1"]["post_as"] == "file",
          f"with no stack the stack is the target PR alone ({calls} {anchors['f1']})")

    proc = subprocess.run([sys.executable, str(SCRIPTS / "finding_anchor.py"), str(ballot)],
                          env={**env, "GH_FAIL": "1"}, text=True, capture_output=True)
    anchors = json.loads((ballot / "anchors.json").read_text())
    check(proc.returncode == 0 and all(a["post_as"] == "unverified" and "502" in a["reason"]
                                       for a in anchors.values()) and len(anchors) == 2,
          f"a gh failure marks every finding unverified and exits 0 ({proc.returncode} {anchors})")


def test_exact_added_lines_only() -> None:
    """The gate's finding 1: re-anchoring matched context lines and substrings."""
    # PR 3 only edits near stale_total, so stale_total is a context line there.
    context_only = "@@ -8,6 +8,6 @@\n   def stale_total\n     rows.sum\n   end\n-  old()\n+  new()\n"
    stack = [
        {"number": 1, "files": {"app/svc.rb": PR1_SVC}},
        {"number": 3, "files": {"app/svc.rb": context_only}},
    ]
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 126, "cited_code": "def stale_total"}, stack, 1)
    check(got["post_as"] == "file" and got["pr"] == 1,
          f"a context line in a later PR is not where the code was introduced ({got})")

    check(finding_anchor.added_lines(PR3_SVC)[-1] == (128, "  end"),
          "added_lines lists + lines only, at their own numbers")
    other = [
        {"number": 2, "files": {"lib/x.rb": PR2_X}},
        {"number": 3, "files": {"app/svc.rb": PR3_SVC}},
    ]
    got = finding_anchor.resolve({"file": "app/svc.rb", "line": 130, "cited_code": "end"}, other, 2)
    check(got["post_as"] == "inline" and got["pr"] == 3 and got["line"] == 128,
          f"cited code that is `end` lands on the added end, not the context end ({got})")

    apple = "@@ -1,1 +1,2 @@\n class Svc\n+  def apple\n"
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 2, "cited_code": "def a"},
        [{"number": 1, "files": {"app/svc.rb": apple}}], 1)
    check(got["post_as"] == "file" and "does not hold the cited code" in got["reason"],
          f"def a is not def apple on the cited line ({got})")
    count = "@@ -1,1 +1,2 @@\n class Svc\n+  items.count_deleted\n"
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 9, "cited_code": "items.count"},
        [{"number": 1, "files": {"app/svc.rb": PR1_SVC}}, {"number": 2, "files": {"app/svc.rb": count}}], 1)
    check(got["post_as"] != "inline" or got["pr"] != 2,
          f"items.count is not first introduced by a line that is items.count_deleted ({got})")

    twice = "@@ -1,1 +1,4 @@\n class Svc\n+  def dup\n+  end\n+  def dup\n"
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 40, "cited_code": "def dup"},
        [{"number": 1, "files": {"lib/x.rb": PR2_X}}, {"number": 2, "files": {"app/svc.rb": twice}}], 1)
    check(got["post_as"] == "flag" and "2 times" in got["reason"],
          f"an added line that occurs twice is flagged, not guessed ({got})")

    no_text = [
        {"number": 1, "files": {"app/svc.rb": ""}},
        {"number": 2, "files": {"lib/x.rb": PR2_X}},
        {"number": 3, "files": {"app/svc.rb": PR3_SVC}},
    ]
    got = finding_anchor.resolve(
        {"file": "app/svc.rb", "line": 126, "cited_code": "def stale_total"}, no_text, 2)
    check(got["post_as"] == "flag" and "no diff text" in got["reason"],
          f"a PR whose file has no diff text is not skipped past ({got})")


def test_target_missing_from_stack(tmp: Path) -> None:
    """The gate's finding 4: stack.json without the target made it the tip."""
    ballot = tmp / "missing-target"
    ballot.mkdir()
    (ballot / "stack.json").write_text(json.dumps({"status": "stack", "prs": [
        {"number": 2}, {"number": 3}]}), encoding="utf-8")
    check(finding_anchor.stack_numbers(ballot, 1) == [1],
          f"a target stack.json does not list is not appended as the tip ({finding_anchor.stack_numbers(ballot, 1)})")
    why = finding_anchor.stack_without_target(ballot, 1)
    check(why == "stack.json lists #2, #3 but not #1", f"the missing target is named ({why})")
    check(finding_anchor.stack_without_target(ballot, 2) is None, "a listed target is placed")

    (ballot / "judgment.md").write_text(
        "kind: review\npr: https://github.com/example/widgets/pull/1\n", encoding="utf-8")
    (ballot / "findings.json").write_text(json.dumps({"findings": [
        {"id": "f1", "file": "app/svc.rb", "line": 126, "cited_code": "def stale_total"}]}), encoding="utf-8")
    bin_dir = tmp / "bin-missing"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(textwrap.dedent(GH), encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    patches = tmp / "patches-missing"
    patches.mkdir()
    for pr in STACK:
        rows = [{"filename": name, "patch": text} for name, text in pr["files"].items()]
        (patches / f"{pr['number']}.json").write_text(json.dumps(rows), encoding="utf-8")
    log = tmp / "calls-missing.jsonl"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
           "GH_LOG": str(log), "GH_PATCHES": str(patches)}
    env.pop("CONCLAVE_GH", None)
    env.pop("COUNCIL_GH", None)
    proc = subprocess.run([sys.executable, str(SCRIPTS / "finding_anchor.py"), str(ballot)],
                          env=env, text=True, capture_output=True)
    anchors = json.loads((ballot / "anchors.json").read_text()) if (ballot / "anchors.json").is_file() else {}
    check(proc.returncode == 0 and anchors.get("f1", {}).get("post_as") == "unverified"
          and "but not #1" in anchors["f1"]["reason"] and not log.exists(),
          f"the cli marks the ballot unverified and fetches nothing ({proc.returncode} {proc.stderr.strip()} {anchors})")


def main() -> None:
    test_labels()
    test_right_lines_moved()
    test_resolve()
    test_how_it_posts()
    test_exact_added_lines_only()
    with tempfile.TemporaryDirectory(prefix="finding-anchor-tests-") as raw:
        test_cli(Path(raw))
        test_target_missing_from_stack(Path(raw))
    print("all finding-anchor checks passed")


if __name__ == "__main__":
    main()
