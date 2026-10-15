#!/usr/bin/env python3
"""Gate boundaries for an executed finding. No API call.

The comparison is the raw float against the floor. 0.7 posts. 0.6999 and a
missing confidence go to second look. Read-only rules stay as they were.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def load():
    path = Path(__file__).resolve().parent / "jev-judge.py"
    spec = importlib.util.spec_from_file_location("jev_judge", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    mod = load()
    cases = [
        (True, "post", 0.7, "post"),
        (True, "post", 0.6999, "second look"),
        (True, "post", None, "second look"),
        (True, "drop", 0.99, "second look"),
        (True, "hold", 0.99, "second look"),
        (False, "post", 0.7, "post"),
        (False, "post", 0.6999, "hold"),
        (False, "drop", 0.99, "second look"),
        (False, "hold", 0.8, "hold"),
    ]
    for executed, choice, conf, want in cases:
        got, note = mod.bind_finding(executed, choice, conf)
        if got != want:
            raise SystemExit(
                f"executed={executed} choice={choice} conf={conf}: "
                f"got {got!r} ({note}), want {want!r}"
            )
        print(f"executed={executed} choice={choice} conf={conf} -> {got}")
    # No rounding before the comparison: a value that prints as 0.700 at
    # three decimals but is still under the floor must not post.
    got, _note = mod.bind_finding(True, "post", 0.6999)
    if round(0.6999, 3) >= mod.FINDING_POST_MIN and got == "post":
        raise SystemExit("rounded before compare")
    if not (0.7 >= mod.FINDING_POST_MIN):
        raise SystemExit("0.7 is under the floor")
    print("ok bind_finding")
    # The Jev cell is confidence in the choice; a hold or drop adds its post
    # probability. One work ballot's F5 read "hold 0.920" with post at 0.02.
    doc = {"findings": [
        {"id": "F1", "claim": "held", "repro_executed": False, "legs": {"a": "found"}},
        {"id": "F2", "claim": "posted", "repro_executed": True, "legs": {"a": "found"}},
        {"id": "F3", "claim": "no probabilities", "repro_executed": False, "legs": {"a": "found"}},
    ]}
    raw = {"model": "jev-test", "answers": {
        "F1": {"choice": "hold", "confidence": 0.92,
               "probabilities": {"drop": 0.03, "hold": 0.95, "post": 0.02}},
        "F2": {"choice": "post", "confidence": 0.81,
               "probabilities": {"drop": 0.04, "hold": 0.09, "post": 0.87}},
        "F3": {"choice": "drop", "confidence": 0.6},
    }}
    table = mod.render_convergence(doc, raw)
    for want in ("| hold 0.920 (post 0.02) |", "| post 0.810 |", "| drop 0.600 |",
                 "not in the finding"):
        if want not in table:
            raise SystemExit(f"convergence table lacks {want!r}:\n{table}")
    print("ok convergence Jev cell")
    # Audit: strong is executed, weak is read-only, missing is second look.
    # Misleading stays second look whatever the match.
    audit = [
        ("Misleading", "strong", "post", 0.95, "second look"),
        ("false-claim", "none", "post", 0.91, "second look"),
        ("false-claim", "strong", "post", 0.7, "post"),
        ("false-claim", "strong", "drop", 0.99, "second look"),
        ("false-claim", "weak", "post", 0.7, "post"),
        ("false-claim", "weak", "post", 0.69, "hold"),
        ("false-claim", "weak", "drop", 0.99, "second look"),
        ("false-claim", "weak", "hold", 0.8, "hold"),
        ("Inaccurate", True, "post", 0.7, "post"),
        ("false-claim", False, "post", 0.9, "second look"),
    ]
    for verdict, match, choice, conf, want in audit:
        got, note = mod.bind_audit_finding(verdict, match, choice, conf)
        if got != want:
            raise SystemExit(
                f"audit {verdict} match={match} choice={choice} conf={conf}: "
                f"got {got!r} ({note}), want {want!r}"
            )
        print(f"audit {verdict} match={match} {choice} {conf} -> {got}")
    # JSON true and null are not numbers. true must not become 1.0, and null
    # must not raise after jev-raw.json is written.
    true_answers = {
        "decision": {"choice": "implement", "confidence": True},
        "same_approach_shape": {"noul": True},
        "disagreements_only_minor": {"noul": True},
    }
    same = mod.noul(true_answers, "same_approach_shape")
    minor = mod.noul(true_answers, "disagreements_only_minor")
    choice, conf, _probs = mod.choice_answer(true_answers, "decision")
    if same is not None or minor is not None or conf is not None:
        raise SystemExit(f"JSON true became a score: same={same} minor={minor} conf={conf}")
    decision, notes, _changes = mod.bind_decision(choice, conf, same, minor, 2, [])
    if decision != "no automatic decision" or not notes:
        raise SystemExit(f"true scores bound {decision!r} notes={notes!r}")
    gate, _note = mod.bind_finding(True, "post", conf)
    if gate != "second look":
        raise SystemExit(f"true confidence on an executed finding gated {gate!r}")
    false_conf = mod.choice_answer({"decision": {"choice": "implement", "confidence": False}}, "decision")[1]
    if false_conf is not None:
        raise SystemExit(f"JSON false became {false_conf!r}")
    try:
        null_noul = mod.noul({"same_approach_shape": {"noul": None}}, "same_approach_shape")
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"null noul raised {type(exc).__name__}: {exc}") from exc
    if null_noul is not None:
        raise SystemExit(f"null noul became {null_noul!r}")
    number = {
        "decision": {"choice": "implement", "confidence": 0.95},
        "same_approach_shape": {"noul": 0.9},
        "disagreements_only_minor": {"noul": 0.9},
    }
    same_n = mod.noul(number, "same_approach_shape")
    minor_n = mod.noul(number, "disagreements_only_minor")
    choice_n, conf_n, _probs = mod.choice_answer(number, "decision")
    decision_n, notes_n, _changes = mod.bind_decision(choice_n, conf_n, same_n, minor_n, 2, [])
    if decision_n != "implement" or notes_n:
        raise SystemExit(f"numeric scores bound {decision_n!r} notes={notes_n!r}")
    print("ok JSON boolean and null scores")
    print("ok")


if __name__ == "__main__":
    main()
