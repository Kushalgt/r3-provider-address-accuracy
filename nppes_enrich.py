"""
================================================================================
NPPES ENRICHMENT MODULE  (free-source triangulation)
================================================================================

Enriches the R3 Base Data with the CMS NPPES NPI Registry — the free backbone
source from `docs/multi_source_triangulation_plan.md` and the
`provider-data-triangulation` skill.

Design split (so the same feature logic runs both live and offline):

  fetch_nppes_live(npi)      -> hits the public NPPES API (needs internet)
  parse_nppes_result(result) -> pure function: NPPES result dict -> flat attrs
  build_nppes_features(...)   -> per-NPI attribute table (one row per NPI)
  add_row_level_match(...)    -> per-BASE-ROW address match tiers vs NPPES

`parse_nppes_result` accepts a NPPES API *result object* — the shape returned
under results[0] of https://npiregistry.cms.hhs.gov/api/. The MCP npi_lookup
tool returns that same object under record['raw'], so a sample fetched via MCP
and a live batch fetched over HTTP feed the identical parser. That is what lets
us validate the exact code in the sandbox (no internet) before running it at
full scale on a machine that has internet.

NPI is a public, FOIA-disclosable identifier and NPPES is the official public
registry; only the 10-digit NPI leaves the machine, never names/addresses.
Everything else (claims join, feature build) runs locally.

Free-derivable features produced (per the plan's "All free-derivable" scope):
  nppes_found, nppes_entity_type, nppes_is_org, nppes_deactivated,
  nppes_days_since_update, nppes_credential, nppes_primary_taxonomy_code,
  nppes_primary_taxonomy_desc, is_hospital_affiliated_specialty,
  nppes_num_locations, nppes_practice_ne_mailing,
  nppes_practice_addr_match_tier   (per base row),
  nppes_any_location_match_tier    (per base row).

USPS/geocode features (dpv_confirmed, rdi_residential, distances) are NOT here
— they need a paid CASS/DPV vendor and were explicitly excluded from this pass.
"""

from __future__ import annotations

import re
import json
import time
import datetime as _dt
from typing import Optional, Dict, List, Any

import pandas as pd


# ============================================================================
# ADDRESS NORMALIZATION + MATCH TIERS
# ============================================================================

# USPS-style abbreviations (minimal, high-value subset — extend as needed).
_ABBREV = {
    'STREET': 'ST', 'AVENUE': 'AVE', 'BOULEVARD': 'BLVD', 'DRIVE': 'DR',
    'ROAD': 'RD', 'LANE': 'LN', 'COURT': 'CT', 'PLACE': 'PL', 'SUITE': 'STE',
    'PARKWAY': 'PKWY', 'HIGHWAY': 'HWY', 'CIRCLE': 'CIR', 'TERRACE': 'TER',
    'NORTH': 'N', 'SOUTH': 'S', 'EAST': 'E', 'WEST': 'W',
    'NORTHEAST': 'NE', 'NORTHWEST': 'NW', 'SOUTHEAST': 'SE', 'SOUTHWEST': 'SW',
    'FLOOR': 'FL', 'BUILDING': 'BLDG', 'APARTMENT': 'APT', 'UNIT': 'UNIT',
}

# Match-tier codes (higher = stronger corroboration).
TIER_NONE, TIER_ZIP, TIER_STREET_ZIP, TIER_EXACT = 0, 1, 2, 3
TIER_LABELS = {0: 'NONE', 1: 'ZIP', 2: 'STREET_ZIP', 3: 'EXACT'}


def norm_street(s: Any) -> str:
    """Uppercase, strip punctuation, apply USPS abbreviations, collapse spaces."""
    if s is None:
        return ''
    s = str(s).upper()
    s = re.sub(r'[^A-Z0-9 ]', ' ', s)
    toks = [_ABBREV.get(t, t) for t in s.split()]
    return ' '.join(toks).strip()


def zip5(z: Any) -> str:
    """Return the first 5 digits of a ZIP (drops ZIP+4 and any '.0' float tail)."""
    if z is None:
        return ''
    d = re.sub(r'\D', '', str(z))
    return d[:5]


def _house_num(street_norm: str) -> str:
    m = re.match(r'^(\d+)', street_norm)
    return m.group(1) if m else ''


def _tokens(street_norm: str) -> set:
    return set(street_norm.split())


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def match_tier(a_street: Any, a_zip: Any, b_street: Any, b_zip: Any) -> int:
    """Tiered address comparison (see provider-address-validation skill).

    EXACT       normalized street identical AND zip5 identical
    STREET_ZIP  same zip5, same house number, strong street-token overlap
    ZIP         same zip5 only
    NONE        otherwise / missing candidate
    """
    na, nb = norm_street(a_street), norm_street(b_street)
    az, bz = zip5(a_zip), zip5(b_zip)
    if not nb or not bz:
        return TIER_NONE
    if na == nb and az == bz and az:
        return TIER_EXACT
    if az and az == bz:
        if _house_num(na) and _house_num(na) == _house_num(nb) and _jaccard(_tokens(na), _tokens(nb)) >= 0.5:
            return TIER_STREET_ZIP
        return TIER_ZIP
    return TIER_NONE


