"""The answer's shape and the rules applied to it after the model wrote it (BUILD_SPEC 7.4 "Answer format", 7.7,
R9).

The model answers by calling the ``give_answer`` tool (one structured call works the same way on Gemini and
Claude). The fields follow the spec's format: **Answer** (lead, 1-2 sentences, with the key figure) · **Evidence**
(2-4 bullets) · **Why it matters** · **Recommendation** · **Data notes** · **Next question**. Every figure carries
its result id: ``₹28.0 L [r3]``.

After the model answers, code decides what stays:

* the number check (``provenance``): one repair turn for unsupported figures, then sentences that still carry one
  are removed and a data note says so;
* **₹ impact (R9):** a recommendation is kept when the person asked for one, or when its rupee impact is
  supported by a result *and* material: at least the workspace's ``impact_threshold_inr`` (₹1 lakh) or
  ``impact_threshold_pct`` (5%) of the monthly figure it is measured against;
* data notes from code (quality warnings, an unconfirmed dictionary, limits that were hit) are added as codes,
  never left to the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent import provenance
from app.llm.base import ToolSpec

MAX_LEAD = 600
MAX_ITEM = 500
MAX_EVIDENCE = 5
MAX_NOTES = 6
MAX_NEXT = 3

ANSWER_TOOL = ToolSpec(
    name="give_answer",
    description=(
        "Give your final answer. Call this exactly once, when you have the numbers you need. Every figure must "
        "come from a tool result and be followed by its result id in brackets, e.g. '₹28.0 L [r3]' or "
        "'-17.7% [r2]'. Write amounts in rupees with lakh/crore (₹8,450, ₹12.4 L, ₹1.24 Cr)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lead": {
                "type": "string",
                "description": "The answer in 1-2 sentences, with the single most important figure.",
            },
            "key_figure": {
                "type": "string",
                "description": "The most important figure exactly as written in the lead, e.g. '-17.7%'.",
            },
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-4 short bullets with the figures that support the answer.",
            },
            "why_it_matters": {
                "type": "string",
                "description": "Only if the finding is material. Otherwise leave empty.",
            },
            "recommendation": {
                "type": "object",
                "description": "Only if the person asked for advice, or the finding is material (see rules).",
                "properties": {
                    "text": {"type": "string"},
                    "impact_inr": {
                        "type": "number",
                        "description": "The estimated rupee impact, a number taken from a tool result.",
                    },
                    "impact_result_id": {"type": "string"},
                    "basis_inr": {
                        "type": "number",
                        "description": "The monthly figure the impact is compared with, from a tool result.",
                    },
                },
                "required": ["text"],
            },
            "data_notes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data gaps, assumptions and quality issues the person should know.",
            },
            "next_questions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-3 useful follow-up questions, phrased as the person would ask them.",
            },
            "charts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Chart ids from make_chart to show with the answer.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "low when the data is thin or a key input is missing.",
            },
        },
        "required": ["lead", "evidence"],
    },
)

_ASKS_FOR_ADVICE = re.compile(
    r"\b(recommend\w*|suggest\w*|advi[cs]e\w*|should (i|we)|what (should|can|could) (i|we)|"
    r"how (can|do|could|should|to) (i|we)?|what to do|next steps?|action|improve|fix|reduce|cut|increase|grow|"
    r"boost|optimi[sz]e|where to spend|reallocat\w*)\b",
    re.IGNORECASE,
)


def asks_for_advice(question: str) -> bool:
    return bool(_ASKS_FOR_ADVICE.search(question))


@dataclass
class Recommendation:
    text: str
    impact_inr: float | None = None
    impact_result_id: str | None = None
    basis_inr: float | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "impact_inr": self.impact_inr,
            "impact_result_id": self.impact_result_id,
            "basis_inr": self.basis_inr,
        }


@dataclass
class Answer:
    lead: str
    key_figure: str | None = None
    evidence: list[str] = field(default_factory=list)
    why_it_matters: str | None = None
    recommendation: Recommendation | None = None
    data_notes: list[str] = field(default_factory=list)
    next_questions: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)
    confidence: str | None = None

    def texts(self) -> list[str]:
        out = [self.lead, *self.evidence]
        if self.why_it_matters:
            out.append(self.why_it_matters)
        if self.recommendation:
            out.append(self.recommendation.text)
        return out


def _text(value: Any, limit: int = MAX_ITEM) -> str:
    return str(value).strip()[:limit] if value is not None else ""


def _texts(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [t for t in (_text(v) for v in value) if t][:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return None


def parse(raw: dict[str, Any]) -> Answer:
    """The ``give_answer`` input as an ``Answer``; lenient about types, strict about lengths."""
    recommendation = None
    rec = raw.get("recommendation")
    if isinstance(rec, dict) and _text(rec.get("text")):
        recommendation = Recommendation(
            text=_text(rec.get("text")),
            impact_inr=_number(rec.get("impact_inr")),
            impact_result_id=_text(rec.get("impact_result_id"), 10) or None,
            basis_inr=_number(rec.get("basis_inr")),
        )
    elif isinstance(rec, str) and rec.strip():
        recommendation = Recommendation(text=_text(rec))
    confidence = raw.get("confidence")
    return Answer(
        lead=_text(raw.get("lead"), MAX_LEAD),
        key_figure=_text(raw.get("key_figure"), 40) or None,
        evidence=_texts(raw.get("evidence"), MAX_EVIDENCE),
        why_it_matters=_text(raw.get("why_it_matters")) or None,
        recommendation=recommendation,
        data_notes=_texts(raw.get("data_notes"), MAX_NOTES),
        next_questions=_texts(raw.get("next_questions"), MAX_NEXT),
        charts=[c for c in _texts(raw.get("charts"), 4) if re.fullmatch(r"c\d+", c)],
        confidence=confidence if confidence in ("high", "medium", "low") else None,
    )


def from_plain_text(text: str) -> Answer:
    """A model that answered in plain text instead of calling ``give_answer``: the text is the lead."""
    lines = [line.strip(" -*•") for line in text.strip().splitlines() if line.strip()]
    lead = lines[0] if lines else ""
    return Answer(
        lead=lead[:MAX_LEAD], evidence=[line[:MAX_ITEM] for line in lines[1 : MAX_EVIDENCE + 1]]
    )


# --------------------------------------------------------------------------------------------- checks
def number_check(answer: Answer, store: provenance.ResultStore) -> provenance.NumberCheck:
    """Check every figure; adds missing citations in place. ``report.unsupported`` lists the rest."""
    report = provenance.NumberCheck()
    answer.lead = provenance.check_text(answer.lead, store, report)
    answer.evidence = [provenance.check_text(e, store, report) for e in answer.evidence]
    if answer.why_it_matters:
        answer.why_it_matters = provenance.check_text(answer.why_it_matters, store, report)
    if answer.recommendation:
        answer.recommendation.text = provenance.check_text(
            answer.recommendation.text, store, report
        )
    return report


def remove_unsupported(answer: Answer, unsupported: list[str]) -> int:
    """Remove what still carries an unsupported figure. Returns how many pieces were removed."""
    removed = 0
    lead = provenance.remove_unsupported(answer.lead, unsupported)
    if lead != answer.lead:
        removed += 1
    kept_evidence = [e for e in answer.evidence if not any(u in e for u in unsupported)]
    removed += len(answer.evidence) - len(kept_evidence)
    answer.evidence = kept_evidence
    if not lead:
        # The lead is gone: the first remaining bullet leads, else the answer says it could not be checked.
        lead = answer.evidence.pop(0) if answer.evidence else ""
    answer.lead = lead
    if answer.key_figure and answer.key_figure not in answer.lead:
        answer.key_figure = None
    if answer.why_it_matters:
        why = provenance.remove_unsupported(answer.why_it_matters, unsupported)
        removed += why != answer.why_it_matters
        answer.why_it_matters = why or None
    if answer.recommendation:
        text = provenance.remove_unsupported(answer.recommendation.text, unsupported)
        if text != answer.recommendation.text:
            removed += 1
            answer.recommendation = None if not text else answer.recommendation
            if answer.recommendation:
                answer.recommendation.text = text
    return removed


def _supported(value: float | None, store: provenance.ResultStore) -> bool:
    if value is None:
        return False
    return any(
        abs(abs(v) - abs(value)) <= max(1.0, abs(value) * 0.005)
        for vals in store.values.values()
        for v in vals
    )


def impact_rule(
    answer: Answer,
    store: provenance.ResultStore,
    *,
    asked: bool,
    threshold_inr: float,
    threshold_pct: float,
) -> str | None:
    """R9. Returns why a recommendation was removed (a note code), or ``None`` when it stays (or there is none)."""
    rec = answer.recommendation
    if rec is None or asked:
        return None
    impact = rec.impact_inr if _supported(rec.impact_inr, store) else None
    if impact is None:
        answer.recommendation = None
        return "recommendation_not_material"
    basis = rec.basis_inr if _supported(rec.basis_inr, store) else None
    material = abs(impact) >= threshold_inr or (
        basis is not None and basis != 0 and abs(impact) / abs(basis) * 100.0 >= threshold_pct
    )
    if not material:
        answer.recommendation = None
        return "recommendation_not_material"
    return None


def as_json(
    answer: Answer, *, notes: list[dict[str, Any]], charts: list[dict[str, Any]]
) -> dict[str, Any]:
    cited = set()
    for text in answer.texts():
        cited |= provenance.citations_in(text)
    return {
        "lead": answer.lead,
        "key_figure": answer.key_figure
        if answer.key_figure and answer.key_figure in answer.lead
        else None,
        "evidence": answer.evidence,
        "why_it_matters": answer.why_it_matters,
        "recommendation": answer.recommendation.as_json() if answer.recommendation else None,
        "data_notes": answer.data_notes,
        "notes": notes,
        "next_questions": answer.next_questions,
        "charts": charts,
        "confidence": answer.confidence,
        "cited": sorted(cited, key=lambda c: int(c[1:])),
    }
