#!/usr/bin/env python3
"""Jev (TypeSafe System One) extractor judge for Conclave.

Bound to locked decision rules. Not a voter. Does not edit app source.
Requires TYPESAFE_API_KEY or JEV_API_KEY.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import audit_gate  # noqa: E402
import finding_context  # noqa: E402
from conclave_env import setting  # noqa: E402

API_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip(
    "/"
) + "/v1/systemone"
MODEL = setting("JEV_MODEL", "jev-latest")

# Bound thresholds (code enforces locked rules beyond Jev's raw Choice)
SAME_SHAPE_MIN = float(setting("JEV_SAME_SHAPE_MIN", "0.75"))
MINOR_ONLY_MIN = float(setting("JEV_MINOR_ONLY_MIN", "0.70"))
CHOICE_CONF_MIN = float(setting("JEV_CHOICE_CONF_MIN", "0.55"))
# A finding posts only at this confidence. An executed finding under it,
# or one Jev holds or drops, goes to second look. Read-only under it holds.
FINDING_POST_MIN = float(setting("JEV_FINDING_POST_MIN", "0.7"))


def api_key() -> str:
    key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY") or ""
    if not key:
        raise SystemExit(
            "missing TYPESAFE_API_KEY or JEV_API_KEY "
            "(e.g. export TYPESAFE_API_KEY=$(op read \"$CONCLAVE_JEV_OP_REF\"))"
        )
    return key


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_state(brief: str, opinions: list[str]) -> str:
    parts = ["# Brief\n", brief.strip(), "\n\n# Anonymized voter opinions\n"]
    for i, op in enumerate(opinions, 1):
        parts.append(f"\n## Opinion {i}\n{op.strip()}\n")
    parts.append(
        "\n# Rules for the judge\n"
        "- implement only if all opinions share the same approach shape "
        "(not merely similar wording).\n"
        "- implement_with_changes only for minor disagreements; "
        "exact changes must be listable from the opinions.\n"
        "- otherwise no_automatic_decision: list options and stop.\n"
        "- Do not invent a fourth design.\n"
    )
    return "".join(parts)


def questions() -> dict:
    return {
        "same_approach_shape": {
            "type": "noul",
            "instructions": (
                "Do ALL voter opinions agree on the same approach shape "
                "(same architecture / plan / merge disposition), ignoring wording "
                "differences? Answer yes only if a single shared shape is clear."
            ),
            "criteria": {
                "true": "One shared approach shape across all opinions.",
                "false": "Competing shapes, or too little agreement to treat as one plan.",
            },
        },
        "disagreements_only_minor": {
            "type": "noul",
            "instructions": (
                "If there are disagreements, are they ONLY minor tweaks "
                "(wording, small edge cases, tiny checklist items) rather than "
                "competing designs or material scope changes?"
            ),
            "criteria": {
                "true": "Only minor tweaks; one design still holds.",
                "false": "Material conflict, competing designs, or major unknowns.",
            },
        },
        "decision": {
            "type": "choice",
            "criteria": {
                "implement": (
                    "All voters agree on the same approach shape. "
                    "Safe to implement that shared shape as proposed."
                ),
                "implement_with_changes": (
                    "Voters mostly share one shape; only minor disagreements. "
                    "Implement that shape with exact small changes taken from the opinions."
                ),
                "no_automatic_decision": (
                    "Material conflict, missing information, or fewer than two "
                    "usable opinions. Do not invent a fourth design; list options and stop."
                ),
            },
        },
    }


def post_systemone(payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"jev HTTP {e.code}: {err}") from e


def call_jev(state: str) -> dict:
    return post_systemone(
        {"state": state, "model": MODEL, "questions": questions()}
    )


def _json_number(value: object) -> float | None:
    """A JSON number. true, false, and null are missing, not 1, 0, or an error."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def noul(answers: dict, key: str) -> float | None:
    a = answers.get(key) or {}
    if "noul" not in a:
        return None
    return _json_number(a["noul"])


def choice_answer(answers: dict, key: str) -> tuple[str | None, float | None, dict]:
    a = answers.get(key) or {}
    ch = a.get("choice")
    conf = a.get("confidence")
    probs = a.get("probabilities") or a.get("probability") or {}
    return (str(ch) if ch is not None else None, _json_number(conf), probs)


def _finite(x: float | None) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


def extract_change_bullets(opinions: list[str]) -> list[str]:
    """Collect actionable bullets from Approach sections. Fail-closed if empty."""
    bullets: list[str] = []
    seen: set[str] = set()
    for op in opinions:
        m = re.search(r"(?ms)^## Approach\n(.*?)(?=^## |\Z)", op)
        if not m:
            continue
        for line in m.group(1).splitlines():
            s = line.strip()
            if re.match(r"^[-*]\s+\S", s) or re.match(r"^\d+[.)]\s+\S", s):
                # normalize leading marker
                body = re.sub(r"^([-*]|\d+[.)])\s+", "", s).strip()
                key = body.lower()
                if len(body) < 8 or key in seen:
                    continue
                seen.add(key)
                bullets.append(body)
    return bullets[:12]


_DECISION_LABELS = {
    "implement": "implement",
    "implement_with_changes": "implement with changes",
    "no_automatic_decision": "no automatic decision",
}


def header_confidence(raw_choice: str | None, decision: str, conf: float | None) -> str:
    """Confidence in the bound decision.

    Jev's confidence belongs to the choice it returned. When the script
    overrides that choice, the header does not keep the overridden confidence.
    """
    if raw_choice not in _DECISION_LABELS:
        return "n/a"
    if _DECISION_LABELS[raw_choice] != decision or not _finite(conf):
        return "n/a"
    return f"{float(conf):.3f}"