# ============================================================================
# SPECIALTY INSTABILITY FLAG (taxonomy-derived, free)
# ============================================================================

# Hospital-affiliated / rotation-prone specialties that agree with the phone
# only ~2-5% in this dataset (CLAUDE.md "Verified Data Facts"). Matched as
# case-insensitive substrings against the NPPES taxonomy description AND/OR the
# base Specialty field. Extend from the NUCC crosswalk if a full copy is loaded.
_HOSPITAL_AFFIL_KEYWORDS = (
    'PEDIATRIC', 'CARDIOLOG', 'CARDIOVASCULAR', 'OBSTETRIC', 'GYNECOLOG',
    'OB/GYN', 'OBGYN', 'INTERNAL MEDICINE', 'HOSPITALIST', 'ANESTHESIOLOG',
    'EMERGENCY MEDICINE', 'CRITICAL CARE', 'NEONATAL', 'RADIOLOG',
    'PATHOLOG', 'SURGERY', 'SURGICAL',
)


def is_hospital_affiliated_specialty(*descriptions: Any) -> int:
    for d in descriptions:
        if not d:
            continue
        u = str(d).upper()
        if any(k in u for k in _HOSPITAL_AFFIL_KEYWORDS):
            return 1
    return 0


# ============================================================================
# NPPES RESULT PARSING  (pure function — no I/O)
# ============================================================================

def _first_addr(addresses: List[dict], purpose: str) -> Optional[dict]:
    for a in addresses or []:
        if str(a.get('address_purpose', '')).upper() == purpose:
            return a
    return None


def parse_nppes_result(result: Optional[dict], as_of: Optional[_dt.date] = None) -> Dict[str, Any]:
    """Flatten one NPPES result object into per-NPI attributes.

    `result` is the object at results[0] of the NPPES API (== MCP record['raw']).
    Pass None (not found) to get an all-null / not-found row.
    """
    as_of = as_of or _dt.date.today()
    out: Dict[str, Any] = {
        'nppes_found': 0,
        'nppes_entity_type': None,
        'nppes_is_org': None,
        'nppes_deactivated': None,
        'nppes_last_updated': None,
        'nppes_days_since_update': None,
        'nppes_credential': None,
        'nppes_primary_taxonomy_code': None,
        'nppes_primary_taxonomy_desc': None,
        'nppes_practice_addr1': None,
        'nppes_practice_zip': None,
        'nppes_mailing_addr1': None,
        'nppes_mailing_zip': None,
        'nppes_practice_ne_mailing': None,
        'nppes_num_locations': None,
        # kept as a python list of (addr1, zip) for row-level matching later:
        'nppes_location_addrs': None,
    }
    if not result:
        return out

    out['nppes_found'] = 1
    et = str(result.get('enumeration_type', '') or '')
    out['nppes_entity_type'] = et or None
    out['nppes_is_org'] = 1 if et.endswith('2') else (0 if et.endswith('1') else None)

    basic = result.get('basic', {}) or {}
    status = str(basic.get('status', '') or '').upper()
    out['nppes_deactivated'] = 1 if status == 'D' else 0
    out['nppes_credential'] = basic.get('credential')
    lu = basic.get('last_updated')
    out['nppes_last_updated'] = lu
    if lu:
        try:
            d = _dt.date.fromisoformat(str(lu)[:10])
            out['nppes_days_since_update'] = (as_of - d).days
        except ValueError:
            pass

    taxes = result.get('taxonomies', []) or []
    prim = next((t for t in taxes if t.get('primary')), (taxes[0] if taxes else None))
    if prim:
        out['nppes_primary_taxonomy_code'] = prim.get('code')
        out['nppes_primary_taxonomy_desc'] = prim.get('desc')

    # Addresses: the top-level `addresses` list holds MAILING + primary LOCATION;
    # `practiceLocations` holds ADDITIONAL practice sites (multi-site signal).
    addresses = result.get('addresses', []) or []
    loc = _first_addr(addresses, 'LOCATION')
    mail = _first_addr(addresses, 'MAILING')
    if loc:
        out['nppes_practice_addr1'] = loc.get('address_1')
        out['nppes_practice_zip'] = zip5(loc.get('postal_code'))
    if mail:
        out['nppes_mailing_addr1'] = mail.get('address_1')
        out['nppes_mailing_zip'] = zip5(mail.get('postal_code'))
    if loc and mail:
        same = (norm_street(loc.get('address_1')) == norm_street(mail.get('address_1'))
                and zip5(loc.get('postal_code')) == zip5(mail.get('postal_code')))
        out['nppes_practice_ne_mailing'] = 0 if same else 1

    # All distinct practice-location addresses (primary + extras).
    loc_addrs: List[List[str]] = []
    seen = set()
    for a in ([loc] if loc else []) + list(result.get('practiceLocations', []) or []):
        if not a:
            continue
        pair = (norm_street(a.get('address_1')), zip5(a.get('postal_code')))
        if pair[0] and pair not in seen:
            seen.add(pair)
            loc_addrs.append([a.get('address_1'), zip5(a.get('postal_code'))])
    out['nppes_location_addrs'] = loc_addrs
    out['nppes_num_locations'] = len(loc_addrs) if loc_addrs else 0
    return out


