"""
WELL Launcher v2
Run: python server.py
Open: http://localhost:9000
"""

from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import subprocess, threading, os, socket, json, time, signal, functools, secrets, hashlib, hmac, logging
from collections import deque
from pathlib import Path
from datetime import timedelta, datetime
import requests as req_lib

# Silence Werkzeug's default per-request access log (e.g. "GET /api/status 200 -").
# NOTE: Werkzeug logs this line at INFO level for every response, including 4xx/5xx —
# so we can't just raise the log level, that would hide real errors too. Instead we
# silence Werkzeug entirely and replace it with our own after_request hook below,
# which always prints on errors (any endpoint) and only stays quiet for routine,
# successful polling calls (/api/status, /api/logs/batch, /api/logs/<pid>).
logging.getLogger("werkzeug").setLevel(logging.ERROR)

def clog(msg):
    """Console log: a clear, timestamped line for meaningful launcher events
    (start/stop/settings changes), as opposed to routine request polling."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# Endpoints polled every second by the dashboard UI — noisy and low-signal when they
# succeed, so we skip the console line for these ONLY on success (2xx). Any status
# code >= 400 from ANY endpoint (including these) is always printed below.
_QUIET_ON_SUCCESS_PATHS = ("/api/status", "/api/logs/batch")
def _is_quiet_poll(path):
    return path in _QUIET_ON_SUCCESS_PATHS or path.startswith("/api/logs/")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

@app.after_request
def _log_requests(response):
    # Always print on error, from ANY endpoint — this is what keeps the console safe
    # to rely on: a request that failed will show up here even though routine,
    # successful polling requests are kept quiet below.
    if response.status_code >= 400:
        clog(f"✗ {request.method} {request.path} → {response.status_code} (from {request.remote_addr})")
    elif not _is_quiet_poll(request.path):
        clog(f"  {request.method} {request.path} → {response.status_code} (from {request.remote_addr})")
    return response

# Fresh secret key every restart — all sessions are invalidated on server restart
app.secret_key = secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(hours=24)  # updated after config loads
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "launcher_config.json"
USERS_FILE = BASE_DIR / "users.json"
ENV_FILE = BASE_DIR / ".env"
PIDS_DIR = BASE_DIR / "pids"
PIDS_DIR.mkdir(exist_ok=True)
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# ── USERS ─────────────────────────────────────────────────────────
PBKDF2_ITERATIONS = 260000

def hash_password(password):
    """Salted PBKDF2-HMAC-SHA256 hash. Format: pbkdf2$<iterations>$<salt_hex>$<hash_hex>"""
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${derived.hex()}"

def verify_password(password, stored_hash):
    """Verify a password against either a new pbkdf2$... hash or a legacy
    unsalted SHA-256 hex hash (64 hex chars, no '$'). Returns True/False."""
    if not stored_hash:
        return False
    if stored_hash.startswith("pbkdf2$"):
        try:
            _, iterations, salt, expected_hex = stored_hash.split("$", 3)
            derived = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
            return hmac.compare_digest(derived.hex(), expected_hex)
        except Exception:
            return False
    # Legacy unsalted SHA-256 hash — kept only for backward compatibility with
    # existing users.json files created before the pbkdf2 migration.
    legacy_hash = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(legacy_hash, stored_hash)

def is_legacy_hash(stored_hash):
    return bool(stored_hash) and not stored_hash.startswith("pbkdf2$")

def load_users():
    """Load users from users.json, migrate from .env if needed."""
    if USERS_FILE.exists():
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    # Migrate from .env or create default admin
    users = {}
    env_user, env_pass = "admin", "admin"
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == "LAUNCHER_USER": env_user = v.strip()
                if k.strip() == "LAUNCHER_PASS": env_pass = v.strip()
    users[env_user] = {
        "password": hash_password(env_pass),
        "role": "admin",
        "last_login": None
    }
    save_users(users)
    return users

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

users_db = load_users()

# ── SESSION BLACKLIST ─────────────────────────────────────────────
# Stores logged-out session _ids — prevents reuse of old cookies
invalidated_sessions = set()

# ── SESSION AUTH ──────────────────────────────────────────────────
def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        # Check blacklist — reject if this session has already logged out
        sid = session.get("_id")
        if sid and sid in invalidated_sessions:
            session.clear()
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        # Check blacklist for admin routes too
        sid = session.get("_id")
        if sid and sid in invalidated_sessions:
            session.clear()
            return jsonify({"error": "Unauthorized"}), 401
        if session.get("role") != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

def current_role():
    return session.get("role", "viewer")

def is_admin():
    return current_role() == "admin"

def collect_login_info(request):
    """Collect IP, device, browser, location info from request."""
    ip = request.headers.get("CF-Connecting-IP") or request.remote_addr
    ua = request.headers.get("User-Agent", "")
    if "Android" in ua: device = "Android"
    elif "iPhone" in ua or "iPad" in ua: device = "iOS"
    elif "Windows" in ua: device = "Windows"
    elif "Mac" in ua: device = "Mac"
    else: device = "Unknown"
    if "Chrome" in ua and "Edg" not in ua: browser = "Chrome"
    elif "Edg" in ua: browser = "Edge"
    elif "Firefox" in ua: browser = "Firefox"
    elif "Safari" in ua: browser = "Safari"
    else: browser = "Browser"
    city, country = "", ""
    try:
        geo = req_lib.get(f"http://ip-api.com/json/{ip}", timeout=3).json()
        if geo.get("status") == "success":
            city = geo.get("city", "")
            country = geo.get("country", "")
    except: pass
    return {
        "time": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "ip": ip,
        "device": device,
        "browser": browser,
        "city": city,
        "country": country,
    }

# ── LOGIN / LOGOUT API ────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def api_login():
    global users_db
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    user = users_db.get(username)
    if user and verify_password(password, user.get("password", "")):
        # Auto-upgrade legacy unsalted SHA-256 hashes to pbkdf2 on next successful login
        if is_legacy_hash(user.get("password", "")):
            users_db[username]["password"] = hash_password(password)
        session["logged_in"] = True
        session["username"] = username
        session["role"] = user.get("role", "viewer")
        session["_id"] = secrets.token_hex(16)  # unique ID per session, for the blacklist
        session.permanent = True
        login_info = collect_login_info(request)
        session["last_login"] = login_info
        users_db[username]["last_login"] = login_info
        save_users(users_db)
        clog(f"✓ LOGIN    {username} ({session['role']}) from {login_info.get('ip', '?')}")
        return jsonify({"ok": True, "role": session["role"], "username": username})
    clog(f"✗ LOGIN FAILED  username={username!r} from {request.remote_addr}")
    return jsonify({"error": "Incorrect username or password."}), 401

@app.route("/api/logout", methods=["POST"])
def api_logout():
    # Add session _id to blacklist so old cookies can no longer be used
    sid = session.get("_id")
    if sid:
        invalidated_sessions.add(sid)
    session.clear()
    resp = jsonify({"ok": True})
    # Clear cookie with every possible attribute combination (Cloudflare tunnel compat)
    for kwargs in [
        {"path": "/", "samesite": "Lax"},
        {"path": "/", "samesite": "Lax", "secure": True},
        {"path": "/"},
    ]:
        resp.delete_cookie("session", **kwargs)
        resp.set_cookie("session", "", expires=0, max_age=0, **kwargs)
    return resp

# ── DEFAULT CONFIG ────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "settings": {
        "dashboard_port": 9000,
        "start_delay": 1500,
        "stop_delay": 5000,
        "stop_timeout": 5000,
        "session_lifetime_hours": 24,
        "port_scan_range_start": 1000,
        "port_scan_range_end": 10000,
        "status_refresh_interval": 1000,
        "log_refresh_interval": 1000,
        "max_log_lines": 300,
    },
    "profiles": [
        {"id": "profile_1", "name": "Profile 1", "projects": []},
        {"id": "profile_2", "name": "Profile 2", "projects": []},
    ],
    "projects": []  # empty — user adds projects via dashboard
    # Note: set "is_background": true for background services
}

# ── CONFIG LOAD/SAVE ──────────────────────────────────────────────
def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    # First run — save DEFAULT_CONFIG so launcher_config.json is created
    default = json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(default, f, indent=2, ensure_ascii=False)
    return default

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def migrate_config(cfg):
    """Migrate old config — set is_background for cloudflared, ensure profiles, backfill new settings."""
    changed = False
    for p in cfg.get("projects", []):
        if p.get("id") == "cloudflared" and "is_background" not in p:
            p["is_background"] = True
            changed = True
        if "is_background" not in p:
            p["is_background"] = False
    # Ensure profiles section exists
    if "profiles" not in cfg:
        cfg["profiles"] = [
            {"id": "profile_1", "name": "Profile 1", "projects": []},
            {"id": "profile_2", "name": "Profile 2", "projects": []},
        ]
        changed = True
    # Backfill new settings keys
    new_defaults = {
        "stop_timeout": 5000,
        "status_refresh_interval": 1000,
        "log_refresh_interval": 1000,
        "max_log_lines": 300,    }
    s = cfg.setdefault("settings", {})
    for k, v in new_defaults.items():
        if k not in s:
            s[k] = v
            changed = True
    if changed:
        save_config(cfg)
    return cfg

config = migrate_config(load_config())
# Apply session lifetime from config (overrides the default 24h above)
app.permanent_session_lifetime = timedelta(hours=config["settings"].get("session_lifetime_hours", 24))

# ── RUNTIME STATE ─────────────────────────────────────────────────
processes = {}
logs = {}
toggle_state = {}  # project_id -> index (int, 0-based)

active_profile_id = None  # null until a project is actually started

# ── PID FILE HELPERS ──────────────────────────────────────────────
def pid_file(project_id):
    return PIDS_DIR / f"{project_id}.pid"

def write_pid(project_id, pid):
    try: pid_file(project_id).write_text(str(pid), encoding="utf-8")
    except: pass

def read_pid(project_id):
    try:
        pf = pid_file(project_id)
        if pf.exists(): return int(pf.read_text(encoding="utf-8").strip())
    except: pass
    return None

def delete_pid(project_id):
    try: pid_file(project_id).unlink(missing_ok=True)
    except: pass

def is_pid_alive(pid):
    if not pid or pid <= 0: return False
    try:
        result = subprocess.check_output(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            stderr=subprocess.DEVNULL
        ).decode(errors='replace').strip()
        # tasklist returns empty string or "INFO: No tasks..." when PID not found
        # A real result always starts with a quoted exe name e.g. "python.exe","1234",...
        return bool(result) and result.startswith('"') and 'INFO:' not in result
    except: return False

def find_alive_pid_for_project(saved_pid, port):
    """
    When shell=True, Popen saves cmd.exe PID. If CMD was closed, that PID dies
    but the actual child process (python/node/etc) may still be alive on its port.
    Priority: 1) saved_pid if alive, 2) PID listening on port.
    """
    if saved_pid and is_pid_alive(saved_pid):
        return saved_pid
    if port:
        port_pids = get_pids_on_port(port)
        if port_pids:
            child_pid, _ = port_pids[0]
            if child_pid and is_pid_alive(child_pid):
                return child_pid
    return None

def reattach_pids():
    global active_profile_id
    for p in config.get("projects", []):
        proj_id = p["id"]
        saved_pid = read_pid(proj_id)
        port = p.get("port")
        alive_pid = find_alive_pid_for_project(saved_pid, port)
        if alive_pid:
            class ReattachedProc:
                def __init__(self, pid):
                    self.pid = pid
                    self._last_check = 0
                    self._last_result = True
                def poll(self):
                    now = time.time()
                    if now - self._last_check > 2:  # re-check at most every 2s
                        self._last_result = is_pid_alive(self.pid)
                        self._last_check = now
                    return None if self._last_result else 0
            processes[proj_id] = ReattachedProc(alive_pid)
            write_pid(proj_id, alive_pid)  # update to real PID if changed
            get_logs(proj_id).append(f"[{time.strftime('%H:%M:%S')}] ♻ Re-attached to PID {alive_pid}")
        else:
            # Process is truly dead — clean up so START works normally
            delete_pid(proj_id)
            processes.pop(proj_id, None)

    # Auto-detect active_profile_id from which profile has the most running projects
    if processes:
        running_ids = {pid for pid, proc in processes.items() if proc.poll() is None}
        if running_ids:
            best_profile = None
            best_count = 0
            for prf in config.get("profiles", []):
                count = sum(1 for pid in prf.get("projects", []) if pid in running_ids)
                if count > best_count:
                    best_count = count
                    best_profile = prf["id"]
            if best_profile:
                active_profile_id = best_profile

def get_logs(pid):
    max_lines = config["settings"].get("max_log_lines", 300)
    if pid not in logs:
        logs[pid] = deque(maxlen=max_lines)
    elif logs[pid].maxlen != max_lines:
        # Re-create deque with new maxlen if setting changed
        logs[pid] = deque(logs[pid], maxlen=max_lines)
    return logs[pid]

# Run re-attach on startup (called after all helpers are ready)
def _do_reattach():
    reattach_pids()

# Re-attach processes still running when launcher restarts
def _startup_reattach():
    reattach_pids()

def read_output(pid, proc):
    try:
        for line in iter(proc.stdout.readline, b''):
            decoded = line.decode('utf-8', errors='replace').rstrip()
            if decoded:
                ts = time.strftime("%H:%M:%S")
                get_logs(pid).append(f"[{ts}] {decoded}")
    except:
        pass

def is_port_open(port):
    if not port:
        return None
    try:
        with socket.create_connection(("localhost", int(port)), timeout=0.2):
            return True
    except:
        return False

def get_project_by_id(pid):
    for p in config["projects"]:
        if p["id"] == pid:
            return p
    return None

# ── STATUS CACHE ─────────────────────────────────────────────────
_status_cache = {}
_status_lock = threading.Lock()
_status_last_update = 0

def _check_one_project(p):
    """Check a single project status — run in thread pool."""
    pid = p["id"]
    proc = processes.get(pid)
    running = proc is not None and proc.poll() is None
    has_port = bool(p.get("port"))
    if not has_port:
        port_open = running
    else:
        port_open = is_port_open(p.get("port")) if running else False
    return pid, {
        "running": running,
        "port_open": port_open,
        "port": p.get("port"),
        "toggle": toggle_state.get(pid, 0),
    }

def _refresh_status_cache():
    """Refresh all project statuses in parallel."""
    global _status_cache, _status_last_update
    projects = config["projects"]
    result = {}
    threads = []
    results_list = [None] * len(projects)

    def worker(i, p):
        results_list[i] = _check_one_project(p)

    for i, p in enumerate(projects):
        t = threading.Thread(target=worker, args=(i, p), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=1.5)  # max 1.5s total regardless of how many projects

    for item in results_list:
        if item:
            k, v = item
            result[k] = v

    with _status_lock:
        _status_cache = result
        _status_last_update = time.time()

def _background_status_loop():
    """Background thread: refresh status every 1s so API calls return instantly."""
    consecutive_errors = 0
    while True:
        try:
            _refresh_status_cache()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            # Back off if errors keep happening, max 5s
            backoff = min(consecutive_errors * 0.5, 5.0)
            time.sleep(backoff)
            continue
        time.sleep(config["settings"].get("status_refresh_interval", 1000) / 1000)

# Start background status refresh
threading.Thread(target=_background_status_loop, daemon=True).start()

# Tracks the most recent MANUAL (single-project, non-bulk) start/stop action per
# project: {pid: {"action": "start"|"stop", "ts": float}}. Bulk operations (Start All,
# Stop All, Stop Except Background) read this via /api/status to detect "the user
# manually overrode this project mid-sequence" — from ANY tab or device, not just the
# one that kicked off the bulk run — so they can stop waiting on it immediately instead
# of blocking for the full timeout and reporting a misleading "failed" toast.
manual_overrides = {}

# Tracks whether a bulk operation (Start All / Stop All / Stop Except Background) is
# currently in progress, from ANY tab/device — so every connected dashboard can lock
# its own bulk-action buttons in real time instead of only the tab that clicked it.
# ts is used to auto-expire a stale lock (e.g. if a tab crashed mid-sequence) rather
# than requiring a guaranteed unlock call.
bulk_operation = {"active": False, "label": None, "ts": 0}
BULK_LOCK_MAX_AGE = 90  # seconds — generous upper bound for any realistic bulk run

def _bulk_operation_public():
    if bulk_operation["active"] and (time.time() - bulk_operation["ts"]) > BULK_LOCK_MAX_AGE:
        bulk_operation["active"] = False  # self-heal a lock left behind by a crashed/closed tab
    return bulk_operation

@app.route("/api/bulk/lock", methods=["POST"])
@require_admin
def api_bulk_lock():
    data = request.json or {}
    bulk_operation["active"] = True
    bulk_operation["label"] = data.get("label", "Running a bulk operation")
    bulk_operation["ts"] = time.time()
    return jsonify({"ok": True})

@app.route("/api/bulk/unlock", methods=["POST"])
@require_admin
def api_bulk_unlock():
    bulk_operation["active"] = False
    return jsonify({"ok": True})

# ── API: STATUS ───────────────────────────────────────────────────
@app.route("/api/status")
@require_auth
def api_status():
    with _status_lock:
        cached = dict(_status_cache)
    if not cached:
        _refresh_status_cache()
        with _status_lock:
            cached = dict(_status_cache)
    cached["_active_profile"] = active_profile_id
    cached["_manual_overrides"] = manual_overrides
    cached["_bulk_operation"] = _bulk_operation_public()
    return jsonify(cached)

# ── API: START / STOP ─────────────────────────────────────────────
@app.route("/api/start/<pid>", methods=["POST"])
@require_admin
def api_start(pid):
    global active_profile_id
    p = get_project_by_id(pid)
    if not p:
        return jsonify({"error": "Project not found"}), 404
    if p.get("disabled"):
        return jsonify({"error": "Project is disabled"}), 400
    if pid in processes and processes[pid].poll() is None:
        return jsonify({"error": "Already running"}), 400

    data = request.json or {}
    idx = int(data.get("toggle_idx", toggle_state.get(pid, 0)))
    if not data.get("bulk"):
        manual_overrides[pid] = {"action": "start", "ts": time.time()}

    # Set active_profile_id if a profile_id was provided in the request.
    # Guard: don't let a stray/late request hijack ownership away from a DIFFERENT
    # profile that still has projects actively running. This used to overwrite
    # unconditionally, which is how a frontend race (switching profiles mid Start-All)
    # could silently flip server-side ownership to the wrong profile.
    from_profile = data.get("profile_id")
    if from_profile:
        profile_exists = any(prf["id"] == from_profile for prf in config.get("profiles", []))
        if profile_exists:
            if active_profile_id is None or active_profile_id == from_profile:
                active_profile_id = from_profile
            else:
                current_prf = next((prf for prf in config.get("profiles", []) if prf["id"] == active_profile_id), None)
                current_still_running = False
                if current_prf:
                    current_still_running = any(
                        pid2 in processes and processes[pid2].poll() is None
                        for pid2 in current_prf.get("projects", [])
                    )
                if not current_still_running:
                    active_profile_id = from_profile
                # else: keep the existing active_profile_id; the project itself will
                # still be allowed to start below, it just won't change ownership.
    toggle_state[pid] = idx

    if p.get("has_toggle"):
        options = p.get("toggle_options", [])
        if options and 0 <= idx < len(options):
            raw_cmd = options[idx].get("cmd", "")
        else:
            raw_cmd = options[0].get("cmd", "") if options else ""
    else:
        raw_cmd = p.get("cmd", "")

    if not raw_cmd.strip():
        return jsonify({"error": "Command is empty"}), 400

    cwd = p.get("cwd", ".")
    try:
        win_flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
        # Inherit env and force unbuffered output so all logs (print, logging, stderr) are captured
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"
        child_env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            raw_cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=child_env,
            creationflags=win_flags,
        )
        processes[pid] = proc
        write_pid(pid, proc.pid)
        get_logs(pid).clear()
        get_logs(pid).append(f"[{time.strftime('%H:%M:%S')}] ▶ Starting: {raw_cmd}")
        clog(f"▶ START   {p.get('name', pid)} (id={pid}, port={p.get('port') or '-'})")
        threading.Thread(target=read_output, args=(pid, proc), daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        get_logs(pid).append(f"[{time.strftime('%H:%M:%S')}] ✗ Failed to start: {e}")
        clog(f"✗ START FAILED  {p.get('name', pid)} (id={pid}) — {e}")
        return jsonify({"error": str(e)}), 500

def get_cmd_name(pid):
    """Return process exe name for a PID (Windows)."""
    try:
        result = subprocess.check_output(
            ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
            stderr=subprocess.DEVNULL
        ).decode(errors='replace').strip()
        if result and 'INFO:' not in result:
            return result.split(',')[0].strip('"').lower()
    except Exception:
        pass
    return ''

def get_parent_pid(pid):
    """Return parent PID of a given PID using WMIC (Windows)."""
    try:
        result = subprocess.check_output(
            ['wmic', 'process', 'where', f'ProcessId={pid}', 'get', 'ParentProcessId', '/VALUE'],
            stderr=subprocess.DEVNULL
        ).decode(errors='replace')
        for line in result.splitlines():
            line = line.strip()
            if line.startswith('ParentProcessId='):
                return int(line.split('=')[1].strip())
    except Exception:
        pass
    return None

# Executable names that must NEVER be killed for safety
SAFE_EXES = {
    'chrome.exe', 'firefox.exe', 'msedge.exe', 'opera.exe', 'brave.exe',
    'explorer.exe', 'taskmgr.exe', 'svchost.exe', 'system', 'lsass.exe',
    'winlogon.exe', 'csrss.exe', 'smss.exe', 'wininit.exe', 'services.exe',
    'python.exe',  # never kill python.exe blindly — only kill by port/tree
}

def kill_pid_safe(pid):
    """Kill a PID and its tree if it's not a protected process."""
    if pid <= 0:
        return
    name = get_cmd_name(pid)
    if name in SAFE_EXES:
        return
    subprocess.call(
        ['taskkill', '/F', '/T', '/PID', str(pid)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def get_pids_on_port(port):
    """Return list of (child_pid, parent_pid) for processes listening on port."""
    result_pids = []
    if not port or os.name != 'nt':
        return result_pids
    try:
        result = subprocess.check_output(
            ['netstat', '-ano'],
            stderr=subprocess.DEVNULL
        ).decode(errors='replace')
        for line in result.splitlines():
            parts = line.strip().split()
            if len(parts) >= 5 and parts[3] == 'LISTENING':
                addr = parts[1]
                if addr.endswith(f':{port}'):
                    try:
                        child_pid = int(parts[4])
                        if child_pid > 0:
                            parent_pid = get_parent_pid(child_pid)
                            result_pids.append((child_pid, parent_pid))
                    except ValueError:
                        pass
    except Exception:
        pass
    return result_pids

def kill_by_port(port):
    """Kill process listening on port — kills both child AND parent (cmd.exe wrapper)."""
    for child_pid, parent_pid in get_pids_on_port(port):
        # Kill child first (actual program)
        subprocess.call(
            ['taskkill', '/F', '/T', '/PID', str(child_pid)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # Kill parent (cmd.exe shell spawned by shell=True) — safe because
        # we only kill the direct parent, not the whole chain
        if parent_pid:
            parent_name = get_cmd_name(parent_pid)
            if parent_name in ('cmd.exe', 'conhost.exe', ''):
                subprocess.call(
                    ['taskkill', '/F', '/T', '/PID', str(parent_pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

def kill_proc_tree(proc):
    """Kill a tracked subprocess and its entire process tree."""
    if proc is None:
        return
    try:
        if os.name == 'nt':
            subprocess.call(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            import signal as _signal
            os.killpg(os.getpgid(proc.pid), _signal.SIGTERM)
        try: proc.wait(timeout=config["settings"].get("stop_timeout", 5000) / 1000)
        except: proc.kill()
    except Exception:
        pass

@app.route("/api/stop/<pid>", methods=["POST"])
@require_admin
def api_stop(pid):
    global active_profile_id
    p = get_project_by_id(pid)
    proc = processes.get(pid)

    if not p:
        return jsonify({"error": "Project not found"}), 404

    data = request.json or {}
    if not data.get("bulk"):
        manual_overrides[pid] = {"action": "stop", "ts": time.time()}

    # 1) Kill by port — works even if launcher was restarted (no PID memory)
    if p.get("port"):
        kill_by_port(p["port"])

    # 2) Kill tracked process tree — for no-port processes (e.g. cloudflared)
    if proc:
        kill_proc_tree(proc)

    processes.pop(pid, None)
    delete_pid(pid)
    get_logs(pid).append(f"[{time.strftime('%H:%M:%S')}] ■ Process stopped.")
    clog(f"■ STOP    {p.get('name', pid)} (id={pid})")

    # Auto-reset active_profile_id if all projects in the active profile are now offline
    if active_profile_id:
        active_prf = next((prf for prf in config.get("profiles", []) if prf["id"] == active_profile_id), None)
        if active_prf:
            profile_proj_ids = active_prf.get("projects", [])
            any_still_running = any(
                pid2 in processes and processes[pid2].poll() is None
                for pid2 in profile_proj_ids
            )
            if not any_still_running:
                active_profile_id = None

    return jsonify({"ok": True})

# ── API: TOGGLE STATE ────────────────────────────────────────────
@app.route("/api/toggle/<pid>", methods=["POST"])
@require_admin
def api_toggle(pid):
    data = request.json or {}
    idx = int(data.get("idx", 0))
    toggle_state[pid] = idx
    return jsonify({"ok": True})

# ── API: LOGS ─────────────────────────────────────────────────────
@app.route("/api/logs/batch", methods=["POST"])
@require_auth
def api_logs_batch():
    data = request.json or {}
    ids = data.get("ids", [])
    result = {}
    for pid in ids:
        result[pid] = list(get_logs(pid))
    return jsonify(result)

@app.route("/api/logs/<pid>")
@require_auth
def api_logs(pid):
    return jsonify({"logs": list(get_logs(pid))})

# ── API: CONFIG / PROJECTS ────────────────────────────────────────
@app.route("/api/config", methods=["GET"])
@require_auth
def api_config_get():
    return jsonify(config)

@app.route("/api/config/settings", methods=["POST"])
@require_admin
def api_settings_save():
    data = request.json or {}
    changed = {k: (config["settings"].get(k), v) for k, v in data.items() if config["settings"].get(k) != v}
    config["settings"].update(data)
    save_config(config)
    if changed:
        diff = ", ".join(f"{k}: {old} → {new}" for k, (old, new) in changed.items())
        clog(f"⚙ SETTINGS saved — {diff}")
    # Apply session lifetime immediately — no restart needed
    if "session_lifetime_hours" in data:
        app.permanent_session_lifetime = timedelta(hours=int(data["session_lifetime_hours"]))
    return jsonify({"ok": True})

@app.route("/api/projects", methods=["GET"])
@require_auth
def api_projects_get():
    return jsonify(config["projects"])

@app.route("/api/projects", methods=["POST"])
@require_admin
def api_project_add():
    data = request.json or {}
    import uuid
    new_id = data.get("id") or "proj_" + uuid.uuid4().hex[:8]
    data["id"] = new_id
    # ensure all fields
    for f, v in [("name","New Project"),("icon","⚙"),("port",None),("color","#ffffff"),
                  ("cwd",""),("cmd",""),("has_toggle",False),("toggle_label",""),
                  ("toggle_options",[]),("enabled",True),("is_background",False)]:
        data.setdefault(f, v)
    # Insert before background projects so BG always stays at the end
    insert_idx = next(
        (i for i, p in enumerate(config["projects"]) if p.get("is_background")),
        len(config["projects"])
    )
    config["projects"].insert(insert_idx, data)
    save_config(config)
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/projects/<pid>", methods=["PUT"])
@require_admin
def api_project_update(pid):
    data = request.json or {}
    for i, p in enumerate(config["projects"]):
        if p["id"] == pid:
            config["projects"][i].update(data)
            config["projects"][i]["id"] = pid
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.route("/api/projects/<pid>", methods=["DELETE"])
@require_admin
def api_project_delete(pid):
    proc = processes.get(pid)
    if proc and proc.poll() is None:
        return jsonify({"error": "Stop the project before deleting"}), 400
    config["projects"] = [p for p in config["projects"] if p["id"] != pid]
    # Auto-cleanup: remove project from all profiles
    for prf in config.get("profiles", []):
        prf["projects"] = [p for p in prf.get("projects", []) if p != pid]
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/projects/<pid>/disable", methods=["POST"])
@require_admin
def api_project_disable(pid):
    for p in config["projects"]:
        if p["id"] == pid:
            p["disabled"] = True
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.route("/api/projects/<pid>/enable", methods=["POST"])
@require_admin
def api_project_enable(pid):
    for p in config["projects"]:
        if p["id"] == pid:
            p["disabled"] = False
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404

@app.route("/api/projects/reorder", methods=["POST"])
@require_admin
def api_project_reorder():
    data = request.json or {}
    order = data.get("order", [])
    id_map = {p["id"]: p for p in config["projects"]}
    config["projects"] = [id_map[i] for i in order if i in id_map]
    save_config(config)
    return jsonify({"ok": True})

# ── API: PROFILES ─────────────────────────────────────────────────
@app.route("/api/profiles", methods=["GET"])
@require_auth
def api_profiles_get():
    return jsonify({
        "profiles": config.get("profiles", []),
        "active_profile": active_profile_id,
    })

@app.route("/api/profiles", methods=["POST"])
@require_admin
def api_profiles_add():
    import uuid
    data = request.json or {}
    profiles = config.setdefault("profiles", [])
    new_id = "profile_" + uuid.uuid4().hex[:8]
    name = data.get("name", "").strip() or f"Profile {len(profiles)+1}"
    profiles.append({"id": new_id, "name": name, "projects": []})
    save_config(config)
    return jsonify({"ok": True, "id": new_id})

@app.route("/api/profiles/<profile_id>", methods=["PUT"])
@require_admin
def api_profiles_update(profile_id):
    global active_profile_id
    data = request.json or {}
    for prf in config.get("profiles", []):
        if prf["id"] == profile_id:
            if "name" in data:
                prf["name"] = data["name"].strip() or prf["name"]
            if "projects" in data:
                prf["projects"] = data["projects"]
            save_config(config)
            return jsonify({"ok": True})
    return jsonify({"error": "Profile not found"}), 404

@app.route("/api/profiles/<profile_id>", methods=["DELETE"])
@require_admin
def api_profiles_delete(profile_id):
    global active_profile_id
    profiles = config.get("profiles", [])
    if len(profiles) <= 1:
        return jsonify({"error": "Cannot delete the last profile"}), 400
    target_prf = next((p for p in profiles if p["id"] == profile_id), None)
    if target_prf:
        still_running = any(
            pid in processes and processes[pid].poll() is None
            for pid in target_prf.get("projects", [])
        )
        if still_running:
            return jsonify({"error": "Stop all running projects in this profile before deleting it"}), 400
    config["profiles"] = [p for p in profiles if p["id"] != profile_id]
    if active_profile_id == profile_id:
        active_profile_id = None
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/profiles/reorder", methods=["POST"])
@require_admin
def api_profiles_reorder():
    data = request.json or {}
    order = data.get("order", [])
    prfs = config.get("profiles", [])
    id_to_profile = {p["id"]: p for p in prfs}
    new_profiles = [id_to_profile[pid] for pid in order if pid in id_to_profile]
    existing_ids = {p["id"] for p in new_profiles}
    for p in prfs:
        if p["id"] not in existing_ids:
            new_profiles.append(p)
    config["profiles"] = new_profiles
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/profiles/<profile_id>/set_default", methods=["POST"])
@require_admin
def api_profiles_set_default(profile_id):
    prfs = config.get("profiles", [])
    idx = next((i for i, p in enumerate(prfs) if p["id"] == profile_id), None)
    if idx is None:
        return jsonify({"error": "Profile not found"}), 404
    # Move to front (index 0 = default)
    prfs.insert(0, prfs.pop(idx))
    config["profiles"] = prfs
    save_config(config)
    return jsonify({"ok": True})

@app.route("/api/profiles/active", methods=["GET"])
@require_auth
def api_profiles_active():
    return jsonify({"active_profile": active_profile_id})

@app.route("/api/profiles/active", methods=["POST"])
@require_auth
def api_profiles_set_active():
    """Switch active profile — only allowed if no non-BG projects from other profiles are running."""
    global active_profile_id
    data = request.json or {}
    profile_id = data.get("profile_id")
    if not profile_id:
        return jsonify({"error": "profile_id required"}), 400
    profile = next((p for p in config.get("profiles", []) if p["id"] == profile_id), None)
    if not profile:
        return jsonify({"error": "Profile not found"}), 404

    # Get project IDs that belong to the requested profile
    profile_project_ids = set(profile.get("projects", []))

    # Check if any non-BG project outside this profile is still running
    # (projects that belong to the current active profile but not the target)
    blocking = []
    for p in config["projects"]:
        if p.get("is_background"):
            continue  # BG services never block
        if p["id"] in profile_project_ids:
            continue  # belongs to target profile, fine
        proc = processes.get(p["id"])
        if proc and proc.poll() is None:
            blocking.append(p.get("name", p["id"]))

    if blocking:
        return jsonify({
            "error": f"Stop these projects first: {', '.join(blocking)}",
            "blocking": blocking
        }), 409

    active_profile_id = profile_id
    return jsonify({"ok": True, "active_profile": active_profile_id})

# ── API: PORT SCANNER ─────────────────────────────────────────────
@app.route("/api/portscan", methods=["POST"])
@require_admin
def api_portscan():
    data = request.json or {}
    start = int(data.get("start", 1000))
    end = int(data.get("end", 10000))
    end = min(end, start + 2000)  # max 2000 ports per scan

    results = []
    known_ports = {p.get("port"): p["name"] for p in config["projects"] if p.get("port")}

    def check(port):
        open_ = is_port_open(port)
        if open_:
            results.append({
                "port": port,
                "status": "open",
                "project": known_ports.get(port, "Unknown process"),
                "known": port in known_ports,
            })

    threads = []
    for port in range(start, end + 1):
        t = threading.Thread(target=check, args=(port,))
        threads.append(t)
        t.start()
        if len(threads) >= 200:
            for t in threads: t.join(timeout=1)
            threads = []
    for t in threads: t.join(timeout=1)

    results.sort(key=lambda x: x["port"])
    return jsonify({"results": results, "scanned": end - start + 1})

# ── API: LAST LOGIN ───────────────────────────────────────────────
@app.route("/api/lastlogin")
@require_auth
def api_last_login():
    return jsonify(session.get("last_login", {}))

# ── API: CURRENT USER ─────────────────────────────────────────────
@app.route("/api/me")
@require_auth
def api_me():
    return jsonify({
        "username": session.get("username", ""),
        "role": session.get("role", "viewer"),
    })

# ── API: USER MANAGEMENT (admin only) ────────────────────────────
@app.route("/api/users", methods=["GET"])
@require_admin
def api_users_get():
    result = []
    for uname, udata in users_db.items():
        result.append({
            "username": uname,
            "role": udata.get("role", "viewer"),
            "last_login": udata.get("last_login"),
        })
    return jsonify(result)

@app.route("/api/users", methods=["POST"])
@require_admin
def api_users_add():
    global users_db
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    role = data.get("role", "viewer")
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
    if username in users_db:
        return jsonify({"error": "Username already exists"}), 400
    if role not in ("admin", "viewer"):
        return jsonify({"error": "Invalid role"}), 400
    users_db[username] = {
        "password": hash_password(password),
        "role": role,
        "last_login": None,
    }
    save_users(users_db)
    return jsonify({"ok": True})

@app.route("/api/users/<username>", methods=["PUT"])
@require_auth
def api_users_update(username):
    global users_db
    if username not in users_db:
        return jsonify({"error": "User not found"}), 404
    data = request.json or {}

    # Self password change — any logged-in user can change their own password
    # but must verify current password first
    if data.get("verify_current") is not None:
        if username != session.get("username"):
            return jsonify({"error": "Cannot change another user's password this way"}), 403
        if not verify_password(data["verify_current"], users_db[username]["password"]):
            return jsonify({"error": "Current password is incorrect"}), 400
        if data.get("password"):
            users_db[username]["password"] = hash_password(data["password"])
            save_users(users_db)
        return jsonify({"ok": True})

    # Admin-only: role change and password reset
    if session.get("role") != "admin":
        return jsonify({"error": "Admin access required"}), 403
    new_role = data.get("role", users_db[username]["role"])
    if new_role != "admin":
        admin_count = sum(1 for u in users_db.values() if u.get("role") == "admin")
        if admin_count <= 1 and users_db[username].get("role") == "admin":
            return jsonify({"error": "Cannot remove the last admin"}), 400
    users_db[username]["role"] = new_role
    if data.get("password"):
        users_db[username]["password"] = hash_password(data["password"])
    save_users(users_db)
    return jsonify({"ok": True})

@app.route("/api/users/<username>", methods=["DELETE"])
@require_admin
def api_users_delete(username):
    global users_db
    if username not in users_db:
        return jsonify({"error": "User not found"}), 404
    if username == session.get("username"):
        return jsonify({"error": "Cannot delete your own account"}), 400
    admin_count = sum(1 for u in users_db.values() if u.get("role") == "admin")
    if admin_count <= 1 and users_db[username].get("role") == "admin":
        return jsonify({"error": "Cannot delete the last admin"}), 400
    del users_db[username]
    save_users(users_db)
    return jsonify({"ok": True})

# ── FRONTEND ──────────────────────────────────────────────────────
@app.route("/")
@app.route("/dashboard")
@app.route("/split-log")
@app.route("/projects")
@app.route("/profiles")
@app.route("/port-scanner")
@app.route("/settings")
def index():
    # Single-page app: every "route" serves the same index.html; the client-side
    # JS reads the URL path and shows the matching tab (see _applyRouteFromLocation).
    return send_from_directory(BASE_DIR / "static", "index.html")

@app.route("/favicon.ico")
@app.route("/favicon.png")
def favicon():
    # Try static/ folder first
    for fname, mime in [("favicon.ico", "image/vnd.microsoft.icon"), ("favicon.png", "image/png")]:
        if (STATIC_DIR / fname).exists():
            return send_from_directory(STATIC_DIR, fname, mimetype=mime)
    # Fallback: tiny base64 encoded rocket emoji as PNG
    import base64
    FALLBACK_B64 = (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAB"
        "hGlDQ1BJQ0MgcHJvZmlsZQAAKJF9kT1Iw0AcxV9TpSIVh3YQ"
        "cchQnSyIijhqFYpQIdQKrTqYXPoFTRqSFBdHwbXg4Mdi1cHF"
        "WVcHV0EQfABxc3NSdJES/5cUWsR4cBzHu3uP3XuA0Kgw1eyZ"
        "AFRNMzLJhJjNrYqBVwQRQj8GEZOZaUwpJTm2r0e4eN2F2Gv/"
        "7s21oqawgIcQRxjmqERbxBPb9oazj3nkCWoSviciDqhDhGJH"
        "d8ovOJcdFnhmyMimueJI8TCopttYaWNqIFHiqOqpqO+kGFdY"
        "t3HOlWq3tp/wl5bX1lup1qGMSwghAgiaEBBRQwkJGnESKVBo"
        "YIelDhTiSdEuQGkCQmQIoIsCJAiSJAWQERISEhISEhISEhIS"
        "EhISEhISEjQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0NDQ0"
        "AAAASUVORK5CYII="
    )
    return (base64.b64decode(FALLBACK_B64), 200, {"Content-Type": "image/png"})

if __name__ == "__main__":
    import sys

    # ── RESET PASSWORD MODE ───────────────────────────────────────
    if "--reset-password" in sys.argv:
        print()
        print("  ================================================")
        print("         WELL Launcher v2 - Reset Password")
        print("  ================================================")
        print()
        users = load_users()
        if not users:
            print("  [ERROR] No users found in users.json")
            sys.exit(1)

        print("  Available users:")
        for i, uname in enumerate(users.keys(), 1):
            role = users[uname].get("role", "viewer")
            print(f"    {i}. {uname} ({role})")
        print()

        username = input("  Enter username: ").strip()
        if username not in users:
            print(f"  [ERROR] User '{username}' not found!")
            sys.exit(1)

        import getpass
        while True:
            new_pass = getpass.getpass("  New password: ")
            if not new_pass:
                print("  [ERROR] Password cannot be empty!")
                continue
            confirm = getpass.getpass("  Confirm new password: ")
            if new_pass != confirm:
                print("  [ERROR] Passwords do not match, try again.")
                continue
            break

        users[username]["password"] = hash_password(new_pass)
        save_users(users)
        print()
        print(f"  [OK] Password for '{username}' has been reset!")
        print("  You can now log in with the new password.")
        print()
        input("  Press Enter to exit...")
        sys.exit(0)

    # ── NORMAL SERVER MODE ────────────────────────────────────────
    port = config["settings"].get("dashboard_port", 9000)
    print()
    print("  ================================================")
    print("         WELL Launcher v2")
    print("  ================================================")
    print(f"  URL     : http://localhost:{port}")
    print(f"  Config  : {CONFIG_FILE}")
    print("  Press Ctrl+C to stop")
    print("  ================================================")
    print()
    # Re-attach to any projects still running before launcher was restarted
    reattach_pids()
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)