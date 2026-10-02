"""
Worker: drains the inbound queue, runs the agent brain, sends replies.

Usage:
  python worker.py --once        # single pass (good for cron every minute)
  python worker.py --loop 20     # loop forever, polling every 20s

Orders flagged "a_encaisser" are left for the human: the worker prints them
in its summary so a cron/notification layer can alert the coach. The summary
also lists the Community Manager leads and the escalations (payment claims,
unknown or complex questions) stored by the agent in the leads table.
"""

import argparse
import time

import agent
import db
import sender


def process_once():
    pending = db.fetch_unprocessed()
    results = []
    for msg in pending:
        sender_no = msg["sender"]
        name = msg.get("sender_name") or ""
        body = msg.get("body") or ""

        if body.startswith("[") and body.endswith("]"):
            # non-text message placeholder queued by the webhook
            reply = ("Je ne peux pas encore lire les photos, audios ou fichiers 😅 "
                     "Envoie-moi ton message en texte et je t'aide !")
            action = "none"
        else:
            out = agent.handle(sender_no, name, body)
            reply, action = out["reply"], out["action"]

        ok, detail = sender.send_message(sender_no, reply)
        db.log_outbox(sender_no, reply, action, ok=ok,
                      error=None if ok else detail)
        db.mark_processed(msg["id"])
        results.append({"sender": sender_no, "action": action, "sent": ok,
                        "detail": detail})
        who = name or sender_no
        print(f"[worker] {who}: action={action} sent={ok}")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", type=int, default=0,
                        help="poll interval in seconds (0 = no loop)")
    args = parser.parse_args()

    if args.once or args.loop <= 0:
        results = process_once()
        pending_payment = db.list_orders(status="a_encaisser")
        print(f"[worker] done: {len(results)} message(s) processed, "
              f"{len(pending_payment)} order(s) awaiting payment.")
        for o in pending_payment:
            print(f"  !! A ENCAISSER: #{o['id']} {o['product_name']} "
                  f"({o['price_label']}) - {o.get('customer_name')} "
                  f"{o.get('customer_phone')} via {o['sender']}")
        leads_cm = db.list_leads(kind="cm", status="nouveau")
        if leads_cm:
            print(f"[worker] {len(leads_cm)} Community Manager lead(s) "
                  f"waiting for a quote:")
            for lead in leads_cm:
                d = lead.get("details", {})
                print(f"  !! LEAD CM: #{lead['id']} "
                      f"{d.get('type_commerce', '?')} "
                      f"({d.get('quartier', '?')}) - reseaux: "
                      f"{d.get('reseaux', '?')} - besoin: "
                      f"{d.get('besoin', '?')} via {lead['sender']}")
        escalades = db.list_leads(kind="escalade", status="nouveau")
        if escalades:
            print(f"[worker] {len(escalades)} escalation(s) waiting for "
                  f"the team:")
            for lead in escalades:
                d = lead.get("details", {})
                print(f"  !! ESCALADE: #{lead['id']} {lead['sender']} : "
                      f"{d.get('raison', '?')} - "
                      f"{d.get('question') or d.get('message', '')}")
        return

    print(f"[worker] loop every {args.loop}s (Ctrl+C to stop)")
    while True:
        try:
            process_once()
        except Exception as exc:  # never let the loop die silently
            print(f"[worker] ERROR: {exc}")
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
