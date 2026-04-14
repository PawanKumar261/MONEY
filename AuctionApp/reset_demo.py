import json
import gspread
from google.oauth2.service_account import Credentials

# Extract spreadsheet ID from app.py
SPREADSHEET_ID = ""
try:
    with open("app.py", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("SPREADSHEET_ID"):
                SPREADSHEET_ID = line.split("=")[1].strip().strip('"').strip("'")
                break
except Exception as e:
    print(f"Error reading app.py: {e}")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def reset_data():
    try:
        credentials = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
        gc = gspread.authorize(credentials)
        ss = gc.open_by_key(SPREADSHEET_ID)

        # Reset Players
        print("Resetting Players...")
        player_sheet = ss.worksheet("Players")
        # Col E: Status (Available), Col F: SoldTo (empty), Col G: FinalPrice (0)
        # Starting from row 2
        p_records = player_sheet.get_all_records()
        for i, row in enumerate(p_records, start=2):
            player_sheet.update_cell(i, 5, 'Available')
            player_sheet.update_cell(i, 6, '')
            player_sheet.update_cell(i, 7, 0)
        
        print("Resetting Teams...")
        team_sheet = ss.worksheet("Teams")
        # Col B: Budget (100), Col C: PlayerCount (0)
        t_records = team_sheet.get_all_records()
        for i, row in enumerate(t_records, start=2):
            team_sheet.update_cell(i, 2, 100)
            team_sheet.update_cell(i, 3, 0)

        print("✅ Data Reset Complete!")
    except Exception as e:
        print(f"Error resetting data: {e}")

if __name__ == "__main__":
    reset_data()
