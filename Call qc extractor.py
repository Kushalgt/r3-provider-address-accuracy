# call_qc_extractor.py
"""
Processes all Comment_Call_QC rows through Mistral LLM and saves
structured extraction results.

TWO outputs produced:
1. call_qc_extracted.csv   — raw extraction results (yes/no/unknown)
2. call_qc_numeric.csv     — numeric version (-1/0/1) ready for ML

HOW EACH MODEL USES THESE:
───────────────────────────────────────────────────────────────────────────
Model A — P(R3 is wrong):
  - Use extracted fields to ENRICH THE TARGET (better y label)
  - Use extracted fields to DISCOVER PROXY FEATURES from input columns
  - DO NOT use extracted fields directly as features (don't exist at test time)

Model B — P(call is conclusive):
  - Use call outcome fields to BUILD A BETTER TARGET
  - call_reached_person + went_to_voicemail + hit_ivr = better conclusiveness label
  - These directly define whether a call was useful or not
───────────────────────────────────────────────────────────────────────────

Run:
    python call_qc_extractor.py
"""

import pandas as pd
import numpy as np
from llm import extract_from_comment, to_numeric, DEFAULT_RESULT

# ─────────────────────────────────────────────────────────────
# STEP 1: Load dataset
# ─────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 1: Loading Data")
print("=" * 60)

df = pd.read_excel("Base data_hackathon.xlsx",header=1)
print(f"  Total records: {len(df)}")


# ─────────────────────────────────────────────────────────────
# STEP 2: Extract structured fields from each comment
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Extracting fields from Comment_Call_QC")
print("  This calls Mistral for each comment. May take a few minutes.")
print("=" * 60)

extracted_rows = []

for i, row in df.iterrows():
    comment = row.get("Comment_Call_QC", "")

    # Show progress every 100 records
    if i % 100 == 0:
        print(f"  Processing record {i}/{len(df)}...")

    result = extract_from_comment(comment)
    extracted_rows.append(result)

# Build extracted dataframe
df_extracted = pd.DataFrame(extracted_rows)

# Add NPI as index so we can join back to main dataframe later
df_extracted.index = df.index

print(f"\n  Extraction complete. {len(df_extracted)} records processed.")


# ─────────────────────────────────────────────────────────────
# STEP 3: Save raw extraction (yes/no/unknown strings)
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3: Saving raw extraction")
print("=" * 60)

# Prefix all columns with "llm_" so it's clear they came from LLM extraction
df_extracted.columns = [f"llm_{c}" for c in df_extracted.columns]

df_with_extracted = pd.concat([df, df_extracted], axis=1)
df_with_extracted.to_csv("call_qc_extracted.csv", index=False)
print("  Saved → call_qc_extracted.csv")


# ─────────────────────────────────────────────────────────────
# STEP 4: Convert to numeric (-1/0/1) for ML
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4: Converting to numeric for ML")
print("=" * 60)

# Convert: "yes" → 1, "no" → 0, "unknown" → -1
df_numeric = df_extracted.applymap(
    lambda x: to_numeric(x) if isinstance(x, str) else x
)
df_numeric.columns = [c.replace("llm_", "llm_num_") for c in df_numeric.columns]

df_with_numeric = pd.concat([df, df_numeric], axis=1)
df_with_numeric.to_csv("call_qc_numeric.csv", index=False)
print("  Saved → call_qc_numeric.csv")


# ─────────────────────────────────────────────────────────────
# STEP 5: MODEL A USE — Enrich Target Variable
#
# You CANNOT use extracted fields as features (don't exist at test time)
# But you CAN use them to build a more precise target label for training
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: MODEL A — Enrich Target Variable")
print("=" * 60)

def normalize(val):
    val = str(val).strip().upper()
    if "ACCURATE - KEEP" in val or val == "ACCURATE":
        return "ACCURATE"
    elif "INACCURATE - REMOVE" in val or val == "INACCURATE":
        return "INACCURATE"
    return "INCONCLUSIVE"

df["r3_norm"]      = df["Final_R3_Reco_Address"].apply(normalize)
df["calling_norm"] = df["Calling_Address"].apply(normalize)

# Current target (address only)
df["target_basic"] = (df["r3_norm"] != df["calling_norm"]).astype(int)

# Enriched target:
# R3 is wrong if address disagrees OR any of these failure patterns detected
df["target_enriched"] = (
    (df["r3_norm"] != df["calling_norm"])                          # address mismatch
    | (df_extracted["llm_provider_left_org"]    == "yes")          # provider left
    | (df_extracted["llm_provider_not_found"]   == "yes")          # ghost provider
    | (df_extracted["llm_provider_retired"]     == "yes")          # retired
    | (df_extracted["llm_provider_deceased"]    == "yes")          # deceased
    | (df_extracted["llm_provider_at_diff_location"] == "yes")     # moved location
).astype(int)

basic_count    = df["target_basic"].sum()
enriched_count = df["target_enriched"].sum()

print(f"  Basic target    (address mismatch only) : {basic_count} R3-wrong records")
print(f"  Enriched target (+ failure patterns)    : {enriched_count} R3-wrong records")
print(f"  Additional failures captured            : {enriched_count - basic_count}")
print()
print("  → Use target_enriched as y in Model A training")
print("    It captures more true failures that address comparison alone misses")


