# Agent commercial WhatsApp — Bronbah Tech

Vendeur automatique sur WhatsApp via l'**API Cloud Meta**, pour le numéro
**+223 73255873**. Il accueille les clients avec un menu, présente les
catalogues, répond aux questions fréquentes, prend les commandes de
formations (nom + téléphone du client), qualifie les demandes Community
Manager et **ne confirme jamais un paiement** : dès que l'argent entre en
jeu, la commande est marquée `a_encaisser` (ou le cas est escaladé) pour
que l'équipe Bronbah Tech (humaine) prenne le relais.

Catalogue : `catalog.json` — les **11 formations** Bronbah Tech
(13 000 FCFA chacune : inscription 3 000 + formation 10 000) et les
**services Community Manager** (tarifs indicatifs, devis par l'équipe),
plus une section `autres_catalogues` (vide pour l'instant, à remplir avec
les catalogues de l'application WhatsApp Business du propriétaire).
Paiement **Wave ou Orange Money au 73255873**.

## Architecture

```
Meta (WhatsApp)                Ce serveur
──────────────                 ──────────
   │  POST /webhook               │
   │  (événements)                ▼
   │                     ┌─────────────────┐
   ├────────────────────▶│  server.py      │  vérifie la signature Meta (GET),
   │                     │  (FastAPI)      │  met les messages en file (SQLite)
   │                     └────────┬────────┘
   │                              │ file d'attente (table inbox)
   │                     ┌────────▼────────┐
   │                     │  worker.py      │  --once (cron) ou --loop,
   │                     │  + agent.py     │  ou thread intégrée (INLINE_WORKER=1)
   │                     └────────┬────────┘
   │                              │ décide la réponse (catalogue, FAQ,
   │                              │ commande, détection paiement)
   │                     ┌────────▼────────┐
   └─────────────────────┤  sender.py      │  POST https://graph.facebook.com/…
                         └─────────────────┘  (token + phone_number_id en env)
```

### Fichiers

| Fichier          | Rôle |
|------------------|------|
| `server.py`      | Webhook FastAPI : `GET /webhook` (vérification Meta), `POST /webhook` (réception, mise en file, 200 immédiat), `GET /health`, helpers dev (`/dev/*`, seulement si `DEV_MODE=1`), worker intégré en option (`INLINE_WORKER=1`) |
| `worker.py`      | Vide la file : `agent.handle()` → `sender.send_message()` → log. `--once` pour cron, `--loop N` en continu. Affiche dans son résumé les commandes `a_encaisser`, les leads Community Manager et les escalades |
| `agent.py`       | Le « cerveau » (fonctions pures, déterministe, en français) : accueil avec menu (formations / Community Manager / catalogue), prix et détails des 11 formations, tunnel de commande (produit → nom → téléphone → `a_encaisser`), qualification Community Manager → lead transmis à l'équipe pour le devis, détection des mots-clés paiement → **ne confirme jamais d'argent**, questions inconnues ou complexes → escalade à l'équipe |
| `sender.py`      | Envoi via l'API Cloud Meta. `DRY_RUN=1` = log au lieu d'appeler Meta (tests) |
| `db.py`          | SQLite : `inbox` (file, dédupliquée par `wa_message_id`), `conv_state` (état par client), `orders` (`draft` / `a_encaisser` / `done` / `cancelled`), `leads` (`cm` = demande Community Manager qualifiée, `escalade` = paiement annoncé ou question transmise à l'équipe), `outbox_log` |
| `catalog.json`   | Les 11 formations (13 000 FCFA), les services Community Manager (tarifs indicatifs), les moyens de paiement (Wave / Orange Money au 73255873) et `autres_catalogues` (à remplir plus tard) |
| `.env.example`   | Modèle des variables d'environnement (aucun secret dans le code) |

## Variables d'environnement

| Variable | Obligatoire | Description |
|----------|-------------|-------------|
| `WA_ACCESS_TOKEN` | prod | Token d'accès permanent (system user) de l'appli Meta |
| `WA_PHONE_NUMBER_ID` | prod | ID du numéro (WhatsApp > API Setup, dashboard Meta) |
| `WA_API_VERSION` | non | Version Graph API, défaut `v21.0` |
| `WA_VERIFY_TOKEN` | oui | Chaîne **que tu inventes** ; doit être identique dans la config webhook Meta |
| `PORT` | non | Port du serveur, défaut `8000` |
| `WA_DB_PATH` | non | Chemin SQLite, défaut `./data/agent.db` |
| `DRY_RUN` | non | `1` = n'appelle pas Meta, log les envois (tests) |
| `DEV_MODE` | non | `1` = active `/dev/simulate` et `/dev/orders` (**jamais en prod**) |
| `INLINE_WORKER` | non | `1` = traite la file dans le même processus (toutes les 15 s) — recommandé sur hébergeur gratuit à instance unique |

## Démarrage en local

```bash
cd ~/workspace/whatsapp-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # puis renseigne WA_VERIFY_TOKEN (le reste viendra de Meta)

# Terminal 1 : serveur webhook
WA_VERIFY_TOKEN=mon-token-secret DEV_MODE=1 DRY_RUN=1 INLINE_WORKER=1 \
  .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000

# Terminal 2 : simuler un client (DEV_MODE=1 requis)
curl -X POST http://127.0.0.1:8000/dev/simulate -H "Content-Type: application/json" \
  -d '{"sender":"22370000000","name":"Awa","text":"Bonjour"}'
# -> la réponse est traitée automatiquement (INLINE_WORKER=1), visible dans DRY_RUN
# Voir les commandes : curl http://127.0.0.1:8000/dev/orders
```

Variante sans worker intégré (2 processus, même machine) :

```bash
# Terminal 1 : serveur seul
WA_VERIFY_TOKEN=... DEV_MODE=1 DRY_RUN=1 .venv/bin/uvicorn server:app --port 8000
# Terminal 2 : worker en boucle
DRY_RUN=1 .venv/bin/python worker.py --loop 15
# ou en cron toutes les minutes : .venv/bin/python worker.py --once
```

Test de la poignée de main Meta (vérification webhook) :

```bash
# doit renvoyer CHALLENGE_XYZ :
curl "http://127.0.0.1:8000/webhook?hub.mode=subscribe&hub.verify_token=mon-token-secret&hub.challenge=CHALLENGE_XYZ"
# mauvais token -> 403
```

## Branchement Meta (à faire par le propriétaire du compte)

1. **developers.facebook.com** → compte développeur → créer une appli (type *Business*) → ajouter le produit **WhatsApp**.
2. Créer le **compte WhatsApp Business (WABA)** et y **ajouter le +223 73255873**.
   ⚠️ Objectif : brancher le numéro en **mode coexistence**, pour que le
   propriétaire garde son application WhatsApp Business sur son téléphone
   (ses catalogues y sont déjà) pendant que l'agent répond via l'API.
   **Vérifier que la coexistence est possible pour ce numéro AVANT toute
   bascule** : sans coexistence, un numéro migré vers l'API Cloud ne reçoit
   plus les messages dans l'application. Commencer les essais avec le
   numéro de test fourni par Meta (voir « Ce qui reste à faire »).
3. **Vérification de l'entreprise** Meta (documents, 1 à 3 jours en général).
4. Récupérer : **ID du numéro** (`WA_PHONE_NUMBER_ID`), **token permanent** (`WA_ACCESS_TOKEN`, via un utilisateur système), choisir un **verify token** (`WA_VERIFY_TOKEN`).
5. Dans l'appli Meta → WhatsApp → Configuration → **Webhook** : URL `https://<ton-domaine>/webhook`, verify token identique, s'abonner au champ **messages**. Meta appellera le `GET /webhook` pour vérifier.
6. Ajouter un **moyen de paiement** sur le compte Meta : depuis le 1er oct. 2026, au-delà de 1000 messages de service gratuits/mois/numéro, Meta facture au message, et **sans moyen de paiement les messages ne sont plus livrés**.

## URL HTTPS publique — enquête et recommandation

Enquête menée le 2026-09-30 sur la VM de dev :

- ❌ **La VM n'a pas d'IP publique** (interfaces privées `198.19.0.2` / ULA IPv6, sortie via NAT/proxy) : Meta ne peut pas l'atteindre directement.
- ❌ **Tunnel cloudflared** (`*.trycloudflare.com`) : installé mais **bloqué** — le proxy de sortie refuse la connexion (`tls: first record does not look like a TLS handshake`, API `api.trycloudflare.com` → 403). De toute façon éphémère : inutilisable en production.
- ✅ **Sortie vers `graph.facebook.com` fonctionnelle** : l'envoi de messages via l'API Cloud marchera depuis n'importe où, mais la **réception (webhook) exige un hébergeur avec HTTPS public stable**.

**Recommandation : [Render](https://render.com) (offre gratuite)** — le plus simple :
1. Pousser ce dossier dans un repo GitHub privé.
2. Render → *New +* → *Web Service* → connecter le repo.
   - Build : `pip install -r requirements.txt`
   - Start : `uvicorn server:app --host 0.0.0.0 --port $PORT`
3. Variables d'env sur Render : `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN`, `INLINE_WORKER=1` (traite la file dans le même processus — indispensable car le plan gratuit = 1 seule instance, et SQLite est un fichier local).
4. URL obtenue : `https://<nom>.onrender.com` → à renseigner comme webhook Meta (`https://<nom>.onrender.com/webhook`).
5. ⚠️ Le plan gratuit **met le service en veille après 15 min d'inactivité** : le premier message après une pause met ~30-60 s à être traité (Meta retente la livraison, rien n'est perdu). Acceptable pour démarrer ; passer au plan payant (~7 $/mois) si le volume augmente.

**Alternative : [Fly.io](https://fly.io)** (quota gratuit, ne dort pas) — un peu plus de configuration (`fly launch`, Dockerfile), à envisager si la latence de réveil de Render gêne.

## Comportement de l'agent (règles métier)

- **Accueil** : message de bienvenue avec menu — 1 = formations, 2 = Community Manager, 3 = catalogue complet.
- **Formations / prix / contenu** : réponses directes depuis `catalog.json` (13 000 FCFA : inscription 3 000 + formation 10 000 ; inclus : cours PDF, exercices, corrections, projet final, suivi avec Adrian), sans inventer de programme détaillé.
- **Commande d'une formation** : produit → nom → téléphone (≥ 8 chiffres) → commande `a_encaisser` + message « paie par Wave ou Orange Money au 73255873 ; dès que l'équipe confirme la réception, tu reçois ton PDF ». Le PDF est envoyé par un humain, après confirmation du paiement.
- **Community Manager** : présente les packs (tarifs **indicatifs**), pose 4 questions de qualification (type de commerce, quartier à Bamako, réseaux actuels, besoin) puis enregistre un lead `cm` transmis à l'équipe, qui prépare le devis gratuit. Jamais de devis ferme par l'agent.
- **Paiement** (`payé`, `Wave`, `Orange Money`, `transfert`, `capture`…) : **jamais de confirmation d'argent** ; la commande passe en `a_encaisser`, et un paiement annoncé sans commande est escaladé à l'équipe.
- **Question inconnue ou complexe** : réponse polie (« je transmets à l'équipe ») + escalade enregistrée, visible dans le résumé du worker avec les leads CM et les `a_encaisser`.
- **Annulation** : `annuler` à tout moment annule le brouillon ou la qualification en cours.
- **Non-texte** (audio, image…) : réponse polie demandant un message texte.
- Doublons Meta (même `wa_message_id`) ignorés ; statuts de livraison (`statuses`) ignorés.

## Ce qui reste à faire

- [ ] Branchement Meta **en cours** : d'abord brancher et tester avec le **numéro de test** fourni par Meta (webhook, envois, réponses de l'agent), sans toucher au vrai numéro
- [ ] Bascule du vrai numéro **+223 73255873** : uniquement en **mode coexistence**, pour que le propriétaire garde son application WhatsApp Business (et ses catalogues) sur son téléphone — **vérifier la faisabilité de la coexistence pour ce numéro avant toute bascule**
- [ ] Branchement Meta réel (compte, token permanent, webhook, moyen de paiement) — côté propriétaire
- [ ] Déploiement sur Render (ou Fly.io) + configuration du webhook Meta
- [ ] Notifications à l'équipe : brancher la sortie du worker (`a_encaisser` + leads CM + escalades, via `db.list_orders('a_encaisser')` et `db.list_leads(...)`) vers un message au propriétaire
- [ ] Remplir `autres_catalogues` dans `catalog.json` avec les catalogues de l'application WhatsApp Business du propriétaire
- [ ] Durcir la prod : `DEV_MODE=0`, `DRY_RUN=0`, rate-limit basique, logs persistants
- [ ] Enrichir l'agent : relances panier abandonné, FAQ étoffée (validée par l'équipe)
- [ ] Migrer SQLite → Postgres si plusieurs instances ou volume important

## Sécurité

- Aucun token/secret dans le code — tout passe par les variables d'environnement.
- `DEV_MODE` et `DRY_RUN` à `0` en production.
- Le `WA_VERIFY_TOKEN` doit être une chaîne aléatoire longue, identique côté Meta et côté serveur.
