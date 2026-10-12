"""Bulletproof launcher/supervisor for the CRM.

Runs the webhook server and Streamlit as managed subprocesses and keeps them
alive: it health-checks each one and automatically restarts any that dies or
stops responding. Designed so you never see a 502 (dead webhook) mid-dial.

Run this instead of start.bat's individual commands:
    python run_crm.py

The Tailscale funnel is still started separately by start.bat (it's a
long-lived OS service, not a Python process this supervisor manages).

Ctrl+C stops everything cleanly.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} [supervisor] {msg}", flush=True)


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
    proc = subprocess.Popen(cfg["cmd"], cwd=str(ROOT))
    return proc


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

    log("all services launched. Watching... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(CHECK_EVERY)
            now = time.time()
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