# ─────────────────────────────────────────────────────────────
# STEP 6: MODEL B USE — Better Conclusiveness Target
#
# Model B predicts: will a robocall yield a conclusive result?
# Extracted fields tell us exactly what happened on each call.
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 6: MODEL B — Better Conclusiveness Target")
print("=" * 60)

# Current target B (from train.py): just checks if Calling_Address has a verdict
df["target_b_basic"] = df["calling_norm"].isin(["ACCURATE", "INACCURATE"]).astype(int)

# Enriched target B: a call is truly conclusive only if:
# - someone answered (not voicemail, not IVR)
# - AND we got a clear answer on address OR provider status
df["target_b_enriched"] = (
    (df_extracted["llm_call_reached_person"]  == "yes")    # someone answered
    & (df_extracted["llm_went_to_voicemail"]  == "no")     # not voicemail
    & (df_extracted["llm_hit_ivr"]            == "no")     # not IVR
    & (
        (df_extracted["llm_address_confirmed"] == "yes")   # got clear address answer
        | (df_extracted["llm_address_denied"]  == "yes")
        | (df_extracted["llm_provider_not_found"] == "yes")# or provider status
        | (df_extracted["llm_provider_left_org"]  == "yes")
    )
).astype(int)

basic_b_count    = df["target_b_basic"].sum()
enriched_b_count = df["target_b_enriched"].sum()

print(f"  Basic target B    (Calling_Address verdict): {basic_b_count} conclusive calls")
print(f"  Enriched target B (truly spoke + got answer): {enriched_b_count} conclusive calls")
print()
print("  → Use target_b_enriched as y in Model B training")
print("    It filters out calls where someone 'answered' but gave no useful info")


# ─────────────────────────────────────────────────────────────
# STEP 7: PROXY FEATURE DISCOVERY
#
# Find which INPUT features (that exist at test time) correlate
# with each LLM-extracted failure pattern.
# These correlations tell you what new features to engineer.
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 7: Proxy Feature Discovery")
print("  Finding which INPUT features predict each failure pattern")
print("=" * 60)

# The failure patterns we care most about for Model A
failure_patterns = [
    "llm_provider_not_found",
    "llm_provider_left_org",
    "llm_provider_retired",
    "llm_offers_telehealth_only",
    "llm_phone_linked_diff_org",
]

# Input features that exist at prediction time
input_signals = [
    "Comment_Web_QC",       # web QC notes — very important
    "Provider_Type",        # PERSON vs ORG
    "Specialty",            # specialty type
    "ANP",                  # accepting new patients
    "Provider_in_Organization",
]

print()
for pattern in failure_patterns:
    if pattern not in df_extracted.columns:
        continue

    yes_mask = df_extracted[pattern] == "yes"
    yes_count = yes_mask.sum()

    if yes_count < 5:
        continue

    print(f"  ── {pattern} (n={yes_count}) ──")

    # What does Comment_Web_QC say for these records?
    if "Comment_Web_QC" in df.columns:
        top_web_comments = (
            df[yes_mask]["Comment_Web_QC"]
            .fillna("UNKNOWN")
            .value_counts()
            .head(5)
        )
        print(f"    Top Comment_Web_QC values:")
        for val, cnt in top_web_comments.items():
            print(f"      {cnt:4d}x  {val}")

    # What Specialty is most common?
    if "Specialty" in df.columns:
        top_specialty = (
            df[yes_mask]["Specialty"]
            .value_counts()
            .head(3)
        )
        print(f"    Top Specialties: {dict(top_specialty)}")

    print()


# ─────────────────────────────────────────────────────────────
# STEP 8: Save enriched targets back to a combined file
# ─────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 8: Saving Enriched Targets")
print("=" * 60)

df_final = df[[
    "Specialty", "Provider_Type", "OrganizationName", "City", "State",
    "Zip", "Phone", "Final_R3_Reco_Address", "Final_R3_Score_Address",
    "Calling_Address", "Comment_Web_QC", "Comment_Call_QC",
    "r3_norm", "calling_norm",
    "target_basic",       # ← original target
    "target_enriched",    # ← better target for Model A
    "target_b_basic",     # ← original Model B target
    "target_b_enriched",  # ← better target for Model B
]].copy()

# Join numeric LLM features
df_final = pd.concat([df_final, df_numeric], axis=1)
df_final.to_csv("data_with_enriched_targets.csv", index=False)
print("  Saved → data_with_enriched_targets.csv")

print(f"""
  ─── Summary ───────────────────────────────────────────────

  Model A (P R3 wrong):
    Old target  : {basic_count} wrong records
    New target  : {enriched_count} wrong records
    Use         : target_enriched column
    Also do     : look at Step 7 output to engineer proxy features
                  from Comment_Web_QC + Specialty that exist at test time

  Model B (P call conclusive):
    Old target  : {basic_b_count} conclusive calls
    New target  : {enriched_b_count} truly conclusive calls
    Use         : target_b_enriched column

  Files saved:
    call_qc_extracted.csv         → raw yes/no/unknown strings
    call_qc_numeric.csv           → numeric -1/0/1 version
    data_with_enriched_targets.csv → final file to use in train.py
""")