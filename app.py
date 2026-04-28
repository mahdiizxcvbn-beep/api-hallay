import json
import os
import sqlite3
import time
from base64 import b64decode, b64encode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from flask import Flask, request, jsonify

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
HISTORY_DB_PATH = os.path.join(DATA_DIR, "history.db")
API_KEY = os.environ.get("API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_HISTORY_PATH = os.environ.get("GITHUB_HISTORY_PATH", "server/history.json")


def is_github_sync_enabled():
    return bool(GITHUB_TOKEN and GITHUB_REPO and GITHUB_HISTORY_PATH)


def github_api_request(method, url, payload=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "User-Agent": "ScannerSemplice-Server",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=30) as response:
        body = response.read().decode("utf-8")
        return json.loads(body) if body else {}


def load_github_history():
    url = (
        f"https://api.github.com/repos/{GITHUB_REPO}/contents/"
        f"{GITHUB_HISTORY_PATH}?ref={GITHUB_BRANCH}"
    )
    try:
        response = github_api_request("GET", url)
    except HTTPError as exc:
        if exc.code == 404:
            return [], None
        raise

    content = response.get("content", "")
    sha = response.get("sha")
    if not content:
        return [], sha

    decoded = b64decode(content).decode("utf-8")
    try:
        history = json.loads(decoded)
    except json.JSONDecodeError:
        history = []
    if not isinstance(history, list):
        history = []
    return history, sha


def sync_history_to_github(payload, retries=2):
    if not is_github_sync_enabled():
        return False, "github_sync_disabled"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_HISTORY_PATH}"
    last_error = None

    for attempt in range(retries + 1):
        try:
            history, sha = load_github_history()
            history.append(payload)
            encoded_content = b64encode(
                json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("utf-8")

            body = {
                "message": f"Update history.json: {payload.get('nome_pc', 'unknown')}",
                "content": encoded_content,
                "branch": GITHUB_BRANCH,
            }
            if sha:
                body["sha"] = sha

            github_api_request("PUT", url, body)
            return True, None
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt < retries:
                time.sleep(1)

    return False, last_error


def get_db_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_history_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome_pc TEXT NOT NULL,
                data TEXT NOT NULL,
                file_scansionati INTEGER NOT NULL DEFAULT 0,
                errori INTEGER NOT NULL DEFAULT 0,
                tempo_elaborazione_sec REAL NOT NULL DEFAULT 0,
                raw_payload TEXT NOT NULL
            )
            """
        )
        conn.commit()

    migrate_history_json_if_needed()


def migrate_history_json_if_needed():
    if not os.path.exists(HISTORY_PATH):
        return

    with get_db_connection() as conn:
        existing = conn.execute("SELECT COUNT(*) AS total FROM history").fetchone()["total"]
        if existing:
            return

        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                rows = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(rows, list):
            return

        for payload in rows:
            insert_history_record(conn, payload)
        conn.commit()


def insert_history_record(conn, payload):
    stats = payload.get("statistiche") or {}
    conn.execute(
        """
        INSERT INTO history (
            nome_pc,
            data,
            file_scansionati,
            errori,
            tempo_elaborazione_sec,
            raw_payload
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(payload.get("nome_pc", "")),
            str(payload.get("data", "")),
            int(stats.get("file_scansionati", 0) or 0),
            int(stats.get("errori", 0) or 0),
            float(stats.get("tempo_elaborazione_sec", 0) or 0),
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def append_local_history_json(payload):
    os.makedirs(DATA_DIR, exist_ok=True)
    history = []
    if os.path.exists(HISTORY_PATH):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (OSError, json.JSONDecodeError):
            history = []

    if not isinstance(history, list):
        history = []

    history.append(payload)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_history():
    ensure_history_db()
    with get_db_connection() as conn:
        rows = conn.execute(
            """
            SELECT raw_payload
            FROM history
            ORDER BY id DESC
            """
        ).fetchall()
    return [json.loads(row["raw_payload"]) for row in rows]


def check_api_key():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key", "")
    return key == API_KEY


@app.route("/", methods=["GET"])
def index():
    if not check_api_key():
        return jsonify({"error": "Unauthorized - invalid or missing X-API-Key header"}), 401
    return jsonify(load_history()), 200


@app.route("/api/stats", methods=["POST"])
def receive_stats():
    if not check_api_key():
        return jsonify({"error": "Unauthorized - invalid or missing X-API-Key header"}), 401

    ensure_history_db()
    payload = request.get_json(force=True, silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    required = ("nome_pc", "data", "statistiche")
    if not all(k in payload for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    stats = payload.get("statistiche")
    if not isinstance(stats, dict):
        return jsonify({"error": "Invalid statistiche"}), 400

    with get_db_connection() as conn:
        insert_history_record(conn, payload)
        conn.commit()
        total_records = conn.execute("SELECT COUNT(*) AS total FROM history").fetchone()["total"]

    append_local_history_json(payload)
    github_synced, github_error = sync_history_to_github(payload)

    return jsonify(
        {
            "status": "ok",
            "records": total_records,
            "storage": "sqlite",
            "history_json_path": HISTORY_PATH,
            "github_synced": github_synced,
            "github_error": github_error,
        }
    ), 200


@app.route("/api/history", methods=["GET"])
def get_history():
    if not check_api_key():
        return jsonify({"error": "Unauthorized - invalid or missing X-API-Key header"}), 401
    return jsonify(load_history()), 200


if __name__ == "__main__":
    ensure_history_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

