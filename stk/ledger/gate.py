"""The trust gate. Pure: same inputs, same tier, so every decision replays."""

from __future__ import annotations

from stk.ledger.contracts import (
    EXTRACTED_PROPS,
    GateInput,
    PolicyRow,
    PrincipalKind,
    Prop,
    Tier,
)


def resolve(
    policies: list[PolicyRow], capability: str, doc_type: str, sensitivity: str
) -> PolicyRow:
    """Most specific policy row wins; exact beats "*", capability weighs most.
    No matching row is a configuration error, never a silent default."""
    best, best_score = None, -1
    for p in policies:
        score = 0
        for have, want, weight in (
            (p.capability, capability, 4),
            (p.doc_type, doc_type, 2),
            (p.sensitivity, sensitivity, 1),
        ):
            if have == want:
                score += weight
            elif have != "*":
                break
        else:
            if score > best_score:
                best, best_score = p, score
    if best is None:
        raise LookupError(f"no policy for {capability}/{doc_type}/{sensitivity}")
    return best


def gate(s: GateInput, policy: PolicyRow) -> tuple[Tier, str]:
    """Return the tier and the rule that decided it."""
    if s.prop is Prop.ACCESS:
        return Tier.ACCEPTED, "access_log"
    if s.retracted:
        return Tier.REJECTED, "retracted"
    if s.author_kind is PrincipalKind.AGENT and s.prop in EXTRACTED_PROPS:
        if not s.has_span:
            return Tier.REJECTED, "no_span"
        if not s.span_valid:
            return Tier.REJECTED, "span_not_in_source"
    if s.verdict == "reject":
        return Tier.REJECTED, "human_reject"
    if s.verdict == "accept":
        return Tier.ACCEPTED, "human_accept"
    if s.author_kind is PrincipalKind.HUMAN and s.attested:
        return Tier.ACCEPTED, "human_attested"
    if policy.auto_accept and s.author_kind is PrincipalKind.AGENT:
        return Tier.ACCEPTED, "auto_accept"
    if (
        policy.min_origins
        and policy.calibration
        and s.distinct_origins >= policy.min_origins
    ):
        return Tier.CORROBORATED, "origins"
    return Tier.UNCONFIRMED, "default"
