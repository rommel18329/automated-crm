"""Bulletproof launcher/supervisor for the CRM.

Runs the webhook server and Streamlit as managed subprocesses and keeps them
alive: it health-checks each one and automatically restarts any that dies or
stops responding. Designed so you never see a 502 (dead webhook) mid-dial.

Run this instead of start.bat's individual commands:
    python run_crm.py

The Tailscale funnel is still started separately by start.bat (it's a
long-lived OS service, not a Python process this supervisor manages), but the
supervisor also watches the PUBLIC URL: if Twilio's path through the funnel
breaks while the webhook itself is fine, it re-applies the funnel.

Everything the supervisor and each service prints is also written to
logs/<name>.log (size-capped), so a failure leaves a record.

Ctrl+C stops everything cleanly.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_MAX_BYTES = 5_000_000   # rotate to <name>.log.1 past this size

# name -> config
SERVICES = {
    "webhook": {
        "cmd": [sys.executable, str(ROOT / "webhook_server.py")],
        "port": 5001,
        "health": "http://127.0.0.1:5001/health",
        "grace": 8,     # seconds to allow for startup before health-checking
    },
    "streamlit": {
        "cmd": [sys.executable, "-m", "streamlit", "run", str(ROOT / "dashboard.py"),
                "--server.port", "8501", "--server.headless", "true"],
        "port": 8501,
        "health": None,   # streamlit has no cheap health endpoint; use port check
        "grace": 12,
    },
    "amd": {
        "cmd": [sys.executable, str(ROOT / "amd_server.py")],
        "port": 5002,
        "health": None,   # websocket server; the port check is the health check
        "grace": 6,
    },
    # The 2026 UI was promoted into dashboard.py on 2026-07-07; the old UI is
    # archived in legacy_ui/dashboard_legacy.py (see its README to roll back).
}

CHECK_EVERY = 5          # seconds between health checks
MAX_RESTARTS_PER_MIN = 5  # circuit breaker: stop hammering if it keeps dying


_log_lock = threading.Lock()


def _append_log(name: str, line: str, log_dir: Path = LOG_DIR) -> None:
    """Append one line to logs/<name>.log, rotating once past LOG_MAX_BYTES.
    Logging must never take the supervisor down, so failures are swallowed."""
    try:
        with _log_lock:
            log_dir.mkdir(exist_ok=True)
            path = log_dir / f"{name}.log"
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, log_dir / f"{name}.log.1")
            with open(path, "a", encoding="utf-8", errors="replace") as f:
                f.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [supervisor] {msg}"
    print(line[11:], flush=True)
    _append_log("supervisor", line)


def _public_base_url() -> str:
    """PUBLIC_BASE_URL from the environment or .env (the funnel address Twilio
    calls). Empty disables the public watchdog."""
    url = os.getenv("PUBLIC_BASE_URL", "")
    if not url:
        try:
            for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
                k, _, v = raw.partition("=")
                if k.strip() == "PUBLIC_BASE_URL":
                    url = v.strip().strip('"').strip("'")
        except Exception:
            pass
    return url.rstrip("/")


class FunnelWatch:
    """Decides when to re-apply the Tailscale funnel. Only counts a public
    failure while the webhook answers locally (otherwise the normal restart
    path handles it), needs `threshold` in a row, and waits `cooldown` seconds
    between repairs so a real outage can't turn into a reset loop."""

    def __init__(self, threshold: int = 3, cooldown: float = 60.0):
        self.threshold = threshold
        self.cooldown = cooldown
        self.fails = 0
        self.last_repair = float("-inf")

    def record(self, public_ok: bool, local_ok: bool, now: float) -> bool:
        if public_ok or not local_ok:
            self.fails = 0
            return False
        self.fails += 1
        if self.fails >= self.threshold and now - self.last_repair >= self.cooldown:
            self.fails = 0
            self.last_repair = now
            return True
        return False

    def request_repair(self, now: float) -> bool:
        """Repair requested by an outside signal (Twilio saw a 502). Honors the
        same cooldown as record() and shares its last_repair clock."""
        if now - self.last_repair < self.cooldown:
            return False
        self.fails = 0
        self.last_repair = now
        return True


TWILIO_ALERT_POLL = 30.0      # seconds between Twilio Monitor alert reads
TWILIO_ALERT_WINDOW = 300.0   # only alerts created in the last 5 minutes count


