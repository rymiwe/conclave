#!/usr/bin/env python3
"""Offline checks for the review follow-ups. No network and no API call.

Covers the Codex resolver version rules, the rollout usage parser, decide.md
keep and fresh-run cleanup, later-in-the-stack lines, the stack walk, and
leg wall times.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def load_judge():
    path = SCRIPTS / "jev-judge.py"
    spec = importlib.util.spec_from_file_location("jev_judge_followups", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def models_home(home: Path) -> None:
    cache = home / ".codex" / "models_cache.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "models": [
                    {"visibility": "list", "priority": 1, "slug": "gpt-6-astra"}
                ]
            }
        ),
        encoding="utf-8",
    )


def codex_script(token: str, noise: str = "installed 9.9.9") -> str:
    return f"#!/bin/bash\necho {shlex.quote(noise)}\necho {shlex.quote('codex-cli ' + token)}\n"


def run_resolver(tmp: Path, bins: list[tuple[str, str]]) -> subprocess.CompletedProcess[str]:
    """bins is (directory name, version token). Each dir holds a codex stub."""
    home = tmp / "home"
    models_home(home)
    path_dirs = []
    for i, (name, token) in enumerate(bins):
        bindir = tmp / f"bin-{i}-{name}"
        write_exe(bindir / "codex", codex_script(token))
        path_dirs.append(str(bindir))
    # jq reads the models cache. Pin the jq this process can already see.
    # A PATH of only /usr/bin misses jq installed elsewhere, and the resolver
    # then rejects a good Codex binary because CODEX_REVIEW_MODEL is empty.
    jq = shutil.which("jq")
    if not jq:
        raise SystemExit("FAIL jq is not on PATH")
    jqdir = tmp / "jq-bin"
    jqdir.mkdir()
    os.symlink(jq, jqdir / "jq")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PATH"] = ":".join(path_dirs + [str(jqdir), "/usr/bin", "/bin"])
    env.pop("COUNCIL_CODEX_BIN", None)
    return subprocess.run(
        ["bash", str(SCRIPTS / "resolve-models.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def codex_path(proc: subprocess.CompletedProcess[str]) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith("CODEX="):
            return shlex.split(line)[0].split("=", 1)[1]
    return ""


def test_resolver(tmp: Path) -> None:
    low = run_resolver(tmp / "low", [("stable", "0.159.2")])
    check(low.returncode != 0 and codex_path(low) == "", "stable 0.159.2 rejected")

    floor = run_resolver(tmp / "floor", [("stable", "0.159.3")])
    check(floor.returncode == 0 and codex_path(floor).endswith("/codex"), "stable 0.159.3 accepted")

    alpha_low = run_resolver(tmp / "alpha-low", [("alpha", "0.153.0-alpha.1")])
    check(alpha_low.returncode != 0, "alpha below 0.154 rejected")

    alpha_ok = run_resolver(tmp / "alpha-ok", [("alpha", "0.154.0-alpha.1")])
    check(alpha_ok.returncode == 0, "alpha 0.154.0-alpha.1 accepted")

    beta = run_resolver(tmp / "beta", [("beta", "0.200.0-beta.1")])
    check(beta.returncode != 0, "beta rejected")

    rc = run_resolver(tmp / "rc", [("rc", "0.200.0-rc.1")])
    check(rc.returncode != 0, "rc rejected")

    beats = run_resolver(
        tmp / "beats",
        [("alpha", "0.200.0-alpha.9"), ("stable", "0.159.3")],
    )
    picked = codex_path(beats)
    check(
        beats.returncode == 0 and "/bin-1-stable/" in picked,
        f"stable beats higher alpha ({picked})",
    )

    newest = run_resolver(
        tmp / "newest",
        [("old", "0.159.3"), ("new", "0.170.0")],
    )
    picked = codex_path(newest)
    check(
        newest.returncode == 0 and "/bin-1-new/" in picked,
        f"newest stable wins ({picked})",
    )

    beta_noise = run_resolver(tmp / "beta-noise", [("beta", "0.200.0-beta.1"), ("stable", "0.159.3")])
    picked = codex_path(beta_noise)
    check(
        beta_noise.returncode == 0 and "/bin-1-stable/" in picked,
        f"beta prefix is not a stable 0.200 ({picked})",
    )


def bash_council(script: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def test_usage(tmp: Path) -> None:
    script = textwrap.dedent(
        """\
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        # shellcheck disable=SC1091
        . "$1"
        DIR="$2"
        shift 2
        review_codex_record_usage "$@"
        cat "$DIR/voter-codex.usage.txt"
        """
    )

    def record(name: str, files: list[Path]) -> str:
        dest = tmp / name
        dest.mkdir()
        proc = bash_council(script, [str(SCRIPTS / "council.sh"), str(dest), *[str(p) for p in files]])
        if proc.returncode != 0:
            raise SystemExit(f"usage {name} failed: {proc.stderr}")
        return proc.stdout

    def event(total: int, last: int) -> str:
        return json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"reasoning_output_tokens": total},
                        "last_token_usage": {"reasoning_output_tokens": last},
                    },
                },
            }
        )

    def turn(effort: str) -> str:
        return json.dumps({"type": "turn_context", "payload": {"effort": effort, "model": "x"}})

    one = tmp / "roll-one.jsonl"
    one.write_text(
        "\n".join([event(100, 100), turn("low"), event(1404, 309), turn("high")]) + "\n",
        encoding="utf-8",
    )
    got = record("one", [one])
    check(got == "reasoning_tokens=1404\neffort=high\n", f"total not last turn ({got!r})")

    parent = tmp / "roll-parent.jsonl"
    parent.write_text(turn("low") + "\n", encoding="utf-8")
    child = tmp / "roll-child.jsonl"
    child.write_text("\n".join([event(50, 50), turn("medium")]) + "\n", encoding="utf-8")
    larger = tmp / "roll-large.jsonl"
    larger.write_text("\n".join([event(1404, 10), turn("high")]) + "\n", encoding="utf-8")
    # Parent is last, the order find would use if it listed the parent last.
    got = record("many", [child, larger, parent])
    check(
        got == "reasoning_tokens=1404\neffort=high\n",
        f"largest rollout not the last file ({got!r})",
    )

    bad = tmp / "roll-bad.jsonl"
    bad.write_text(
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"reasoning_output_tokens": "nope"}},
                },
            }
        )
        + "\n"
        + turn("high")
        + "\n",
        encoding="utf-8",
    )
    got = record("bad", [bad])
    check(got == "reasoning_tokens=unknown\neffort=unknown\n", f"unparseable total ({got!r})")

    zero = tmp / "roll-zero.jsonl"
    zero.write_text("\n".join([event(0, 0), turn("low")]) + "\n", encoding="utf-8")
    got = record("zero", [zero])
    check(got == "reasoning_tokens=0\neffort=low\n", f"zero is a real total ({got!r})")

    small = tmp / "roll-small.jsonl"
    small.write_text("\n".join([event(3, 3), turn("high")]) + "\n", encoding="utf-8")
    winner = tmp / "roll-winner.jsonl"
    winner.write_text(
        "\n".join(
            [
                event(10, 1),
                json.dumps({"type": "turn_context", "payload": {"effort": ""}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    got = record("effort-file", [small, winner])
    check(
        got == "reasoning_tokens=10\neffort=unknown\n",
        f"effort comes from the winning file ({got!r})",
    )


def finding(fid: str, path: str = "a.rb", executed: bool = False) -> dict:
    return {
        "id": fid,
        "claim": f"claim for {fid} must not be copied",
        "file": path,
        "line": 1,
        "cited_code": "cited",
        "evidence": "no run was made",
        "repro_executed": executed,
        "legs": {"A": "found"},
    }


def answers(ids: list[str], choice: str = "hold", conf: float = 0.5) -> dict:
    return {
        "model": "jev-canned",
        "answers": {
            fid: {"choice": choice, "confidence": conf, "probabilities": {}}
            for fid in ids
        },
    }


def test_decide(tmp: Path, judge) -> None:
    ballot = tmp / "ballot"
    ballot.mkdir()
    (ballot / "header.json").write_text(
        json.dumps({"kind": "review", "stack": "none"}) + "\n",
        encoding="utf-8",
    )
    doc = {"findings": [finding("f1")]}
    raw = answers(["f1"])
    judge.write_decide(ballot, doc, raw)
    text = (ballot / "decide.md").read_text(encoding="utf-8")
    check("TODO(chair)" in text and "claim for f1" not in text, "skeleton omits the claim")
    check(
        text.endswith("each with its ballot id (for example `ballot f1`), or none.\n")
        and "Reply with the ids" not in text,
        "closing line asks for ballot-prefixed ids",
    )

    filled = text.replace("TODO(chair)", "chair filled this")
    (ballot / "decide.md").write_text(filled, encoding="utf-8")
    (ballot / "decide.md.prev").write_text("older prev\n", encoding="utf-8")
    import io
    from contextlib import redirect_stderr

    err = io.StringIO()
    with redirect_stderr(err):
        judge.write_decide(ballot, doc, raw)
    check(
        "left in place" in err.getvalue() and (ballot / "decide.md").read_text(encoding="utf-8") == filled,
        "filled file kept when ids and pre-filled lines match",
    )
    check((ballot / "decide.md.prev").read_text(encoding="utf-8") == "older prev\n", "keep does not replace .prev")

    err = io.StringIO()
    with redirect_stderr(err):
        judge.write_decide(ballot, doc, answers(["f1"], choice="post", conf=0.81))
    rewritten = (ballot / "decide.md").read_text(encoding="utf-8")
    prev = (ballot / "decide.md.prev").read_text(encoding="utf-8")
    check("copied decide.md to decide.md.prev" in err.getvalue(), "how-sure change copies to .prev")
    check(prev == filled and "0.810" in rewritten and "chair filled this" not in rewritten, "rewrite drops stale chair text")

    err = io.StringIO()
    with redirect_stderr(err):
        judge.write_decide(ballot, {"findings": [finding("f2")]}, answers(["f2"]))
    check("f2 —" in (ballot / "decide.md").read_text(encoding="utf-8"), "id change rewrites")
    check(
        (ballot / "decide.md.prev").read_text(encoding="utf-8") == filled,
        "unedited skeleton does not replace chair text in .prev",
    )

    seq = tmp / "seq-rejudge"
    seq.mkdir()
    (seq / "header.json").write_text(
        json.dumps({"kind": "review", "stack": "none"}) + "\n",
        encoding="utf-8",
    )
    judge.write_decide(seq, doc, raw)
    chair = (seq / "decide.md").read_text(encoding="utf-8").replace("TODO(chair)", "chair filled this")
    (seq / "decide.md").write_text(chair, encoding="utf-8")
    judge.write_decide(seq, doc, answers(["f1"], choice="post", conf=0.81))
    judge.write_decide(seq, doc, answers(["f1"], choice="hold", conf=0.62))
    check(
        (seq / "decide.md.prev").read_text(encoding="utf-8") == chair,
        "second re-judge leaves the chair text in .prev",
    )
    check("0.620" in (seq / "decide.md").read_text(encoding="utf-8"), "second re-judge writes the new skeleton")

    fresh = tmp / "fresh"
    fresh.mkdir()
    (fresh / "decide.md").write_text("chair text that must survive\n", encoding="utf-8")
    (fresh / "decide.md.prev").write_text("older\n", encoding="utf-8")
    script = textwrap.dedent(
        """\
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        # shellcheck disable=SC1091
        . "$1"
        DIR="$2"
        ballot_move_decide
        """
    )
    proc = bash_council(script, [str(SCRIPTS / "council.sh"), str(fresh)])
    check(proc.returncode == 0, f"fresh cleanup ran ({proc.stderr})")
    check(not (fresh / "decide.md").exists(), "fresh run removes decide.md")
    check(
        (fresh / "decide.md.prev").read_text(encoding="utf-8") == "chair text that must survive\n",
        "fresh run moves chair text to decide.md.prev",
    )
    check("note: moved decide.md to decide.md.prev" in proc.stderr, "fresh run names the copy")

    # Chair fill, fresh run, judge, second fresh run. .prev keeps the chair text.
    cycle = tmp / "seq-fresh"
    cycle.mkdir()
    (cycle / "header.json").write_text(
        json.dumps({"kind": "review", "stack": "none"}) + "\n",
        encoding="utf-8",
    )
    (cycle / "decide.md").write_text("chair text that must survive the second fresh run\n", encoding="utf-8")
    proc = bash_council(script, [str(SCRIPTS / "council.sh"), str(cycle)])
    check(proc.returncode == 0 and "moved decide.md" in proc.stderr, "first fresh run moves the chair file")
    judge.write_decide(cycle, doc, raw)
    check("TODO(chair)" in (cycle / "decide.md").read_text(encoding="utf-8"), "judge writes a skeleton after the fresh run")
    proc = bash_council(script, [str(SCRIPTS / "council.sh"), str(cycle)])
    check(proc.returncode == 0 and "removed unedited decide.md" in proc.stderr, "second fresh run removes the skeleton")
    check(not (cycle / "decide.md").exists(), "second fresh run does not leave decide.md")
    check(
        (cycle / "decide.md.prev").read_text(encoding="utf-8")
        == "chair text that must survive the second fresh run\n",
        "second fresh run leaves the chair text in .prev",
    )


def test_later(judge) -> None:
    def pr(number: int, title: str, files: list[str], target: bool = False, changed: int | None = None) -> dict:
        return {
            "number": number,
            "title": title,
            "files": files,
            "target": target,
            "changedFiles": len(files) if changed is None else changed,
        }

    stack = {
        "status": "stack",
        "truncated": False,
        "prs": [
            pr(1, "bottom", ["app/a.rb"], target=True),
            pr(2, "later touch", ["app/services/imports/row.rb", ".env"]),
            pr(3, "later other", ["README.md"]),
        ],
    }
    hit = judge.later_in_stack_line(
        {"file": "app/services/imports/row.rb"},
        "#1 (target) -> #2 -> #3",
        stack,
    )
    check(hit == "#2 later touch", f"match ({hit!r})")
    miss = judge.later_in_stack_line({"file": "README.md"}, "#1 (target) -> #2 -> #3", stack)
    check(miss == "#3 later other", f"second later match ({miss!r})")
    none = judge.later_in_stack_line({"file": "config/routes.rb"}, "#1 (target) -> #2 -> #3", stack)
    check(none == "not touched later in the stack", f"no match ({none!r})")

    truncated = dict(stack)
    truncated["truncated"] = True
    truncated["prs"] = [pr(1, "bottom", ["a.rb"], target=True), pr(2, "later", ["b.rb"])]
    line = judge.later_in_stack_line({"file": "missing.rb"}, "#1 (target) -> #2", truncated)
    check(line == "not touched in the first 20 PRs; walk truncated", f"truncated ({line!r})")
    truncated["prs"] = [pr(1, "bottom", ["z.rb"], target=True), pr(7, "t7", ["a.rb"])]
    line = judge.later_in_stack_line({"file": "a.rb"}, "#1 (target) -> #7", truncated)
    check(line == "#7 t7; walk truncated at 20 PRs", f"truncated hit ({line!r})")

    capped_files = [f"f{i}.rb" for i in range(100)]
    capped = {
        "status": "stack",
        "truncated": False,
        "prs": [
            pr(1, "bottom", ["a.rb"], target=True),
            pr(9, "wide", capped_files, changed=150),
        ],
    }
    line = judge.later_in_stack_line({"file": "not-listed.rb"}, "#1 (target) -> #9", capped)
    check(line == "unknown (PR #9 lists 100 of 150 files)", f"file cap ({line!r})")
    listed = judge.later_in_stack_line({"file": "f0.rb"}, "#1 (target) -> #9", capped)
    check(listed == "#9 wide", f"file present in a short list still matches ({listed!r})")

    env_stack = {
        "status": "stack",
        "truncated": False,
        "prs": [
            pr(1, "bottom", ["env"], target=True),
            pr(2, "later", [".env"]),
        ],
    }
    dot = judge.later_in_stack_line({"file": ".env"}, "#1 (target) -> #2", env_stack)
    plain = judge.later_in_stack_line({"file": "env"}, "#1 (target) -> #2", env_stack)
    prefixed = judge.later_in_stack_line({"file": "./.env"}, "#1 (target) -> #2", env_stack)
    check(dot == "#2 later", f".env matches .env ({dot!r})")
    check(plain == "not touched later in the stack", f"env does not match .env ({plain!r})")
    check(prefixed == "#2 later", f"./.env matches .env ({prefixed!r})")
    check(judge._norm_path(".env") == ".env", "norm keeps .env")
    check(judge._norm_path("env") == "env", "norm keeps env")


def gh_stub() -> str:
    return textwrap.dedent(
        '''\
        #!/usr/bin/env python3
        import json, os, sys

        mode = os.environ["GH_FIXTURE"]
        args = sys.argv[1:]

        def pr(number, title, base, head, cross=False, owner="example"):
            return {
                "number": number,
                "title": title,
                "url": f"https://example.test/{number}",
                "body": f"body {number}",
                "baseRefName": base,
                "headRefName": head,
                "isCrossRepository": cross,
                "headRepositoryOwner": {"login": owner},
            }

        def emit(obj):
            json.dump(obj, sys.stdout)
            sys.stdout.write("\\n")

        def fields():
            return args[args.index("--json") + 1]

        if args[:2] == ["repo", "view"]:
            emit({"defaultBranchRef": {"name": os.environ.get("GH_DEFAULT", "develop")}})
            raise SystemExit(0)

        if mode == "chain":
            prs = {
                11: pr(11, "one", "develop", "branch-a"),
                12: pr(12, "two", "branch-a", "branch-b"),
                13: pr(13, "three", "branch-b", "branch-c"),
                14: pr(14, "four", "branch-c", "branch-d"),
            }
            files = {
                11: ["app/services/imports/row.rb"],
                12: ["app/services/imports/row.rb", "other.rb"],
                13: ["grid.rb"],
                14: ["app/services/imports/row.rb"],
            }
        elif mode == "alone":
            prs = {7: pr(7, "Alone", "develop", "feature-alone")}
            files = {7: ["only.rb"]}
        elif mode == "fork":
            prs = {
                50: pr(50, "fork target", "feature-base", "fork-feature", cross=True, owner="other"),
                49: pr(49, "fork parent", "elsewhere", "feature-base", cross=True, owner="other"),
                48: pr(48, "same-repo parent", "older", "feature-base"),
                51: pr(51, "same-repo child", "fork-feature", "fork-next"),
            }
            files = {50: ["fork.rb"], 49: ["parent.rb"], 48: ["same.rb"], 51: ["child.rb"]}
        elif mode == "release":
            prs = {
                100: pr(100, "Release", "master", "develop"),
                201: pr(201, "feature a", "develop", "feature-a"),
                202: pr(202, "feature b", "develop", "feature-b"),
            }
            files = {100: ["CHANGELOG"], 201: ["a.rb"], 202: ["b.rb"]}
        else:
            sys.stderr.write(f"unknown fixture {mode}\\n")
            raise SystemExit(1)

        if "view" in args and args[0] == "pr":
            number = int(args[2])
            if fields() == "files,changedFiles":
                paths = files[number]
                emit({"files": [{"path": p} for p in paths], "changedFiles": len(paths)})
            else:
                emit(prs[number])
            raise SystemExit(0)

        if args[0] == "pr" and "list" in args:
            flag = "head" if "--head" in args else "base"
            branch = args[args.index(f"--{flag}") + 1]
            key = "headRefName" if flag == "head" else "baseRefName"
            emit([row for row in prs.values() if row[key] == branch])
            raise SystemExit(0)

        sys.stderr.write("unexpected gh: " + " ".join(args) + "\\n")
        raise SystemExit(1)
        '''
    )


def run_stack(tmp: Path, mode: str, number: str, default: str = "develop") -> tuple[str, str]:
    bindir = tmp / f"gh-{mode}-{number}-{default}"
    write_exe(bindir / "gh", gh_stub())
    dest = tmp / f"stack-{mode}-{number}-{default}"
    dest.mkdir()
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:/usr/bin:/bin"
    env["GH_FIXTURE"] = mode
    env["GH_DEFAULT"] = default
    proc = subprocess.run(
        ["python3", str(SCRIPTS / "review-stack.py"), str(tmp), number, str(dest)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"stack {mode} {number} failed: {proc.stderr}")
    return proc.stdout.strip(), (dest / "stack.md").read_text(encoding="utf-8")


def test_stack(tmp: Path) -> None:
    header, md = run_stack(tmp, "chain", "11")
    check(
        header == "#11 (target) -> #12 -> #13 -> #14",
        f"four-PR chain ({header})",
    )
    check("#11 (target)" in md and "#14" in md and "row.rb" in md, "chain stack.md")

    header, md = run_stack(tmp, "alone", "7")
    check(header == "none", f"lone PR header ({header})")
    check(
        md.startswith("No stack: no open PR builds on this branch and its base is not another open PR's head."),
        "lone PR stack.md",
    )

    header, md = run_stack(tmp, "fork", "50")
    check(header == "none", f"fork PR header ({header})")
    check(
        "#51" not in md and "#49" not in md and "#48" not in md,
        "fork PR does not link a same-repo parent or a child",
    )

    header, md = run_stack(tmp, "release", "100")
    check(header == "none", f"release PR header ({header})")
    check("#201" not in md and "#202" not in md, "release PR does not list PRs into develop")

    header, md = run_stack(tmp, "release", "201")
    check(header == "none", f"feature on develop header ({header})")
    check("#100" not in md, "release PR is not the parent of a feature PR")

    header, _md = run_stack(tmp, "release", "100", default="master")
    check(
        header == "#100 (target) -> #201 -> #202",
        f"master default still links the release PR upward ({header})",
    )
    header, _md = run_stack(tmp, "release", "201", default="master")
    check(
        header == "#100 -> #201 (target)",
        f"master default still links the release PR as a parent ({header})",
    )


def test_leg_seconds(tmp: Path, judge) -> None:
    """Leg wall times: the wrapper, the reader, the JSON writer, the header."""
    script = textwrap.dedent(
        """\
        set -eu
        export COUNCIL_SOURCE_ONLY=1
        # shellcheck disable=SC1091
        . "$1"
        DIR="$2"
        rc=0
        review_timed_leg codex bash -c 'sleep 2; exit 7' || rc=$?
        printf 'garbage\\n' >"$DIR/voter-grok.seconds"
        printf 'rc=%s codex=%s grok=%s opus=%s\\n' "$rc" \\
          "$(review_leg_seconds codex)" "$(review_leg_seconds grok)" \\
          "$(review_leg_seconds opus)"
        KIND=review HDR_BRANCH=b HDR_PR=none HDR_VA=fa HDR_VB=fb HDR_BUILDER=x
        HDR_HAS_OPUS=1 HDR_VOPUS=fo
        review_load_leg_seconds
        council_write_header_json
        """
    )
    dest = tmp / "seconds"
    dest.mkdir()
    proc = bash_council(script, [str(SCRIPTS / "council.sh"), str(dest)])
    if proc.returncode != 0:
        raise SystemExit(f"leg seconds failed: {proc.stderr}")
    out = proc.stdout.strip()
    check(out.startswith("rc=7 codex="), f"failed leg keeps its exit status ({out})")
    secs = out.split("codex=")[1].split()[0]
    check(secs in ("2", "3"), f"failed leg still records its time ({secs})")
    check("grok=unknown opus=unknown" in out, f"garbage and missing are unknown ({out})")
    meta = json.loads((dest / "header.json").read_text(encoding="utf-8"))
    check(meta.get("voter_b_seconds") == secs, f"header.json voter_b_seconds ({meta})")
    check(meta.get("coverage_opus_seconds") == "unknown", "opus ran with no file is unknown")
    check("voter_c_seconds" not in meta, "no Grok leg, no voter_c_seconds")

    lines = judge.format_judgment_header(
        decision="clean (0 post, 0 hold, 0 second look)",
        confidence="n/a",
        judge_model="n/a",
        meta={
            "kind": "review",
            "voter_a_model": "fa",
            "voter_a_seconds": "490",
            "voter_b_model": "fb",
            "voter_b_seconds": "152",
            "voter_c_model": "gc",
            "voter_c_seconds": "88",
            "coverage_opus_model": "fo",
        },
    ).splitlines()
    want = [
        "voter_a_model: fa",
        "voter_a_seconds: 490",
        "voter_b_model: fb",
        "voter_b_seconds: 152",
        "voter_b_reasoning_tokens: unknown",
        "voter_b_effort: unknown",
        "voter_c_model: gc",
        "voter_c_seconds: 88",
        "coverage_opus_model: fo",
        "coverage_opus_seconds: unknown",
    ]
    start = lines.index("voter_a_model: fa")
    check(lines[start : start + len(want)] == want, f"seconds follow their model lines ({lines})")
    design = judge.format_judgment_header(
        decision="implement", confidence="0.9", judge_model="j", meta={"kind": "design"}
    )
    check("_seconds" not in design, "design carries no seconds")


def main() -> None:
    import tempfile

    judge = load_judge()
    with tempfile.TemporaryDirectory(prefix="council-followups-") as raw:
        tmp = Path(raw)
        test_resolver(tmp)
        test_usage(tmp)
        test_decide(tmp, judge)
        test_later(judge)
        test_stack(tmp)
        test_leg_seconds(tmp, judge)
    print("ok")


if __name__ == "__main__":
    main()
