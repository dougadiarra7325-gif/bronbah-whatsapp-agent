"""
Conversation logic of the WhatsApp sales agent (Bronbah Tech).

Pure functions (no network I/O): given a sender + message text, decide the
reply and the action. Conversation/order state lives in db.py.

Golden rules:
  - The agent NEVER confirms a payment, transfer or receipt of money.
    When money is claimed, the order is flagged "a_encaisser" (to be
    collected) or the case is escalated to the team so a human takes over.
    The training PDF is only sent by a human, after they confirm payment.
  - Community Manager prices are INDICATIVE only: the agent presents the
    packs and qualifies the lead, but never gives a firm quote — the team
    prepares the free quote.
  - Unknown or complex questions are escalated to the team: the client
    gets a polite reply and a lead of kind "escalade" is stored so the
    worker summary shows it.
"""

import json
import os
import re
import unicodedata

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE_DIR, "catalog.json"), encoding="utf-8") as f:
    CATALOG = json.load(f)

FORMATIONS = {f["id"]: f for f in CATALOG["formations"]}
CM_PACKS = {s["id"]: s for s in CATALOG["services_cm"]}
PAY_NUMBER = CATALOG["payment"]["number"]            # "73255873"
PAY_LABEL = "Wave ou Orange Money au " + PAY_NUMBER
FORMATION_INCLUDES = "cours PDF complet, exercices pratiques, corrections, " \
                     "petit projet final et suivi étape par étape avec Adrian"

# formation matchers: (substrings, word-tokens, product_id), checked in order
FORMATION_MATCHERS = [
    (["c++", "cpp", "c plus plus"], [], "formation-cpp"),
    (["javascript", "java script"], ["js"], "formation-javascript"),
    (["intelligence artificielle"], ["ia"], "formation-ia"),
    (["chatgpt", "chat gpt"], [], "formation-chatgpt"),
    (["creation de sites web", "creation de site web", "creation de site",
      "sites web", "site web", "site internet"], [], "formation-sites-web"),
    (["capcut", "cap cut"], [], "formation-capcut"),
    (["canva"], [], "formation-canva"),
    (["langage c", "programmation c"], [], "formation-c"),
    ([], ["html"], "formation-html"),
    ([], ["css"], "formation-css"),
    ([], ["php"], "formation-php"),
]
# a bare "c" only means the C language with a learning/order context around
C_CONTEXT = ["formation", "cours", "langage", "programmation", "apprendre",
             "commander", "veux", "prix", "combien", "inscription"]

# CM pack matchers: (substrings, word-tokens, pack_id)
CM_PACK_MATCHERS = [
    (["lancement"], [], "cm-pack-lancement"),
    (["essentiel"], [], "cm-gestion-essentiel"),
    (["gestion pro"], ["pro"], "cm-gestion-pro"),
    (["premium"], [], "cm-gestion-premium"),
    (["visuel"], [], "cm-visuels"),
]

GREETINGS = ["bonjour", "salut", "hello", "bonsoir", "coucou", "slt", "yo"]
CATALOG_WORDS = [
    "produit", "produits", "propose", "proposez", "vendez", "vendre",
    "liste", "tarif", "tarifs", "prix", "combien", "cout", "coût",
    "dispo", "disponible", "offre", "offres",
]
ORDER_WORDS = [
    "commande", "commander", "veux", "veut", "voudrais", "interesse",
    "intéressé", "acheter", "achete", "achète", "prendre", "prends",
    "souscrire", "inscrire", "inscription", "partant", "je m'inscris",
    "apprendre",
]
PRICE_WORDS = ["combien", "prix", "tarif", "cout", "coût"]
CONTENT_WORDS = ["contenu", "programme", "c'est quoi", "keske", "qu'est-ce",
                 "parle moi", "décris", "detail", "détail", "duree", "durée",
                 "combien de temps"]
# money is mentioned: generic words (a payment question...)
PAYMENT_GENERIC = [
    "wave", "orange", "paiement", "payer", "transfert", "argent", "depot",
    "dépôt", "mobile money", "regler", "régler",
]
# ...vs a payment CLAIM ("I paid / I sent"): never confirm, hand to the team
PAYMENT_CLAIM = [
    "j'ai paye", "j ai paye", "ai paye", "paye par", "paye via", "paye avec",
    "c'est paye", "deja paye", "transfere", "transféré", "capture", "recu",
    "reçu", "j'ai envoye", "j ai envoye", "paiement fait", "paiement effectue",
    "paiement effectué", "paiement envoye",
]
CM_WORDS = [
    "community", "community manager", "reseaux sociaux", "gerer ma page",
    "gerer mes reseaux", "gerer mon compte", "gestion de page",
    "gestion de compte", "marketing", "publicite", "page facebook",
    "page instagram", "compte instagram", "visuel", "visuels",
]
CANCEL_WORDS = ["annule", "annuler", "laisse tomber", "oublie", "stop"]
THANKS_WORDS = ["merci", "parfait", "d'accord", "entendu", "ok"]

