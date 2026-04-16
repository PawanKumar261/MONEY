import os
import json
import time
import threading
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# ─────────────────────────────────────────────
#  APP SETUP
# ─────────────────────────────────────────────
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
app = Flask(__name__,
            template_folder=os.path.join(frontend_dir, 'templates'),
            static_folder=os.path.join(frontend_dir, 'static'))
app.secret_key = os.environ.get("SECRET_KEY", "auction-secret-2025")

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ─────────────────────────────────────────────
#  PASSWORDS
# ─────────────────────────────────────────────
ADMIN_PASSWORD = "Bp2rut5y37"   # Admin login

# Each team gets their own password (TeamName -> password)
# Password = team name lowercase, spaces replaced by underscore + "@bid"
def get_team_password(team_name):
    """Generate a simple deterministic password from team name."""
    return team_name.lower().replace(" ", "_") + "@bid"


# ─────────────────────────────────────────────
#  AUTH HELPERS
# ─────────────────────────────────────────────
def is_admin():
    return session.get("role") == "admin"

def is_team():
    return session.get("role") == "team"

def require_admin():
    if not is_admin():
        return redirect(url_for("login"))
    return None

def require_team():
    if not is_team():
        return redirect(url_for("login"))
    return None

# ─────────────────────────────────────────────
#  GOOGLE SHEETS SETUP
# ─────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Try backend/ folder first
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
# Fallback to the parent folder (for Render deployment)
if not os.path.exists(CREDENTIALS_FILE):
    CREDENTIALS_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'credentials.json'))

# Replace with your actual Google Sheet ID (found in the URL)
SPREADSHEET_ID = "1e6iVPrpcp-9r1Gs0_b4SvPNQF2V_XtQA5exWgheBGl8"


def get_gsheet_client():
    """Authenticate and return a gspread client."""
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        client = gspread.authorize(creds)
        return client
    except FileNotFoundError:
        print("⚠️  credentials.json not found. Google Sheets features disabled.")
        return None
    except Exception as e:
        print(f"⚠️  GSheet auth error: {e}")
        return None


def get_spreadsheet():
    client = get_gsheet_client()
    if client is None:
        return None
    try:
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        print(f"⚠️  Could not open spreadsheet: {e}")
        return None


# ─────────────────────────────────────────────
#  IN-MEMORY AUCTION STATE
# ─────────────────────────────────────────────
auction_state = {
    "current_player": None,       # dict with player data
    "current_bid": 0,
    "highest_bidder": None,       # team name
    "timer": 20,
    "timer_running": False,
    "players": [],                # list of player dicts
    "teams": {},                  # { teamName: { budget, playerCount } }
    "auction_active": False,
}

timer_thread = None
timer_lock = threading.Lock()


# ─────────────────────────────────────────────
#  TIMER LOGIC
# ─────────────────────────────────────────────
def run_timer():
    global auction_state
    while True:
        time.sleep(1)
        with timer_lock:
            if not auction_state["timer_running"]:
                break
            auction_state["timer"] -= 1
            remaining = auction_state["timer"]

        socketio.emit("timer_update", {"timer": remaining})

        if remaining <= 0:
            with timer_lock:
                auction_state["timer_running"] = False
            socketio.emit("timer_expired", {
                "player": auction_state["current_player"],
                "winner": auction_state["highest_bidder"],
                "final_bid": auction_state["current_bid"],
            })
            break


def start_timer():
    global timer_thread
    with timer_lock:
        auction_state["timer"] = 20
        auction_state["timer_running"] = True

    timer_thread = threading.Thread(target=run_timer, daemon=True)
    timer_thread.start()


def reset_timer():
    """Reset timer to 20 when a new bid is placed."""
    with timer_lock:
        auction_state["timer"] = 10
        # Keep timer_running = True; existing thread will pick up new value


# ─────────────────────────────────────────────
#  ROUTES
# ─────────────────────────────────────────────
@app.route("/")
def root():
    """Root redirects based on session role."""
    if is_admin():
        return redirect(url_for("admin"))
    if is_team():
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.route("/login")
def login():
    # Pass team names so login page can populate the dropdown
    teams = list(auction_state["teams"].keys())
    return render_template("login.html", teams=teams)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/bid")
def index():
    """Team bidding page — requires team login."""
    redir = require_team()
    if redir:
        return redir
    return render_template("index.html")


@app.route("/admin")
def admin():
    """Admin command center — requires admin login."""
    redir = require_admin()
    if redir:
        return redir
    return render_template("admin.html")


@app.route("/audience")
def audience():
    """Public read-only spectator view."""
    return render_template("audience.html")


# ─────────────────────────────────────────────
#  REST API ENDPOINTS
# ─────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.json or {}
    role = data.get("role")
    password = data.get("password")

    if role == "admin":
        if password == ADMIN_PASSWORD:
            session["role"] = "admin"
            return jsonify({"success": True, "redirect": url_for("admin")})
        return jsonify({"success": False, "error": "Invalid admin password"}), 401

    elif role == "team":
        team_name = data.get("team")
        if not team_name:
            return jsonify({"success": False, "error": "Team missing"}), 400
        
        expected_pass = get_team_password(team_name)
        if password == expected_pass:
            session["role"] = "team"
            session["team_name"] = team_name
            return jsonify({"success": True, "redirect": url_for("index")})
        return jsonify({"success": False, "error": f"Invalid password. Try '{expected_pass}'"}), 401
    
    return jsonify({"success": False, "error": "Invalid role"}), 400