def find_funnel_502(alerts, base_url: str, now_utc: datetime,
                    since_utc: datetime | None = None,
                    window: float = TWILIO_ALERT_WINDOW):
    """Return the newest Twilio alert that shows Twilio got HTTP 502 (error
    11200) from our public webhook URL within `window` seconds, ignoring any
    created before `since_utc` (the last repair). None if there isn't one.
    Pure function: `alerts` is any iterable of objects with error_code,
    alert_text, request_url and date_created (tz-aware datetime)."""
    if not base_url:
        return None
    best = None
    for a in alerts:
        try:
            if str(a.error_code) != "11200":
                continue
            created = a.date_created
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now_utc - created).total_seconds() > window:
                continue
            if since_utc is not None and created <= since_utc:
                continue
            url = str(getattr(a, "request_url", "") or "")
            text = str(getattr(a, "alert_text", "") or "")
            if not (url.startswith(base_url) or base_url in text):
                continue
            if not re.search(r"\b502\b", text):
                continue
            if best is None or created > best.date_created:
                best = a
        except Exception:
            continue
    return best


class TwilioAlertWatch:
    """Polls Twilio Monitor for 'Got HTTP 502' alerts on our funnel URL --
    the failure Twilio sees but a probe from this PC does not. Cheap (one
    small list call per `interval`) and never raises."""

    def __init__(self, base_url: str, interval: float = TWILIO_ALERT_POLL):
        self.base_url = base_url
        self.interval = interval
        self.next_poll = 0.0
        self.last_repair_utc: datetime | None = None
        self._client = None

    def _get_client(self):
        if self._client is None:
            from dotenv import load_dotenv
            from twilio.rest import Client
            load_dotenv(ROOT / ".env")
            sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
            if not (sid and token):
                raise RuntimeError("TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set")
            self._client = Client(sid, token)
        return self._client

    def _fetch(self, now_utc: datetime):
        return self._get_client().monitor.v1.alerts.list(
            log_level="error",
            start_date=now_utc - timedelta(seconds=TWILIO_ALERT_WINDOW + 60),
            limit=50)

    def check(self, now: float, now_utc: datetime | None = None, fetch=None):
        """Return the matching alert if one is due for repair, else None.
        `now` is monotonic-ish seconds for scheduling; `fetch` is injectable."""
        if now < self.next_poll:
            return None
        self.next_poll = now + self.interval
        now_utc = now_utc or datetime.now(timezone.utc)
        try:
            alerts = (fetch or self._fetch)(now_utc)
            return find_funnel_502(alerts, self.base_url, now_utc, self.last_repair_utc)
        except Exception as e:
            log(f"twilio alert check failed: {type(e).__name__}: {e}")
            return None

    def mark_repaired(self, now_utc: datetime | None = None) -> None:
        self.last_repair_utc = now_utc or datetime.now(timezone.utc)


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def healthy(cfg: dict) -> bool:
    if not port_open(cfg["port"]):
        return False
    if cfg["health"]:
        return http_ok(cfg["health"])
    return True


def wait_for_port_release(port: int, timeout: int = 15) -> None:
    """Avoid the bind race that caused the 502: wait for the old process to
    free the port before starting a new one."""
    start = time.time()
    while port_open(port) and time.time() - start < timeout:
        time.sleep(0.5)


