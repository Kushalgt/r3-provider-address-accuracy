"""
================================================================================
R3 ADDRESS ACCURACY — FEATURE ENGINEERING (v3, configurable)
================================================================================

Reads feature_config.yaml to determine which features to compute and at what
thresholds. Iterate on the model by editing the YAML, not this file.

PUBLIC API
----------
    load_config(path='feature_config.yaml')           -> dict
    normalize_r3_label(x)                              -> str
    merge_claims(base_df, claims_agg_df)               -> DataFrame
    build_features(df, config=None)                    -> DataFrame
    list_active_features(config=None)                  -> list[str]

The full-feature matrix has 69 columns by default; turning families or
individual features off in the config produces a smaller matrix.
"""

import re
import os
import yaml
import numpy as np
import pandas as pd

# ============================================================================
# CONSTANTS
# ============================================================================

EVIDENCE_COLS = {
    'pv_nf_org':  'Address_ProviderView_not_found_in_websites_URLS_Orgwebsite',
    'pv_nf_prov': 'Address_ProviderView_not_found_in_websites_URLS_Providerwebsite',
    'pv_nf_agg':  'Address_ProviderView_not_found_in_websites_URLS_Aggregator',
    'pv_f_org':   'Address_ProviderView_found_in_websites_URLS_Orgwebsite',
    'pv_f_prov':  'Address_ProviderView_found_in_websites_URLS_Providerwebsite',
    'pv_f_agg':   'Address_ProviderView_found_in_websites_URLS_Aggregator',
    'ov_nf_org':  'Address_OrgView_not_found_in_websites_URLS_Orgwebsite',
    'ov_nf_prov': 'Address_OrgView_not_found_in_websites_URLS_Providerwebsite',
    'ov_nf_agg':  'Address_OrgView_not_found_in_websites_URLS_Aggregator',
    'ov_f_org':   'Address_OrgView_found_in_websites_URLS_Orgwebsite',
    'ov_f_prov':  'Address_OrgView_found_in_websites_URLS_Providerwebsite',
    'ov_f_agg':   'Address_OrgView_found_in_websites_URLS_Aggregator',
}

DEFAULT_CONFIG = {
    'families': {
        'specialty_provenance': True, 'r3_internals': True,
        'evidence_cube_raw': True, 'evidence_rollups': True,
        'cross_view_agreement': True, 'geography': True,
        'provider_attributes': True, 'org_linkage': True,
        'claims_raw': True, 'claims_derived': True,
        'cross_interactions': True,
    },
    'features': {},
    'thresholds': {
        'claims_high_volume_min': 20,
        'claims_recent_days': 400,
        'claims_strong_corroborate_min': 3,
        'claims_strong_contradict_min': 20,
    },
    'categorical_features': ['feat_state', 'feat_zip_prefix'],
    'high_risk_states': ['AL', 'MI', 'NJ'],
}


def load_config(path='feature_config.yaml'):
    """Load feature config from YAML. Returns DEFAULT_CONFIG if file missing."""
    if not os.path.exists(path):
        return DEFAULT_CONFIG
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # Merge with defaults so partial configs work
    out = {**DEFAULT_CONFIG, **cfg}
    out['families'] = {**DEFAULT_CONFIG['families'], **(cfg.get('families') or {})}
    out['thresholds'] = {**DEFAULT_CONFIG['thresholds'], **(cfg.get('thresholds') or {})}
    out['features'] = cfg.get('features') or {}
    return out


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def _count_urls(x):
    if pd.isna(x):
        return 0
    s = str(x).strip()
    if not s or s.lower() in ('nan', 'none', 'null', '-'):
        return 0
    return sum(1 for p in re.split(r'[|,;\n]+', s) if 'http' in p.lower() or 'www.' in p.lower())


def normalize_r3_label(x):
    if pd.isna(x):
        return 'UNKNOWN'
    x = str(x).upper()
    if 'ACCURATE - KEEP' in x:
        return 'ACCURATE'
    if 'INACCURATE - REMOVE' in x:
        return 'INACCURATE'
    if 'INCONCLUSIVE' in x:
        return 'INCONCLUSIVE'
    return x


