"""Deterministic rule evaluation: (approved requirement, bidder claims) -> status.

No model is involved here. Statuses are never inferred from retrieval scores or model
confidence. Each claim is judged on its own; if claims lead to different outcomes the
result is a contradiction for human review.
"""
import re

from .schemas import Claim, Requirement

RULE_ENGINE_VERSION = "1.0"
MEETS, FAILS, INSUFFICIENT, REVIEW, NA = ("MEETS_REQUIREMENT", "DOES_NOT_MEET_REQUIREMENT", "INSUFFICIENT_EVIDENCE",
                                          "REQUIRES_HUMAN_REVIEW", "NOT_APPLICABLE")
STOP = {"shall", "will", "be", "with", "the", "of", "and", "process", "to", "as", "per", "is", "a", "on", "in", "grade"}


def _src(c: Claim):
    where = f"p.{c.source_page}" if c.source_page else (c.source_label or "")
    return f"'{c.raw_value}' ({c.evidence_type.replace('_', ' ')}, {where}, {c.source_label or ''})".replace(", )", ")")


def _norm_tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower().replace("-", "")) if w not in STOP}


def text_matches(required, offered):
    """Exact (normalised) match, or one ' / '-separated alternative matches, or all key words
    of the requirement appear in the offer ('H2/H4' accepts either H2 or H4)."""
    norm = lambda t: re.sub(r"[^a-z0-9/]", "", t.lower())
    if norm(required) == norm(offered):
        return True
    alts = [a for a in re.split(r"\s+/\s+", required) if a.strip()]
    if len(alts) > 1 and any(text_matches(a, offered) for a in alts):
        return True
    have = _norm_tokens(offered)
    for word in re.findall(r"[a-z0-9/]+", required.lower().replace("-", "")):  # same normalisation as offer
        options = {w for w in _norm_tokens(word.replace("/", " "))}
        if options and not options & have:
            return False
    return bool(_norm_tokens(required))


def _judge(req: Requirement, c: Claim):
    """One claim -> (status, reason)."""
    rule = req.evaluation_rule
    op = rule.op
    if op == "provided":
        ok = isinstance(c.value, str) and c.value.strip(" -") != ""
        return (MEETS, "value furnished") if ok else (INSUFFICIENT, "no value furnished")
    if op == "matches":
        if not c.value or not str(c.value).strip(" -"):
            return INSUFFICIENT, "no value furnished"
        if text_matches(str(rule.value), str(c.value)):
            return MEETS, f"'{c.value}' matches '{rule.value}'"
        return REVIEW, f"'{c.value}' differs from '{rule.value}' (text comparison - needs expert judgement)"
    if op == "in":
        ok = c.value in (rule.permitted_values or [])
        return (MEETS if ok else FAILS), f"'{c.value}' {'is' if ok else 'is not'} in {rule.permitted_values}"
    if not isinstance(c.value, (int, float)):
        if c.unit and c.unit != rule.unit:
            return REVIEW, f"bidder states {_src(c)}; {c.unit} cannot be converted exactly to {rule.unit}"
        return INSUFFICIENT, f"no numeric value in {_src(c)}"
    v, t = float(c.value), float(rule.value)
    unit = f" {rule.unit}" if rule.unit else ""
    cmp = {"<=": v <= t, "<": v < t, ">=": v >= t, ">": v > t, "==": v == t}
    if op in cmp:
        return (MEETS if cmp[op] else FAILS), f"{v:g}{unit} {op} {t:g}{unit} is {cmp[op]}"
    if op == "approx":
        if v == t:
            return MEETS, f"{v:g}{unit} equals nominal {t:g}{unit}"
        pct = (v - t) / t * 100 if t else float("inf")
        return REVIEW, f"{v:g}{unit} differs from nominal {t:g}{unit} by {pct:+.1f}%; no tolerance stated in tender"
    return REVIEW, f"operator '{op}' has no deterministic rule"


def evaluate(req: Requirement, claims: list[Claim]):
    """-> (status, explanation, calc)"""
    rule = req.evaluation_rule
    calc = {"rule": rule.model_dump(exclude_none=True), "engine_version": RULE_ENGINE_VERSION, "claims": []}
    if rule.op == "document":
        if not claims:
            if req.mandatory_status == "conditional":
                return REVIEW, f"Not found; conditional requirement ({req.applicability_conditions}) - applicability needs review", calc
            return INSUFFICIENT, "Required document not found in the bidder's submission", calc
        c = claims[0]
        if "missing:" in (c.raw_value or ""):
            return REVIEW, f"Document found ({c.source_label}, p.{c.source_page}) but {c.raw_value.split('; ')[1]}", calc
        return MEETS, (f"Document found: {c.source_label} (p.{c.source_page}). Presence checked only; "
                       "authenticity and contents not verified"), calc
    exempt = [c for c in claims if c.value == "exemption_claimed"]
    claims = [c for c in claims if c.value != "exemption_claimed"]
    if not claims:
        if exempt:
            c = exempt[0]
            return REVIEW, (f"No payment evidence; bidder claims exemption ({c.source_label}, p.{c.source_page}). "
                            f"Exemption conditions: {req.applicability_conditions}"), calc
        return INSUFFICIENT, "No evidence found in the bidder's submission", calc

    if rule.aggregate == "max":
        numeric = [c for c in claims if isinstance(c.value, (int, float))]
        if numeric:
            claims = [max(numeric, key=lambda c: c.value)]
    judged = [(c, *_judge(req, c)) for c in claims]
    calc["claims"] = [{"value": c.value, "unit": c.unit, "raw": c.raw_value, "status": s, "reason": r,
                       "evidence_type": c.evidence_type} for c, s, r in judged]
    informative = [j for j in judged if j[1] != INSUFFICIENT] or judged
    statuses = {s for _, s, _ in informative}
    detail = "; ".join(f"{r} - {_src(c)}" for c, s, r in informative)
    if len(statuses) > 1:
        kind = "Contradictory evidence" if {MEETS, FAILS} <= statuses else "Evidence values differ"
        return REVIEW, f"{kind}: {detail}", calc
    status = statuses.pop()
    if any(c.verification_status == "ai_extracted" for c, _, _ in informative) and status != INSUFFICIENT:
        return REVIEW, f"Value extracted by AI, needs confirmation ({status}): {detail}", calc
    needs_third_party = "third_party_document" in req.required_evidence_types
    if status == MEETS and needs_third_party and not any(c.evidence_type == "third_party_document" for c, _, _ in informative):
        return REVIEW, f"Meets numerically but only self-declared; tender requires documentary proof: {detail}", calc
    return status, detail, calc
