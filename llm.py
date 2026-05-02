# llm.py
"""
LLM-based extractor for Comment_Call_QC column.

Calls local Mistral via Ollama to extract structured fields
from each free-text calling agent comment.

Usage:
    from llm import extract_from_comment, prompt_template
    result = extract_from_comment("S/W JOHN CONFIRMED ADDRESS IS ACCURATE...")
    # returns dict with all structured fields
"""

import json
import re
import requests

# ─────────────────────────────────────────────────────────────
# OLLAMA ENDPOINT
# Make sure Ollama is running locally: ollama serve
# and Mistral is pulled: ollama pull mistral
# ─────────────────────────────────────────────────────────────
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"


# ─────────────────────────────────────────────────────────────
# DEFAULT EXTRACTION RESULT
# Returned when comment is missing or call LLM fails
# All fields are "unknown" — safe neutral default
# ─────────────────────────────────────────────────────────────
DEFAULT_RESULT = {
    # ── Call outcome ──────────────────────────────────────────
    "call_reached_person"     : "unknown",  # spoke with someone (S/W ...)
    "went_to_voicemail"       : "unknown",  # call landed on voicemail
    "hit_ivr"                 : "unknown",  # hit automated IVR system
    "call_refused_validation" : "unknown",  # operator refused to validate

    # ── Provider status ───────────────────────────────────────
    "provider_not_found"      : "unknown",  # "no provider by this name"
    "provider_left_org"       : "unknown",  # "no longer practicing here"
    "provider_retired"        : "unknown",  # "retired"
    "provider_deceased"       : "unknown",  # "passed away"
    "provider_at_diff_location": "unknown", # practices at different location

    # ── Address signals ───────────────────────────────────────
    "address_confirmed"       : "unknown",  # address is ACCURATE
    "address_denied"          : "unknown",  # address is INACCURATE

    # ── Phone signals ─────────────────────────────────────────
    "phone_confirmed"         : "unknown",  # phone is ACCURATE
    "phone_denied"            : "unknown",  # phone is INACCURATE
    "phone_linked_same_org"   : "unknown",  # phone belongs to same org
    "phone_linked_diff_org"   : "unknown",  # phone belongs to different org

    # ── Org signals ───────────────────────────────────────────
    "org_confirmed"           : "unknown",  # org name is ACCURATE
    "org_denied"              : "unknown",  # org name is INACCURATE
    "org_name_changed"        : "unknown",  # org has a new name

    # ── Practice type signals ─────────────────────────────────
    "offers_telehealth_only"  : "unknown",  # ONLY telehealth, no physical
    "offers_telehealth"       : "unknown",  # offers telehealth (may have physical too)
    "inperson_only"           : "unknown",  # in-person appointments only
    "accepts_new_patients"    : "unknown",  # accepting new patients
}