# ============================================================================
# CLAIMS MERGE
# ============================================================================
# Change here CLAIMS FEATURE =======////////////////////////================
def merge_claims(base_df, claims_agg_df):
    base = base_df.copy()
    claims = claims_agg_df.copy() if claims_agg_df is not None else pd.DataFrame()

    base['_npi'] = pd.to_numeric(base['OrigNPI'], errors='coerce').astype('Int64')

    if len(claims) > 0 and 'BASE_NPI' in claims.columns:
        claims['_npi'] = pd.to_numeric(claims['BASE_NPI'], errors='coerce').astype('Int64')
        df = base.merge(claims, on='_npi', how='left')
    else:
        df = base.copy()

    fill_zero = ['N_CLAIMS', 'DISTINCT_ORGS', 'DISTINCT_ADDRS',
                 'ADDR_EXACT_MATCH', 'ZIP_MATCH', 'STREET_ZIP_MATCH',
                 'RECENT_ZIP_MATCH', 'RECENT_STREET_ZIP_MATCH']
    for c in fill_zero:
        if c not in df.columns:
            df[c] = 0
        df[c] = df[c].fillna(0).astype(int)

    if 'DAYS_SINCE' not in df.columns:
        df['DAYS_SINCE'] = 9999
    df['DAYS_SINCE'] = df['DAYS_SINCE'].fillna(9999)

    df['HAS_CLAIMS'] = (df['N_CLAIMS'] > 0).astype(int)
    return df


# ============================================================================
# FEATURE BUILDER (config-driven)
# ============================================================================

def _add_feature(f, name, value, config):
    """Add a feature only if not explicitly disabled in config."""
    enabled = config['features'].get(name, True)
    if enabled:
        f[name] = value


