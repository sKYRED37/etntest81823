import hashlib
import hmac
import json
import os
import sqlite3
import time
from urllib.parse import parse_qsl
from flask import Flask, jsonify, request, send_from_directory

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ETN_DB_PATH", "/app/data/etn.sqlite3" if os.path.isdir("/app/data") else os.path.join(ROOT, "etn.sqlite3"))
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
INITDATA_MAX_AGE = int(os.environ.get("INITDATA_MAX_AGE", "86400"))

app = Flask(__name__, static_folder="web", static_url_path="")

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY, username TEXT, first_name TEXT,
            last_name TEXT, created_at INTEGER NOT NULL, last_seen INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS user_state (
            telegram_id TEXT NOT NULL, state_key TEXT NOT NULL, value_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL, PRIMARY KEY (telegram_id, state_key),
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )""")

def validate_init_data(raw):
    if not BOT_TOKEN:
        return None, "На сервере не задан BOT_TOKEN."
    if not raw:
        return None, "Нет Telegram initData. Откройте приложение через бота в Telegram."
    try:
        pairs = dict(parse_qsl(raw, keep_blank_values=True, strict_parsing=True))
        received_hash = pairs.pop("hash")
        auth_date = int(pairs["auth_date"])
        if abs(int(time.time()) - auth_date) > INITDATA_MAX_AGE:
            return None, "Данные Telegram устарели. Закройте и снова откройте приложение."
        check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            return None, "Не удалось проверить подпись Telegram."
        user = json.loads(pairs.get("user", "{}"))
        if not user.get("id"):
            return None, "Telegram не передал ID пользователя."
        return user, None
    except Exception:
        return None, "Некорректные данные авторизации Telegram."

def require_user():
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user, error = validate_init_data(raw)
    if error:
        return None, (jsonify({"error": error}), 401)
    now = int(time.time())
    with db() as c:
        c.execute("""INSERT INTO users(telegram_id, username, first_name, last_name, created_at, last_seen)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(telegram_id) DO UPDATE SET username=excluded.username,
            first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen""",
            (str(user["id"]), user.get("username"), user.get("first_name"), user.get("last_name"), now, now))
    return user, None

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok": True, "service": "ETN RAPIRA ONLINE", "database": "sqlite"})

@app.get("/api/me")
def me():
    user, err = require_user()
    if err: return err
    return jsonify({"ok": True, "user": {
        "id": str(user["id"]), "username": user.get("username"),
        "first_name": user.get("first_name"), "last_name": user.get("last_name")
    }})

@app.get("/api/state/<state_key>")
def get_state(state_key):
    user, err = require_user()
    if err: return err
    with db() as c:
        row = c.execute("SELECT value_json FROM user_state WHERE telegram_id=? AND state_key=?",
                        (str(user["id"]), state_key)).fetchone()
    if not row: return jsonify({"found": False})
    try: value = json.loads(row["value_json"])
    except Exception: value = None
    return jsonify({"found": True, "value": value})

@app.put("/api/state/<state_key>")
def put_state(state_key):
    user, err = require_user()
    if err: return err
    allowed = {
        "etn_active_account_v1", "etn_approved_match_stats_v1", "etn_demo_accounts_v1",
        "etn_match_submissions_v1", "etn_organizer_applications_v1",
        "etn_player_profile_v1", "etn_screenshot_registrations_v1"
    }
    if state_key not in allowed:
        return jsonify({"error": "Ключ данных не разрешён."}), 400
    body = request.get_json(silent=True) or {}
    try:
        value_json = json.dumps(body.get("value"), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return jsonify({"error": "Некорректный формат данных."}), 400
    if len(value_json.encode("utf-8")) > 1_500_000:
        return jsonify({"error": "Слишком большой объём данных."}), 413
    with db() as c:
        c.execute("""INSERT INTO user_state(telegram_id,state_key,value_json,updated_at)
            VALUES(?,?,?,?) ON CONFLICT(telegram_id,state_key) DO UPDATE SET
            value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (str(user["id"]), state_key, value_json, int(time.time())))
    return jsonify({"ok": True})