# ─────────────────────────────────────────────────────────────
# PROMPT TEMPLATE
# Designed specifically for the patterns seen in this dataset
# ─────────────────────────────────────────────────────────────
def prompt_template(comment: str) -> str:
    return f"""
You are an information extraction system for healthcare provider verification calls.

Extract structured information from this calling agent comment and return ONLY valid JSON.

=== FIELD DEFINITIONS ===
All fields must be: "yes", "no", or "unknown"

call_reached_person     : Did the agent speak with a person? ("S/W" = spoke with = yes)
went_to_voicemail       : Did the call go to voicemail?
hit_ivr                 : Did the call hit an automated IVR system?
call_refused_validation : Did the operator refuse to validate provider details?

provider_not_found      : Was there "no provider by this name" at that location?
provider_left_org       : Did the provider leave / no longer practice at that org?
provider_retired        : Did the provider retire?
provider_deceased       : Did the provider pass away?
provider_at_diff_location : Does the provider practice at a different location?

address_confirmed       : Was the address confirmed as ACCURATE?
address_denied          : Was the address confirmed as INACCURATE?

phone_confirmed         : Was the phone confirmed as ACCURATE?
phone_denied            : Was the phone confirmed as INACCURATE?
phone_linked_same_org   : Is the phone linked to the same organization?
phone_linked_diff_org   : Is the phone linked to a DIFFERENT organization?

org_confirmed           : Was the organization name confirmed as ACCURATE?
org_denied              : Was the organization name confirmed as INACCURATE?
org_name_changed        : Was a new/different organization name mentioned?

offers_telehealth_only  : Does the provider offer ONLY telehealth (no physical location)?
offers_telehealth       : Does the provider offer telehealth (may also have physical)?
inperson_only           : Does the provider only accept in-person appointments?
accepts_new_patients    : Is the provider accepting new patients?

=== EXAMPLES ===

Comment: "S/W JOHN CONFIRMED ADDRESS IS ACCURATE. PHONE NUMBER IS INACCURATE."
Output:
{{
  "call_reached_person": "yes",
  "went_to_voicemail": "no",
  "hit_ivr": "no",
  "call_refused_validation": "no",
  "provider_not_found": "no",
  "provider_left_org": "no",
  "provider_retired": "no",
  "provider_deceased": "no",
  "provider_at_diff_location": "no",
  "address_confirmed": "yes",
  "address_denied": "no",
  "phone_confirmed": "no",
  "phone_denied": "yes",
  "phone_linked_same_org": "unknown",
  "phone_linked_diff_org": "no",
  "org_confirmed": "unknown",
  "org_denied": "no",
  "org_name_changed": "no",
  "offers_telehealth_only": "no",
  "offers_telehealth": "unknown",
  "inperson_only": "unknown",
  "accepts_new_patients": "unknown"
}}

Comment: "CALLED THE PF NO. OPERATOR CONFIRMED PROVIDER LEFT ORG IN FEB 2025 AND NO LONGER PRACTICES AT PF ADDRESS."
Output:
{{
  "call_reached_person": "yes",
  "went_to_voicemail": "no",
  "hit_ivr": "no",
  "call_refused_validation": "no",
  "provider_not_found": "no",
  "provider_left_org": "yes",
  "provider_retired": "no",
  "provider_deceased": "no",
  "provider_at_diff_location": "yes",
  "address_confirmed": "no",
  "address_denied": "yes",
  "phone_confirmed": "unknown",
  "phone_denied": "unknown",
  "phone_linked_same_org": "yes",
  "phone_linked_diff_org": "no",
  "org_confirmed": "unknown",
  "org_denied": "no",
  "org_name_changed": "no",
  "offers_telehealth_only": "no",
  "offers_telehealth": "unknown",
  "inperson_only": "unknown",
  "accepts_new_patients": "unknown"
}}

Comment: "CALL DIRECTLY LANDED TO THE VOICE MAIL, LEFT VOICE MAIL."
Output:
{{
  "call_reached_person": "no",
  "went_to_voicemail": "yes",
  "hit_ivr": "no",
  "call_refused_validation": "no",
  "provider_not_found": "unknown",
  "provider_left_org": "unknown",
  "provider_retired": "unknown",
  "provider_deceased": "unknown",
  "provider_at_diff_location": "unknown",
  "address_confirmed": "unknown",
  "address_denied": "unknown",
  "phone_confirmed": "unknown",
  "phone_denied": "unknown",
  "phone_linked_same_org": "unknown",
  "phone_linked_diff_org": "unknown",
  "org_confirmed": "unknown",
  "org_denied": "unknown",
  "org_name_changed": "unknown",
  "offers_telehealth_only": "unknown",
  "offers_telehealth": "unknown",
  "inperson_only": "unknown",
  "accepts_new_patients": "unknown"
}}

=== NOW EXTRACT ===

Comment: "{comment}"

Return ONLY valid JSON. No explanation. No markdown. No extra text.
"""


