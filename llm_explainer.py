"""
================================================================================
R3 ADDRESS ACCURACY — LLM EXPLAINER
================================================================================

Generates a human-readable explanation for each record's decision.

Two modes:

  TEMPLATE_MODE (default — no API key required):
    Deterministic rule-based explanation built from feature values and
    decision reason code. Fast, free, predictable, runs offline.

  LLM_MODE (when ANTHROPIC_API_KEY is set):
    Uses claude-haiku-4-5 to write a more nuanced one-sentence explanation
    grounded in the same feature values. Costs roughly $0.0001 per record.

The LLM call is wrapped in try/except so a network failure, API outage,
or auth error gracefully degrades to TEMPLATE_MODE — the pipeline never
fails because of the explainer.

USAGE
-----
    explanations = explain_records(per_record_df, mode='auto')
    # mode='auto'     -> use LLM if API key present, else template
    # mode='template' -> force template
    # mode='llm'      -> force LLM (raises if no key)

The input DataFrame must have at minimum these columns:
    R3, p_r3_wrong, p_call_conclusive, decision_label, decision_reason,
    feat_specialty_has_taxonomy, feat_claims_n, feat_claims_days_since,
    feat_claims_recent_zip_match, feat_only_pv_found, feat_only_ov_found
"""

import os
import json
from typing import Optional, List

import pandas as pd


# ============================================================================
# TEMPLATE-BASED EXPLAINER
# ============================================================================

REASON_TEMPLATES = {
    'KEEP_R3_HIGH_CONFIDENCE':
        "Model is {confidence:.0%} confident R3's verdict is correct. "
        "{evidence_summary}",
    'MEDIUM_UNCERTAINTY_KEEP_R3_FAILED_CALL_THRESHOLDS':
        "Model uncertain but call unlikely to resolve "
        "(P(conclusive)={p_conc:.0%}). Keeping R3's verdict as conservative default. "
        "{evidence_summary}",
    'HIGH_CONF_FLIP_VERIFY_BY_CALL':
        "Model is {confidence:.0%} confident R3 is wrong. {root_cause} "
        "Sending to robocall to verify before flipping.",
    'HIGH_CONF_PASSIVE_FLIP':
        "Model is {confidence:.0%} confident R3 is wrong but call is "
        "unlikely to succeed. Flipping label based on model + evidence: {root_cause}",
    'MEDIUM_UNCERTAINTY_CALLED':
        "Model has medium uncertainty (P(R3 wrong)={p_wrong:.0%}). "
        "Call has good pickup probability ({p_conc:.0%}); sending for verification.",
    'R3_INCONCLUSIVE_CALL_LIKELY_CONCLUSIVE':
        "R3 itself was inconclusive. Phone is likely to answer "
        "(P(conclusive)={p_conc:.0%}); sending to robocall to obtain a definitive verdict.",
    'R3_INCONCLUSIVE_CALL_UNLIKELY':
        "R3 was inconclusive and this record didn't make the call cap "
        "(priority too low or call thresholds not met). Leaving as INCONCLUSIVE — record needs manual review.",
}


def _root_cause(row) -> str:
    """Identify the dominant feature pattern explaining why model thinks R3 is wrong."""
    cues = []
    if row.get('feat_specialty_has_taxonomy', 0) == 1 and row.get('feat_claims_has_any', 0) == 0:
        cues.append("Record came from stale upstream source (NPI taxonomy code in specialty) "
                    "AND provider has no recent billing activity")
    elif row.get('feat_specialty_has_taxonomy', 0) == 1:
        cues.append("Record came from stale upstream source (NPI taxonomy code in specialty)")
    if row.get('feat_claims_strong_contradict', 0) == 1:
        cues.append("provider has 20+ recent claims but none from this ZIP, suggesting they've moved")
    if row.get('feat_claims_strong_corroborate', 0) == 1:
        cues.append("provider has recent claims billing from this exact street and ZIP")
    if row.get('feat_only_pv_found', 0) == 1:
        cues.append("provider's personal sources mention this address but org does not (departure pattern)")
    if row.get('feat_only_ov_found', 0) == 1:
        cues.append("org lists this address but provider has no presence there (ghost listing)")
    if row.get('feat_npi_changed_by_r3', 0) == 1:
        cues.append("R3 had to correct the NPI itself, indicating broader record staleness")
    if row.get('feat_npi_deactivated', 0) == 1:
        cues.append("NPI is flagged as deactivated")
    if not cues:
        # Generic fallback based on probability
        if row.get('p_r3_wrong', 0) > 0.7:
            cues.append("multiple weak signals combined indicate R3 is likely wrong")
        else:
            cues.append("evidence pattern is borderline")
    return "; ".join(cues) + "."


def _evidence_summary(row) -> str:
    """One-sentence summary of available evidence."""
    parts = []
    if row.get('feat_both_views_found', 0) == 1:
        parts.append("Address corroborated by both provider-name and org-name web searches")
    elif row.get('feat_neither_view_found', 0) == 1:
        parts.append("No web evidence found for this address")
    if row.get('feat_claims_recent_active', 0) == 1:
        parts.append(f"provider has {int(row.get('feat_claims_n', 0))} recent claims")
    if row.get('feat_claims_recent_zip_match', 0) == 1:
        parts.append("recent claim from the same ZIP")
    if not parts:
        return ""
    return " (" + "; ".join(parts) + ")."