@app.get("/api/leaderboard")
def leaderboard():
    """Global leaderboard assembled from all Telegram users' saved profile and approved match data."""
    user, err = require_user()
    if err:
        return err
    with db() as c:
        rows = c.execute("SELECT u.telegram_id, u.username, u.first_name, s.state_key, s.value_json "
                         "FROM users u LEFT JOIN user_state s ON s.telegram_id=u.telegram_id").fetchall()
    states = {}
    identities = {}
    for row in rows:
        uid = str(row["telegram_id"])
        identities.setdefault(uid, {"id": uid, "username": row["username"], "first_name": row["first_name"]})
        if row["state_key"]:
            try:
                states.setdefault(uid, {})[row["state_key"]] = json.loads(row["value_json"])
            except (TypeError, ValueError):
                pass
    players = {}
    def norm(value):
        return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())
    def ensure(nick, team="", game_id="", registered=False):
        key = norm(nick)
        if not key:
            return None
        if key not in players:
            players[key] = {"player": str(nick).strip(), "team": "", "gameId": "", "registered": False,
                            "kills": 0, "deaths": 0, "assists": 0, "mvp": 0, "matchIds": set()}
        item = players[key]
        if team and (not item["team"] or item["team"] == "Без команды"):
            item["team"] = str(team).strip()
        if game_id and not item["gameId"]:
            item["gameId"] = str(game_id).strip()
        item["registered"] = item["registered"] or registered
        return item
    all_submissions = []
    for uid, data in states.items():
        account = data.get("etn_active_account_v1")
        profile = data.get("etn_player_profile_v1")
        if isinstance(account, dict):
            nick = account.get("nickname") or account.get("nick") or account.get("first_name")
            ensure(nick, account.get("team") or "", account.get("gameId") or "", True)
        if isinstance(profile, dict):
            nick = profile.get("nick") or profile.get("nickname")
            ensure(nick, profile.get("team") or "", profile.get("gameId") or "", True)
        submissions = data.get("etn_match_submissions_v1")
        if isinstance(submissions, list):
            all_submissions.extend(submissions)
    for match in all_submissions:
        if not isinstance(match, dict) or match.get("status") not in ("Одобрен", "approved"):
            continue
        if not str(match.get("team1") or "").strip() or not str(match.get("team2") or "").strip():
            continue
        if not isinstance(match.get("players"), list) or not match["players"]:
            continue
        if not isinstance(match.get("screenshots"), list) or not match["screenshots"]:
            continue
        match_id = str(match.get("id") or "")
        for stat in match["players"]:
            if not isinstance(stat, dict):
                continue
            nick = stat.get("nick") or stat.get("submittedNick")
            team = stat.get("team") or ""
            item = ensure(nick, team)
            if not item:
                continue
            if match_id and match_id in item["matchIds"]:
                continue
            item["kills"] += max(0, float(stat.get("kills") or 0))
            item["deaths"] += max(0, float(stat.get("deaths") or 0))
            item["assists"] += max(0, float(stat.get("assists") or 0))
            item["mvp"] += max(0, float(stat.get("mvp") or 0))
            if match_id:
                item["matchIds"].add(match_id)
    result = []
    for item in players.values():
        rating = round(item["kills"] * 2 + item["assists"] - item["deaths"] * .5 + item["mvp"] * 3, 1)
        result.append({"team": item["team"] or "Без команды", "player": item["player"],
                       "gameId": item["gameId"], "registered": item["registered"],
                       "matches": len(item["matchIds"]), "rating": rating})
    result.sort(key=lambda x: (-x["rating"], -int(x["registered"]), x["player"].casefold()))
    for index, item in enumerate(result, 1):
        item["place"] = index
    return jsonify({"ok": True, "players": result})

init_db()

if __name__ == "__main__":
    host = os.environ.get("ETN_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"ETN server: http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
