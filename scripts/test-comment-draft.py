#!/usr/bin/env python3
"""Repro blocks, reply-label stripping, and summary-opening labels."""
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
        hand = "Note.\n\n<details><summary>config.yml</summary>\n\nname: kept\n\n</details>\n"
        check(comment_draft.prose_without_repro(hand) == hand,
              "a details block is prose unless it carries the repro marker")
        written = (
            "**Requested:** note.\n\n"
            "<details><summary>f1.rb</summary>\n\n"
            "```ruby\n"
            "puts :hand\n"
            "```\n\n"
            "</details>\n"
        )
        adopted = comment_draft.attach_repro(written, path, key="demo-1/f1.rb")
        check(
            "<!-- repro:demo-1/f1.rb -->" in adopted
            and adopted.count("<summary>f1.rb</summary>") == 1
            and "puts :hand" in adopted
            and "puts 'kept'" not in adopted,
            "a hand-written summary is adopted by inserting the marker",
        )
        check(
            comment_draft.prose_without_repro(adopted).strip() == "**Requested:** note.",
            "once marked, the hand-written block is a repro block",
        )
        mention = "**Requested:** see <summary>f1.rb</summary> in the note.\n"
        mentioned = comment_draft.attach_repro(mention, path, key="demo-1/f1.rb")
        check(
            "<!-- repro:demo-1/f1.rb -->" in mentioned and "puts 'kept'" in mentioned,
            "a bare summary mention does not swallow the repro",
        )
        huge_hand = (
            "**Requested:** note.\n\n"
            "<details><summary>f1.rb</summary>\n\n"
            "```ruby\n"
            + ("x" * 5000)
            + "\n```\n\n</details>\n"
        )
        shrunk_hand = comment_draft.attach_repro(huge_hand, path, key="demo-1/f1.rb", limit=800)
        check(
            "<!-- repro:demo-1/f1.rb -->" in shrunk_hand
            and shrunk_hand.count("<summary>f1.rb</summary>") == 1
            and len(shrunk_hand) <= 800,
            f"an over-limit hand-written block is resized with a marker ({len(shrunk_hand)})",
        )
        unrelated = (
            "**Requested:** note.\n\n"
            "<details><summary>config.yml</summary>\n\nname: kept\n\n</details>\n\n"
            "The script <summary>f1.rb</summary> is below.\n"
        )
        attached = comment_draft.attach_repro(unrelated, path, key="demo-1/f1.rb")
        check(
            "<!-- repro:demo-1/f1.rb -->\n<details><summary>f1.rb</summary>" in attached
            and "puts 'kept'" in attached
            and "<!-- repro:demo-1/f1.rb -->\n<details><summary>config.yml" not in attached
            and "name: kept" in comment_draft.prose_without_repro(attached),
            "an unrelated details block before a bare summary mention is not adopted",
        )
        other_marker = (
            "Notes.\n\n<!-- repro:demo-2/f9.py -->\n<details><summary>f9.py</summary>\n\n"
            "```python\nprint(1)\n```\n\n</details>\n\n" + written
        )
        both_adopted = comment_draft.attach_repro(other_marker, path, key="demo-1/f1.rb")
        check(
            both_adopted.count("<summary>f1.rb</summary>") == 1
            and "<!-- repro:demo-1/f1.rb -->\n<details><summary>f1.rb</summary>" in both_adopted
            and "puts :hand" in both_adopted,
            "a hand-written block is adopted when another file's marker is already present",
        )
        other_ballot = written.replace(
            "<details><summary>f1.rb</summary>",
            "<!-- repro:demo-2/f1.rb -->\n<details><summary>f1.rb</summary>",
        )
        second = comment_draft.attach_repro(other_ballot, path, key="demo-1/f1.rb")
        check(
            second.count("<summary>f1.rb</summary>") == 2
            and "<!-- repro:demo-2/f1.rb -->\n<details><summary>f1.rb</summary>" in second
            and "puts 'kept'" in second,
            "another ballot's marked block for the same filename is not adopted",
        )
        fenced = "Example:\n\n````html\n<details><summary>f1.rb</summary>\n</details>\n````\n"
        fenced_out = comment_draft.attach_repro(fenced, path, key="demo-1/f1.rb")
        check(
            fenced_out.startswith(fenced.rstrip("\n")) and "puts 'kept'" in fenced_out
            and "<!-- repro:demo-1/f1.rb -->\n<details><summary>f1.rb</summary>\n</details>\n````" not in fenced_out,
            "a documented block inside a fence is not adopted",
        )

        million = "x" * 1_000_000
        million_total = len(million.encode("utf-8"))
        million_key = "demo-1/f1.py"
        million_marker = f"<!-- repro:{million_key} -->"
        million_floor = comment_draft.repro_floor("f1.py", key=million_key, total=million_total)
        six_floor = comment_draft.repro_floor("f1.py", key=million_key, total=999_999)
        million_block = comment_draft.render_repro_block(
            "f1.py", million, budget=million_floor, marker=million_marker
        )
        check(million_floor == six_floor + 1,
              f"a 7-digit total lengthens the note ({six_floor} -> {million_floor})")
        check(million_block != "" and len(million_block) == million_floor and "of 1000000 bytes" in million_block,
              f"a 7-digit script fits the floor taken from its length ({len(million_block)} {million_floor})")
        check(comment_draft.render_repro_block(
            "f1.py", million, budget=million_floor - 1, marker=million_marker
        ) == "", "one byte under that floor does not fit")
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
        try:
            comment_draft.draft_comment(
                "size counts deleted items. as **Requested:** above",
                kind="inline",
                others_pr=True,
            )
        except ValueError as exc:
            check("needs" in str(exc), "a new inline comment whose label is not at the start is refused")
        else:
            raise SystemExit("FAIL a buried label was drafted")
        buried_own = comment_draft.draft_comment(
            "size counts deleted items. as **Requested:** above",
            kind="inline",
            others_pr=False,
        )
        check(buried_own == "size counts deleted items. as **Requested:** above",
              "your own PR can draft a label that is not at the start")
        own = comment_draft.draft_comment("no label here", kind="inline", others_pr=False)
        check(own == "no label here", "your own PR can draft an unlabeled inline comment")
        labeled = comment_draft.draft_comment("**Informational:** noted.", kind="inline", others_pr=True)
        check(labeled == "**Informational:** noted.", "a labeled new inline comment is kept")

        summary = comment_draft.draft_comment(
            "\n\n**Requested:** the count is wrong.\n\n- **Advisory:** look again\n",
            kind="body",
            others_pr=True,
        )
        check(summary == "\n\nthe count is wrong.\n\n- look again\n",
              f"drafter strips a leading summary label and the bullet label ({summary!r})")
        question = comment_draft.draft_comment("**Question:** why is this here?\n", kind="body")
        check(question == "why is this here?\n", "a leading Question on the summary is stripped")
        stacked = comment_draft.draft_comment("**Requested:** **Advisory:** both\n", kind="body")
        check(stacked == "both\n", f"stacked opening labels are stripped ({stacked!r})")
        kept = comment_draft.draft_comment("**Requested:** see **Advisory:** rest\n", kind="body")
        check(kept == "see **Advisory:** rest\n", f"a later label on the opening line stays ({kept!r})")
        bullet_only = comment_draft.draft_comment("- **Requested:** fix the count\n", kind="body")
        check(bullet_only == "- fix the count\n", f"a summary that is only a labeled bullet loses the label ({bullet_only!r})")
        plain_summary = comment_draft.draft_comment("Four notes.\n", kind="body")
        check(plain_summary == "Four notes.\n", "a summary without a label stays")
        check(comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS is False,
              "labels go only on new inline comments: the bullet constant is False")
        stripped_bullets = comment_draft.draft_comment(
            "**Informational:** overview\n\n- **Requested:** fix the count\n1. **Question:** why\n",
            kind="body",
        )
        check(
            stripped_bullets == "overview\n\n- fix the count\n1. why\n",
            f"drafter strips bullet labels by default ({stripped_bullets!r})",
        )
        saved = comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS
        comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS = True
        try:
            kept_bullet = comment_draft.draft_comment("- **Requested:** fix the count\n", kind="body")
            check(kept_bullet == "- **Requested:** fix the count\n",
                  "a bullet label stays only when the constant is flipped to True")
        finally:
            comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS = saved
        check(comment_draft.SUMMARY_BULLETS_MAY_HAVE_LABELS is False, "the bullet constant is restored")
        inline_kept = comment_draft.draft_comment("**Requested:** fix it\n", kind="inline", others_pr=True)
        check(inline_kept == "**Requested:** fix it\n", "a new inline comment keeps its label")

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