def build_features(df, config=None):
    """Build the feature matrix. Honors the family/feature switches in config."""
    if config is None:
        config = load_config()
    fams = config['families']
    th = config['thresholds']
    high_risk = set(config['high_risk_states'])

    f = pd.DataFrame(index=df.index)

    if 'R3' not in df.columns:
        df = df.copy()
        df['R3'] = df['Final_R3_Reco_Address'].apply(normalize_r3_label)

    # -------- A: Specialty / Provenance --------
    if fams.get('specialty_provenance'):
        _add_feature(f, 'feat_specialty_has_taxonomy',
                     df['Specialty'].astype(str).str.contains(
                         r'\([0-9A-Z]{6,}\)', regex=True).astype(int), config)
        _add_feature(f, 'feat_specialty_length',
                     df['Specialty'].astype(str).str.len(), config)

    # -------- B: R3 internals --------
    if fams.get('r3_internals'):
        _add_feature(f, 'feat_r3_score', df['Final_R3_Score_Address'].astype(float), config)
        _add_feature(f, 'feat_r3_is_accurate', (df['R3'] == 'ACCURATE').astype(int), config)
        _add_feature(f, 'feat_r3_is_inaccurate', (df['R3'] == 'INACCURATE').astype(int), config)
        _add_feature(f, 'feat_r3_is_inconclusive', (df['R3'] == 'INCONCLUSIVE').astype(int), config)
        _add_feature(f, 'feat_r3_score_is_zero', (df['Final_R3_Score_Address'] <= 0).astype(int), config)
        _add_feature(f, 'feat_r3_score_is_perfect', (df['Final_R3_Score_Address'] >= 100).astype(int), config)

    # -------- C: Evidence cube — raw --------
    if fams.get('evidence_cube_raw'):
        for k, col in EVIDENCE_COLS.items():
            _add_feature(f, f'feat_ev_{k}_n', df[col].apply(_count_urls), config)

    # Need raw counts for rollups even if rollups are disabled — compute privately
    raw_counts = {}
    for k, col in EVIDENCE_COLS.items():
        raw_counts[k] = df[col].apply(_count_urls)

    # -------- D: Evidence rollups --------
    if fams.get('evidence_rollups'):
        total_found = sum(raw_counts[k] for k in EVIDENCE_COLS if '_f_' in k)
        total_nf = sum(raw_counts[k] for k in EVIDENCE_COLS if '_nf_' in k)
        total = total_found + total_nf
        _add_feature(f, 'feat_ev_total_found', total_found, config)
        _add_feature(f, 'feat_ev_total_not_found', total_nf, config)
        _add_feature(f, 'feat_ev_total', total, config)
        _add_feature(f, 'feat_ev_found_ratio',
                     (total_found / total.replace(0, np.nan)).fillna(-1), config)

    # -------- E: Cross-view agreement --------
    if fams.get('cross_view_agreement'):
        pv_found = ((raw_counts['pv_f_org'] + raw_counts['pv_f_prov'] + raw_counts['pv_f_agg']) > 0).astype(int)
        ov_found = ((raw_counts['ov_f_org'] + raw_counts['ov_f_prov'] + raw_counts['ov_f_agg']) > 0).astype(int)
        pv_nf = ((raw_counts['pv_nf_org'] + raw_counts['pv_nf_prov'] + raw_counts['pv_nf_agg']) > 0).astype(int)
        ov_nf = ((raw_counts['ov_nf_org'] + raw_counts['ov_nf_prov'] + raw_counts['ov_nf_agg']) > 0).astype(int)
        _add_feature(f, 'feat_pv_found_any', pv_found, config)
        _add_feature(f, 'feat_ov_found_any', ov_found, config)
        _add_feature(f, 'feat_pv_notfound_any', pv_nf, config)
        _add_feature(f, 'feat_ov_notfound_any', ov_nf, config)
        _add_feature(f, 'feat_both_views_found', (pv_found & ov_found).astype(int), config)
        _add_feature(f, 'feat_only_pv_found', (pv_found & ~ov_found.astype(bool)).astype(int), config)
        _add_feature(f, 'feat_only_ov_found', (ov_found & ~pv_found.astype(bool)).astype(int), config)
        _add_feature(f, 'feat_neither_view_found',
                     ((~pv_found.astype(bool)) & (~ov_found.astype(bool))).astype(int), config)
        _add_feature(f, 'feat_source_diversity',
                     (((raw_counts['pv_f_org'] + raw_counts['ov_f_org']) > 0).astype(int) +
                      ((raw_counts['pv_f_prov'] + raw_counts['ov_f_prov']) > 0).astype(int) +
                      ((raw_counts['pv_f_agg'] + raw_counts['ov_f_agg']) > 0).astype(int)), config)

    # -------- F: Geography --------
    if fams.get('geography'):
        _add_feature(f, 'feat_state', df['State'].astype(str), config)
        _add_feature(f, 'feat_state_high_risk',
                     df['State'].isin(high_risk).astype(int), config)
        _add_feature(f, 'feat_zip_prefix',
                     df['Zip'].astype(str).str.replace(r'\.0$', '', regex=True).str[:3], config)

    # -------- G: Provider attributes --------
    if fams.get('provider_attributes'):
        _add_feature(f, 'feat_has_middle_name', df['MiddleName'].notna().astype(int), config)
        _add_feature(f, 'feat_has_credentials', df['Credentials'].notna().astype(int), config)
        orig = df['OrigNPI'].astype(str).str.replace(r'\.0$', '', regex=True)
        curr = df['NPI'].astype(str).str.replace(r'\.0$', '', regex=True)
        _add_feature(f, 'feat_npi_changed_by_r3', (orig != curr).astype(int), config)
        npi_status = df['Mcheck R3 NPI Status Recommendation'].astype(str).str.upper()
        _add_feature(f, 'feat_npi_deactivated', npi_status.str.contains('DEACTIV', na=False).astype(int), config)
        name_reco = df['Mcheck R3 Name Recommendation'].astype(str).str.upper()
        _add_feature(f, 'feat_r3_suggests_name_change',
                     name_reco.str.contains('REPLACE|UPDATE|CHANGE|NEW', na=False).astype(int), config)
        cred = df['Credentials'].astype(str).str.upper().fillna('')
        _add_feature(f, 'feat_cred_is_md_do',
                     cred.str.contains(r'\bMD\b|\bDO\b', regex=True, na=False).astype(int), config)
        _add_feature(f, 'feat_cred_is_midlevel',
                     cred.str.contains(r'\bNP\b|\bPA\b', regex=True, na=False).astype(int), config)

    # -------- H: Org linkage / org name --------
    if fams.get('org_linkage'):
        _add_feature(f, 'feat_has_pio_evidence', df['PIO_Evidence'].notna().astype(int), config)
        pio = df['Provider_in_Organization'].astype(str).str.upper()
        _add_feature(f, 'feat_pio_yes',
                     pio.str.contains(r'\bYES\b|\bTRUE\b', regex=True, na=False).astype(int), config)
        _add_feature(f, 'feat_pio_no',
                     pio.str.contains(r'\bNO\b|\bFALSE\b|\bNOT\b', regex=True, na=False).astype(int), config)
        org = df['OrganizationName'].astype(str).str.upper()
        _add_feature(f, 'feat_org_is_hospital',
                     org.str.contains('HOSPITAL|HEALTH SYSTEM|MEDICAL CENTER', na=False).astype(int), config)
        _add_feature(f, 'feat_org_is_missing',
                     (org.isin(['NAN', 'NONE', 'NULL', '']) | df['OrganizationName'].isna()).astype(int), config)
        _add_feature(f, 'feat_org_name_length', org.str.len(), config)

    # -------- I: Claims raw --------
    has_any = (df['N_CLAIMS'] > 0).astype(int)
    if fams.get('claims_raw'):
        _add_feature(f, 'feat_claims_has_any', has_any, config)
        _add_feature(f, 'feat_claims_n', df['N_CLAIMS'], config)
        _add_feature(f, 'feat_claims_distinct_orgs', df['DISTINCT_ORGS'], config)
        _add_feature(f, 'feat_claims_distinct_addrs', df['DISTINCT_ADDRS'], config)
        _add_feature(f, 'feat_claims_days_since', df['DAYS_SINCE'], config)
        _add_feature(f, 'feat_claims_addr_exact_match', df['ADDR_EXACT_MATCH'], config)
        _add_feature(f, 'feat_claims_zip_match', df['ZIP_MATCH'], config)
        _add_feature(f, 'feat_claims_street_zip_match', df['STREET_ZIP_MATCH'], config)
        _add_feature(f, 'feat_claims_recent_zip_match', df['RECENT_ZIP_MATCH'], config)
        _add_feature(f, 'feat_claims_recent_street_zip', df['RECENT_STREET_ZIP_MATCH'], config)

    # -------- J: Claims derived --------
    if fams.get('claims_derived'):
        _add_feature(f, 'feat_claims_log1p_n', np.log1p(df['N_CLAIMS']), config)
        _add_feature(f, 'feat_claims_high_volume',
                     (df['N_CLAIMS'] >= th['claims_high_volume_min']).astype(int), config)
        _add_feature(f, 'feat_claims_addrs_per_org',
                     (df['DISTINCT_ADDRS'] / df['DISTINCT_ORGS'].replace(0, np.nan)).fillna(0), config)
        _add_feature(f, 'feat_claims_recent_active',
                     ((df['DAYS_SINCE'] < th['claims_recent_days']) &
                      (df['N_CLAIMS'] > 0)).astype(int), config)
        _add_feature(f, 'feat_claims_no_match_high_n',
                     ((df['N_CLAIMS'] >= th['claims_high_volume_min']) &
                      (df['ZIP_MATCH'] == 0)).astype(int), config)
        _add_feature(f, 'feat_claims_strong_corroborate',
                     ((df['RECENT_STREET_ZIP_MATCH'] == 1) &
                      (df['N_CLAIMS'] >= th['claims_strong_corroborate_min'])).astype(int), config)
        _add_feature(f, 'feat_claims_strong_contradict',
                     ((df['N_CLAIMS'] >= th['claims_strong_contradict_min']) &
                      (df['RECENT_ZIP_MATCH'] == 0) &
                      (df['DAYS_SINCE'] < th['claims_recent_days'])).astype(int), config)

    # -------- K: Cross-family interactions --------
    if fams.get('cross_interactions'):
        spec_tax = df['Specialty'].astype(str).str.contains(r'\([0-9A-Z]{6,}\)', regex=True).astype(int)
        _add_feature(f, 'feat_taxonomy_x_no_claims',
                     (spec_tax & (1 - has_any)).astype(int), config)
        if 'feat_claims_strong_contradict' in f.columns:
            r3_acc = (df['R3'] == 'ACCURATE').astype(int)
            _add_feature(f, 'feat_r3acc_x_claims_contradict',
                         (r3_acc & f['feat_claims_strong_contradict']).astype(int), config)
        if 'feat_claims_strong_corroborate' in f.columns:
            r3_ina = (df['R3'] == 'INACCURATE').astype(int)
            _add_feature(f, 'feat_r3ina_x_claims_corroborate',
                         (r3_ina & f['feat_claims_strong_corroborate']).astype(int), config)

    return f


