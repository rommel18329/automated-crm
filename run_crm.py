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


def main() -> None:
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