def explain_template(row) -> str:
    """Return a deterministic explanation for one record."""
    reason = row.get('decision_reason', 'UNKNOWN')
    tpl = REASON_TEMPLATES.get(reason)
    if tpl is None:
        return f"Decision: {row.get('decision_label', '?')} (reason code: {reason})"
    p_wrong = float(row.get('p_r3_wrong', 0.5) or 0.5)
    p_conc = float(row.get('p_call_conclusive', 0.5) or 0.5)
    confidence = max(p_wrong, 1 - p_wrong)
    return tpl.format(
        confidence=confidence,
        p_wrong=p_wrong,
        p_conc=p_conc,
        root_cause=_root_cause(row),
        evidence_summary=_evidence_summary(row),
    ).strip()


# ============================================================================
# LLM-BASED EXPLAINER (Anthropic API)
# ============================================================================

LLM_SYSTEM_PROMPT = """You are explaining provider-address validation decisions to an operations team. \
For each record, write ONE sentence (max 35 words) that:
  - states the decision and confidence
  - cites specific evidence from the features given
  - is plain English, no jargon

Do not invent facts. Use only the evidence provided. If evidence is thin, say so."""


def _build_llm_prompt(row) -> str:
    """Build the user-prompt portion for one record."""
    facts = {
        'R3_verdict': row.get('R3'),
        'final_decision': row.get('decision_label'),
        'reason_code': row.get('decision_reason'),
        'p_r3_wrong': round(float(row.get('p_r3_wrong', 0.5) or 0.5), 3),
        'p_call_conclusive': round(float(row.get('p_call_conclusive', 0.5) or 0.5), 3),
        'has_taxonomy_code_in_specialty': bool(row.get('feat_specialty_has_taxonomy', 0)),
        'has_any_claims': bool(row.get('feat_claims_has_any', 0)),
        'n_claims': int(row.get('feat_claims_n', 0)),
        'days_since_last_claim': int(row.get('feat_claims_days_since', 9999)),
        'recent_claim_from_same_zip': bool(row.get('feat_claims_recent_zip_match', 0)),
        'claims_strong_contradict': bool(row.get('feat_claims_strong_contradict', 0)),
        'claims_strong_corroborate': bool(row.get('feat_claims_strong_corroborate', 0)),
        'only_provider_view_found_address': bool(row.get('feat_only_pv_found', 0)),
        'only_org_view_found_address': bool(row.get('feat_only_ov_found', 0)),
        'npi_changed_by_r3': bool(row.get('feat_npi_changed_by_r3', 0)),
        'npi_deactivated': bool(row.get('feat_npi_deactivated', 0)),
    }
    return f"Record facts:\n{json.dumps(facts, indent=2)}\n\nWrite the one-sentence explanation."


def _explain_one_via_llm(row, client, model='claude-haiku-4-5') -> Optional[str]:
    """Single-record LLM call. Returns None on any error."""
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=120,
            system=LLM_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': _build_llm_prompt(row)}],
        )
        # Extract first text block
        for block in resp.content:
            if getattr(block, 'type', None) == 'text':
                return block.text.strip()
        return None
    except Exception:
        return None


def _have_anthropic_sdk() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def explain_records(df: pd.DataFrame, mode: str = 'auto', model: str = 'claude-haiku-4-5',
                    max_llm_calls: Optional[int] = None) -> List[str]:
    """Generate explanations for every row in df.
    
    Args:
        df: per-record DataFrame with the columns listed in module docstring
        mode: 'auto' | 'template' | 'llm'
        model: Anthropic model id to use in llm mode
        max_llm_calls: cap on LLM calls per batch (for cost control). Records
                       beyond the cap get template explanations.
    
    Returns:
        list of explanation strings, one per row, in df order.
    """
    n = len(df)
    if n == 0:
        return []

    # Determine actual mode
    has_key = bool(os.environ.get('ANTHROPIC_API_KEY'))
    has_sdk = _have_anthropic_sdk()
    use_llm = (mode == 'llm') or (mode == 'auto' and has_key and has_sdk)
    if mode == 'llm' and not (has_key and has_sdk):
        raise RuntimeError("LLM mode requested but ANTHROPIC_API_KEY or anthropic SDK missing")

    if not use_llm:
        return [explain_template(r) for _, r in df.iterrows()]

    # LLM mode
    import anthropic
    client = anthropic.Anthropic()

    cap = max_llm_calls if max_llm_calls is not None else n
    out = []
    for i, (_, row) in enumerate(df.iterrows()):
        if i < cap:
            llm_out = _explain_one_via_llm(row, client, model=model)
            if llm_out is None:
                # graceful fallback to template on per-record failure
                llm_out = explain_template(row)
            out.append(llm_out)
        else:
            out.append(explain_template(row))
    return out