def start(name: str, cfg: dict) -> subprocess.Popen:
    wait_for_port_release(cfg["port"])
    log(f"starting {name}...")
    proc = subprocess.Popen(cfg["cmd"], cwd=str(ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, bufsize=1, text=True,
                            encoding="utf-8", errors="replace")
    threading.Thread(target=_tee_output, args=(name, proc), daemon=True).start()
    return proc


def _tee_output(name: str, proc: subprocess.Popen) -> None:
    """Show a service's output in this console as before AND keep it in
    logs/<name>.log."""
    for line in proc.stdout:
        try:
            print(line, end="", flush=True)
        except Exception:
            pass
        _append_log(name, f"{datetime.now():%Y-%m-%d %H:%M:%S} {line.rstrip()}")


# A fixed loopback port doubles as an atomic single-instance mutex: only one
# supervisor can bind it, and the OS releases it the instant that process dies.
# This is what stops multiple supervisors from racing over port 5001 (the cause
# of the intermittent 502s / "application error" in the dialer).
_MUTEX_PORT = 5099
_singleton_sock = None


def _acquire_singleton_lock():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", _MUTEX_PORT))
        sock.listen(1)
    except OSError:
        log(f"Another CRM supervisor is already running (mutex :{_MUTEX_PORT} held). "
            f"Not starting a second one.")
        sys.exit(1)
    return sock


def _kill_stale_crm_processes() -> None:
    """Kill leftover CRM processes from a previous/crashed launch so we always
    start exactly one clean stack (no duplicate webhooks fighting over :5001)."""
    try:
        import psutil
    except Exception:
        log("psutil not available; skipping stale-process cleanup")
        return
    me, parent = os.getpid(), os.getppid()
    targets = ("webhook_server.py", "amd_server.py", "run_crm.py")
    killed = 0
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            if p.info["pid"] in (me, parent):
                continue
            cl = " ".join(p.info.get("cmdline") or [])
            if not cl:
                continue
            if any(t in cl for t in targets) or ("streamlit" in cl and "8501" in cl):
                p.kill()
                killed += 1
        except Exception:
            pass
    if killed:
        log(f"cleaned up {killed} stale CRM process(es) before starting")
        time.sleep(1.5)   # let the OS release their ports


def _refresh_funnel() -> None:
    """Re-establish the Tailscale funnel after the webhook (re)starts. tailscaled
    can keep proxying to the OLD (dead) webhook process after a restart and keep
    returning HTTP 502 to Twilio until the funnel is re-applied -- which is what
    produced 'an application error has occurred' in the dialer."""
    steps = (
        ["tailscale", "funnel", "reset"],
        ["tailscale", "funnel", "--bg", "5001"],
        ["tailscale", "funnel", "--bg", "--set-path", "/amd", "5002"],
    )
    for cmd in steps:
        try:
            subprocess.run(cmd, timeout=20, capture_output=True)
        except Exception as e:
            log(f"funnel refresh step failed ({' '.join(cmd)}): {e}")
    log("tailscale funnel re-applied (pointing at the current webhook)")


def main() -> None:
    global _singleton_sock
    _singleton_sock = _acquire_singleton_lock()
    _kill_stale_crm_processes()
    procs: dict[str, subprocess.Popen] = {}
    grace_until: dict[str, float] = {}
    restart_times: dict[str, list[float]] = {n: [] for n in SERVICES}

    for name, cfg in SERVICES.items():
        procs[name] = start(name, cfg)
        grace_until[name] = time.time() + cfg["grace"]

    _refresh_funnel()
    public_health = _public_base_url() + "/health" if _public_base_url() else ""
    funnel_watch = FunnelWatch()
    alert_watch = TwilioAlertWatch(_public_base_url()) if public_health else None
    if public_health:
        log(f"watching public URL {public_health}")
    else:
        log("PUBLIC_BASE_URL not set; public funnel watchdog disabled")
    log("all services launched. Watching... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(CHECK_EVERY)
            now = time.time()
            # Twilio reaches the webhook only through the funnel. It can break
            # (HTTP 502) while the webhook is perfectly healthy, which is the
            # "an application error has occurred" seen on 2026-10-14. Re-apply it.
            if public_health and now >= grace_until["webhook"]:
                local_ok = healthy(SERVICES["webhook"])
                public_ok = http_ok(public_health) if local_ok else False
                if not public_ok and local_ok:
                    log(f"public URL check failed ({funnel_watch.fails + 1} in a row) "
                        f"while webhook is healthy locally")
                if funnel_watch.record(public_ok, local_ok, now):
                    log("funnel unreachable from outside — re-applying tailscale funnel")
                    _refresh_funnel()
                    ok = http_ok(public_health)
                    log(f"public URL after funnel repair: {'OK' if ok else 'STILL FAILING'}")
                    alert_watch.mark_repaired()
                # Twilio's own alert log is the reliable signal: it sees 502s
                # the probe above (same PC) never reproduces.
                hit = alert_watch.check(now) if alert_watch else None
                if hit is not None and funnel_watch.request_repair(now):
                    log(f"Twilio reported HTTP 502 from the funnel "
                        f"({getattr(hit, 'request_url', '?')} at {hit.date_created}) "
                        f"— re-applying tailscale funnel")
                    _refresh_funnel()
                    alert_watch.mark_repaired()
            for name, cfg in SERVICES.items():
                proc = procs[name]
                exited = proc.poll() is not None
                in_grace = now < grace_until[name]
                if not exited and in_grace:
                    continue
                if exited or not healthy(cfg):
                    # circuit breaker: count restarts in the last 60s
                    recent = [t for t in restart_times[name] if now - t < 60]
                    restart_times[name] = recent
                    if len(recent) >= MAX_RESTARTS_PER_MIN:
                        log(f"{name} keeps failing ({len(recent)}x/min) — "
                            f"backing off 60s. Check its window for errors.")
                        time.sleep(60)
                        restart_times[name] = []
                    reason = "exited" if exited else "unhealthy"
                    log(f"{name} {reason} — restarting")
                    try:
                        if not exited:
                            proc.terminate()
                            try:
                                proc.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                    except Exception:
                        pass
                    procs[name] = start(name, cfg)
                    if name == "webhook":
                        _refresh_funnel()   # re-point the funnel at the new webhook
                    grace_until[name] = time.time() + cfg["grace"]
                    restart_times[name].append(time.time())
    except KeyboardInterrupt:
        log("shutting down...")
        for name, proc in procs.items():
            try:
                proc.terminate()
            except Exception:
                pass
        log("stopped.")


if __name__ == "__main__":
    main()