# ============================================================================
# LIVE FETCH  (needs internet — used when the user runs this at full scale)
# ============================================================================

NPPES_API = 'https://npiregistry.cms.hhs.gov/api/'


def fetch_nppes_live(npi: str, session=None, timeout: int = 15) -> Optional[dict]:
    """Fetch one NPI's NPPES result object over HTTP. Returns None on any error
    or if the NPI is unassigned. Requires the `requests` package + internet.
    """
    import requests  # local import so the module loads without requests present
    s = session or requests.Session()
    try:
        r = s.get(NPPES_API, params={'version': '2.1', 'number': str(npi)}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        results = data.get('results') or []
        return results[0] if results else None
    except Exception:
        return None


def fetch_many_live(npis: List[str], sleep: float = 0.05, progress_every: int = 200) -> Dict[str, Optional[dict]]:
    """Fetch a list of NPIs live, one at a time (NPPES has no batch-by-number).
    Returns {npi: result_or_None}. Polite sleep between calls.
    """
    import requests
    s = requests.Session()
    out: Dict[str, Optional[dict]] = {}
    for i, npi in enumerate(npis):
        out[npi] = fetch_nppes_live(npi, session=s)
        if sleep:
            time.sleep(sleep)
        if progress_every and (i + 1) % progress_every == 0:
            print(f"  fetched {i + 1}/{len(npis)} NPIs")
    return out


# ============================================================================
# FEATURE ASSEMBLY
# ============================================================================

def build_nppes_features(npis: List[str],
                         results_by_npi: Dict[str, Optional[dict]],
                         as_of: Optional[_dt.date] = None,
                         base_specialty_by_npi: Optional[Dict[str, str]] = None) -> pd.DataFrame:
    """One row per NPI of NPPES-derived attributes (address-independent)."""
    base_specialty_by_npi = base_specialty_by_npi or {}
    rows = []
    for npi in npis:
        attrs = parse_nppes_result(results_by_npi.get(npi), as_of=as_of)
        attrs['OrigNPI'] = str(npi)
        attrs['is_hospital_affiliated_specialty'] = is_hospital_affiliated_specialty(
            attrs.get('nppes_primary_taxonomy_desc'),
            base_specialty_by_npi.get(str(npi)),
        )
        rows.append(attrs)
    return pd.DataFrame(rows)


def add_row_level_match(base_df: pd.DataFrame,
                        addr_col: str = 'Address1',
                        zip_col: str = 'Zip') -> pd.DataFrame:
    """Compute per-BASE-ROW NPPES address match tiers.

    Requires base_df to already carry (from the per-NPI join):
      nppes_practice_addr1, nppes_practice_zip, nppes_location_addrs
    Adds:
      nppes_practice_addr_match_tier / _label
      nppes_any_location_match_tier  / _label   (max over all NPPES locations)
    """
    df = base_df.copy()

    def _primary_tier(r):
        if pd.isna(r.get('nppes_practice_addr1')):
            return TIER_NONE
        return match_tier(r.get(addr_col), r.get(zip_col),
                          r.get('nppes_practice_addr1'), r.get('nppes_practice_zip'))

    def _any_tier(r):
        locs = r.get('nppes_location_addrs')
        if not isinstance(locs, list) or not locs:
            return _primary_tier(r)
        best = TIER_NONE
        for a1, z in locs:
            best = max(best, match_tier(r.get(addr_col), r.get(zip_col), a1, z))
            if best == TIER_EXACT:
                break
        return best

    df['nppes_practice_addr_match_tier'] = df.apply(_primary_tier, axis=1)
    df['nppes_any_location_match_tier'] = df.apply(_any_tier, axis=1)
    df['nppes_practice_addr_match_label'] = df['nppes_practice_addr_match_tier'].map(TIER_LABELS)
    df['nppes_any_location_match_label'] = df['nppes_any_location_match_tier'].map(TIER_LABELS)
    return df


# ============================================================================
# JSON CACHE HELPERS (so a live fetch can be saved and re-used offline)
# ============================================================================

def save_cache(results_by_npi: Dict[str, Optional[dict]], path: str) -> None:
    with open(path, 'w') as fh:
        json.dump(results_by_npi, fh)


def load_cache(path: str) -> Dict[str, Optional[dict]]:
    with open(path) as fh:
        return json.load(fh)
