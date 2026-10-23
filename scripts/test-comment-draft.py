#!/usr/bin/env python3
"""Repro blocks and reply-label stripping."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import comment_draft  # noqa: E402


def check(cond: bool, label: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {label}")
    print(f"ok {label}")


def main() -> None:
    prose = "**Requested:** the count is wrong.\n"
    plain = comment_draft.draft_comment(prose, kind="inline", others_pr=True)
    check(plain == prose, "draft without a repro leaves the comment alone")

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        script = "puts 'kept'\n"
        path = directory / "f1.rb"
        path.write_text(script, encoding="utf-8")
        drafted = comment_draft.draft_comment(prose, kind="inline", others_pr=True, repro=path)
        check("<summary>f1.rb</summary>" in drafted, "summary is the filename")
        check("```ruby\n" + script + "```\n" in drafted, "fence language is ruby and the script is whole")
        check("Truncated to" not in drafted, "a short script has no truncation note")
        check(drafted.startswith(prose), "the prose stays ahead of the block")

        py = directory / "f2.py"
        py.write_text("print(1)\n", encoding="utf-8")
        py_block = comment_draft.render_repro_block("f2.py", "print(1)\n")
        check("```python\n" in py_block, "py fences as python")
        sh_block = comment_draft.render_repro_block("f3.sh", "echo hi\n")
        check("```bash\n" in sh_block, "sh fences as bash")
        ext_block = comment_draft.render_repro_block("f4.ext", "data\n")
        check("```ext\n" in ext_block, "an unknown extension is the fence language")

        nested = "```\nprint(1)\n```\n"
        fenced = comment_draft.fence_for(nested, "python")
        check(fenced.startswith("````python\n"), "a script backtick run gets a longer fence")
        check(nested in fenced and fenced.rstrip().endswith("````"), "the longer fence closes")

        huge = "x" * 80000
        huge_path = directory / "f9.py"
        huge_path.write_text(huge, encoding="utf-8")
        capped = comment_draft.attach_repro(prose, huge_path)
        check(len(capped) <= comment_draft.GITHUB_COMMENT_LIMIT, f"huge script stays within {len(capped)}")
        check("Truncated to" in capped and "of 80000 bytes" in capped, "huge script carries a truncation note")
        check("<summary>f9.py</summary>" in capped and "```python\n" in capped, "truncated block keeps the summary and fence")
        check(huge not in capped, "the truncated comment is not the whole script")
        check(capped.rstrip().endswith("</details>"), "the truncated block still closes")
        shrunk = comment_draft.attach_repro(capped, huge_path, limit=4000)
        check(len(shrunk) <= 4000, f"an attached block shrinks to a smaller limit ({len(shrunk)})")
        check(shrunk.count("<summary>f9.py</summary>") == 1, "shrinking keeps one summary")
        check("Truncated to" in shrunk and shrunk.rstrip().endswith("</details>"),
              "a shrunk block still notes the cut and closes")

        posted = comment_draft.attach_repro(drafted, path, key="demo-1/f1.rb")
        check(posted.count("<summary>f1.rb</summary>") == 1, "posting adopts the draft marker")
        check("<!-- repro:demo-1/f1.rb -->" in posted and "<!-- repro:f1.rb -->" not in posted,
              "the draft marker becomes the ballot marker")
        check(script in posted, "the adopted block still holds the script")

        tricky = directory / "f5.py"
        tricky.write_text('print("</details>")\n' + ("x" * 5000) + "\n", encoding="utf-8")
        wide = comment_draft.attach_repro("**Requested:** note.\n", tricky, key="demo-1/f5.py")
        tight = comment_draft.attach_repro(wide, tricky, key="demo-1/f5.py", limit=900)
        prose = comment_draft.prose_without_repro(tight).strip()
        check(prose == "**Requested:** note.",
              f"resizing ignores </details> inside the script ({prose!r})")
        check(tight.rstrip().endswith("</details>") and "<!-- repro:demo-1/f5.py -->" in tight,
              "the resized block still closes")

        again = comment_draft.attach_repro(drafted, path)
        check(again.count("<summary>f1.rb</summary>") == 1, "a second attach does not duplicate the block")
        other = directory / "other"
        other.mkdir()
        (other / "f1.rb").write_text("from the other ballot\n", encoding="utf-8")
        both = comment_draft.attach_repro(
            comment_draft.attach_repro("**Requested:** one.\n", path, key="demo-1/f1.rb"),
            other / "f1.rb",
            key="demo-2/f1.rb",
        )
        check(both.count("<summary>f1.rb</summary>") == 2, "two ballots keep two summaries")
        check("puts 'kept'" in both and "from the other ballot" in both, "two ballots keep both scripts")

        ballot = directory / "ballot"
        repro = ballot / "repro"
        repro.mkdir(parents=True)
        (repro / "f1.rb").write_text(script, encoding="utf-8")
        (repro / "f10.rb").write_text("nope\n", encoding="utf-8")
        found = comment_draft.find_repro(ballot, "f1")
        check(found == repro / "f1.rb", "f1 does not match f10")
        check(comment_draft.find_repro(ballot, "f3") is None, "a missing repro is no block")
        (repro / "f1.py").write_text("print(1)\n", encoding="utf-8")
        try:
            comment_draft.find_repro(ballot, "f1")
        except comment_draft.ReproError as exc:
            check("more than one repro" in str(exc), "two files for one finding is an error")
        else:
            raise SystemExit("FAIL two repro files were accepted")

        reply = comment_draft.draft_comment(
            "**Requested:** please fix\n**Question:** why\n",
            kind="reply",
            others_pr=True,
            repro=path,
        )
        check("**Requested:**" not in reply and "**Question:**" not in reply, "a reply loses Requested and Question")
        check("please fix" in reply and "why" in reply, "reply prose stays")
        check("<summary>" not in reply, "a reply does not take the repro block")
        advisory = comment_draft.strip_labels("**Advisory:** note **Informational:** rest")
        check(advisory == "note rest", "Advisory and Informational are stripped")

        fenced_label = "See the script.\n\n```\n**Requested:** inside\n```\n"
        stripped = comment_draft.strip_labels(fenced_label)
        check(stripped == fenced_label, "a label inside a fence is part of the script")

        try:
            comment_draft.draft_comment("no label here", kind="inline", others_pr=True)
        except ValueError as exc:
            check("needs" in str(exc), "an unlabeled new inline comment on someone else's PR is refused")
        else:
            raise SystemExit("FAIL unlabeled inline comment was drafted")
        own = comment_draft.draft_comment("no label here", kind="inline", others_pr=False)
        check(own == "no label here", "your own PR can draft an unlabeled inline comment")
        labeled = comment_draft.draft_comment("**Informational:** noted.", kind="inline", others_pr=True)
        check(labeled == "**Informational:** noted.", "a labeled new inline comment is kept")

        # A script that would fail the prose lint if the fence were ignored.
        risky = directory / "f8.py"
        risky_text = "council\nabc1234\napp/widget.rb:3\n"
        risky.write_text(risky_text, encoding="utf-8")
        risky_comment = comment_draft.draft_comment(
            "**Advisory:** the helper drops a row.\n",
            kind="inline",
            others_pr=True,
            repro=risky,
        )
        checker = ROOT / "check-comment-links.py"
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / "comments.md"
            target.write_text(risky_comment, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(checker), str(target)], capture_output=True, text=True)
        check(proc.returncode == 0, f"fenced repro does not trip the citation or word lint ({proc.stdout} {proc.stderr})")
        bare = "**Advisory:** council at app/widget.rb:3 and abc1234.\n"
        with tempfile.TemporaryDirectory() as out:
            target = Path(out) / "comments.md"
            target.write_text(bare, encoding="utf-8")
            proc = subprocess.run([sys.executable, str(checker), str(target)], capture_output=True, text=True)
        check(proc.returncode != 0 and "council" in proc.stdout, "the same words outside a fence still fail")

    print("ok comment draft")


if __name__ == "__main__":
    main()