# CM qualification: (answer key, question asked to get it), in order
CM_FLOW = [
    ("type_commerce",
     "1️⃣ C'est quel type de commerce ? (boutique, restaurant, salon, autre…)"),
    ("quartier",
     "2️⃣ Tu es dans quel quartier à Bamako ?"),
    ("reseaux",
     "3️⃣ Tu as déjà quels réseaux sociaux ? (Facebook, Instagram, TikTok, aucun…)"),
    ("besoin",
     "4️⃣ Tu as besoin de quoi exactement ? (publications, visuels, "
     "réponses aux clients, tout gérer…)"),
]


def _norm(text):
    text = (text or "").lower()
    text = "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return text


def _has_any(normed, words):
    return any(w in normed for w in words)


def _has_token(normed, token):
    return re.search(rf"\b{re.escape(token)}\b", normed) is not None


def match_formations(normed):
    """Return the list of formation ids clearly named in the message."""
    hits = []
    for subs, tokens, pid in FORMATION_MATCHERS:
        if _has_any(normed, subs) or any(_has_token(normed, t) for t in tokens):
            if pid not in hits:
                hits.append(pid)
    # Bare "c" for the C language, only with a learning/order context.
    # Apostrophe words are removed first so "c'est" is not read as "c",
    # and a spaced "c est" is just as much "c'est", not the language.
    cleaned = re.sub(r"\b\w+'\w*", " ", normed)
    cleaned = re.sub(r"\bc\s+est\b", " ", cleaned)
    if ("formation-c" not in hits and "formation-cpp" not in hits
            and _has_token(cleaned, "c")
            and _has_any(normed, C_CONTEXT)):
        hits.append("formation-c")
    return hits


def match_cm_pack(normed):
    """Return a CM pack id if one is clearly named, else None."""
    for subs, tokens, pid in CM_PACK_MATCHERS:
        if _has_any(normed, subs) or any(_has_token(normed, t) for t in tokens):
            return pid
    return None


def _has_cm(normed):
    return (_has_any(normed, CM_WORDS) or _has_token(normed, "cm")
            or match_cm_pack(normed) is not None)


# ---------------------------------------------------------------- texts

def welcome_text(sender_name):
    first = (sender_name or "").strip().split(" ")[0]
    hello = f"Salut {first} ! 👋" if first else "Salut ! 👋"
    return (f"{hello} Bienvenue chez Bronbah Tech.\n"
            "Voici ce que je peux faire pour toi :\n"
            "1️⃣ Formations informatique & tech — 13 000 FCFA "
            "(HTML, CSS, JavaScript, PHP, C, C++, sites web, CapCut, IA, "
            "ChatGPT, Canva)\n"
            "2️⃣ Community Manager pour ton commerce — devis gratuit\n"
            "3️⃣ Voir tout le catalogue\n"
            "Réponds 1, 2 ou 3, ou dis-moi directement ce que tu cherches 😊")


def formations_text():
    lines = ["🎓 Nos formations — 13 000 FCFA chacune "
             "(inscription 3 000 + formation 10 000) :", ""]
    for f in CATALOG["formations"]:
        lines.append(f"{f['emoji']} {f['name']}")
    lines += [
        "",
        f"Chaque formation comprend : {FORMATION_INCLUDES}.",
        "📩 Le PDF est envoyé après confirmation du paiement par l'équipe.",
        "Dis-moi le nom d'une formation pour les détails ou pour t'inscrire 😊",
    ]
    return "\n".join(lines)


def cm_packs_text():
    lines = ["📣 Nos offres Community Manager pour les commerces de Bamako",
             "(tarifs indicatifs — le devis exact est gratuit et préparé "
             "par l'équipe) :", ""]
    for s in CATALOG["services_cm"]:
        lines.append(f"{s['emoji']} {s['name']} — {s['price_label']}")
    return "\n".join(lines)


def catalog_text():
    lines = ["Voici le catalogue Bronbah Tech 💼", "", formations_text(), "",
             cm_packs_text()]
    others = CATALOG.get("autres_catalogues") or []
    if others:
        lines += ["", "📚 Autres catalogues :"]
        for item in others:
            label = item.get("name") or item.get("title") if isinstance(item, dict) \
                else str(item)
            if label:
                lines.append(f"• {label}")
    lines += [
        "",
        f"💳 Paiement : {PAY_LABEL}",
        "Écris « community » pour un devis Community Manager gratuit 😊",
    ]
    return "\n".join(lines)