@app.route("/api/reset_auction", methods=["POST"])
def reset_auction():
    """Reset the entire Google Sheet and server state back to default."""
    if not is_admin():
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    ss = get_spreadsheet()
    if ss is None:
        return jsonify({"success": False, "error": "Google Sheets not configured"}), 500

    try:
        # Reset Players
        player_sheet = ss.worksheet("Players")
        p_records = player_sheet.get_all_records()
        for i, row in enumerate(p_records, start=2):
            player_sheet.update_cell(i, 5, 'Available')
            player_sheet.update_cell(i, 6, '')
            player_sheet.update_cell(i, 7, 0)

        # Reset Teams
        team_sheet = ss.worksheet("Teams")
        t_records = team_sheet.get_all_records()
        for i, row in enumerate(t_records, start=2):
            team_sheet.update_cell(i, 2, 100)
            team_sheet.update_cell(i, 3, 0)
            
        return jsonify({"success": True, "message": "Auction data completely wiped!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/fetch_players", methods=["GET"])
def fetch_players():
    """Fetch all players from Google Sheet (Sheet1)."""
    ss = get_spreadsheet()
    if ss is None:
        return jsonify({"error": "Google Sheets not configured"}), 500

    try:
        sheet = ss.worksheet("Players")
        rows = sheet.get_all_records()
        players = []
        for row in rows:
            players.append({
                "id": row.get("ID", ""),
                "name": row.get("Name", ""),
                "skill": row.get("Skill", ""),
                "base_price": int(row.get("BasePrice", 0)),
                "status": row.get("Status", "Available"),
                "sold_to": row.get("SoldTo", ""),
                "final_price": row.get("FinalPrice", 0),
            })
        auction_state["players"] = players
        socketio.emit("players_updated", {"players": players})
        return jsonify({"players": players})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/fetch_teams", methods=["GET"])
def fetch_teams():
    """Fetch all teams from Google Sheet (Sheet2)."""
    ss = get_spreadsheet()
    if ss is None:
        return jsonify({"error": "Google Sheets not configured"}), 500

    try:
        sheet = ss.worksheet("Teams")
        rows = sheet.get_all_records()
        teams = {}
        for row in rows:
            name = row.get("TeamName", "")
            teams[name] = {
                "budget": int(row.get("Budget", 100)),
                "player_count": int(row.get("PlayerCount", 0)),
            }
        auction_state["teams"] = teams
        socketio.emit("teams_updated", {"teams": teams})
        return jsonify({"teams": teams})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/get_state", methods=["GET"])
def get_state():
    """Return current auction state (for reconnecting clients)."""
    return jsonify({
        "current_player": auction_state["current_player"],
        "current_bid": auction_state["current_bid"],
        "highest_bidder": auction_state["highest_bidder"],
        "timer": auction_state["timer"],
        "timer_running": auction_state["timer_running"],
        "players": auction_state["players"],
        "teams": auction_state["teams"],
        "auction_active": auction_state["auction_active"],
    })


@app.route("/api/sold", methods=["POST"])
def sold():
    """Mark player as SOLD, update Sheets, deduct budget."""
    data = request.json or {}
    player_id = data.get("player_id") or (
        auction_state["current_player"]["id"] if auction_state["current_player"] else None
    )
    winner = data.get("winner", auction_state["highest_bidder"])
    final_price = data.get("final_price", auction_state["current_bid"])

    if not player_id or not winner:
        return jsonify({"error": "Missing player_id or winner"}), 400

    ss = get_spreadsheet()
    if ss is None:
        return jsonify({"error": "Google Sheets not configured"}), 500

    try:
        # Update Players sheet
        players_sheet = ss.worksheet("Players")
        player_rows = players_sheet.get_all_records()
        for i, row in enumerate(player_rows, start=2):  # row 1 is header
            if str(row.get("ID")) == str(player_id):
                players_sheet.update_cell(i, 5, "Sold")
                players_sheet.update_cell(i, 6, winner)
                players_sheet.update_cell(i, 7, final_price)
                break

        # Update Teams sheet – deduct budget, increment player count
        teams_sheet = ss.worksheet("Teams")
        team_rows = teams_sheet.get_all_records()
        for i, row in enumerate(team_rows, start=2):
            if row.get("TeamName") == winner:
                current_budget = int(row.get("Budget", 0))
                current_count = int(row.get("PlayerCount", 0))
                new_budget = max(current_budget - int(final_price), 0)
                teams_sheet.update_cell(i, 2, new_budget)
                teams_sheet.update_cell(i, 3, current_count + 1)
                # Update in-memory teams
                auction_state["teams"][winner] = {
                    "budget": new_budget,
                    "player_count": current_count + 1,
                }
                break

        # Update in-memory players
        for p in auction_state["players"]:
            if str(p["id"]) == str(player_id):
                p["status"] = "Sold"
                p["sold_to"] = winner
                p["final_price"] = final_price
                break

        # Reset auction state
        sold_player = auction_state["current_player"]
        auction_state["current_player"] = None
        auction_state["current_bid"] = 0
        auction_state["highest_bidder"] = None
        auction_state["timer_running"] = False
        auction_state["auction_active"] = False

        socketio.emit("player_sold", {
            "player": sold_player,
            "winner": winner,
            "final_price": final_price,
            "teams": auction_state["teams"],
            "players": auction_state["players"],
        })

        return jsonify({"success": True, "winner": winner, "final_price": final_price})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
#  SOCKET EVENTS
# ─────────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    emit("state_sync", {
        "current_player": auction_state["current_player"],
        "current_bid": auction_state["current_bid"],
        "highest_bidder": auction_state["highest_bidder"],
        "timer": auction_state["timer"],
        "timer_running": auction_state["timer_running"],
        "teams": auction_state["teams"],
        "players": auction_state["players"],
        "auction_active": auction_state["auction_active"],
    })


