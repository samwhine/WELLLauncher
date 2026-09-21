"""WELL Launcher public demo for Vercel.

This app intentionally never starts local commands, reads local files, creates PID
files, or controls Cloudflare/firewall services. It only provides simulated data so
visitors can try the dashboard UI safely.
"""
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, jsonify, request, session

app = Flask(__name__)
app.secret_key = "well-launcher-public-demo-session-key-change-me"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
)

DEMO_PROJECTS = [
    {
        "id": "demo_http_server",
        "name": "Demo HTTP Server",
        "icon": "🌐",
        "cwd": "demo/",
        "cmd": "python -m http.server 8765",
        "port": 8765,
        "color": "#00d4ff",
        "domain": None,
        "has_toggle": False,
        "toggle_label": "",
        "toggle_options": [],
        "is_background": False,
        "enabled": True,
        "disabled": False,
    },
    {
        "id": "demo_log_stream",
        "name": "Demo Log Stream",
        "icon": "▣",
        "cwd": "demo/",
        "cmd": "simulated log stream",
        "port": None,
        "color": "#a78bfa",
        "domain": None,
        "has_toggle": False,
        "toggle_label": "",
        "toggle_options": [],
        "is_background": False,
        "enabled": True,
        "disabled": False,
    },
    {
        "id": "demo_background",
        "name": "Demo Background Service",
        "icon": "⚙",
        "cwd": "demo/",
        "cmd": "simulated background service",
        "port": 8766,
        "color": "#f472b6",
        "domain": None,
        "has_toggle": False,
        "toggle_label": "",
        "toggle_options": [],
        "is_background": True,
        "enabled": True,
        "disabled": False,
    },
]
PROJECT_IDS = {p["id"] for p in DEMO_PROJECTS}
PROFILES = [{"id": "profile_demo", "name": "DEMO", "projects": list(PROJECT_IDS)}]
LOGS = {
    "demo_http_server": [
        "[demo] HTTP server ready on port 8765",
        "[demo] Waiting for incoming requests...",
        "[demo] GET / 200 OK",
        "[demo] GET /favicon.ico 200 OK",
    ],
    "demo_log_stream": [
        "[demo] Log stream initialized",
        "[demo] Watching simulated worker output",
        "[demo] Worker heartbeat: healthy",
        "[demo] New log event received",
        "[demo] Terminal output is being simulated safely",
        "[demo] Auto-scroll can be tested here",
    ],
    "demo_background": [
        "[demo] Background service is online",
        "[demo] Infrastructure mode: simulated",
        "[demo] No local process is started in the public demo",
    ],
}


def auth_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapped


def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return fn(*args, **kwargs)
    return wrapped


def demo_states():
    saved = session.get("demo_states", {})
    return {pid: bool(saved.get(pid, True)) for pid in PROJECT_IDS}


def status_payload():
    states = demo_states()
    result = {}
    for index, project in enumerate(DEMO_PROJECTS, start=1):
        result[project["id"]] = {
            "running": states[project["id"]],
            "pid": 9000 + index if states[project["id"]] else None,
            "port": project["port"],
            "toggle": 0,
            "started_at": "DEMO MODE" if states[project["id"]] else None,
            "returncode": None,
        }
    result["_active_profile"] = "profile_demo" if any(states.values()) else None
    result["_bulk_operation"] = None
    return result


@app.route("/api/login", methods=["POST"])
@app.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    if data.get("username", "").strip() == "admin" and data.get("password") == "admin":
        session["logged_in"] = True
        session["username"] = "admin"
        session["role"] = "admin"
        session["last_login"] = {
            "time": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
            "ip": "Public demo",
            "device": "Browser",
            "browser": "Browser",
            "city": "",
            "country": "",
        }
        return jsonify({"ok": True, "role": "admin", "username": "admin"})
    return jsonify({"error": "Demo login: use admin / admin"}), 401


@app.route("/api/logout", methods=["POST"])
@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
@app.route("/me")
@auth_required
def me():
    return jsonify({"username": session.get("username", "admin"), "role": "admin"})


