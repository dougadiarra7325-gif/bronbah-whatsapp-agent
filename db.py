"""
SQLite storage for the WhatsApp sales agent.

Tables:
  inbox      - incoming messages queued from the Meta webhook (deduplicated)
  conv_state - per-sender conversation state for the order flow
  orders     - orders taken by the agent (status: draft / a_encaisser / done / cancelled)
  outbox_log - log of every reply the agent sent (or tried to send)
"""

import json
import os
import sqlite3
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WA_DB_PATH", os.path.join(BASE_DIR, "data", "agent.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  wa_message_id TEXT UNIQUE,
  sender        TEXT NOT NULL,
  sender_name   TEXT,
  body          TEXT,
  received_at   REAL NOT NULL,
  processed     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS conv_state (
  sender     TEXT PRIMARY KEY,
  state      TEXT NOT NULL DEFAULT 'idle',
  order_json TEXT,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  sender         TEXT NOT NULL,
  sender_name    TEXT,
  product_id     TEXT NOT NULL,
  product_name   TEXT NOT NULL,
  price_label    TEXT NOT NULL,
  customer_name  TEXT,
  customer_phone TEXT,
  status         TEXT NOT NULL DEFAULT 'draft',
  created_at     REAL NOT NULL,
  updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_log (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  sender  TEXT NOT NULL,
  text    TEXT NOT NULL,
  action  TEXT,
  sent_at REAL NOT NULL,
  ok      INTEGER NOT NULL DEFAULT 1,
  error   TEXT
);
CREATE TABLE IF NOT EXISTS leads (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  sender       TEXT NOT NULL,
  sender_name  TEXT,
  kind         TEXT NOT NULL,
  details_json TEXT,
  status       TEXT NOT NULL DEFAULT 'nouveau',
  created_at   REAL NOT NULL
);
"""


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)


def queue_message(wa_message_id, sender, sender_name, body):
    """Queue an inbound message. Returns True if it was new, False if duplicate."""
    init_db()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO inbox (wa_message_id, sender, sender_name, body, received_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (wa_message_id, sender, sender_name, body, time.time()),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # duplicate delivery from Meta


def fetch_unprocessed(limit=50):
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM inbox WHERE processed = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_processed(row_id):
    with _connect() as conn:
        conn.execute("UPDATE inbox SET processed = 1 WHERE id = ?", (row_id,))


def get_state(sender):
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT state, order_json FROM conv_state WHERE sender = ?", (sender,)
        ).fetchone()
        if row is None:
            return {"state": "idle", "order": {}}
        order = json.loads(row["order_json"]) if row["order_json"] else {}
        return {"state": row["state"], "order": order}


def set_state(sender, state, order=None):
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conv_state (sender, state, order_json, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(sender) DO UPDATE SET state=excluded.state,"
            " order_json=excluded.order_json, updated_at=excluded.updated_at",
            (sender, state, json.dumps(order or {}), time.time()),
        )


def create_order(sender, sender_name, product_id, product_name, price_label):
    init_db()
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO orders (sender, sender_name, product_id, product_name,"
            " price_label, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
            (sender, sender_name, product_id, product_name, price_label, now, now),
        )
        return cur.lastrowid


def update_order(order_id, **fields):
    allowed = {"customer_name", "customer_phone", "status"}
    sets = [f"{k} = ?" for k in fields if k in allowed]
    if not sets:
        return
    with _connect() as conn:
        params = [fields[k] for k in fields if k in allowed] + [time.time(), order_id]
        conn.execute(
            f"UPDATE orders SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
            params,
        )


def get_draft_order(sender):
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE sender = ? AND status = 'draft'"
            " ORDER BY id DESC LIMIT 1",
            (sender,),
        ).fetchone()
        return dict(row) if row else None


def list_orders(status=None, limit=50):
    init_db()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def log_outbox(sender, text, action, ok=True, error=None):
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO outbox_log (sender, text, action, sent_at, ok, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (sender, text, action, time.time(), 1 if ok else 0, error),
        )


def create_lead(sender, sender_name, kind, details):
    """Store a lead for the human team. kind: 'cm' (Community Manager
    qualification) or 'escalade' (payment claim / unknown question)."""
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO leads (sender, sender_name, kind, details_json, status,"
            " created_at) VALUES (?, ?, ?, ?, 'nouveau', ?)",
            (sender, sender_name, kind,
             json.dumps(details or {}, ensure_ascii=False), time.time()),
        )
        return cur.lastrowid


def list_leads(kind=None, status=None, limit=50):
    init_db()
    query = "SELECT * FROM leads"
    clauses, params = [], []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    leads = []
    for row in rows:
        lead = dict(row)
        raw = lead.pop("details_json", None)
        try:
            lead["details"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            lead["details"] = {}
        leads.append(lead)
    return leads