def list_active_features(config=None):
    """Return the list of feature names that would be produced by this config."""
    if config is None:
        config = load_config()
    # Easiest is to actually build on a tiny dummy frame, but that needs the
    # full base schema. Instead, replicate the gating logic.
    active = []
    fams = config['families']
    feat_overrides = config['features']

    def _g(name):
        return feat_overrides.get(name, True)

    if fams.get('specialty_provenance'):
        for n in ['feat_specialty_has_taxonomy', 'feat_specialty_length']:
            if _g(n): active.append(n)
    if fams.get('r3_internals'):
        for n in ['feat_r3_score', 'feat_r3_is_accurate', 'feat_r3_is_inaccurate',
                  'feat_r3_is_inconclusive', 'feat_r3_score_is_zero', 'feat_r3_score_is_perfect']:
            if _g(n): active.append(n)
    if fams.get('evidence_cube_raw'):
        for k in EVIDENCE_COLS:
            n = f'feat_ev_{k}_n'
            if _g(n): active.append(n)
    if fams.get('evidence_rollups'):
        for n in ['feat_ev_total_found', 'feat_ev_total_not_found',
                  'feat_ev_total', 'feat_ev_found_ratio']:
            if _g(n): active.append(n)
    if fams.get('cross_view_agreement'):
        for n in ['feat_pv_found_any', 'feat_ov_found_any',
                  'feat_pv_notfound_any', 'feat_ov_notfound_any',
                  'feat_both_views_found', 'feat_only_pv_found',
                  'feat_only_ov_found', 'feat_neither_view_found',
                  'feat_source_diversity']:
            if _g(n): active.append(n)
    if fams.get('geography'):
        for n in ['feat_state', 'feat_state_high_risk', 'feat_zip_prefix']:
            if _g(n): active.append(n)
    if fams.get('provider_attributes'):
        for n in ['feat_has_middle_name', 'feat_has_credentials',
                  'feat_npi_changed_by_r3', 'feat_npi_deactivated',
                  'feat_r3_suggests_name_change', 'feat_cred_is_md_do',
                  'feat_cred_is_midlevel']:
            if _g(n): active.append(n)
    if fams.get('org_linkage'):
        for n in ['feat_has_pio_evidence', 'feat_pio_yes', 'feat_pio_no',
                  'feat_org_is_hospital', 'feat_org_is_missing', 'feat_org_name_length']:
            if _g(n): active.append(n)
    if fams.get('claims_raw'):
        for n in ['feat_claims_has_any', 'feat_claims_n',
                  'feat_claims_distinct_orgs', 'feat_claims_distinct_addrs',
                  'feat_claims_days_since', 'feat_claims_addr_exact_match',
                  'feat_claims_zip_match', 'feat_claims_street_zip_match',
                  'feat_claims_recent_zip_match', 'feat_claims_recent_street_zip']:
            if _g(n): active.append(n)
    if fams.get('claims_derived'):
        for n in ['feat_claims_log1p_n', 'feat_claims_high_volume',
                  'feat_claims_addrs_per_org', 'feat_claims_recent_active',
                  'feat_claims_no_match_high_n', 'feat_claims_strong_corroborate',
                  'feat_claims_strong_contradict']:
            if _g(n): active.append(n)
    if fams.get('cross_interactions'):
        for n in ['feat_taxonomy_x_no_claims',
                  'feat_r3acc_x_claims_contradict',
                  'feat_r3ina_x_claims_corroborate']:
            if _g(n): active.append(n)
    return active