def describe_formation(pid):
    f = FORMATIONS[pid]
    return (f"{f['emoji']} {f['name']} — {f['price_label']} "
            f"(inscription 3 000 + formation 10 000)\n"
            f"{f['description']}.\n"
            f"La formation comprend : {FORMATION_INCLUDES}.\n"
            f"📩 Le PDF est envoyé après confirmation de ton paiement par "
            f"l'équipe ({PAY_LABEL}).")


def choices_text():
    return ("Avec plaisir ! Qu'est-ce qui t'intéresse ?\n"
            "🎓 Une formation (13 000 FCFA) : dis le nom — HTML, CSS, "
            "JavaScript, PHP, C, C++, Création de sites web, CapCut, IA, "
            "ChatGPT ou Canva.\n"
            "📣 Ou écris « community » pour les offres Community Manager "
            "(devis gratuit).")


def _latest_collectable(sender):
    for o in db.list_orders(status="a_encaisser", limit=20):
        if o["sender"] == sender:
            return o
    return None


# ---------------------------------------------------------------- flows

def _start_order(sender, sender_name, product_id):
    f = FORMATIONS[product_id]
    order_id = db.create_order(sender, sender_name, product_id, f["name"],
                               f["price_label"])
    db.set_state(sender, "awaiting_name",
                 {"product_id": product_id, "order_id": order_id})
    return {"reply": (f"Super choix ! 🎉 {f['emoji']} {f['name']} — "
                      f"{f['price_label']}.\n"
                      "Pour enregistrer ta commande, c'est quoi ton nom ?"),
            "action": "order_started"}


def _start_cm(sender, sender_name, normed):
    answers = {}
    pack = match_cm_pack(normed)
    if pack:
        answers["pack_souhaite"] = CM_PACKS[pack]["name"]
    db.set_state(sender, "cm_qualif", {"flow": "cm", "step": 0,
                                       "answers": answers})
    return {"reply": (cm_packs_text() + "\n\n"
                      "Pour préparer ton devis gratuit, je te pose 4 petites "
                      "questions.\n" + CM_FLOW[0][1]),
            "action": "cm_started"}


def _cm_answer(sender, sender_name, text, normed, order):
    if "catalogue" in normed or normed.strip() == "menu":
        db.set_state(sender, "idle", {})
        return {"reply": catalog_text(), "action": "catalog"}
    answers = dict(order.get("answers") or {})
    step = int(order.get("step") or 0)
    answer = (text or "").strip()
    if len(answer) < 2:
        return {"reply": "Je n'ai pas bien compris 😅 " + CM_FLOW[step][1],
                "action": "cm_qualif"}
    answers[CM_FLOW[step][0]] = answer
    if step < len(CM_FLOW) - 1:
        db.set_state(sender, "cm_qualif",
                     {"flow": "cm", "step": step + 1, "answers": answers})
        return {"reply": "Noté 👍 " + CM_FLOW[step + 1][1],
                "action": "cm_qualif"}
    # last answer: store the lead for the team (they make the quote)
    db.create_lead(sender, sender_name, "cm", answers)
    db.set_state(sender, "idle", {})
    recap = (f"{answers.get('type_commerce', '?')} à "
             f"{answers.get('quartier', '?')} — réseaux : "
             f"{answers.get('reseaux', '?')} — besoin : "
             f"{answers.get('besoin', '?')}")
    if answers.get("pack_souhaite"):
        recap += f" — pack visé : {answers['pack_souhaite']}"
    return {"reply": ("Merci ! ✅ J'ai tout noté : " + recap + ".\n"
                      "Je transmets à l'équipe Bronbah Tech : elle te "
                      "prépare un devis gratuit et personnalisé (les tarifs "
                      "affichés sont indicatifs) et te recontacte ici. 🙏"),
            "action": "lead_cm"}


def _escalate(sender, sender_name, text, reason):
    db.create_lead(sender, sender_name, "escalade",
                   {"raison": reason, "question": (text or "").strip()})
    return {"reply": ("Bonne question 👍 Je n'ai pas la réponse exacte, "
                      "alors je transmets à l'équipe Bronbah Tech qui te "
                      "répondra directement.\n"
                      "En attendant, je peux te montrer les formations, le "
                      "catalogue ou les offres Community Manager."),
            "action": "escalated"}


