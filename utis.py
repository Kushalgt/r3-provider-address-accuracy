import re

from typing import Optional
import pandas as pd
from openpyxl import load_workbook
from xml.etree import ElementTree as ET
import zipfile

def has_merged_header(path):
    """Return True if row 1 of the first sheet has any merged cells.

    Reads merge info directly from the XLSX's XML without loading row data,
    so it's fast even on very large files.
    """
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

    with zipfile.ZipFile(path) as z:
        # xl/worksheets/sheet1.xml is the first sheet by file order
        with z.open("xl/worksheets/sheet1.xml") as f:
            # Stream-parse and stop as soon as we've seen <mergeCells> (it appears
            # after <sheetData>, so we have to skip past row data — but we don't
            # build a tree, we just walk events and discard).
            for event, elem in ET.iterparse(f, events=("end",)):
                if elem.tag == f"{ns}mergeCell":
                    ref = elem.get("ref", "")  # e.g. "A1:C1"
                    # ref looks like "A1:C1"; the row part of the start cell is
                    # the trailing digits of the left side.
                    start = ref.split(":")[0]
                    m = re.search(r"\d+$", start)
                    if m and int(m.group()) == 1:
                        return True
                    elem.clear()
                elif elem.tag == f"{ns}mergeCells":
                    # End of the mergeCells block — no row-1 merge found.
                    return False
                else:
                    elem.clear()
    return False

def load_dataframe(file_path: str,) -> pd.DataFrame:
    """
    Loads a CSV or Excel file into a pandas DataFrame.

    Rules:
    - CSV files are loaded using pd.read_csv()
    - Excel files automatically use the first sheet
    - If the Excel file has a merged header, use header=1
    - Otherwise use the default header row

    Args:
        file_path: Path to CSV or Excel file
        header_row_if_merged: Header row index to use when merged header exists

    Returns:
        pandas DataFrame
    """

    if file_path.lower().endswith(".csv"):
        return pd.read_csv(file_path)

    # Excel file
    if has_merged_header(file_path):
        return pd.read_excel(file_path, header=1)

    return pd.read_excel(file_path)

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
