"""
grounding.py — does the report only contain numbers the tools actually returned?

Teaching note:
  The rest of guardrails.py (InputGuardrails / ToolGuardrails / OutputGuardrails)
  answers "is this input/tool-call/output safe?" This module answers a narrower,
  deterministic question: "is every figure in the final report grounded in real
  tool output?" It is a *check*, not a blocker — it reports what it found and
  lets the caller decide whether to surface it, same as the existing
  prediction_flag check in OutputGuardrails.

  Ported from zvi's upstream/main (src/stocks_crew/guardrails.py), which framed
  it well: "call this tool, do not guess" is a prompt, not a guarantee — this is
  the plain-Python check that verifies the guarantee actually held, done in
  Python rather than by asking another model. A guardrail that can hallucinate
  is not a guardrail.

  Two checks:
    1. grounding — every figure in the report traces to some tool output.
    2. advice    — the report does not slip into investment advice.

  Both are heuristics tuned to under-report: a rounding tolerance, a scale
  tolerance for "44.1 million", and a floor under which integers are ignored.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

#: Numbers as they appear in prose: optional sign, thousands separators, decimals.
NUMBER_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")

#: Dates are stripped before scanning — they are figures in the regex sense but
#: not claims about the market.
ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

_MONTHS = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?"
    r"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
PROSE_DATE_RE = re.compile(
    r"\b(?:"
    rf"(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}"  # August 4, 2026
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?,?\s+\d{{4}}"  # 4 August 2026
    rf"|(?:{_MONTHS})\.?\s+\d{{4}}"  # August 2026
    r")\b",
    re.IGNORECASE,
)

#: Bare integers below this are ignored: section numbers, indicator windows
#: (20/50/14), "52-week", the horizon in days. Anything with a decimal point is
#: checked regardless of size — that is where prices, percentages and RSI live.
MIN_INTEGER_CLAIM = 1000

#: A report may restate a volume of 44,123,456 as "44.1 million", so a claim is
#: also compared against the tool's number divided by each of these.
SCALES = (1.0, 1e3, 1e6, 1e9)

#: Phrases that are advice however they are phrased. Deliberately multi-word:
#: matching a bare "buy" would fire on the mandatory disclaimer.
ADVICE_RE = re.compile(
    r"\b("
    r"price targets?"
    r"|strong (?:buy|sell)"
    r"|(?:we|i) recommend"
    r"|(?:you|investors|readers) should (?:buy|sell|hold)"
    r"|will (?:reach|hit|climb to|fall to|drop to)"
    r"|(?:expected|likely) to (?:reach|hit)"
    r")\b",
    re.IGNORECASE,
)

#: How far back to look for a negation before an advice match.
NEGATION_WINDOW = 48
NEGATION_RE = re.compile(r"\b(?:no|not|never|without|cannot|can't|avoid|refrain)\b", re.IGNORECASE)


def _to_float(literal: str) -> float:
    return float(literal.replace(",", ""))


def _decimal_places(literal: str) -> int:
    return len(literal.split(".")[1]) if "." in literal else 0


def extract_numbers(text: str) -> List[str]:
    """Every numeric literal in `text`, as written, minus dates."""
    without_dates = PROSE_DATE_RE.sub(" ", ISO_DATE_RE.sub(" ", text or ""))
    return NUMBER_RE.findall(without_dates)


def _is_claim(literal: str) -> bool:
    """Is this literal a market figure worth checking, or scaffolding?"""
    if "." in literal:
        return True
    return abs(_to_float(literal)) >= MIN_INTEGER_CLAIM


def _grounded(literal: str, evidence: Iterable[float]) -> bool:
    """Does some tool output contain this number, allowing for rounding?

    Magnitudes are compared, not signed values — a report properly writing
    "down 1.24" for a tool's `-1.24` should not be flagged as invented.
    """
    value = abs(_to_float(literal))
    tolerance = 10.0 ** -_decimal_places(literal)
    for number in evidence:
        for scale in SCALES:
            if abs(abs(number) / scale - value) < tolerance:
                return True
    return False


def _advice_hits(report: str) -> List[Dict[str, str]]:
    """Advice-shaped phrases, skipping the ones that are being denied."""
    hits: List[Dict[str, str]] = []
    for match in ADVICE_RE.finditer(report or ""):
        before = report[max(0, match.start() - NEGATION_WINDOW) : match.start()]
        if NEGATION_RE.search(before):
            continue
        start = max(0, match.start() - 60)
        hits.append({
            "phrase": match.group(0).lower(),
            "context": report[start : match.end() + 60].replace("\n", " ").strip(),
        })
    return hits


def check_report(report: str, evidence: Iterable[str]) -> Dict[str, Any]:
    """Check a report against the tool output it was supposed to use.

    Args:
        report: The final report text.
        evidence: The *full* output of every tool call in the run (see
            packages/shared-observability/tool_tracer.py's ToolTracer.evidence
            — full, not the truncated preview kept for display/logging).

    Returns a dict with `passed` plus the detail behind it. It reports; it does
    not block — see how OutputGuardrails.check() uses this in guardrails.py.
    """
    numbers: List[float] = []
    for output in evidence:
        numbers.extend(_to_float(n) for n in extract_numbers(str(output)))

    claims = [n for n in extract_numbers(report) if _is_claim(n)]
    ungrounded = [n for n in dict.fromkeys(claims) if not _grounded(n, numbers)]
    advice = _advice_hits(report)

    notes: List[str] = []
    if not numbers:
        notes.append(
            "No tool output to check against — the agents produced this report "
            "without calling a single tool."
        )
    if claims and not ungrounded:
        notes.append("Every figure in the report traces to a tool result.")

    return {
        "passed": not ungrounded and not advice,
        "figures_checked": len(set(claims)),
        "ungrounded_figures": ungrounded,
        "advice_phrases": advice,
        "evidence_values": len(set(numbers)),
        "notes": notes,
    }
