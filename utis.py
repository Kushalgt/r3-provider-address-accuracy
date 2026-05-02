import re
def predict_with_explain(model, explainer, X):
    # probability
    prob = model.predict_proba(X)[:, 1]

    # shap values
    shap_values = explainer.shap_values(X)[1]

    return prob, shap_values

def extract_area_code(phone_val):
    """
    Extracts 3-digit area code from a phone number.
 
    Handles all common anomalies:
      - +1XXXXXXXXXX       country code prefix
      - (XXX) XXX-XXXX     formatted with parentheses
      - XXX-XXX-XXXX       dashes
      - XXX.XXX.XXXX       dots
      - missing/null/empty/dash-only/placeholder values
 
    Returns:
        Area code as string (e.g. '212')
        '000' if phone is missing or unresolvable
    """
    val = str(phone_val).strip()
 
    # Handle missing / empty / placeholder values
    if val in ['', 'nan', 'None', 'null', '-', '0', 'N/A', 'NA', 'UNKNOWN']:
        return '000'
 
    # Remove country code +1 at the start
    val = re.sub(r'^\+1', '', val)
 
    # Remove leading 1 only if remaining digits would be 10 (i.e. was 11 digit)
    val = re.sub(r'^1(?=\d{10})', '', val)
 
    # Strip all non-digit characters (dashes, dots, spaces, parentheses)
    digits_only = re.sub(r'\D', '', val)
 
    # Need at least 10 digits to extract a valid area code
    if len(digits_only) < 10:
        return '000'
 
    # Area code = first 3 digits
    return digits_only[:3]
