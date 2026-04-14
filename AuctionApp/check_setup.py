import json
import re
import sys
from google.oauth2.service_account import Credentials
import gspread

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Read SPREADSHEET_ID live from app.py so there's no duplication
try:
    with open("app.py", encoding="utf-8") as _f:
        _match = re.search(r'SPREADSHEET_ID\s*=\s*["\']([^"\']+)["\']', _f.read())
    SPREADSHEET_ID = _match.group(1) if _match else "NOT_FOUND"
except Exception as _e:
    SPREADSHEET_ID = "NOT_FOUND"
    print(f"Could not read app.py: {_e}")

# ── Step 1: credentials.json ──────────────────────────────
print("--- Step 1: Reading credentials.json ---")
try:
    with open("credentials.json") as f:
        cred_data = json.load(f)
    email = cred_data.get("client_email", "NOT FOUND")
    proj  = cred_data.get("project_id",   "NOT FOUND")
    typ   = cred_data.get("type",          "NOT FOUND")
    print(f"  type         : {typ}")
    print(f"  client_email : {email}")
    print(f"  project_id   : {proj}")
    if typ != "service_account":
        print("  WARNING: 'type' is not 'service_account' — wrong JSON file?")
    else:
        print("  credentials.json looks valid")
except FileNotFoundError:
    print("  ERROR: credentials.json NOT FOUND in this folder!")
    sys.exit(1)
except Exception as e:
    print(f"  ERROR reading credentials.json: {e}")
    sys.exit(1)

# ── Step 2: Auth ──────────────────────────────────────────
print()
print("--- Step 2: Google Auth ---")
try:
    creds  = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    client = gspread.authorize(creds)
    print("  Auth OK")
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

# ── Step 3: Spreadsheet ID ────────────────────────────────
print()
print(f"--- Step 3: Opening Spreadsheet (ID: {SPREADSHEET_ID}) ---")

# Validate length: real Sheets IDs are 44 chars
if len(SPREADSHEET_ID) < 20:
    print(f"  WARNING: ID '{SPREADSHEET_ID}' looks too short!")
    print("  A real Google Sheets ID is ~44 characters long.")
    print("  Example: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms")
    print("  Please copy it from the browser URL bar.")

try:
    ss = client.open_by_key(SPREADSHEET_ID)
    print(f"  Spreadsheet title : {ss.title}")
    worksheets = [w.title for w in ss.worksheets()]
    print(f"  Worksheets found  : {worksheets}")
except gspread.exceptions.APIError as e:
    code = e.response.status_code if hasattr(e, "response") else "?"
    print(f"  API ERROR ({code}): {e}")
    if "404" in str(e):
        print("  >> Spreadsheet not found — check your SPREADSHEET_ID in app.py")
    elif "403" in str(e):
        print("  >> Permission denied — share the sheet with the service account email above")
    sys.exit(1)
except Exception as e:
    print(f"  ERROR: {e}")
    sys.exit(1)

# ── Step 4: Players sheet ─────────────────────────────────
print()
print("--- Step 4: Checking 'Players' worksheet ---")
try:
    sheet = ss.worksheet("Players")
    rows  = sheet.get_all_records()
    print(f"  Rows (excluding header): {len(rows)}")
    if rows:
        print(f"  Column headers : {list(rows[0].keys())}")
        required = {"ID", "Name", "Skill", "BasePrice", "Status", "SoldTo", "FinalPrice"}
        missing  = required - set(rows[0].keys())
        if missing:
            print(f"  MISSING COLUMNS: {missing}")
        else:
            print("  All required columns present!")
    else:
        print("  Sheet is empty (no data rows yet)")
except gspread.exceptions.WorksheetNotFound:
    print("  ERROR: Sheet tab named 'Players' not found — tab name is case-sensitive!")
except Exception as e:
    print(f"  ERROR: {e}")

# ── Step 5: Teams sheet ───────────────────────────────────
print()
print("--- Step 5: Checking 'Teams' worksheet ---")
try:
    sheet = ss.worksheet("Teams")
    rows  = sheet.get_all_records()
    print(f"  Rows (excluding header): {len(rows)}")
    if rows:
        print(f"  Column headers : {list(rows[0].keys())}")
        required = {"TeamName", "Budget", "PlayerCount"}
        missing  = required - set(rows[0].keys())
        if missing:
            print(f"  MISSING COLUMNS: {missing}")
        else:
            print("  All required columns present!")
        for r in rows:
            print(f"    Team: {r.get('TeamName')}  Budget: {r.get('Budget')}  Players: {r.get('PlayerCount')}")
    else:
        print("  Sheet is empty (add team rows!)")
except gspread.exceptions.WorksheetNotFound:
    print("  ERROR: Sheet tab named 'Teams' not found — tab name is case-sensitive!")
except Exception as e:
    print(f"  ERROR: {e}")

print()
print("=== SETUP CHECK COMPLETE ===")
