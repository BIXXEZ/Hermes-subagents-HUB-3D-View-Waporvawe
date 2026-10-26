"""office_hermes — живой фид офиса для Hermes.

Отдаёт состояние сфокусированного чата и его субагентов из state.db Hermes.
База открывается ТОЛЬКО на чтение (URI mode=ro), писать в неё сервер не может.

    GET /              — однофайловый вьювер (office.html, модель вшита)
    GET /state?sid=    — JSON: chat + workers[] (активные) + history[] (все) + sys + main
    GET /health        — ok + счётчики запросов (диагностика плагина)
    GET /beacon?stage= — 204, метка живости страницы внутри Electron

Запуск:
    python office_live.py [--port 8797] [--home <HERMES_HOME>]

Порт также берётся из OFFICE_PORT, домашний каталог — из HERMES_HOME.
Зависимостей нет: только стандартная библиотека.
Windows: CPU/RAM/топ-процессы через ctypes (kernel32/psapi), psutil не нужен.
Linux/macOS: RAM из /proc/meminfo или vm_stat, CPU/процессы — если есть psutil.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"


def default_home():
    """Каталог Hermes: HERMES_HOME → платформенный дефолт."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "hermes")
    for p in ("~/.hermes", "~/.local/share/hermes", "~/Library/Application Support/hermes"):
        cand = os.path.expanduser(p)
        if os.path.isdir(cand):
            return cand
    return os.path.expanduser("~/.hermes")


HOME = default_home()
DB = os.path.join(HOME, "state.db")
PORT = int(os.environ.get("OFFICE_PORT", "8797"))

CACHE_TTL = 1.0
_cache = {"t": 0.0, "key": None, "payload": None}
HITS = {}   # диагностика: кто дергал сервер (плагин/браузер)