# ---------------------------------------------------------------- main

def handle(sender, sender_name, text):
    """
    Returns dict(reply=str, action=str).
    action is one of: none, welcome, catalog, product_info, price_info,
    payment_info, order_started, order_cancelled, cancelled,
    order_completed, payment_flagged, cm_started, cm_qualif, lead_cm,
    escalated.
    """
    normed = _norm(text)
    conv = db.get_state(sender)
    state, order = conv["state"], conv["order"]

    # --- cancel works in any state --------------------------------------
    if _has_any(normed, CANCEL_WORDS) and state != "idle":
        draft = db.get_draft_order(sender)
        if draft:
            db.update_order(draft["id"], status="cancelled")
        db.set_state(sender, "idle", {})
        if state == "cm_qualif":
            return {"reply": ("Pas de souci, j'arrête là 😊 L'équipe reste "
                              "disponible si tu as besoin d'un devis ou "
                              "d'une info."),
                    "action": "cancelled"}
        return {"reply": ("Pas de souci, commande annulée. "
                          "Dis-moi si tu as besoin d'autre chose 😊"),
                "action": "order_cancelled"}

    # --- money: NEVER confirm; flag the order or escalate ----------------
    has_claim = _has_any(normed, PAYMENT_CLAIM)
    has_pay_words = has_claim or _has_any(normed, PAYMENT_GENERIC)
    draft = db.get_draft_order(sender)
    if draft and has_pay_words:
        db.update_order(draft["id"], status="a_encaisser")
        db.set_state(sender, "idle", {})
        return {"reply": (f"Bien noté ✅ Ta commande ({draft['product_name']} "
                          f"— {draft['price_label']}) est enregistrée.\n"
                          "Je transmets à l'équipe : c'est elle qui vérifie "
                          "la réception du paiement et qui te confirme "
                          "directement. 🙏"),
                "action": "payment_flagged"}
    if has_claim:
        details = {"raison": "Paiement annoncé par le client",
                   "message": (text or "").strip()}
        last = _latest_collectable(sender)
        if last:
            details["commande"] = (f"{last['product_name']} — "
                                   f"{last['price_label']}")
        db.create_lead(sender, sender_name, "escalade", details)
        db.set_state(sender, "idle", {})
        return {"reply": ("Merci ! 🙏 Je transmets tout de suite à l'équipe : "
                          "c'est elle qui vérifie et confirme les paiements, "
                          "jamais moi. Elle te répond directement."),
                "action": "payment_flagged"}

    # --- Community Manager qualification flow ----------------------------
    if state == "cm_qualif":
        return _cm_answer(sender, sender_name, text, normed, order)

    # --- order flow states ----------------------------------------------
    if state == "awaiting_product":
        pids = match_formations(normed)
        if len(pids) == 1:
            return _start_order(sender, sender_name, pids[0])
        if len(pids) > 1:
            names = " ou ".join(FORMATIONS[p]["name"] for p in pids)
            return {"reply": f"Tu veux {names} ? Dis-moi laquelle 🙂",
                    "action": "none"}
        if _has_cm(normed):
            return _start_cm(sender, sender_name, normed)
        if "catalogue" in normed:
            db.set_state(sender, "idle", {})
            return {"reply": catalog_text(), "action": "catalog"}
        return {"reply": ("Je n'ai pas bien compris. " + choices_text()),
                "action": "none"}

    if state == "awaiting_name":
        name = (text or "").strip()
        if len(name) < 2 or _has_any(normed, ORDER_WORDS + CATALOG_WORDS) \
                or _has_cm(normed):
            return {"reply": "C'est quoi ton nom s'il te plaît ? 🙂",
                    "action": "none"}
        order["customer_name"] = name
        db.set_state(sender, "awaiting_phone", order)
        draft = db.get_draft_order(sender)
        if draft:
            db.update_order(draft["id"], customer_name=name)
        f = FORMATIONS[order["product_id"]]
        return {"reply": (f"Enchanté {name} ! 👍 Quel est ton numéro de "
                          f"téléphone pour ta commande {f['emoji']} "
                          f"{f['name']} ?"),
                "action": "none"}

    if state == "awaiting_phone":
        digits = re.sub(r"\D", "", text or "")
        if len(digits) < 8:
            return {"reply": ("Hmm, ce numéro semble incomplet. "
                              "Tu peux me le renvoyer ? 📱"),
                    "action": "none"}
        order["customer_phone"] = digits
        draft = db.get_draft_order(sender)
        f = FORMATIONS[order["product_id"]]
        name = order.get("customer_name", "")
        if draft:
            db.update_order(draft["id"], customer_phone=digits,
                            status="a_encaisser")
        db.set_state(sender, "idle", {})
        reply = (f"Merci {name} ! ✅\nTa commande est enregistrée :\n"
                 f"• {f['emoji']} {f['name']} — {f['price_label']}\n\n"
                 f"Pour la suite : paie par {PAY_LABEL}. Dès que l'équipe "
                 "confirme la réception de ton paiement, tu reçois ton PDF "
                 "et ton suivi commence avec Adrian. 🙏")
        return {"reply": reply, "action": "order_completed"}

    # --- idle: detect intent ---------------------------------------------
    stripped = normed.strip()

    # menu shortcuts from the welcome message
    if stripped in ("1", "2", "3"):
        if stripped == "1":
            return {"reply": formations_text(), "action": "catalog"}
        if stripped == "2":
            return _start_cm(sender, sender_name, normed)
        return {"reply": catalog_text(), "action": "catalog"}

    pids = match_formations(normed)

    # explicit order intent about a formation ("je veux...", "je commande...")
    if _has_any(normed, ORDER_WORDS) and pids:
        if len(pids) > 1:
            names = " ou ".join(FORMATIONS[p]["name"] for p in pids)
            return {"reply": f"Avec plaisir ! Tu veux {names} ? "
                             "Dis-moi laquelle 🙂",
                    "action": "none"}
        return _start_order(sender, sender_name, pids[0])

    # price question about a specific formation
    if len(pids) == 1 and _has_any(normed, PRICE_WORDS):
        f = FORMATIONS[pids[0]]
        return {"reply": (f"{f['emoji']} {f['name']} : {f['price_label']} "
                          f"(inscription 3 000 + formation 10 000).\n"
                          f"Elle comprend : {FORMATION_INCLUDES}.\n"
                          f"📩 PDF envoyé après confirmation du paiement "
                          f"par l'équipe ({PAY_LABEL}).\n"
                          "Tu veux t'inscrire ? 😊"),
                "action": "price_info"}

    # "what is it / what's inside" question about a formation
    if len(pids) == 1 and _has_any(normed, CONTENT_WORDS):
        return {"reply": describe_formation(pids[0]), "action": "product_info"}

    # bare formation mention -> describe softly, don't auto-start an order
    if len(pids) == 1:
        return {"reply": describe_formation(pids[0]) +
                "\n\nTu veux t'inscrire ? 🙂",
                "action": "product_info"}

    if len(pids) > 1:
        names = " ou ".join(FORMATIONS[p]["name"] for p in pids)
        return {"reply": f"Tu parles de {names} ? Dis-moi laquelle 🙂",
                "action": "none"}

    # Community Manager intent (packs, prices, qualification -> lead)
    if _has_cm(normed):
        return _start_cm(sender, sender_name, normed)

    # full catalog asked explicitly
    if "catalogue" in normed:
        return {"reply": catalog_text(), "action": "catalog"}

    # generic order intent, no product named yet
    if _has_any(normed, ORDER_WORDS):
        db.set_state(sender, "awaiting_product", {})
        return {"reply": choices_text(), "action": "order_started"}

    # generic payment question (methods, how to pay) - info only
    if has_pay_words:
        return {"reply": (f"Pour payer, c'est simple : {PAY_LABEL}. 💳\n"
                          "C'est toujours l'équipe qui confirme la réception "
                          "de ton paiement ; après confirmation, tu reçois "
                          "ton PDF de formation.\n"
                          "Tu veux commander quelque chose ?"),
                "action": "payment_info"}

    # greeting (or empty message) -> welcome menu
    if not stripped or _has_any(normed, GREETINGS):
        return {"reply": welcome_text(sender_name), "action": "welcome"}

    # prices / offers asked without a product -> full catalog
    if _has_any(normed, CATALOG_WORDS):
        return {"reply": catalog_text(), "action": "catalog"}

    # "formation" / "cours" mentioned but no known subject -> the list
    if "formation" in normed or "cours" in normed:
        return {"reply": ("Voici les formations disponibles chez Bronbah "
                          "Tech :\n\n" + formations_text()),
                "action": "catalog"}

    # short thank-you / acknowledgement -> friendly close, no escalation
    if _has_any(normed, THANKS_WORDS) and len(stripped) < 30:
        return {"reply": ("Avec plaisir ! 😊 Dis-moi si tu as besoin "
                          "d'autre chose."),
                "action": "none"}

    # unknown or complex question -> polite reply + escalation to the team
    return _escalate(sender, sender_name, text,
                     "Question non reconnue ou complexe")
