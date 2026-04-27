import os
import json
from flask import Flask, request, jsonify

app = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")


def ensure_history():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump([], f)


def load_history():
    ensure_history()
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@app.route("/", methods=["GET"])
def index():
    return jsonify(load_history()), 200


@app.route("/api/stats", methods=["POST"])
def receive_stats():
    ensure_history()
    payload = request.get_json(force=True, silent=True)
    if not payload or not isinstance(payload, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    required = ("nome_pc", "data", "statistiche")
    if not all(k in payload for k in required):
        return jsonify({"error": "Missing required fields"}), 400

    stats = payload.get("statistiche")
    if not isinstance(stats, dict):
        return jsonify({"error": "Invalid statistiche"}), 400

    history = load_history()
    history.append(payload)

    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return jsonify({"status": "ok", "records": len(history)}), 200


@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(load_history()), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