# ---------------------------------------------------------------- системные метрики
_sys_cache = {"t": 0.0, "data": None}

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)

    class _MEMSTAT(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    class _FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class _PMC(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    _prev_cpu = {"idle": 0, "total": 0, "cpu": 0.0}
    TH32CS_SNAPPROCESS = 0x2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def _ft(ft):
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    def cpu_pct():
        idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
        if not _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return _prev_cpu["cpu"]
        i, k, u = _ft(idle), _ft(kern), _ft(user)
        total = k + u
        d_total = total - _prev_cpu["total"]
        d_idle = i - _prev_cpu["idle"]
        if d_total > 0 and _prev_cpu["total"] > 0:
            _prev_cpu["cpu"] = round(100.0 * (d_total - d_idle) / d_total, 1)
        _prev_cpu["total"], _prev_cpu["idle"] = total, i
        return _prev_cpu["cpu"]

    def mem():
        m = _MEMSTAT()
        m.dwLength = ctypes.sizeof(m)
        if not _k32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return {"total_mb": 0, "used_mb": 0, "pct": 0}
        total = m.ullTotalPhys
        used = total - m.ullAvailPhys
        return {"total_mb": round(total / 1048576),
                "used_mb": round(used / 1048576),
                "pct": int(m.dwMemoryLoad)}

    def top_procs(limit=4):
        """Топ процессов по resident-памяти (агрегировано по имени exe)."""
        snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap in (0, ctypes.c_void_p(-1).value):
            return []
        pe = _PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        agg = {}
        if _k32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID)
                if h:
                    pmc = _PMC()
                    pmc.cb = ctypes.sizeof(pmc)
                    if _psapi.GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                        name = (pe.szExeFile or "").lower()
                        if name:
                            agg[name] = agg.get(name, 0) + pmc.WorkingSetSize
                    _k32.CloseHandle(h)
                if not _k32.Process32NextW(snap, ctypes.byref(pe)):
                    break
        _k32.CloseHandle(snap)
        rows = sorted(agg.items(), key=lambda kv: -kv[1])[:limit]
        return [{"n": n.replace(".exe", "")[:16], "mb": round(v / 1048576, 1)} for n, v in rows]
else:
    # macOS/Linux: без зависимостей; если есть psutil — берём его
    def mem():
        try:
            with open("/proc/meminfo") as f:
                info = {k: int(v.split()[0]) for k, v in
                        (ln.split(":", 1) for ln in f if ":" in ln)}
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", info.get("MemFree", 0))
            return {"total_mb": round(total / 1024), "used_mb": round((total - avail) / 1024),
                    "pct": int(100 * (total - avail) / total) if total else 0}
        except Exception:
            return {"total_mb": 0, "used_mb": 0, "pct": 0}

    def cpu_pct():
        try:
            import psutil
            return round(psutil.cpu_percent(interval=None), 1)
        except Exception:
            return 0.0

    def top_procs(limit=4):
        try:
            import psutil
            agg = {}
            for p in psutil.process_iter(["name", "memory_info"]):
                try:
                    nm = (p.info["name"] or "").lower()
                    agg[nm] = agg.get(nm, 0) + p.info["memory_info"].rss
                except Exception:
                    pass
            rows = sorted(agg.items(), key=lambda kv: -kv[1])[:limit]
            return [{"n": n.replace(".exe", "")[:16], "mb": round(v / 1048576, 1)} for n, v in rows]
        except Exception:
            return []


def sysinfo(ttl=2.5):
    now = time.time()
    if _sys_cache["data"] and now - _sys_cache["t"] < ttl:
        return _sys_cache["data"]
    data = {"cpu": cpu_pct(), "ram": mem(), "procs": top_procs()}
    _sys_cache.update({"t": now, "data": data})
    return data


# ---------------------------------------------------------------- данные Hermes
def db():
    if not os.path.exists(DB):
        raise FileNotFoundError(f"state.db не найден: {DB}")
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def head(txt, n=64):
    if not txt:
        return ""
    txt = re.sub(r"\s+", " ", str(txt)).strip()
    return txt[:n] + ("…" if len(txt) > n else "")


def stamp(ts):
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def message_logs(conn, session_id, limit=4):
    rows = conn.execute(
        """select role, tool_name, content, tool_calls, timestamp
           from messages
           where session_id = ? and role != 'system' and (active is null or active = 1)
           order by id desc limit ?""",
        (session_id, limit),
    ).fetchall()
    out = []
    last = 0.0
    for r in reversed(rows):
        role, tool, content, calls = r["role"], r["tool_name"], r["content"], r["tool_calls"]
        if role == "assistant":
            names = []
            if calls:
                try:
                    names = [c.get("function", {}).get("name") for c in json.loads(calls)]
                except Exception:
                    names = []
            text = ("→ " + ", ".join([n for n in names if n][:2])) if names else ("« " + head(content, 58))
        elif role == "tool":
            text = "← " + (tool or "tool") + ": " + head(content, 46)
        elif role == "user":
            text = "› " + head(content, 60)
        else:
            text = head(content, 60)
        out.append({"t": stamp(r["timestamp"]), "text": text})
        last = max(last, float(r["timestamp"] or 0))
    return out, last


def pick_session(conn, sid):
    if sid:
        row = conn.execute("select * from sessions where id = ?", (sid,)).fetchone()
        if row:
            return row
        row = conn.execute(
            "select * from sessions where session_key = ? order by started_at desc limit 1", (sid,)
        ).fetchone()
        if row:
            return row
        row = conn.execute(
            "select * from sessions where id like ? order by started_at desc limit 1", (sid + "%",)
        ).fetchone()
        if row:
            return row
    # фолбэк: самый свежий корневой чат
    return conn.execute(
        """select * from sessions
           where (parent_session_id is null or parent_session_id = '')
             and last_activity_at is not null
           order by last_activity_at desc limit 1"""
    ).fetchone()


def children(conn, sid):
    return conn.execute(
        """select id, title, display_name, message_count, tool_call_count,
                  last_activity_at, started_at, ended_at, model
           from sessions where parent_session_id = ? order by started_at""",
        (sid,),
    ).fetchall()


def delegation_workers(conn, sid, now):
    """Если субагенты ещё не создали сессий — берём их из async_delegations."""
    row = conn.execute(
        """select state, task_json, dispatched_at, event_json from async_delegations
           where origin_session = ? order by dispatched_at desc limit 1""",
        (sid,),
    ).fetchone()
    if not row:
        return []
    try:
        tasks = json.loads(row["task_json"] or "{}").get("tasks") or []
    except Exception:
        tasks = []
    running = row["state"] in ("running", "dispatched", "claimed")
    out = []
    for i, t in enumerate(tasks):
        goal = t.get("goal") or t.get("description") or "subagent task"
        out.append({
            "id": f"worker_{i+1:02d}",
            "label": head(goal, 40),
            "status": "running" if running else "done",
            "eff": 78 if running else 34,
            "msgs": 0, "tools": 0,
            "log": [{"t": stamp(row["dispatched_at"]), "text": "› dispatch: " + head(goal, 52)}],
        })
    return out


def build_state(sid):
    now = time.time()
    conn = db()
    try:
        s = pick_session(conn, sid)
        if not s:
            return {"chat": {"title": "нет активного чата", "busy": False}, "workers": [], "main": {"log": []}}
        parent_id = s["id"]
        last_act = s["last_activity_at"] or 0
        busy = (now - last_act) < 180 and not s["ended_at"]

        kids = children(conn, parent_id)
        workers = []
        for i, k in enumerate(kids):
            logs, k_last = message_logs(conn, k["id"], 4)
            if not k_last:
                k_last = k["last_activity_at"] or 0
            if k["ended_at"] and now - k_last > 90:
                st = "done"
            elif now - k_last < 120:
                st = "running"
            elif now - k_last < 900:
                st = "idle"
            else:
                st = "done"
            eff = {"running": 62 + (int(k["tool_call_count"] or 0) % 32),
                   "idle": 44, "done": 28}[st]
            title = k["display_name"] or k["title"] or "subagent"
            label = re.sub(r"^subagent:\s*", "", title, flags=re.I)
            workers.append({
                "id": f"worker_{i+1:02d}",
                "label": head(label, 40),
                "status": st,
                "eff": eff,
                "msgs": int(k["message_count"] or 0),
                "tools": int(k["tool_call_count"] or 0),
                "log": logs,
            })

        if not workers:
            workers = delegation_workers(conn, parent_id, now)

        # «только активные» = кто ещё не закончил; history = все, кто вообще был
        history = list(workers)
        active = [w for w in workers if w["status"] != "done"]

        main_log, _main_last = message_logs(conn, parent_id, 4)
        title = s["display_name"] or s["title"] or "чат без названия"
        return {
            "chat": {
                "id": parent_id,
                "title": title,
                "model": (s["model"] or "").split("/")[-1][:28],
                "busy": bool(busy),
                "msgs": int(s["message_count"] or 0),
                "tools": int(s["tool_call_count"] or 0),
                "activity": s["last_activity_description"] or "",
                "ago": int(now - last_act) if last_act else None,
            },
            "workers": active,
            "history": history,
            "sys": sysinfo(),
            "main": {
                "label": "main agent",
                "status": "STREAMING" if busy else "IDLE",
                "task": s["last_activity_description"] or title,
                "msgs": int(s["message_count"] or 0),
                "tools": int(s["tool_call_count"] or 0),
                "eff": 70 if busy else 40,
                "log": main_log,
            },
        }
    finally:
        conn.close()


def viewer_path():
    """office.html: рядом с сервером, в ./web или в ../web (репозиторий)."""
    for p in (os.path.join(HERE, "office.html"),
              os.path.join(HERE, "web", "office.html"),
              os.path.join(os.path.dirname(HERE), "web", "office.html")):
        if os.path.exists(p):
            return p
    return None


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return      # браузер/Electron закрыл keep-alive — не шумим в лог
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        HITS[u.path] = HITS.get(u.path, 0) + 1
        HITS["_last"] = time.strftime("%H:%M:%S")
        ua = self.headers.get("User-Agent", "")
        tag = "electron" if "Electron" in ua else ("headless" if "Headless" in ua else "other")
        HITS["ua_" + tag] = HITS.get("ua_" + tag, 0) + 1
        if u.path == "/":
            HITS["_last_page_ua"] = ua[:90]
        if u.path == "/state" and "sid=" in self.path:
            HITS["_state_with_sid"] = HITS.get("_state_with_sid", 0) + 1
        if u.path in ("/", "/index.html", "/office.html"):
            path = viewer_path()
            if not path:
                return self._send(500, "office.html не найден: положите его рядом с сервером",
                                  "text/plain; charset=utf-8")
            with open(path, "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if u.path == "/beacon":
            qs = parse_qs(u.query)
            stage = (qs.get("stage") or ["?"])[0]
            HITS["beacon_" + stage] = HITS.get("beacon_" + stage, 0) + 1
            if qs.get("m"):
                HITS["beacon_msg_" + stage] = (qs.get("m") or [""])[0]
            return self._send(204, b"")
        if u.path == "/health":
            return self._send(200, json.dumps({
                "service": "office_hermes", "ok": True, "version": VERSION,
                "home": HOME, "db": os.path.exists(DB), "port": PORT,
                "pid": os.getpid(), "hits": HITS,
            }, ensure_ascii=False))
        if u.path == "/state":
            sid = (parse_qs(u.query).get("sid") or [""])[0]
            key = sid or "__latest__"
            if _cache["key"] == key and time.time() - _cache["t"] < CACHE_TTL and _cache["payload"]:
                return self._send(200, _cache["payload"])
            try:
                payload = json.dumps(build_state(sid), ensure_ascii=False)
            except FileNotFoundError as e:
                return self._send(500, json.dumps({"error": str(e), "home": HOME}, ensure_ascii=False))
            except Exception as e:
                return self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))
            _cache.update({"t": time.time(), "key": key, "payload": payload})
            return self._send(200, payload)
        return self._send(404, json.dumps({"error": "not found"}))