@socketio.on("start_auction")
def handle_start_auction(data):
    """Admin starts auction for a specific player."""
    player = data.get("player")
    if not player:
        return

    with timer_lock:
        auction_state["current_player"] = player
        auction_state["current_bid"] = player.get("base_price", 0)
        auction_state["highest_bidder"] = None
        auction_state["timer"] = 20
        auction_state["auction_active"] = True

    socketio.emit("auction_started", {
        "player": player,
        "current_bid": auction_state["current_bid"],
    })
    start_timer()


@socketio.on("place_bid")
def handle_bid(data):
    """A team places a bid (increment)."""
    team = data.get("team")
    increment = int(data.get("increment", 1))

    if not auction_state["auction_active"]:
        emit("bid_rejected", {"reason": "No active auction"})
        return

    if not team:
        emit("bid_rejected", {"reason": "No team specified"})
        return

    # Check budget from in-memory state (live fetch optional)
    team_data = auction_state["teams"].get(team)
    if team_data:
        proposed_bid = auction_state["current_bid"] + increment
        if proposed_bid > team_data["budget"]:
            emit("bid_rejected", {
                "reason": f"Insufficient budget! You have {team_data['budget']} credits.",
                "team": team,
            })
            return

    with timer_lock:
        auction_state["current_bid"] += increment
        auction_state["highest_bidder"] = team
        # Reset timer on every bid
        auction_state["timer"] = 20

    socketio.emit("bid_update", {
        "current_bid": auction_state["current_bid"],
        "highest_bidder": auction_state["highest_bidder"],
        "team": team,
        "increment": increment,
        "timer": 20,
    })


@socketio.on("unsold_player")
def handle_unsold(data):
    """Admin marks current player as unsold."""
    player = auction_state["current_player"]
    with timer_lock:
        auction_state["timer_running"] = False
        auction_state["current_player"] = None
        auction_state["current_bid"] = 0
        auction_state["highest_bidder"] = None
        auction_state["auction_active"] = False

    socketio.emit("player_unsold", {"player": player})


@socketio.on("disconnect")
def handle_disconnect():
    pass


# ─────────────────────────────────────────────
#  STARTUP PRELOAD
# ─────────────────────────────────────────────
def preload_data():
    """Auto-fetch players & teams from Google Sheets on startup."""
    print("[INFO] Preloading data from Google Sheets...")
    ss = get_spreadsheet()
    if ss is None:
        print("[WARN] Skipping preload -- Sheets not configured.")
        return

    # Load Teams
    try:
        sheet = ss.worksheet("Teams")
        rows  = sheet.get_all_records()
        teams = {}
        for row in rows:
            name = row.get("TeamName", "")
            if name:
                teams[name] = {
                    "budget":       int(row.get("Budget", 100)),
                    "player_count": int(row.get("PlayerCount", 0)),
                }
        auction_state["teams"] = teams
        print(f"[OK] Teams loaded: {list(teams.keys())}")
    except Exception as e:
        print(f"[WARN] Could not load Teams: {e}")

    # Load Players
    try:
        sheet = ss.worksheet("Players")
        rows  = sheet.get_all_records()
        players = []
        for row in rows:
            players.append({
                "id":         row.get("ID", ""),
                "name":       row.get("Name", ""),
                "skill":      row.get("Skill", ""),
                "base_price": int(row.get("BasePrice", 0)),
                "status":     row.get("Status", "Available"),
                "sold_to":    row.get("SoldTo", ""),
                "final_price":row.get("FinalPrice", 0),
            })
        auction_state["players"] = players
        print(f"[OK] Players loaded: {len(players)} players")
    except Exception as e:
        print(f"[WARN] Could not load Players: {e}")


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    preload_data()
    socketio.run(app, host="0.0.0.0", port=5000, debug=True, allow_unsafe_werkzeug=True)