# ─────────────────────────────────────────────────────────────
# LLM CALL
# ─────────────────────────────────────────────────────────────
def call_llm(prompt: str) -> str:
    """
    Calls local Mistral via Ollama.
    Returns raw string response.
    Raises exception if Ollama is not running.
    """
    response = requests.post(
        OLLAMA_URL,
        json={
            "model" : OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["response"]


# ─────────────────────────────────────────────────────────────
# JSON PARSER
# Handles cases where Mistral adds extra text around the JSON
# ─────────────────────────────────────────────────────────────
def parse_json_response(raw: str) -> dict:
    """
    Safely parses JSON from LLM response.
    Strips markdown fences and extra text if present.
    Returns DEFAULT_RESULT on any parse failure.
    """
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?", "", raw).strip()

        # Find first { and last } — extract just the JSON object
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start == -1 or end == 0:
            return DEFAULT_RESULT.copy()

        json_str = cleaned[start:end]
        parsed   = json.loads(json_str)

        # Merge with defaults so all keys always exist
        # (in case LLM omits some fields)
        result = DEFAULT_RESULT.copy()
        result.update(parsed)
        return result

    except (json.JSONDecodeError, ValueError):
        return DEFAULT_RESULT.copy()


# ─────────────────────────────────────────────────────────────
# MAIN EXTRACTION FUNCTION
# This is what you call from train.py and extractor script
# ─────────────────────────────────────────────────────────────
def extract_from_comment(comment: str) -> dict:
    """
    Full pipeline: comment text → structured dict

    Args:
        comment: raw text from Comment_Call_QC column

    Returns:
        dict with all fields from DEFAULT_RESULT
        Values are "yes" / "no" / "unknown"
        Never raises — returns DEFAULT_RESULT on any failure

    Example:
        result = extract_from_comment("S/W JOHN, ADDRESS IS ACCURATE")
        result["address_confirmed"]  # → "yes"
        result["went_to_voicemail"]  # → "no"
    """
    # Handle missing / empty comments
    if not comment or str(comment).strip() in ["", "nan", "0", "NULL"]:
        return DEFAULT_RESULT.copy()

    try:
        prompt  = prompt_template(str(comment))
        raw     = call_llm(prompt)
        result  = parse_json_response(raw)
        return result
    except Exception as e:
        # Never crash training because of LLM failure
        print(f"  [LLM WARNING] Extraction failed: {e}")
        return DEFAULT_RESULT.copy()


# ─────────────────────────────────────────────────────────────
# CONVENIENCE: Convert "yes"/"no"/"unknown" → 1 / 0 / -1
# Use -1 for unknown so LightGBM treats it as missing (splits cleanly)
# ─────────────────────────────────────────────────────────────
def to_numeric(val: str) -> int:
    """
    Converts extracted string values to numeric for ML model.
    "yes"     → 1
    "no"      → 0
    "unknown" → -1  (LightGBM handles -1 as missing natively)
    """
    mapping = {"yes": 1, "no": 0, "unknown": -1}
    return mapping.get(str(val).lower(), -1)


if __name__ == "__main__":
    # Quick test with a real comment from your dataset
    test_comments = [
        "S/W JOHN CONFIRMED ADDRESS IS ACCURATE. PHONE NUMBER IS INACCURATE.",
        "CALL DIRECTLY LANDED TO THE VOICE MAIL, LEFT VOICE MAIL.",
        "CALLED THE PF NO. OPERATOR CONFIRMED PROVIDER LEFT ORG IN FEB 2025.",
        "S/W OPERATOR CONFIRMED THE PROVIDER IS PASSED AWAY. ORGANIZATION NAME IS ACCURATE.",
        "S/W SANDY CONFIRMED PROVIDER OFFER TELEHEALTH. ADDRESS, PHONE NUMBER IS INACCURATE.",
    ]

    for comment in test_comments:
        print(f"\nComment: {comment}")
        print("-" * 60)
        result = extract_from_comment(comment)
        for key, val in result.items():
            if val != "unknown":   # only print non-unknown for readability
                print(f"  {key:<30} = {val}")