def main():
    global HOME, DB, PORT
    ap = argparse.ArgumentParser(description="office_hermes live feed")
    ap.add_argument("--port", type=int, default=PORT, help="порт (по умолчанию 8797 / OFFICE_PORT)")
    ap.add_argument("--home", default=HOME, help="каталог Hermes со state.db (по умолчанию HERMES_HOME)")
    ap.add_argument("--log", default=os.environ.get("OFFICE_LOG"),
                    help="писать stdout/stderr в файл (для запуска без консоли)")
    args = ap.parse_args()
    PORT, HOME = args.port, os.path.abspath(os.path.expanduser(args.home))
    DB = os.path.join(HOME, "state.db")
    if args.log:
        logf = open(os.path.abspath(os.path.expanduser(args.log)), "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = logf
        print(f"--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} start (pid {os.getpid()})")

    if not os.path.exists(DB):
        print(f"[office] ВНИМАНИЕ: state.db не найден: {DB}", flush=True)
    if not viewer_path():
        print("[office] ВНИМАНИЕ: office.html не найден рядом с сервером", flush=True)

    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[office] порт {PORT} занят: {e}\n"
              f"         занят другой копией фида? закройте её или запустите с --port N\n"
              f"         (и поменяйте BASE в plugin.js на тот же порт).",
              file=sys.stderr, flush=True)
        return 2

    print(f"office_hermes {VERSION} on http://127.0.0.1:{PORT}/  (db: {DB})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
