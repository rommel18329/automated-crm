"""AMD WebSocket server — self-built voicemail detection for the dialer.

Twilio Media Streams connects here (wss://<funnel>/amd) when a multi-dial
call is answered and streams the callee's audio. The classifier in
crm_engine/amd_detector.py listens to the first few seconds and decides
human / machine / unknown; the verdict is POSTed to the webhook server
(/voice/amd-result/<batch>/<lead>), which connects humans to the softphone
conference and hangs up + auto-disposes machines.

Run: python amd_server.py   (the run_crm.py supervisor manages it)
Port: 5002, exposed through the Tailscale funnel at /amd.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import websockets

from crm_engine.amd_detector import AmdClassifier, mulaw_to_float

PORT = 5002
FLASK_BASE = "http://127.0.0.1:5001"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("amd")


def _post_result(batch: str, lead: str, result: str, detail: str) -> None:
    try:
        req = urllib.request.Request(
            f"{FLASK_BASE}/voice/amd-result/{batch}/{lead}",
            data=json.dumps({"result": result, "detail": detail}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).read()
        log.info(f"[{batch}] lead {lead}: {result} ({detail}) -> reported")
    except Exception as exc:
        log.warning(f"[{batch}] lead {lead}: failed to report {result}: {exc}")


async def handler(ws) -> None:
    clf = AmdClassifier()
    batch = lead = None
    decided = False
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            ev = msg.get("event")
            if ev == "start":
                params = (msg.get("start") or {}).get("customParameters") or {}
                batch = params.get("batch")
                lead = params.get("lead")
                log.info(f"stream start: batch={batch} lead={lead}")
            elif ev == "media" and not decided:
                payload = (msg.get("media") or {}).get("payload")
                if not payload:
                    continue
                pcm = mulaw_to_float(base64.b64decode(payload))
                verdict = clf.feed(pcm)
                if verdict:
                    decided = True
                    if batch and lead:
                        loop = asyncio.get_running_loop()
                        loop.run_in_executor(None, _post_result, batch, lead,
                                             verdict, clf.reason)
                    # keep draining until Twilio closes; nothing more to do
            elif ev == "stop":
                break
    except websockets.ConnectionClosed:
        pass
    finally:
        if not decided and batch and lead:
            log.info(f"[{batch}] lead {lead}: stream ended before a verdict")


async def main() -> None:
    async with websockets.serve(handler, "0.0.0.0", PORT):
        log.info(f"AMD server listening on ws://0.0.0.0:{PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