def bind_decision(
    raw_choice: str | None,
    conf: float | None,
    same: float | None,
    minor: float | None,
    opinion_n: int,
    opinions: list[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Apply locked decision rules in code. Returns (decision, notes, exact_changes)."""
    notes: list[str] = []
    changes: list[str] = []
    decision = _DECISION_LABELS.get(raw_choice or "", "no automatic decision")
    if raw_choice and raw_choice not in _DECISION_LABELS:
        notes.append(f"unknown Jev choice '{raw_choice}'; forcing no automatic decision")
        decision = "no automatic decision"

    if opinion_n < 2:
        notes.append(
            f"need at least 2 opinions for implement*; got {opinion_n}"
        )
        decision = "no automatic decision"

    # Fail closed: missing / non-finite evidence never authorizes implement*
    if not (_finite(conf) and _finite(same) and _finite(minor)):
        notes.append(
            "missing or non-finite confidence/same_approach_shape/"
            "disagreements_only_minor; forcing no automatic decision"
        )
        return "no automatic decision", notes, changes

    assert conf is not None and same is not None and minor is not None

    if conf < CHOICE_CONF_MIN:
        notes.append(
            f"decision confidence {conf:.3f} < {CHOICE_CONF_MIN}; "
            "forcing no automatic decision"
        )
        decision = "no automatic decision"

    if decision == "implement" and same < SAME_SHAPE_MIN:
        notes.append(
            f"same_approach_shape {same:.3f} < {SAME_SHAPE_MIN}; "
            "cannot implement"
        )
        if minor >= MINOR_ONLY_MIN:
            decision = "implement with changes"
            notes.append("downgraded to implement with changes (minor disagreements)")
        else:
            decision = "no automatic decision"

    if decision == "implement with changes":
        if minor < MINOR_ONLY_MIN:
            notes.append(
                f"disagreements_only_minor {minor:.3f} < {MINOR_ONLY_MIN}; "
                "forcing no automatic decision"
            )
            decision = "no automatic decision"
        if same < 0.40:
            notes.append(
                f"same_approach_shape {same:.3f} too low for implement with changes"
            )
            decision = "no automatic decision"
        if decision == "implement with changes":
            changes = extract_change_bullets(opinions or [])
            if not changes:
                notes.append(
                    "could not extract exact change list from Approach bullets; "
                    "forcing no automatic decision"
                )
                decision = "no automatic decision"

    return decision, notes, changes


def load_run_header(d: Path) -> dict:
    path = d / "header.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def confidence_of_value(
    kind: str,
    decision: str,
    meta: dict | None = None,
    confidence: str | None = None,
) -> str:
    """What the confidence figure is about. The figure itself stays in confidence:.

    Design and mixed: which plan relationship the voters reached.
    When the script overrides Jev, confidence is n/a and this value is none.
    Review and audit findings: the lowest Jev confidence among posts, or none.
    An audit proposal has no post gate, so the value is none.
    """
    meta = meta or {}
    if kind in ("design", "mixed"):
        if confidence == "n/a":
            return "none"
        return {
            "implement": "voters propose the same plan",
            "implement with changes": "voters share a plan with minor changes",
            "no automatic decision": "voters propose different plans",
        }.get(decision, "none")
    if kind == "audit" and meta.get("gate") == "verdicts":
        return "none"
    if decision.startswith("comments to post") or decision.startswith("changes to draft"):
        return "lowest Jev confidence among findings gated to post"
    return "none"


def _header_text(val) -> str:
    """Header field text. Missing or blank is unknown. Zero is kept."""
    if isinstance(val, bool) or val is None:
        return "unknown"
    if isinstance(val, int):
        return str(val) if val >= 0 else "unknown"
    if isinstance(val, str):
        text = val.strip()
        return text if text else "unknown"
    return "unknown"


def format_judgment_header(
    *,
    decision: str,
    confidence: str,
    judge_model: str,
    meta: dict | None = None,
) -> str:
    """Fixed header. Same keys, same order, before any other content.

    confidence_of follows confidence. It names what that figure refers to.
    voter_c_model is included only when the run named an opt-in Grok voter.
    coverage_opus_model is included only when the Opus coverage leg ran.
    muse_model (and muse_seconds on review and audit) only when muse voted.
    argon_model when Argon voted. argon_skipped when that leg was named and skipped.
    missing_voters when a named voter had no valid ballot and the run still judged.
    Those keys do not replace voter_a_model, voter_b_model, or voter_c_model.
    voter_b_reasoning_tokens and voter_b_effort always follow voter_b_model.
    Review and audit put each leg's wall seconds directly after its model line:
    voter_a_seconds, voter_b_seconds, voter_c_seconds, coverage_opus_seconds.
    A missing or blank value is unknown. Zero is a real token count.
    Review adds stack after pr. Audit adds target fields after pr.
    Review keys and decision values stay.
    """
    meta = meta or {}
    chair = setting("CHAIR_MODEL") or "unknown"
    raw_kind = meta.get("kind")
    kind = raw_kind.strip() if isinstance(raw_kind, str) and raw_kind.strip() else "unknown"
    lines = [
        f"kind: {kind}",
        f"decision: {decision}",
        f"confidence: {confidence}",
        f"confidence_of: {confidence_of_value(kind, decision, meta, confidence)}",
        f"chair_model: {chair}",
        f"voter_a_model: {meta.get('voter_a_model') or 'unknown'}",
    ]
    timed = kind in ("review", "audit")
    if timed:
        lines.append(f"voter_a_seconds: {_header_text(meta.get('voter_a_seconds'))}")
    lines.append(f"voter_b_model: {meta.get('voter_b_model') or 'unknown'}")
    if timed:
        lines.append(f"voter_b_seconds: {_header_text(meta.get('voter_b_seconds'))}")
    lines.extend(
        [
            f"voter_b_reasoning_tokens: {_header_text(meta.get('voter_b_reasoning_tokens'))}",
            f"voter_b_effort: {_header_text(meta.get('voter_b_effort'))}",
        ]
    )
    if meta.get("voter_c_model"):
        lines.append(f"voter_c_model: {meta['voter_c_model']}")
        if timed:
            lines.append(f"voter_c_seconds: {_header_text(meta.get('voter_c_seconds'))}")
    if meta.get("coverage_opus_model"):
        lines.append(f"coverage_opus_model: {meta['coverage_opus_model']}")
        if timed:
            lines.append(
                f"coverage_opus_seconds: {_header_text(meta.get('coverage_opus_seconds'))}"
            )
    if meta.get("muse_model"):
        lines.append(f"muse_model: {meta['muse_model']}")
        if timed:
            lines.append(f"muse_seconds: {_header_text(meta.get('muse_seconds'))}")
    if meta.get("argon_model"):
        lines.append(f"argon_model: {meta['argon_model']}")
    if meta.get("argon_skipped"):
        lines.append(f"argon_skipped: {meta['argon_skipped']}")
    missing = meta.get("missing_voters")
    if isinstance(missing, str) and missing.strip():
        lines.append(f"missing_voters: {missing.strip()}")
    lines.extend(
        [
            f"builder_model: {meta.get('builder_model') or 'unknown'}",
            f"judge_model: {judge_model}",
            f"branch: {meta.get('branch') or 'none'}",
            f"pr: {meta.get('pr') or 'none'}",
        ]
    )
    if kind == "review":
        lines.append(f"stack: {_header_text(meta.get('stack'))}")
        lines.append(f"head_oid: {_header_text(meta.get('head_oid'))}")
    if kind == "audit":
        for key in (
            "target",
            "target_type",
            "snapshot_sha256",
            "target_drift",
            "redaction_kept",
            "gate",
        ):
            val = meta.get(key)
            if not isinstance(val, str) or not val.strip():
                val = "none"
            lines.append(f"{key}: {val}")
    return "\n".join(lines) + "\n\n"


def render_judgment(
    *,
    council_id: str,
    model: str,
    decision: str,
    notes: list[str],
    same: float | None,
    minor: float | None,
    conf: float | None,
    probs: dict,
    opinion_n: int,
    exact_changes: list[str] | None = None,
    header_meta: dict | None = None,
    raw_choice: str | None = None,
) -> str:
    same_s = f"{same:.3f}" if same is not None else "n/a"
    minor_s = f"{minor:.3f}" if minor is not None else "n/a"
    conf_s = f"{conf:.3f}" if conf is not None else "n/a"
    probs_s = ", ".join(f"{k}={float(v):.3f}" for k, v in sorted(probs.items())) or "n/a"

    if decision == "implement":
        consensus = (
            f"Jev reports shared approach shape (noul={same_s}) across {opinion_n} "
            "anonymized opinions. Wording may differ; shape agrees."
        )
        conflicts = "None material under locked rules."
        missing = "None flagged for an automatic implement."
        decision_body = (
            "implement\n\n"
            "Proceed with the shared approach shape in the opinions. "
            "Do not invent a fourth design."
        )
    elif decision == "implement with changes":
        consensus = (
            f"Majority shared shape (same_approach_shape={same_s}); "
            f"disagreements rated minor (noul={minor_s})."
        )
        conflicts = "Minor only; see Exact changes under Decision."
        missing = "None beyond the listed exact changes."
        change_lines = "\n".join(f"- {c}" for c in (exact_changes or []))
        decision_body = (
            "implement with changes\n\n"
            "Implement the shared shape with these exact changes "
            "(do not invent a fourth design):\n\n"
            f"{change_lines}"
        )
    else:
        consensus = (
            f"No automatic consensus under locked rules "
            f"(same_approach_shape={same_s}, minor_only={minor_s})."
        )
        conflicts = (
            "Material disagreement and/or insufficient agreement. "
            "Options are the distinct approach shapes in the opinion files — "
            "do not blend into a fourth design."
        )
        missing = (
            "Resolve conflicts or missing constraints in a new brief, "
            "or have a human pick an option."
        )
        decision_body = (
            "no automatic decision\n\n"
            "List options from the opinions and stop. Do not invent a fourth design.\n\n"
            "See `options.md`."
        )

    note_block = ""
    if notes:
        note_block = "\n".join(f"- {n}" for n in notes) + "\n\n"

    body = f"""# Judgment

council: {council_id}
judge_model: {model}
judge_family: jev (TypeSafe System One; outside vote+impl set)
decision_confidence: {conf_s}
decision_probabilities: {probs_s}
same_approach_shape: {same_s}
disagreements_only_minor: {minor_s}
opinion_count: {opinion_n}

## Consensus

{consensus}

## Conflicts

{conflicts}

## Missing

{missing}

## Decision

{decision_body}

### Binding notes

{note_block}Jev is the Decision authority for this cycle. Any later prose polish must not override Decision.
"""
    header = format_judgment_header(
        decision=decision,
        confidence=header_confidence(raw_choice, decision, conf),
        judge_model=model,
        meta=header_meta,
    )
    return header, header + body


def options_markdown(header: str) -> str:
    """Empty options form. Same header string judgment.md just used."""
    column = (
        "| option | voter A lean | voter B lean | tradeoffs | risks | "
        "accretiveness | cost to try (lines, days) | cheap | pick |"
    )
    separator = "|---|---|---|---|---|---|---|---|---|"
    note = (
        "Rows are the chair's, from the opinions, one per distinct approach "
        "shape, no blended option."
    )
    return f"{header}{column}\n{separator}\n\n{note}\n"


def load_findings(path: Path, *, audit: bool = False) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        raise SystemExit("findings.json needs an object with a findings array")
    if "verified_at" in doc and (
        not isinstance(doc["verified_at"], str)
        or not re.fullmatch(r"[0-9a-fA-F]{7,40}", doc["verified_at"])
    ):
        raise SystemExit("verified_at must be the head commit the chair verified at")
    seen: set[str] = set()
    for f in doc["findings"]:
        if not isinstance(f, dict):
            raise SystemExit("each finding must be an object")
        for key in (
            "id",
            "claim",
            "file",
            "line",
            "cited_code",
            "evidence",
            "repro_executed",
            "legs",
        ):
            if key not in f:
                raise SystemExit(f"finding missing {key}")
        fid = str(f["id"])
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", fid):
            raise SystemExit(f"bad finding id {fid!r}")
        if fid in seen:
            raise SystemExit(f"duplicate finding id {fid}")
        seen.add(fid)
        if not isinstance(f["repro_executed"], bool):
            raise SystemExit(f"{fid}: repro_executed must be a JSON boolean")
        if f["repro_executed"] and re.sub(
            r"[\s.]+$", "", str(f["evidence"]).strip().lower()
        ) in ("", "no run was made"):
            raise SystemExit(
                f"{fid}: repro_executed is true but evidence holds no command or output"
            )
        legs = f["legs"]
        if not isinstance(legs, dict) or not legs:
            raise SystemExit(f"{fid}: legs must be a non-empty object")
        for lk, lv in legs.items():
            if not re.fullmatch(r"[A-F]", str(lk)):
                raise SystemExit(f"{fid}: leg key {lk!r} must be a blinded letter A-F")
            if lv not in ("found", "missed", "cleared"):
                raise SystemExit(f"{fid}: leg {lk} must be found, missed, or cleared")
        if audit:
            for key in ("verdict", "url", "quote"):
                if key not in f:
                    raise SystemExit(f"{fid}: finding missing {key}")
            if f["verdict"] not in (
                "Inaccurate",
                "Misleading",
                "false-claim",
                "broken-step",
            ):
                raise SystemExit(f"{fid}: unknown verdict {f['verdict']!r}")
            if not isinstance(f["url"], str) or not f["url"].strip():
                raise SystemExit(f"{fid}: url must be a non-empty string")
            if not isinstance(f["quote"], str):
                raise SystemExit(f"{fid}: quote must be a string")
        if "source" in f and f["source"] is not None:
            src = f["source"]
            if not isinstance(src, dict):
                raise SystemExit(f"{fid}: source must be an object")
            leg = src.get("leg")
            row = src.get("row")
            if not isinstance(leg, str) or not re.fullmatch(r"[A-F]", leg):
                raise SystemExit(f"{fid}: source.leg must be one letter A-F")
            if isinstance(row, bool) or not isinstance(row, int) or row < 0:
                raise SystemExit(f"{fid}: source.row must be a non-negative int")
    return doc


def verification_line(doc: dict, f: dict) -> str:
    """What was done to check this finding at the reviewed head. Facts only."""
    at = str(doc.get("verified_at") or "")[:8]
    if f["repro_executed"]:
        where = f" at head {at}" if at else ""
        return (
            f"executed{where}. The evidence is the command that was run and its "
            "real output."
        )
    if at:
        return (
            f"checked at head {at} by reading; no runnable repro was found. "
            "The cited code below was read from the repo at that commit."
        )
    return "none: read-only, no run was made."


def build_findings_state(doc: dict) -> str:
    """State for Jev. Claims and cited code only: no leg labels, vendors, or counts."""
    parts = ["# Findings under review\n"]
    target = doc.get("target")
    if target:
        parts.append(f"target: {target}\n")
    parts.append(
        "\nJudge each finding from its claim, the cited code, the verification, "
        "and the evidence. A finding marked executed was run; its evidence is "
        "the command output, not an opinion.\n"
    )
    for f in doc["findings"]:
        fid = f["id"]
        parts.append(
            f"\n## {fid}\n"
            f"claim: {f['claim']}\n"
            f"cited: {f['file']}:{f['line']}\n"
            f"verification: {verification_line(doc, f)}\n"
            f"cited_code:\n{f['cited_code']}\n"
            f"evidence: {f['evidence']}\n"
        )
    return "".join(parts)


def findings_questions(doc: dict) -> dict:
    qs: dict = {}
    for f in doc["findings"]:
        fid = str(f["id"])
        executed = bool(f["repro_executed"])
        if executed:
            where = f" at head {str(doc['verified_at'])[:8]}" if doc.get("verified_at") else ""
            how = (
                f"The verification for {fid} was executed{where}: its evidence in "
                "state is the command that was run and its output. "
            )
            criteria = {
                "post": (
                    "The command output shows the stated failure, so the "
                    "finding should be reported."
                ),
                "hold": (
                    "The output is inconclusive: it does not show the stated "
                    "failure, or it ran something other than the claimed trigger."
                ),
                "drop": (
                    "The output contradicts the claim, or the failure it shows "
                    "is not the one claimed."
                ),
            }
        else:
            how = (
                f"{fid} was checked by reading. Its cited_code in state is the "
                "enclosing function and one caller, read from the repo. "
            )
            criteria = {
                "post": (
                    "The cited code (function and caller) shows the stated "
                    "failure happens under reachable inputs; the finding "
                    "should be reported."
                ),
                "hold": (
                    "Plausible but unproven: the failure depends on behaviour "
                    "not shown in the cited code, or on input state that "
                    "reading cannot establish. Name what is missing."
                ),
                "drop": (
                    "The cited code does not support the failure, the trigger "
                    "is unreachable, or the finding is a style opinion."
                ),
            }
        qs[fid] = {
            "type": "choice",
            "instructions": (
                "Should this review finding be reported to the author?\n\n"
                f"Finding {fid}: {f['claim']}. "
                + how
                + f"Cited code is the {fid} cited_code in state. "
                f"Evidence is the {fid} evidence in state."
            ),
            "criteria": criteria,
        }
    return qs


def findings_request(doc: dict) -> dict:
    return {
        "state": build_findings_state(doc),
        "model": MODEL,
        "questions": findings_questions(doc),
    }


def bind_finding(
    repro_executed: bool, choice: str | None, conf: float | None
) -> tuple[str, str]:
    """Mechanical gate. Returns (gate, note).

    An executed finding posts only when Jev says post and the raw confidence
    is at least FINDING_POST_MIN. There is no rounding before that comparison.
    Jev drop, hold, post below the floor, or a missing confidence sends an
    executed finding to second look.
    A read-only finding posts only on Jev post at or above FINDING_POST_MIN.
    Jev drop on a read-only finding is a second look, not a discard.
    Everything else holds.
    """
    if repro_executed:
        if (
            choice == "post"
            and _finite(conf)
            and float(conf) >= FINDING_POST_MIN
        ):
            return (
                "post",
                f"executed repro: Jev post at {float(conf):.4f} >= {FINDING_POST_MIN}",
            )
        if choice == "drop":
            return (
                "second look",
                "executed repro: Jev drop; second look, not posted",
            )
        if choice == "hold":
            return (
                "second look",
                "executed repro: Jev hold; second look, not posted",
            )
        if choice == "post":
            shown = f"{float(conf):.4f}" if _finite(conf) else "missing"
            return (
                "second look",
                f"executed repro: Jev post confidence {shown} < {FINDING_POST_MIN}; second look",
            )
        return (
            "second look",
            f"executed repro: missing or unknown Jev choice {choice!r}; second look",
        )
    if choice not in ("post", "hold", "drop"):
        return "hold", f"missing or unknown Jev choice {choice!r}; holding"
    if choice == "drop":
        return (
            "second look",
            "read-only drop: second look by the chair, not discarded",
        )
    if choice == "post":
        if _finite(conf) and float(conf) >= FINDING_POST_MIN:
            return (
                "post",
                f"read-only post at {float(conf):.3f} >= {FINDING_POST_MIN}",
            )
        shown = f"{float(conf):.3f}" if _finite(conf) else "missing"
        return (
            "hold",
            f"read-only post confidence {shown} < {FINDING_POST_MIN}; holding",
        )
    return "hold", "read-only hold"


def bind_audit_finding(
    verdict: str,
    quote_ok: bool | str,
    choice: str | None,
    conf: float | None,
) -> tuple[str, str]:
    """Audit findings gate. Misleading never posts. A failed quote is second look.

    strong uses the executed rule. weak uses the read-only rule.
    True means strong. False and none mean the quote was not found.
    """
    if verdict == "Misleading":
        return "second look", "Misleading is second look whatever Jev says"
    if verdict not in ("false-claim", "broken-step", "Inaccurate"):
        return "second look", f"verdict {verdict!r} is not posted by this gate"
    match: str
    if quote_ok is True or quote_ok == "strong":
        match = "strong"
    elif quote_ok == "weak":
        match = "weak"
    else:
        match = "none"
    if match == "strong":
        return bind_finding(True, choice, conf)
    if match == "weak":
        return bind_finding(False, choice, conf)
    return "second look", "quote not verified; second look, not hold"


def _md_cell(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).replace("|", "/")


# Filenames in blind/key.txt. The claude file is Fable unless that seat
# itself launched Opus native (no separate coverage leg).
_LEG_FILE_LABELS = {
    "voter-opus.json": "Anthropic Opus",
    "voter-codex.md": "OpenAI Astra",
    "voter-grok-report.md": "xAI Grok",
    "voter-muse.md": "Meta Muse",
}


def claude_leg_label(header: dict) -> str:
    """Vendor plus family for voter-claude.json after unblinding."""
    if header.get("coverage_opus_model"):
        return "Anthropic Fable"
    va = str(header.get("voter_a_model") or "").lower()
    if "lineup said opus" in va or ("opus" in va and "fable" not in va):
        return "Anthropic Opus"
    return "Anthropic Fable"


def load_leg_labels(d: Path) -> dict[str, str]:
    """Map blinded letter to a vendor column name using blind/key.txt."""
    path = d / "blind" / "key.txt"
    if not path.is_file():
        return {}
    header = load_run_header(d)
    claude_label = claude_leg_label(header)
    labels: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        letter, src = line.split("=", 1)
        name = Path(src.strip()).name
        if name == "voter-claude.json":
            labels[letter.strip()] = claude_label
        else:
            labels[letter.strip()] = _LEG_FILE_LABELS.get(name, name)
    return labels


def render_convergence(
    doc: dict,
    raw: dict,
    leg_labels: dict[str, str] | None = None,
    *,
    gate_fn=None,
    repro_fn=None,
) -> str:
    answers = raw.get("answers") or {}
    model = str(raw.get("model") or MODEL)
    leg_labels = leg_labels or {}
    leg_names: list[str] = []
    for f in doc["findings"]:
        for name in f["legs"]:
            if name not in leg_names:
                leg_names.append(name)
    leg_names.sort()
    columns = [leg_labels.get(name, name) for name in leg_names]
    header = (
        ["id", "claim", "repro"]
        + columns
        + ["Jev", "gate"]
    )
    lines = [
        "# Convergence",
        "",
        f"judge_model: {model}",
        f"post_min: {FINDING_POST_MIN}",
        "",
    ]
    if "Anthropic Fable" in columns and "Anthropic Opus" in columns:
        lines.append(
            "Fable and Opus are one vendor; their agreement is one vote."
        )
        lines.append("")
    # Two sessions read "hold 0.920" as 92% likely real (2026-10-08).
    lines.append(
        "Jev: its choice and its confidence in that choice, not in the "
        "finding. (post N) is its probability that the finding should post."
    )
    lines.append("")
    lines.extend(
        [
            "| " + " | ".join(header) + " |",
            "|" + "|".join("---" for _ in header) + "|",
        ]
    )
    notes: list[str] = []
    for f in doc["findings"]:
        fid = str(f["id"])
        choice, conf, probs = choice_answer(answers, fid)
        if gate_fn is None:
            gate, note = bind_finding(bool(f["repro_executed"]), choice, conf)
        else:
            gate, note = gate_fn(f, choice, conf)
        if _finite(conf) and choice:
            # Three decimals, unless that rounding would cross the post floor.
            shown = f"{float(conf):.3f}"
            if (float(shown) >= FINDING_POST_MIN) != (float(conf) >= FINDING_POST_MIN):
                shown = f"{float(conf):.4f}"
            jev = f"{choice} {shown}"
        elif choice:
            jev = f"{choice} n/a"
        else:
            jev = "n/a"
        if choice and choice != "post":
            try:
                post_p = float(probs.get("post"))
            except (AttributeError, TypeError, ValueError):
                post_p = None
            if _finite(post_p):
                jev += f" (post {post_p:.2f})"
        if repro_fn is None:
            repro = "executed" if f["repro_executed"] else "read-only"
        else:
            repro = repro_fn(f)
        row = [
            _md_cell(fid),
            _md_cell(f["claim"]),
            repro,
        ]
        for name in leg_names:
            row.append(_md_cell(f["legs"].get(name, "")))
        row.append(_md_cell(jev))
        row.append(_md_cell(gate))
        lines.append("| " + " | ".join(row) + " |")
        notes.append(f"- {fid}: {note}")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.extend(notes)
    lines.append("")
    return "\n".join(lines)


def review_outcome(doc: dict, raw: dict, *, gate_fn=None) -> tuple[str, str]:
    """Return (decision line, confidence) for a review judgment header."""
    answers = raw.get("answers") or {}
    npost = nhold = nsecond = 0
    post_conf: list[float] = []
    for f in doc["findings"]:
        choice, conf, _probs = choice_answer(answers, str(f["id"]))
        if gate_fn is None:
            gate, _note = bind_finding(bool(f["repro_executed"]), choice, conf)
        else:
            gate, _note = gate_fn(f, choice, conf)
        if gate == "post":
            npost += 1
            # Jev's confidence in hold or drop is not confidence in post.
            if choice == "post" and _finite(conf):
                post_conf.append(float(conf))
        elif gate == "second look":
            nsecond += 1
        else:
            nhold += 1
    counts = f"({npost} post, {nhold} hold, {nsecond} second look)"
    if not doc["findings"]:
        return f"clean {counts}", "n/a"
    if npost:
        conf_s = f"{min(post_conf):.3f}" if post_conf else "n/a"
        return f"comments to post {counts}", conf_s
    return f"human review {counts}", "n/a"


DECIDE_MARKER = "TODO(chair)"
DECIDE_HASH_NAME = "decide.md.sha256"


def _confidence_text(conf: float | None) -> str:
    """Same decimals as the convergence table. Missing is unknown."""
    if not _finite(conf):
        return "unknown"
    shown = f"{float(conf):.3f}"
    if (float(shown) >= FINDING_POST_MIN) != (float(conf) >= FINDING_POST_MIN):
        shown = f"{float(conf):.4f}"
    return shown


def _norm_path(path: str) -> str:
    """Drop a leading ./ repeatedly. lstrip("./") would turn .env into env."""
    text = path.strip()
    while text.startswith("./"):
        text = text.removeprefix("./")
    return text


def load_stack(d: Path) -> dict:
    path = d / "stack.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown"}
    return data if isinstance(data, dict) else {"status": "unknown"}


def later_in_stack_line(finding: dict, header_stack: str, stack: dict) -> str:
    """Later open PRs that change this finding's file. No file is unknown."""
    raw_file = finding.get("file")
    if not isinstance(raw_file, str) or not raw_file.strip():
        return "unknown"
    file = _norm_path(raw_file)
    hs = header_stack.strip() if isinstance(header_stack, str) else ""
    if hs == "none":
        return "no stack"
    if hs == "unknown" or not hs:
        return "unknown"
    if stack.get("status") != "stack":
        return "unknown"
    later: list[dict] = []
    seen_target = False
    for pr in stack.get("prs") or []:
        if not isinstance(pr, dict):
            continue
        if pr.get("target"):
            seen_target = True
            continue
        if seen_target:
            later.append(pr)
    if not seen_target:
        return "unknown"
    hits: list[str] = []
    unknowns: list[str] = []
    for pr in later:
        files = []
        for item in pr.get("files") or []:
            if isinstance(item, str) and item.strip():
                files.append(_norm_path(item))
        changed = pr.get("changedFiles")
        capped = (
            isinstance(changed, int)
            and not isinstance(changed, bool)
            and len(files) < changed
        )
        if file in files:
            title = " ".join(str(pr.get("title") or "").split())
            hits.append(f"#{pr.get('number')} {title}".rstrip())
            continue
        if capped:
            unknowns.append(
                f"unknown (PR #{pr.get('number')} lists 100 of {changed} files)"
            )
    if hits or unknowns:
        line = ", ".join(hits + unknowns)
        if stack.get("truncated"):
            line += "; walk truncated at 20 PRs"
        return line
    if stack.get("truncated"):
        return "not touched in the first 20 PRs; walk truncated"
    return "not touched later in the stack"


def render_decide(
    doc: dict, raw: dict, header_stack: str, stack: dict, ballot: str = "<ballot id>"
) -> str:
    """Skeleton beside judgment.md. The claim is not copied into it.

    The closing line asks for ballot-prefixed ids: finding ids repeat
    across ballots, so a bare f3 can name a different finding elsewhere.
    """
    findings = doc.get("findings") or []
    if not findings:
        return "There is nothing to decide.\n"
    answers = raw.get("answers") or {}
    blocks: list[str] = []
    for finding in findings:
        fid = str(finding["id"])
        choice, conf, _probs = choice_answer(answers, fid)
        gate, _note = bind_finding(bool(finding["repro_executed"]), choice, conf)
        sure = "proven by running it" if finding["repro_executed"] else "read, not run"
        later = later_in_stack_line(finding, header_stack, stack)
        blocks.append(
            "\n".join(
                [
                    f"{fid} — **what breaks, for whom**: {DECIDE_MARKER}",
                    f"**how sure**: {sure}; {gate}; {_confidence_text(conf)}",
                    f"**later PRs that change this file**: {later}",
                    (
                        f"**recommend**: {DECIDE_MARKER} post as a bug / "
                        "post as a question / post as advisory / hold. "
                        f"{DECIDE_MARKER} one line of why. "
                        f"{DECIDE_MARKER} whether the baseline before this PR "
                        "had the same problem."
                    ),
                    f"**cost to fix**: {DECIDE_MARKER}",
                ]
            )
        )
    example = f"{ballot} {findings[0]['id']}"
    return "\n\n".join(blocks) + (
        "\n\nReply with the findings to post, each with its ballot id "
        f"(for example `{example}`), or none.\n"
    )


def _prefilled_by_id(text: str) -> dict[str, tuple[str, str]]:
    """Finding id to its how-sure line and later-in-the-stack line."""
    found: dict[str, tuple[str, str]] = {}
    current: str | None = None
    how = ""
    later = ""
    for line in text.splitlines():
        match = re.match(r"^(\S+) — \*\*what breaks", line)
        if match:
            if current is not None:
                found[current] = (how, later)
            current = match.group(1)
            how = ""
            later = ""
            continue
        if line.startswith("**how sure**:"):
            how = line
        elif line.startswith("**later PRs that change this file**:"):
            later = line
    if current is not None:
        found[current] = (how, later)
    return found


def _keep_filled_decide(existing: str, rendered: str, want: set[str]) -> bool:
    """A filled file stays only when ids and pre-filled lines still match."""
    if DECIDE_MARKER in existing:
        return False
    have = set(re.findall(r"^(\S+) — \*\*what breaks", existing, re.M))
    if have != want:
        return False
    old = _prefilled_by_id(existing)
    new = _prefilled_by_id(rendered)
    return all(old.get(fid) == new.get(fid) for fid in want)


def decide_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decide_is_unedited(d: Path, text: str) -> bool:
    """True when text matches the hash of the last script write.

    A missing sidecar means the chair edited the file. That is the safe default.
    """
    sidecar = d / DECIDE_HASH_NAME
    if not sidecar.is_file():
        return False
    recorded = sidecar.read_text(encoding="utf-8").strip()
    return bool(recorded) and recorded == decide_digest(text)


def write_decide_files(d: Path, text: str) -> None:
    (d / "decide.md").write_text(text, encoding="utf-8")
    (d / DECIDE_HASH_NAME).write_text(decide_digest(text) + "\n", encoding="utf-8")


def write_decide(d: Path, doc: dict, raw: dict) -> None:
    """Write decide.md. Keep a filled file only when it still matches.

    The finding ids must match, and every pre-filled line (how sure, later
    PRs that change this file) must equal what would be rendered now.
    A chair edit is copied to decide.md.prev first, replacing any older copy.
    An unedited skeleton is rewritten and leaves decide.md.prev alone.
    """
    meta = load_run_header(d)
    header_stack = meta.get("stack") if isinstance(meta.get("stack"), str) else ""
    rendered = render_decide(doc, raw, header_stack, load_stack(d), d.name)
    path = d / "decide.md"
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        want = {str(f["id"]) for f in doc.get("findings") or []}
        if _keep_filled_decide(existing, rendered, want):
            print(
                "note: decide.md left in place; finding ids and pre-filled lines match",
                file=sys.stderr,
            )
            return
        if not decide_is_unedited(d, existing):
            prev = d / "decide.md.prev"
            prev.write_text(existing, encoding="utf-8")
            print("note: copied decide.md to decide.md.prev", file=sys.stderr)
    write_decide_files(d, rendered)


def write_review_judgment(d: Path, doc: dict, raw: dict, table: str) -> str:
    decision, confidence = review_outcome(doc, raw)
    judge = str(raw.get("model") or "n/a")
    if not doc["findings"]:
        judge = "n/a"
    meta = load_run_header(d)
    header = format_judgment_header(
        decision=decision,
        confidence=confidence,
        judge_model=judge,
        meta=meta,
    )
    text = header + table
    if not text.endswith("\n"):
        text += "\n"
    (d / "judgment.md").write_text(text, encoding="utf-8")
    (d / "convergence.md").write_text(table, encoding="utf-8")
    if (str(meta.get("kind") or "")).strip() == "review":
        write_decide(d, doc, raw)
    return text


def audit_findings_questions(doc: dict) -> dict:
    qs: dict = {}
    for finding in doc["findings"]:
        fid = str(finding["id"])
        qs[fid] = {
            "type": "choice",
            "instructions": (
                "Should this finding be included in the drafted copy changes?\n\n"
                f"Finding {fid}: {finding['claim']}. "
                f"Verdict is {finding['verdict']}. "
                "The cited snapshot text, url, and quote are in state."
            ),
            "criteria": {
                "post": (
                    "The quote from the fetched source shows the claim is false "
                    "or the step cannot be completed as written."
                ),
                "hold": "Plausible, but the quote does not settle it.",
                "drop": (
                    "The quote does not support the claim, or the claim is a "
                    "settled choice."
                ),
            },
        }
    return qs


def audit_findings_request(doc: dict) -> dict:
    return {
        "state": audit_gate.audit_findings_state(doc),
        "model": MODEL,
        "questions": audit_findings_questions(doc),
    }


def _quote_row(fid: str, status: dict, *, source_used: bool | None = None) -> dict:
    row = {
        "id": fid,
        "quote_verified": bool(status["quote_verified"]),
        "match": status["match"],
        "occurrences": int(status["occurrences"]),
        "words": int(status["words"]),
        "reason": status["reason"],
    }
    if source_used is not None:
        row["source_used"] = source_used
    return row


def resolve_source_quote(d: Path, finding: dict) -> tuple[str, str, str]:
    """Return (url, quote, error) from the unblinded report.

    error is empty on success. It never contains the url or the quote.
    """
    src = finding.get("source")
    if not isinstance(src, dict):
        return "", "", "bad source"
    letter = src.get("leg")
    row = src.get("row")
    key = d / "blind" / "key.txt"
    if not key.is_file():
        return "", "", "missing key file"
    report: Path | None = None
    for line in key.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        got, path = line.split("=", 1)
        if got.strip() == letter:
            report = Path(path.strip())
            break
    if report is None:
        return "", "", f"unknown leg {letter}"
    if not report.is_file():
        return "", "", "unparsable report"
    header = load_run_header(d)
    target_type = header.get("target_type")
    if not isinstance(target_type, str) or not target_type.strip():
        return "", "", "unparsable report"
    try:
        text = audit_gate.load_report_text(report)
    except OSError:
        return "", "", "unparsable report"
    findings, err = audit_gate.parse_final_json_block(text, target_type.strip())
    if err or findings is None:
        return "", "", "unparsable report"
    if isinstance(row, bool) or not isinstance(row, int) or row < 0 or row >= len(findings):
        return "", "", "row out of range"
    item = findings[row]
    if not isinstance(item, dict):
        return "", "", "unparsable report"
    if item.get("file") != finding.get("file") or item.get("line") != finding.get("line"):
        return "", "", "source not resolved"
    url = item.get("url")
    quote = item.get("quote")
    if not isinstance(url, str) or not isinstance(quote, str):
        return "", "", "unparsable report"
    return url, quote, ""


def source_quote_reason(status: dict) -> str:
    """Fixed category for a source-resolved quote.

    The reason must not carry a URL, host, path, or raw tool message.
    words and occurrences stay on the row.
    """
    reason = str(status.get("reason") or "")
    match = str(status.get("match") or "")
    if reason == "empty quote":
        return "empty quote"
    if reason == "pdf":
        return "pdf"
    if "unsupported url" in reason:
        return "unsupported url"
    if match == "strong" or reason == "quote found":
        return "quote found"
    if match == "weak":
        return "quote too short or repeated"
    if reason == "quote not in fetched text":
        return "quote not in fetched text"
    return "fetch failed"


def write_quote_check(d: Path, doc: dict) -> dict[str, str]:
    """Write quote-check.json. Return id -> match (strong, weak, or none).

    When a finding carries source, url and quote come from the unblinded
    report. Those strings are not written, printed, or sent to Jev.
    """
    rows = []
    matches: dict[str, str] = {}
    for finding in doc["findings"]:
        fid = str(finding["id"])
        if finding.get("source") is not None:
            url, quote, err = resolve_source_quote(d, finding)
            if err:
                status = {
                    "quote_verified": False,
                    "match": "none",
                    "occurrences": 0,
                    "words": 0,
                    "reason": err,
                }
                source_used = False
            else:
                status = dict(audit_gate.quote_status(url, quote))
                status["reason"] = source_quote_reason(status)
                source_used = True
            row = _quote_row(fid, status, source_used=source_used)
        else:
            status = audit_gate.quote_status(
                str(finding.get("url") or ""),
                str(finding.get("quote") or ""),
            )
            row = _quote_row(fid, status)
        matches[fid] = str(status["match"])
        rows.append(row)
    (d / "quote-check.json").write_text(
        json.dumps({"findings": rows}, indent=2) + "\n", encoding="utf-8"
    )
    return matches


def write_audit_judgment(
    d: Path,
    doc: dict,
    raw: dict,
    table: str,
    verified: dict[str, str],
) -> str:
    meta = load_run_header(d)
    proposal = meta.get("target_type") == "proposal" or meta.get("gate") == "verdicts"

    def gate_of(finding: dict, choice: str | None, conf: float | None):
        return bind_audit_finding(
            str(finding.get("verdict")),
            verified.get(str(finding["id"]), "none"),
            choice,
            conf,
        )

    if proposal:
        decision, confidence = "human review", "n/a"
    else:
        decision, confidence = review_outcome(doc, raw, gate_fn=gate_of)
        decision = decision.replace("comments to post", "changes to draft", 1)
    judge = str(raw.get("model") or "n/a")
    if not doc["findings"]:
        judge = "n/a"
    header = format_judgment_header(
        decision=decision,
        confidence=confidence,
        judge_model=judge,
        meta=meta,
    )
    preface = "A post includes the finding in the drafted copy changes.\n\n"
    verdicts = ""
    verdicts_path = d / "verdicts.md"
    if proposal and verdicts_path.is_file():
        verdicts = verdicts_path.read_text(encoding="utf-8")
        if not verdicts.endswith("\n"):
            verdicts += "\n"
        verdicts += "\n"
    text = header + verdicts + preface + table
    if not text.endswith("\n"):
        text += "\n"
    (d / "judgment.md").write_text(text, encoding="utf-8")
    (d / "convergence.md").write_text(table, encoding="utf-8")
    return text


def _audit_gate(verified: dict[str, str]):
    def gate_of(finding: dict, choice: str | None, conf: float | None):
        return bind_audit_finding(
            str(finding.get("verdict")),
            verified.get(str(finding["id"]), "none"),
            choice,
            conf,
        )

    def repro_of(finding: dict) -> str:
        match = verified.get(str(finding["id"]), "none")
        if match == "strong":
            return "executed"
        if match == "weak":
            return "read-only"
        return "unverified"

    return gate_of, repro_of


def _expand_cited_code(d: Path, doc: dict, header: dict, repo: str) -> None:
    """Fill each short cited_code with the enclosing function and one caller.

    The head is the commit the ballot reviewed (header.json head_oid), or the
    commit the chair verified at. Without a repo or a head the quote stays as
    the chair wrote it, and the note says so.
    """
    head = str(header.get("head_oid") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        head = str(doc.get("verified_at") or "")
        if not re.fullmatch(r"[0-9a-fA-F]{40}", head):
            head = ""
    if not repo or not head:
        why = "no --repo" if not repo else "no head commit in header.json"
        notes = [f"{f['id']}: not expanded ({why})" for f in doc["findings"]]
    else:
        notes = finding_context.expand_findings(doc, repo, head)
    (d / "cited-code-notes.txt").write_text("\n".join(notes) + "\n", encoding="utf-8")
    for line in notes:
        print(f"jev-judge: cited_code {line}", file=sys.stderr)


def findings_main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(
            "usage: jev-judge.py --findings <dir> [--canned response.json] [--repo <git dir>]"
        )
    d = Path(argv[0])
    canned: Path | None = None
    repo = ""
    i = 1
    while i < len(argv):
        if argv[i] == "--canned":
            if i + 1 >= len(argv):
                raise SystemExit("--canned requires a file")
            canned = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--repo":
            if i + 1 >= len(argv):
                raise SystemExit("--repo requires a directory")
            repo = argv[i + 1]
            i += 2
            continue
        raise SystemExit(f"unknown findings argument: {argv[i]}")
    findings_path = d / "findings.json"
    if not findings_path.is_file():
        raise SystemExit(f"missing {findings_path}")
    header = load_run_header(d)
    audit = header.get("kind") == "audit"
    doc = load_findings(findings_path, audit=audit)
    leg_labels = load_leg_labels(d)
    if not audit and doc["findings"]:
        _expand_cited_code(d, doc, header, repo)
    verified: dict[str, str] = {}
    gate_of = None
    repro_of = None
    if audit:
        verified = write_quote_check(d, doc)
        gate_of, repro_of = _audit_gate(verified)
    if not doc["findings"]:
        empty = {"model": "n/a", "answers": {}}
        table = render_convergence(
            doc, empty, leg_labels, gate_fn=gate_of, repro_fn=repro_of
        )
        if audit:
            text = write_audit_judgment(d, doc, empty, table, verified)
        else:
            text = write_review_judgment(d, doc, empty, table)
        print(text, end="" if text.endswith("\n") else "\n")
        return
    payload = audit_findings_request(doc) if audit else findings_request(doc)
    (d / "jev-findings-request.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    if canned is not None:
        if not canned.is_file():
            raise SystemExit(f"canned response not found: {canned}")
        raw = json.loads(canned.read_text(encoding="utf-8"))
    else:
        raw = post_systemone(payload)
    (d / "jev-findings-raw.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table = render_convergence(
        doc, raw, leg_labels, gate_fn=gate_of, repro_fn=repro_of
    )
    if audit:
        text = write_audit_judgment(d, doc, raw, table, verified)
    else:
        text = write_review_judgment(d, doc, raw, table)
    print(text, end="" if text.endswith("\n") else "\n")


def chair_inputs(d: Path) -> str:
    """Text the chair is given for a design or mixed Decision.

    Brief, anonymized opinions, and the judgment files. The Muse shadow
    verdict is a ballot file and is not part of this text.
    """
    parts = [load_text(d / "brief.md")]
    parts.extend(load_decision_opinions(d))
    for name in ("judgment.md", "options.md", "header.json"):
        path = d / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def load_decision_opinions(d: Path) -> list[str]:
    """Opinion files in numeric order. At least two, and no gap from 1."""
    nums: list[int] = []
    for p in d.glob("opinion-*.md"):
        m = re.fullmatch(r"opinion-(\d+)\.md", p.name)
        if m and p.stat().st_size > 0:
            nums.append(int(m.group(1)))
    nums.sort()
    if not nums or nums != list(range(1, nums[-1] + 1)):
        found = ", ".join(f"opinion-{n}.md" for n in nums) or "none"
        raise SystemExit(f"opinion numbering has a gap; found {found}")
    if len(nums) < 2:
        raise SystemExit(f"need at least 2 opinions; found {len(nums)}")
    return [load_text(d / f"opinion-{i}.md") for i in nums]


def _jev_cell(raw: dict | None, change_id: str) -> str:
    if raw is None:
        return "unavailable"
    choice, conf, probs = choice_answer(raw.get("answers") or {}, change_id)
    if not choice:
        return "n/a"
    shown = f"{float(conf):.3f}" if _finite(conf) else "n/a"
    if isinstance(probs, dict) and probs:
        prob_s = ", ".join(f"{k}={float(v):.3f}" for k, v in sorted(probs.items()))
        return f"{choice}, confidence {shown} (probabilities: {prob_s})"
    return f"{choice}, confidence {shown}"


def render_verdicts_table(d: Path, doc: dict, raw: dict | None) -> str:
    ids = [str(change["id"]) for change in doc["changes"]]
    letters: list[str] = []
    parsed_by_letter: dict[str, dict[str, str] | None] = {}
    blind = d / "blind"
    if blind.is_dir():
        for path in sorted(blind.glob("leg-*.md")):
            letter = path.name[len("leg-") : -len(".md")]
            if not re.fullmatch(r"[A-F]", letter):
                continue
            letters.append(letter)
            parsed, err = audit_gate.parse_verdict_lines(
                path.read_text(encoding="utf-8", errors="replace"),
                ids,
            )
            parsed_by_letter[letter] = None if err else parsed
    headers = ["change", *[f"leg {letter}" for letter in letters], "Jev"]
    lines = [
        "# Verdicts",
        "",
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for change_id in ids:
        row = [_md_cell(change_id)]
        for letter in letters:
            parsed = parsed_by_letter.get(letter)
            if not parsed:
                row.append("incomplete")
            else:
                row.append(_md_cell(parsed.get(change_id, "incomplete")))
        row.append(_md_cell(_jev_cell(raw, change_id)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def _clear_verdict_outputs(d: Path) -> None:
    # Before any step that can fail, so a rerun cannot keep the previous answer.
    for name in (
        "jev-verdicts-raw.json",
        "jev-verdicts-unavailable",
        "verdicts.md",
        "judgment.md",
    ):
        (d / name).unlink(missing_ok=True)


def verdicts_fetch(d: Path, canned: Path | None) -> None:
    _clear_verdict_outputs(d)
    doc = audit_gate.load_changes(d / "changes.json")
    payload = {
        "state": audit_gate.verdicts_state(doc),
        "model": MODEL,
        "questions": audit_gate.verdicts_questions(doc),
    }
    (d / "jev-verdicts-request.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    marker = d / "jev-verdicts-unavailable"
    if canned is not None:
        if not canned.is_file():
            raise SystemExit(f"canned response not found: {canned}")
        raw = json.loads(canned.read_text(encoding="utf-8"))
        (d / "jev-verdicts-raw.json").write_text(
            json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return
    if not (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY")):
        marker.write_text("no API key\n", encoding="utf-8")
        return
    raw = post_systemone(payload)
    (d / "jev-verdicts-raw.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def verdicts_render(d: Path) -> str:
    doc = audit_gate.load_changes(d / "changes.json")
    marker = d / "jev-verdicts-unavailable"
    raw = None
    if not marker.is_file():
        raw_path = d / "jev-verdicts-raw.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else None
    table = render_verdicts_table(d, doc, raw)
    (d / "verdicts.md").write_text(
        table if table.endswith("\n") else table + "\n", encoding="utf-8"
    )
    meta = load_run_header(d)
    meta["kind"] = "audit"
    meta["gate"] = "verdicts"
    if not meta.get("target_type"):
        meta["target_type"] = "proposal"
    judge = "n/a"
    if isinstance(raw, dict) and raw.get("model"):
        judge = str(raw["model"])
    header = format_judgment_header(
        decision="human review",
        confidence="n/a",
        judge_model=judge,
        meta=meta,
    )
    text = header + table
    if not text.endswith("\n"):
        text += "\n"
    (d / "judgment.md").write_text(text, encoding="utf-8")
    return text


def verdicts_main(argv: list[str]) -> None:
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(
            "usage: jev-judge.py --verdicts <dir> "
            "[--canned FILE] [--fetch-only|--render-only]"
        )
    d = Path(argv[0])
    canned: Path | None = None
    mode = "both"
    i = 1
    while i < len(argv):
        if argv[i] == "--canned":
            if i + 1 >= len(argv):
                raise SystemExit("--canned requires a file")
            canned = Path(argv[i + 1])
            i += 2
            continue
        if argv[i] == "--fetch-only":
            mode = "fetch"
            i += 1
            continue
        if argv[i] == "--render-only":
            mode = "render"
            i += 1
            continue
        raise SystemExit(f"unknown verdicts argument: {argv[i]}")
    if mode in ("both", "fetch"):
        verdicts_fetch(d, canned)
    if mode in ("both", "render"):
        text = verdicts_render(d)
        print(text, end="" if text.endswith("\n") else "\n")


def _tool_main(argv: list[str]) -> bool:
    """Small audit helpers. Return True when argv was handled."""
    if not argv:
        return False
    cmd = argv[0]
    if cmd == "--check-changes":
        if len(argv) != 2:
            raise SystemExit("usage: jev-judge.py --check-changes changes.json")
        audit_gate.load_changes(Path(argv[1]))
        print("ok")
        return True
    if cmd == "--changes-md":
        if len(argv) != 2:
            raise SystemExit("usage: jev-judge.py --changes-md changes.json")
        doc = audit_gate.load_changes(Path(argv[1]))
        sys.stdout.write(audit_gate.changes_markdown(doc))
        return True
    if cmd == "--keep-list":
        words = audit_gate.keep_words_in_files([Path(p) for p in argv[1:]])
        print(words)
        return True
    if cmd == "--check-report":
        if len(argv) < 3:
            raise SystemExit(
                "usage: jev-judge.py --check-report TYPE report [changes.json]"
            )
        changes = Path(argv[3]) if len(argv) > 3 and argv[3] else None
        msg = audit_gate.check_report(argv[1], Path(argv[2]), changes)
        print(msg)
        if msg != "ok":
            raise SystemExit(1)
        return True
    return False


def lean_questions() -> dict:
    return {
        "effort": {
            "type": "choice",
            "criteria": {
                "low": "A short or obvious question. One look is enough.",
                "medium": "A normal question. It needs a careful read.",
                "high": "A hard question. It needs a long look.",
            },
        }
    }


def lean_state(question: str, excerpt: str) -> str:
    text = "Question:\n" + question.strip() + "\n"
    if excerpt.strip():
        text += "\nExcerpt of the input:\n" + excerpt.strip() + "\n"
    return text


def lean_choice(raw: object) -> str | None:
    answers = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(answers, dict):
        return None
    choice, _conf, _probs = choice_answer(answers, "effort")
    if choice in ("low", "medium", "high"):
        return choice
    return None


def lean_main(argv: list[str]) -> None:
    """Print low, medium, or high. Exit 1 when the response has no such choice."""
    if not argv or argv[0] in ("-h", "--help"):
        raise SystemExit(
            "usage: jev-judge.py --lean --question TEXT [--excerpt TEXT] [--canned FILE]"
        )
    question = ""
    excerpt = ""
    canned: Path | None = None
    i = 0
    while i < len(argv):
        if argv[i] == "--question" and i + 1 < len(argv):
            question = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--excerpt" and i + 1 < len(argv):
            excerpt = argv[i + 1]
            i += 2
            continue
        if argv[i] == "--canned" and i + 1 < len(argv):
            canned = Path(argv[i + 1])
            i += 2
            continue
        raise SystemExit(f"unknown lean argument: {argv[i]}")
    if not question.strip():
        raise SystemExit("--question is required")
    if canned is not None:
        if not canned.is_file():
            raise SystemExit(f"canned response not found: {canned}")
        raw = json.loads(canned.read_text(encoding="utf-8"))
    else:
        raw = post_systemone({
            "state": lean_state(question, excerpt),
            "model": MODEL,
            "questions": lean_questions(),
        })
    choice = lean_choice(raw)
    if choice is None:
        raise SystemExit("lean: no low, medium, or high choice")
    print(choice)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--findings":
        findings_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--verdicts":
        verdicts_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--lean":
        lean_main(sys.argv[2:])
        return
    if _tool_main(sys.argv[1:]):
        return
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage: jev-judge.py <id> <dir>  "
            "# expects brief.md and opinion-*.md in dir"
        )
    council_id = sys.argv[1]
    d = Path(sys.argv[2])
    brief = load_text(d / "brief.md")
    opinions = load_decision_opinions(d)

    state = build_state(brief, opinions)
    (d / "jev-state.md").write_text(state, encoding="utf-8")

    raw = call_jev(state)
    (d / "jev-raw.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    answers = raw.get("answers") or {}
    model = str(raw.get("model") or MODEL)
    same = noul(answers, "same_approach_shape")
    minor = noul(answers, "disagreements_only_minor")
    raw_choice, conf, probs = choice_answer(answers, "decision")
    if not isinstance(probs, dict):
        probs = {}

    decision, notes, changes = bind_decision(
        raw_choice, conf, same, minor, len(opinions), opinions
    )
    header, judgment = render_judgment(
        council_id=council_id,
        model=model,
        decision=decision,
        notes=notes,
        same=same,
        minor=minor,
        conf=conf,
        probs=probs,
        opinion_n=len(opinions),
        exact_changes=changes,
        header_meta=load_run_header(d),
        raw_choice=raw_choice,
    )
    (d / "judgment.md").write_text(judgment, encoding="utf-8")
    options_path = d / "options.md"
    if decision == "no automatic decision":
        options_path.write_text(options_markdown(header), encoding="utf-8")
    elif options_path.is_file():
        options_path.unlink()
    print(
        f"jev judge: model={model} decision={decision} "
        f"conf={conf} same={same} minor={minor} changes={len(changes)}"
    )


if __name__ == "__main__":
    main()
