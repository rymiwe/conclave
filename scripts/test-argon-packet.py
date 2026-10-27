#!/usr/bin/env python3
"""Argon review packet budget: size, priority, partial marker, split, PR context.

Synthetic diffs of 50 KB, 200 KB and 1 MB go through review_run_argon with
run_argon stubbed. No agy call is made.
"""

import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COUNCIL = ROOT / "scripts" / "council.sh"
MAX = 150000

passed = 0
failed = 0


def check(cond, name):
    global passed, failed
    if cond:
        passed += 1
        print(f"ok {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


def body_for(path: str, size: int) -> str:
    line = f"    value_{path.replace('/', '_')} = compute(argument, 12345) # padding\n"
    return line * max(1, size // len(line))


def make_repo(work: Path, files: dict[str, int]) -> Path:
    """A worktree whose `git diff HEAD` is one modified file per entry, about size bytes each."""
    repo = work / "wt"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "base"], check=True, env=env)
    for rel, size in files.items():
        (repo / rel).write_text(body_for(rel, size), encoding="utf-8")
    return repo


def synthetic(total: int, with_generated: bool = False, gen_pct: int = 15) -> dict[str, int]:
    """Ten source files (60%), ten spec files (40%), optionally a schema (gen_pct of the total)."""
    gen = total * gen_pct // 100 if with_generated else 0
    rest = total - gen
    src, spec = rest * 60 // 100, rest * 40 // 100
    files = {f"app/models/model_{i:02d}.rb": src // 10 for i in range(10)}
    files.update({f"spec/models/model_{i:02d}_spec.rb": spec // 10 for i in range(10)})
    if with_generated:
        files["db/schema.rb"] = gen
    return files


def run_bash(body: str, extra_env=None, cwd=None):
    env = os.environ.copy()
    for key in list(env):
        if "ARGON" in key:
            env.pop(key)
    env["CONCLAVE_JEV_OP"] = "/bin/false"
    if extra_env:
        env.update(extra_env)
    head = f"set -u\nexport COUNCIL_SOURCE_ONLY=1\n. {COUNCIL}\nset +e\n"
    return subprocess.run(["bash", "-c", head + body], text=True, capture_output=True, env=env, cwd=cwd)


STUB = r'''
run_argon() {
  CALLS=$(( ${CALLS:-0} + 1 ))
  wc -c <"$PACKET/PROMPT.md" | tr -d ' ' >>"$DIR/sizes"
  if [ "${FAIL_PACKET:-0}" = "$CALLS" ]; then
    argon_skip "agy: stub failure on packet $CALLS"
    return 0
  fi
  printf 'FINDING-FROM-CALL-%s: a reviewer finding\n' "$CALLS" >"$DIR/voter-argon.md"
  printf 'argon: ok model=stub\n' >"$DIR/voter-argon.model.txt"
}
'''


def run_leg(work: Path, files: dict[str, int], extra_env=None, fail_packet=0, brief_bytes=6000,
            seed=None, git_config=None):
    repo = make_repo(work, files)
    for key, value in (git_config or {}).items():
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    ballot = work / "ballot"
    ballot.mkdir()
    for name, text in (seed or {}).items():
        (ballot / name).write_text(text, encoding="utf-8")
    (ballot / "voter-argon.brief.md").write_text("Review brief. " + "x" * brief_bytes + "\n", encoding="utf-8")
    tmp = work / "tmp"
    tmp.mkdir()
    env = {"TMPDIR": str(tmp), "FAIL_PACKET": str(fail_packet)}
    env.update(extra_env or {})
    proc = run_bash(STUB + f"""
DIR={ballot}
ID=pkt
REPO={repo}
ERRORS_FILE={ballot}/errors.md
: >"$ERRORS_FILE"
DRY_RUN=0
KIND=review
review_run_argon {repo}
echo rc=$?
""", env)
    return ballot, proc


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def prompts(ballot: Path):
    out = []
    for name in ("voter-argon.prompt.md", "voter-argon.prompt.2.md"):
        if (ballot / name).is_file():
            out.append(read(ballot / name))
    return out


def test_small(work: Path):
    ballot, proc = run_leg(work, synthetic(50_000))
    ps = prompts(ballot)
    sizes = [int(x) for x in read(ballot / "sizes").split()]
    check("rc=0" in proc.stdout and len(ps) == 1 and sizes and sizes[0] <= MAX,
          f"50 KB diff runs as one packet under the cap ({sizes})")
    check(all(f"app/models/model_{i:02d}.rb" in ps[0] for i in range(10))
          and all(f"model_{i:02d}_spec.rb" in ps[0] for i in range(10)),
          "50 KB packet carries every file")
    check(not (ballot / "voter-argon.partial").exists(), "50 KB writes no .partial")
    log = read(ballot / "voter-argon.packet.txt")
    check(re.search(r"mode=whole full_bytes=\d+ max_bytes=150000 packets=1 packet_bytes=\d+ files_cut=0", log) is not None,
          f"packet bytes are logged ({log.strip()!r})")
    check("WARNING" not in read(ballot / "errors.md") and "Packet coverage" not in ps[0],
          "50 KB has no warning and no coverage notice")


def test_split_clean(work: Path):
    ballot, proc = run_leg(work, synthetic(200_000))
    ps = prompts(ballot)
    sizes = [int(x) for x in read(ballot / "sizes").split()]
    check(len(ps) == 2 and len(sizes) == 2 and all(s <= MAX for s in sizes),
          f"200 KB diff splits into two packets, each under the cap ({sizes})")
    src_names = [f"app/models/model_{i:02d}.rb" for i in range(10)]
    spec_names = [f"spec/models/model_{i:02d}_spec.rb" for i in range(10)]
    if len(ps) == 2:
        check(all(n in ps[0] for n in src_names) and not any(n in ps[0] for n in spec_names),
              "packet 1 is the source diff, with no spec")
        check(all(n in ps[1] for n in spec_names) and not any(n in ps[1] for n in src_names),
              "packet 2 is the spec and test diff, with no source")
        check("packet 2 and are not shown here" in ps[0] and "packet 1 and are not shown here" in ps[1],
              "each packet says where the other half is")
    else:
        check(False, "packet 1 is the source diff")
        check(False, "packet 2 is the spec diff")
        check(False, "each packet names the other half")
    check(not (ballot / "voter-argon.partial").exists(),
          "a split that cuts nothing writes no .partial")
    merged = read(ballot / "voter-argon.md")
    check("## Packet 1 of 2: the changed source files" in merged
          and "## Packet 2 of 2: the specs and tests" in merged
          and merged.index("FINDING-FROM-CALL-1") < merged.index("## Packet 2 of 2")
          and merged.index("## Packet 2 of 2") < merged.index("FINDING-FROM-CALL-2"),
          "merged report keeps each finding under its packet")
    check("## Packet 2 of 2" in merged and read(ballot / "voter-argon.model.txt").startswith("argon: ok")
          and not (ballot / "voter-argon.skipped").exists(),
          "merged split leaves the model proof and no skip marker")
    summary = run_bash(f"""
DIR={ballot}
ID=pkt
REVIEW_REPORTS_PRESENT=2
REVIEW_REPORTS_EXPECTED=2
review_count_reports() {{ :; }}
review_print_result 2>&1 | grep -i 'split in two'
""").stdout
    check("split in two" in summary and "nothing cut" in summary, f"summary notes the clean split ({summary.strip()[:90]!r})")


def test_split_with_generated(work: Path):
    ballot, proc = run_leg(work, synthetic(200_000, with_generated=True, gen_pct=40))
    ps = prompts(ballot)
    partial = read(ballot / "voter-argon.partial")
    check(len(ps) == 2 and "db/schema.rb" in partial and "(generated; not shown" in partial,
          "200 KB with a schema: the generated file is the one cut")
    check("app/models/model_09.rb" in ps[0] and "spec/models/model_09_spec.rb" in ps[1],
          "200 KB with a schema: source and tests are all kept")


def test_large(work: Path):
    ballot, proc = run_leg(work, synthetic(1_000_000, with_generated=True))
    ps = prompts(ballot)
    sizes = [int(x) for x in read(ballot / "sizes").split()]
    check(len(ps) == 2 and all(s <= MAX for s in sizes), f"1 MB diff: two packets, each under the cap ({sizes})")
    partial = read(ballot / "voter-argon.partial")
    check(partial.startswith("WARNING: argon saw a PARTIAL review packet"), f"1 MB writes .partial with the warning ({partial[:60]!r})")
    check("CONCLAVE_ARGON_MAX_BYTES" in partial and "190 KB" in partial, "warning names the cap setting and the 190 KB limit")
    errors = read(ballot / "errors.md")
    check("note: WARNING: argon saw a PARTIAL review packet" in errors and "db/schema.rb" in errors,
          "the warning is in the ballot errors.md")
    check(bool(errors.strip()) and all(l.startswith("note:") for l in errors.splitlines() if l.strip()),
          "every errors.md line is a note, so the ballot does not fail")
    cut = re.findall(r"^- (\S+) \(", partial, re.M)
    check(cut and all(c in ps[0] or c in ps[1] for c in cut) and len(set(cut)) == len(cut),
          f"the packets name what was cut, {len(cut)} files")
    named_in_packet = bool(cut) and all(("- " + c + " (") in ps[0] + ps[1] for c in cut)
    check(named_in_packet, "every file in .partial is also named inside a packet")
    check("db/schema.rb" in cut, "generated files are cut first")
    shown_src = [i for i in range(10) if f"+++ b/app/models/model_{i:02d}.rb" in ps[0]]
    shown_spec = [i for i in range(10) if f"+++ b/spec/models/model_{i:02d}_spec.rb" in ps[1]]
    check(shown_src and shown_spec and shown_src == sorted(shown_src) and "+++ b/db/schema.rb" not in ps[0] + ps[1],
          f"source ({len(shown_src)} files) and specs ({len(shown_spec)} files) are kept before generated")
    check(not any("spec/" in l for l in re.findall(r"^\+\+\+ b/(\S+)", ps[0], re.M)),
          "no spec diff rides in the source packet")
    report = read(ballot / "voter-argon.md")
    check(report.startswith("> WARNING: argon saw a PARTIAL review packet") and "FINDING-FROM-CALL-2" in report,
          "the report opens with the warning and keeps both packets")
    summary = run_bash(f"""
DIR={ballot}
ID=pkt
review_count_reports() {{ REVIEW_REPORTS_PRESENT=2; REVIEW_REPORTS_EXPECTED=2; REVIEW_REPORTS_MISSING=""; }}
review_print_result 2>&1
""").stdout
    check("WARNING: argon saw a PARTIAL review packet" in summary and "Full list:" in summary,
          "the phase-1 summary prints the warning")


def test_priority(work: Path):
    # Source plus a lockfile, no tests: one packet. Source is kept whole, the lockfile is cut.
    files = {f"app/lib/mod_{i:02d}.rb": 20_000 for i in range(6)}
    files["Gemfile.lock"] = 60_000
    files["db/migrate/20260101_add.rb"] = 5_000
    ballot, proc = run_leg(work, files)
    ps = prompts(ballot)
    partial = read(ballot / "voter-argon.partial")
    check(len(ps) == 1 and all(f"+++ b/app/lib/mod_{i:02d}.rb" in ps[0] for i in range(6)),
          "over the cap with source and a lockfile: every source file is kept")
    check("- Gemfile.lock (generated; not shown" in partial, "the lockfile is the cut")
    check("Gemfile.lock" in partial and "db/migrate/20260101_add.rb" not in partial and "db/migrate/20260101_add.rb" in ps[0],
          "a migration that still fits is not cut ahead of the lockfile")


def test_oversize_file(work: Path):
    files = {"app/huge.rb": 400_000, "app/small.rb": 2_000}
    ballot, proc = run_leg(work, files)
    ps = prompts(ballot)
    sizes = [int(x) for x in read(ballot / "sizes").split()]
    partial = read(ballot / "voter-argon.partial")
    check(len(ps) == 1 and sizes[0] <= MAX and "+++ b/app/small.rb" in ps[0] and "app/huge.rb" in partial
          and "truncated" in partial,
          f"one file bigger than the cap is truncated, and named ({sizes})")


def test_setting(work: Path):
    ballot, proc = run_leg(work / "a", synthetic(50_000), {"CONCLAVE_ARGON_MAX_BYTES": "40000"}) if (work / "a").mkdir() is None else (None, None)
    check("max_bytes=40000" in read(ballot / "voter-argon.packet.txt") and (ballot / "voter-argon.partial").exists()
          or "mode=split" in read(ballot / "voter-argon.packet.txt"),
          "CONCLAVE_ARGON_MAX_BYTES lowers the cap")
    (work / "b").mkdir()
    ballot, _ = run_leg(work / "b", synthetic(50_000), {"COUNCIL_ARGON_MAX_BYTES": "40000"})
    check("max_bytes=40000" in read(ballot / "voter-argon.packet.txt"), "COUNCIL_ARGON_MAX_BYTES is the alias")
    (work / "c").mkdir()
    ballot, _ = run_leg(work / "c", synthetic(50_000), {"CONCLAVE_ARGON_MAX_BYTES": "lots"})
    check("max_bytes=150000" in read(ballot / "voter-argon.packet.txt"), "a bad value falls back to 150000")
    out = run_bash("argon_max_bytes").stdout.strip()
    check(out == "150000", f"the default cap is 150000 ({out!r})")


def test_second_packet_fails(work: Path):
    ballot, proc = run_leg(work, synthetic(200_000), fail_packet=2)
    merged = read(ballot / "voter-argon.md")
    check("FINDING-FROM-CALL-1" in merged and "NOT REVIEWED: packet 2 failed" in merged and "stub failure on packet 2" in merged
          and not (ballot / "voter-argon.skipped").exists() and (ballot / "voter-argon.model.txt").is_file(),
          "a split keeps packet 1's findings and says packet 2 did not run")
    check("note: argon packet 2: agy: stub failure on packet 2" in read(ballot / "errors.md"),
          "the packet 2 failure is logged as a note")


def test_both_fail(work: Path):
    repo = make_repo(work, synthetic(200_000))
    ballot = work / "ballot"
    ballot.mkdir()
    (ballot / "voter-argon.brief.md").write_text("brief\n", encoding="utf-8")
    proc = run_bash(STUB.replace('[ "${FAIL_PACKET:-0}" = "$CALLS" ]', 'true') + f"""
DIR={ballot}; ID=pkt; REPO={repo}; ERRORS_FILE={ballot}/errors.md; : >"$ERRORS_FILE"; DRY_RUN=0; KIND=review
review_run_argon {repo}
""", {"TMPDIR": str(work)})
    check((ballot / "voter-argon.skipped").is_file() and not (ballot / "voter-argon.md").exists(),
          "every packet failing skips the leg like any failed Argon")
    check("agy: stub failure" in read(ballot / "errors.md")
          and "note: argon packet" not in read(ballot / "errors.md"),
          "every packet failing logs the reason as a plain error line")


def pr_context(work: Path):
    doc = {
        "labels": [{"name": "risk:low"}],
        "check_runs": [{"name": "CHECKRUN_MARKER", "output": {"text": "r" * 30_000}}],
        "comments": [{"body": "COMMENT_MARKER " + "c" * 60_000}],
    }
    ballot = work / "ballot"
    ballot.mkdir()
    (ballot / "pr-context.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return ballot


def test_pr_context(work: Path):
    ballot = pr_context(work)
    brief = work / "base.md"
    brief.write_text("kind: review\n\nLook at the change.\n", encoding="utf-8")
    proc = run_bash(f"""
DIR={ballot}; DRY_RUN=0; REVIEW_MODE=pr; FLAG_PR=7; ID=ctx; BRIEF={brief}
review_write_argon_brief {work}/argon.md || echo REJECT
review_write_muse_brief {work}/muse.md || echo REJECT
""")
    argon, muse = read(work / "argon.md"), read(work / "muse.md")
    section = argon[argon.index("## Pull request context"):] if "## Pull request context" in argon else ""
    check("REJECT" not in proc.stdout and 0 < len(section.encode()) <= 10240,
          f"argon PR context is at or under 10 KB ({len(section.encode())} bytes)")
    check("CHECKRUN_MARKER" not in argon and "COMMENT_MARKER" in argon and "truncated" in section,
          "argon context drops the check-run JSON and keeps comments, truncated")
    muse_section = muse[muse.index("## Pull request context"):] if "## Pull request context" in muse else ""
    check(len(muse_section.encode()) > 40_000 and "CHECKRUN_MARKER" in muse,
          f"other legs keep the 50 KB context with check runs ({len(muse_section.encode())} bytes)")


def test_brief_floor(work: Path):
    ballot, proc = run_leg(work, synthetic(50_000), {"CONCLAVE_ARGON_MAX_BYTES": "3000"}, brief_bytes=6000)
    fail = read(ballot / "voter-argon.fail")
    check("over the cap" in fail and "3000" in fail and not prompts(ballot) and not (ballot / "sizes").exists(),
          f"a brief over the cap fails the leg loud and sends nothing ({fail.strip()!r})")


STALE_SKIP = {"voter-argon.skipped": "argon: skipped\nagy: not found: /usr/bin/agy\n"}


def test_stale_skip_split(work: Path):
    """A skip file left by an earlier run in a reused ballot dir is not this run's failure."""
    ballot, proc = run_leg(work, synthetic(200_000), seed=STALE_SKIP)
    merged = read(ballot / "voter-argon.md")
    check("FINDING-FROM-CALL-1" in merged and "FINDING-FROM-CALL-2" in merged and "NOT REVIEWED" not in merged
          and "/usr/bin/agy" not in merged,
          f"a stale voter-argon.skipped does not fail packet 1 of a split ({merged[:120]!r})")
    check(not (ballot / "voter-argon.skipped").exists() and read(ballot / "voter-argon.model.txt").startswith("argon: ok"),
          "after a clean split no skip file is left and the model proof is kept")


def test_stale_skip_cut(work: Path):
    ballot, proc = run_leg(work, {f"app/lib/mod_{i:02d}.rb": 20_000 for i in range(10)}, seed=STALE_SKIP)
    report = read(ballot / "voter-argon.md")
    check(report.startswith("> WARNING: argon saw a PARTIAL review packet") and "FINDING-FROM-CALL-1" in report
          and not (ballot / "voter-argon.skipped").exists(),
          f"a stale voter-argon.skipped does not stop the cut packet's partial banner ({report[:80]!r})")


def test_stale_skip_this_run_fails(work: Path):
    ballot, proc = run_leg(work, synthetic(200_000), fail_packet=1, seed=STALE_SKIP)
    merged = read(ballot / "voter-argon.md")
    check("NOT REVIEWED: packet 1 failed (agy: stub failure on packet 1)" in merged and "FINDING-FROM-CALL-2" in merged,
          "a packet this run skipped is still NOT REVIEWED, with this run's reason")


def test_diff_config_pinned(work: Path):
    """User or repo diff settings must not change the headers argon_packet.py parses."""
    for i, cfg in enumerate(({"diff.mnemonicPrefix": "true"}, {"diff.noprefix": "true"},
                             {"color.ui": "always", "color.diff": "always"})):
        (work / str(i)).mkdir()
        ballot, proc = run_leg(work / str(i), synthetic(200_000), git_config=cfg)
        ps = prompts(ballot)
        spec_names = [f"spec/models/model_{n:02d}_spec.rb" for n in range(10)]
        check(len(ps) == 2 and all(f"+++ b/{n}" in ps[1] for n in spec_names)
              and "unparsed" not in "".join(ps) and "\x1b[" not in "".join(ps),
              f"{cfg}: the diff splits with a/ b/ headers and every spec in packet 2")


def gate_a3_files():
    files = {f"app/models/{'x' * 68}/model_{i:02d}.rb": 3000 for i in range(60)}
    files.update({f"spec/models/{'x' * 68}/model_{i:02d}_spec.rb": 3000 for i in range(40)})
    return files


def test_packet_never_over_cap(work: Path):
    """The coverage list must shrink to fit; gate-A3 measured 153519 and 192519 bytes."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    for cap, brief in ((150_000, b"B" * 145_000 + b"\n"), (189_000, b"B" * 184_000 + b"\n")):
        out = work / f"plan-{cap}"
        res = argon_packet.plan(brief, fake_diff(gate_a3_files()), cap, out)
        sizes = [p["bytes"] for p in res["packets"]]
        on_disk = [(out / p["path"]).stat().st_size for p in res["packets"]]
        check(res["mode"] == "split" and sizes == on_disk and all(s <= cap for s in on_disk),
              f"cap {cap}, brief {len(brief)}: every packet is at or under the cap ({on_disk})")
        texts = [(out / p["path"]).read_text(encoding="utf-8") for p in res["packets"]]
        check(all("## Packet coverage" in t and re.search(r"^- and \d+ more files, \d+ bytes$", t, re.M) for t in texts),
              f"cap {cap}: the shortened coverage list still counts the files it no longer names")


def test_packet_cannot_fit_fails(work: Path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    cap = 150_000
    brief = b"B" * (cap - argon_packet.FENCE_OVERHEAD - 20) + b"\n"
    for name, brief_bytes in (("label does not fit", brief), ("brief over the cap", b"B" * 160_000 + b"\n")):
        out = work / name.replace(" ", "-")
        try:
            argon_packet.plan(brief_bytes, fake_diff(gate_a3_files()), cap, out)
            raised = ""
        except argon_packet.PacketOverCap as exc:
            raised = str(exc)
        check("over the cap" in raised and not list(out.glob("packet-*.md")),
              f"{name}: plan fails loud and writes no packet ({raised!r})")


def test_quoted_paths(work: Path):
    """git quotes paths with a space or a non-ASCII byte; none may vanish from the packet."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    files = {"docs/My Notes.md": 3000, "app/user.rb": 3000, "app/caf\u00e9.rb": 3000}
    parse_dir = work / "parse"
    parse_dir.mkdir()
    repo = make_repo(parse_dir, files)
    diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD"], capture_output=True, check=True).stdout
    check(b'diff --git "a/app/caf\\303\\251.rb" "b/app/caf\\303\\251.rb"' in diff
          and b"diff --git a/docs/My Notes.md b/docs/My Notes.md" in diff,
          "fixture: git quotes the non-ASCII path and leaves the space path bare")
    names = [sec.path for sec in argon_packet.parse_diff(diff)]
    check(names == ["app/caf\u00e9.rb", "app/user.rb", "docs/My Notes.md"], f"quoted headers are sections: {names}")
    (work / "leg").mkdir()
    ballot, proc = run_leg(work / "leg", files)
    ps = prompts(ballot)
    check(len(ps) == 1 and "+++ b/app/user.rb" in ps[0] and "My Notes.md" in ps[0],
          "an under-cap diff keeps the quoted-path file in the packet")
    (work / "cut").mkdir()
    ballot, proc = run_leg(work / "cut", files, {"CONCLAVE_ARGON_MAX_BYTES": "9000"})
    partial = read(ballot / "voter-argon.partial")
    seen = "".join(prompts(ballot)) + partial
    check(all(name in seen for name in ("My Notes.md", "app/user.rb", "caf")) and "My Notes.md" in partial + "".join(prompts(ballot)),
          "every file, quoted path or not, is in the packet or named in .partial")


def test_stale_partial_cleared(work: Path):
    """review_run's start-of-run cleanup removes the last run's packet files."""
    text = (ROOT / "scripts" / "review-kind.sh").read_text(encoding="utf-8")
    body = text.split("review_run() {", 1)[1]
    start = body.index('  rm -f \\\n    "$DIR"/voter-*.md')
    end = body.index('"$DIR"/pr-context.json', start) + len('"$DIR"/pr-context.json')
    snippet = body[start:end]
    ballot = work / "ballot"
    ballot.mkdir()
    stale = ["voter-argon.partial", "voter-argon.packet.txt", "voter-argon.packet-1.err",
             "voter-argon.packet-2.stream.jsonl", "voter-argon.skipped"]
    for name in stale:
        (ballot / name).write_text("stale\n", encoding="utf-8")
    (ballot / "voter-argon.brief.md").write_text("brief\n", encoding="utf-8")
    proc = run_bash(f"DIR={ballot}\n{snippet}\nls -A {ballot}\n")
    left = proc.stdout.split()
    check(not [n for n in stale if n in left], f"stale argon packet files are removed at run start ({left})")


def fake_diff(files: dict[str, int]) -> bytes:
    """A unified diff that adds about size bytes to each path, in the given order."""
    out = []
    for path, size in files.items():
        body = body_for(path, size)
        out.append(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
                   f"@@ -1 +1,{body.count(chr(10))} @@\n" + "".join("+" + l + "\n" for l in body.splitlines()))
    return "".join(out).encode()


def plan_of(work: Path, files: dict[str, int], brief_bytes: int, diff: bytes = b""):
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    res = argon_packet.plan(b"B" * brief_bytes, diff or fake_diff(files), MAX, work / "plan")
    return res, [read(work / "plan" / p["path"]) for p in res["packets"]]


def test_graphql_schema_is_source(work: Path):
    """Only the Rails dumps are generated; app/graphql/schema.rb is hand-written source."""
    files = {"app/graphql/schema.rb": 40000, "app/models/user.rb": 130000, "db/schema.rb": 5000}
    res, ps = plan_of(work, files, 10000)
    kinds = {f["path"]: f["class"] for p in res["packets"] for f in p["files"] + p["cut"]}
    check(kinds.get("app/graphql/schema.rb") == "source" and kinds.get("db/schema.rb") == "generated",
          f"app/graphql/schema.rb is source and db/schema.rb is generated ({kinds})")
    check(len(ps) == 1 and "+++ b/app/graphql/schema.rb" in ps[0] and "(generated; not shown" not in
          "".join(l for l in res["warning"].splitlines() if "graphql" in l),
          "an over-cap packet shows app/graphql/schema.rb as source, not as a cut generated file")


def test_test_prefix_only_for_runner_files(work: Path):
    """test_runs_controller.rb is product code; test_*.py is a pytest file."""
    files = {"app/controllers/test_runs_controller.rb": 60000, "app/models/user.rb": 60000,
             "spec/models/user_spec.rb": 60000}
    res, ps = plan_of(work, files, 1000)
    check(res["mode"] == "split" and len(ps) == 2, f"fixture: the over-cap diff splits ({res['mode']})")
    check("+++ b/app/controllers/test_runs_controller.rb" in ps[0]
          and "+++ b/app/controllers/test_runs_controller.rb" not in ps[1],
          "test_runs_controller.rb rides in the source packet, not the spec packet")
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    check(argon_packet.classify("pkg/test_refund.py") == "spec"
          and argon_packet.classify("app/controllers/test_runs_controller.rb") == "source",
          "the test_ prefix marks pytest files only")


def test_b_slash_in_dir_name(work: Path):
    """An unquoted path may contain ` b/`; the header splits where both sides agree."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    files = {"spec/my b/helper.rb": 3000, "app/my b/user.rb": 3000}
    repo = make_repo(work, files)
    diff = subprocess.run(["git", "-C", str(repo), "diff", "HEAD"], capture_output=True, check=True).stdout
    check(b"diff --git a/spec/my b/helper.rb b/spec/my b/helper.rb" in diff,
          "fixture: git leaves a path with ` b/` unquoted")
    secs = argon_packet.parse_diff(diff)
    got = [(s.path, s.kind) for s in secs]
    check(got == [("app/my b/user.rb", "source"), ("spec/my b/helper.rb", "spec")],
          f"paths containing ` b/` parse whole and classify by their real directory: {got}")
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "body"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "mv", "spec/my b/helper.rb", "spec/my b/helper2.rb"], check=True)
    with open(repo / "spec/my b/helper2.rb", "a", encoding="utf-8") as fh:
        fh.write("extra\n")
    (repo / "app/my b/user.rb").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    diff = subprocess.run(["git", "-C", str(repo), "diff", "-M", "HEAD"], capture_output=True, check=True).stdout
    got = [s.path for s in argon_packet.parse_diff(diff)]
    check(got == ["app/my b/user.rb", "spec/my b/helper2.rb"],
          f"a rename takes its path from the +++ line and a deletion keeps its own: {got}")
    pure = (b"diff --git a/x b/old.rb b/x b/new.rb\nsimilarity index 100%\n"
            b"rename from x b/old.rb\nrename to x b/new.rb\n")
    got = [s.path for s in argon_packet.parse_diff(pure)]
    check(got == ["x b/new.rb"], f"a pure rename takes its path from the rename-to line: {got}")


def test_cut_path_cannot_forge_coverage(work: Path):
    """A filename with a newline must not add an entry to the coverage list."""
    forged = "vendor/a.lock\n- app/billing.rb (changed source; not shown, 99999 bytes)"
    files = {forged: 5000, "app/user.rb": 8000}
    ballot, proc = run_leg(work, files, {"CONCLAVE_ARGON_MAX_BYTES": "16000"})
    partial = read(ballot / "voter-argon.partial")
    ps = prompts(ballot)
    check("vendor/a.lock" in partial and len(ps) == 1, f"fixture: the generated file is cut ({partial[:80]!r})")
    lists = partial + (ps[0] if ps else "") + read(ballot / "errors.md")
    check(not re.search(r"^(note: )?- app/billing\.rb", lists, re.M),
          "no forged app/billing.rb entry in .partial, the packet coverage list or errors.md")
    check("- vendor/a.lock\\n- app/billing.rb (changed source; not shown, 99999 bytes) (generated; not shown" in partial,
          "the cut path is listed on one line with its newline escaped")
    sys.path.insert(0, str(ROOT / "scripts"))
    import argon_packet
    check(argon_packet.short('a\\b"c\rd') == 'a\\\\b\\"c\\rd', "backslash, quote and CR are escaped")


TESTS = [test_small, test_split_clean, test_split_with_generated, test_large, test_priority,
         test_oversize_file, test_setting, test_second_packet_fails, test_both_fail,
         test_pr_context, test_brief_floor, test_quoted_paths, test_stale_partial_cleared,
         test_graphql_schema_is_source, test_test_prefix_only_for_runner_files, test_b_slash_in_dir_name,
         test_cut_path_cannot_forge_coverage, test_stale_skip_split, test_stale_skip_cut,
         test_stale_skip_this_run_fails, test_diff_config_pinned, test_packet_never_over_cap,
         test_packet_cannot_fit_fails]


def main():
    for test in TESTS:
        work = Path(tempfile.mkdtemp(prefix="argon-packet-"))
        try:
            test(work)
        except Exception:
            traceback.print_exc()
            check(False, f"{test.__name__} ran")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print(f"{passed} passed, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