@app.route("/api/lastlogin")
@app.route("/lastlogin")
@auth_required
def last_login():
    return jsonify(session.get("last_login", {}))


@app.route("/api/config")
@app.route("/config")
@auth_required
def config():
    return jsonify({
        "settings": {
            "dashboard_port": 9000,
            "start_delay": 1500,
            "stop_delay": 1500,
            "port_scan_range_start": 1000,
            "port_scan_range_end": 10000,
            "session_lifetime_hours": 24,
            "stop_timeout": 5000,
            "status_refresh_interval": 3000,
            "log_refresh_interval": 2000,
            "max_log_lines": 300,
        },
        "projects": DEMO_PROJECTS,
    })


@app.route("/api/status")
@app.route("/status")
@auth_required
def status():
    return jsonify(status_payload())


@app.route("/api/logs/batch", methods=["POST"])
@app.route("/logs/batch", methods=["POST"])
@auth_required
def logs_batch():
    data = request.get_json(silent=True) or {}
    ids = data.get("ids", list(PROJECT_IDS))
    return jsonify({pid: LOGS.get(pid, []) for pid in ids if pid in PROJECT_IDS})


@app.route("/api/logs/<pid>")
@app.route("/logs/<pid>")
@auth_required
def logs(pid):
    return jsonify({"logs": LOGS.get(pid, []) if pid in PROJECT_IDS else []})


@app.route("/api/profiles")
@app.route("/profiles")
@auth_required
def profiles():
    return jsonify({"profiles": PROFILES, "active_profile": "profile_demo"})


@app.route("/api/users")
@app.route("/users")
@admin_required
def users():
    return jsonify([{
        "username": "admin",
        "role": "admin",
        "last_login": session.get("last_login"),
    }])


@app.route("/api/portscan", methods=["POST"])
@app.route("/portscan", methods=["POST"])
@admin_required
def portscan():
    return jsonify({
        "results": [
            {"port": 8765, "status": "open", "project": "Demo HTTP Server", "known": True},
            {"port": 8766, "status": "open", "project": "Demo Background Service", "known": True},
        ],
        "scanned": 2,
    })


def simulated_action(pid, running):
    if pid not in PROJECT_IDS:
        return jsonify({"error": "Demo project not found"}), 404
    states = demo_states()
    states[pid] = running
    session["demo_states"] = states
    return jsonify({"ok": True, "demo": True, "message": "Simulated action — no local process was started."})


@app.route("/api/start/<pid>", methods=["POST"])
@app.route("/start/<pid>", methods=["POST"])
@admin_required
def start(pid):
    return simulated_action(pid, True)


@app.route("/api/stop/<pid>", methods=["POST"])
@app.route("/stop/<pid>", methods=["POST"])
@admin_required
def stop(pid):
    return simulated_action(pid, False)


@app.route("/api/toggle/<pid>", methods=["POST"])
@app.route("/toggle/<pid>", methods=["POST"])
@admin_required
def toggle(pid):
    return jsonify({"ok": True, "demo": True}) if pid in PROJECT_IDS else (jsonify({"error": "Demo project not found"}), 404)


@app.route("/api/bulk/<action>", methods=["POST"])
@app.route("/bulk/<action>", methods=["POST"])
@admin_required
def bulk(action):
    if action == "lock" or action == "unlock":
        return jsonify({"ok": True, "demo": True})
    return jsonify({"ok": True, "demo": True, "message": "Bulk action simulated safely."})


@app.route("/api/config/settings", methods=["POST"])
@app.route("/config/settings", methods=["POST"])
@admin_required
def settings():
    return jsonify({"ok": True, "demo": True})


@app.route("/api/<path:unused>", methods=["POST", "PUT", "DELETE"])
@app.route("/<path:unused>", methods=["POST", "PUT", "DELETE"])
@admin_required
def demo_mutation(unused):
    return jsonify({"ok": True, "demo": True, "message": "This public demo does not persist changes."})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)
