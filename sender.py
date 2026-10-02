"""
Send messages through the Meta WhatsApp Cloud API.

Configuration via environment variables (see .env.example):
  WA_ACCESS_TOKEN    - permanent / system-user access token (REQUIRED)
  WA_PHONE_NUMBER_ID - phone number ID from the Meta dashboard (REQUIRED)
  WA_API_VERSION     - Graph API version, default "v21.0"
  DRY_RUN            - if "1", log instead of calling Meta (for local tests)

No secrets are hardcoded here. Ever.
"""

import json
import os

import requests

API_VERSION = os.environ.get("WA_API_VERSION", "v21.0")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"


def _config():
    token = os.environ.get("WA_ACCESS_TOKEN", "")
    phone_number_id = os.environ.get("WA_PHONE_NUMBER_ID", "")
    if not token or not phone_number_id:
        raise RuntimeError(
            "Missing WA_ACCESS_TOKEN and/or WA_PHONE_NUMBER_ID environment variables."
        )
    return token, phone_number_id


def send_message(to, text):
    """
    Send a text message to a WhatsApp user (E.164-ish digits, e.g. "22373255873").
    Returns (ok: bool, detail: str).
    """
    if DRY_RUN:
        print(f"[DRY_RUN] -> {to}: {text[:120]}")
        return True, "dry_run"

    token, phone_number_id = _config()
    url = f"https://graph.facebook.com/{API_VERSION}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        return False, f"network_error: {exc}"

    if 200 <= resp.status_code < 300:
        return True, resp.text[:200]
    return False, f"http_{resp.status_code}: {resp.text[:200]}"


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("usage: DRY_RUN=1 python sender.py <to> <text>")
        raise SystemExit(2)
    ok, detail = send_message(sys.argv[1], sys.argv[2])
    print(json.dumps({"ok": ok, "detail": detail}, ensure_ascii=False))
