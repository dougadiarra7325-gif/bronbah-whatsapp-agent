"""
Webhook server for the WhatsApp sales agent (Meta Cloud API).

Endpoints:
  GET  /webhook  - Meta verification (hub.mode / hub.verify_token / hub.challenge)
  POST /webhook  - inbound events: queues text messages, acks everything fast
  GET  /health   - health check
  GET  /dev/orders    - (dev only) list orders
  GET  /dev/leads     - (dev only) list leads (CM + escalations)
  POST /dev/simulate  - (dev only) inject a fake inbound message for local tests

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""

import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

import db

VERIFY_TOKEN = os.environ.get("WA_VERIFY_TOKEN", "change-me-verify-token")
DEV_MODE = os.environ.get("DEV_MODE", "0") == "1"
INLINE_WORKER = os.environ.get("INLINE_WORKER", "0") == "1"


def _inline_worker_loop():
    """Background poller for single-process hosting (Render free tier etc.):
    drains the queue in the same process so SQLite stays consistent."""
    import worker as _w

    while True:
        try:
            _w.process_once()
        except Exception as exc:  # never kill the loop
            print(f"[inline-worker] ERROR: {exc}")
        time.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    thread = None
    if INLINE_WORKER:
        thread = threading.Thread(target=_inline_worker_loop, daemon=True)
        thread.start()
        print("[server] inline worker started (15s poll)")
    yield


app = FastAPI(title="whatsapp-agent", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True, "ts": time.time()}


@app.get("/webhook")
def verify_webhook(request: Request):
    """Meta subscription verification handshake."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return PlainTextResponse(challenge, status_code=200)
    return PlainTextResponse("verification failed", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Receive Meta events. We ONLY queue inbound text messages and return 200
    immediately (Meta retries if we are slow). The worker does the thinking.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored: not json"}, status_code=200)

    if payload.get("object") != "whatsapp_business_account":
        return JSONResponse({"status": "ignored: not whatsapp"}, status_code=200)

    queued = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = {c.get("wa_id"): c.get("profile", {}).get("name", "")
                        for c in value.get("contacts", [])}
            for msg in value.get("messages", []):
                sender = msg.get("from", "")
                name = contacts.get(sender, "")
                wa_id = msg.get("id") or f"noid-{time.time_ns()}"
                if msg.get("type") == "text":
                    body = (msg.get("text") or {}).get("body", "")
                else:
                    # non-text (image, audio, sticker...): note it, agent replies politely
                    body = f"[{msg.get('type', 'unknown')}]"
                if db.queue_message(wa_id, sender, name, body):
                    queued += 1
            # value.statuses (delivery/read receipts) are intentionally ignored
    return JSONResponse({"status": "ok", "queued": queued}, status_code=200)


# ---------------- dev helpers (disabled unless DEV_MODE=1) ----------------

@app.get("/dev/orders")
def dev_orders(status: str = None):
    if not DEV_MODE:
        return Response(status_code=404)
    return {"orders": db.list_orders(status=status)}


@app.get("/dev/leads")
def dev_leads(kind: str = None, status: str = None):
    if not DEV_MODE:
        return Response(status_code=404)
    return {"leads": db.list_leads(kind=kind, status=status)}


@app.post("/dev/simulate")
async def dev_simulate(request: Request):
    """Inject a fake inbound message: {"sender": "...", "name": "...", "text": "..."}"""
    if not DEV_MODE:
        return Response(status_code=404)
    data = await request.json()
    sender = data.get("sender", "22300000000")
    name = data.get("name", "Test")
    text = data.get("text", "")
    wa_id = f"sim-{time.time_ns()}"
    db.queue_message(wa_id, sender, name, text)
    return {"queued": True, "wa_message_id": wa_id}
