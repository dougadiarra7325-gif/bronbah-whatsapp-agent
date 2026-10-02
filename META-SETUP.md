# Configuration Meta — Bronbah Tech Assistant

Créée le 2026-10-02 (compte développeur « Mali Doug », vérification mobile terminée,
adresse dougadiarra315@gmail.com confirmée dans l'Espace Comptes).

| Élément | Valeur |
|---|---|
| Application | Bronbah Tech Assistant |
| App ID | 1589968782504354 |
| Portefeuille business | Bronbah Tech (business_id 2299472638119546) |
| WhatsApp Business Account ID | 2295805184602894 |
| Numéro de TEST Meta (gratuit) | +1 (555) 141-2202 |
| Phone number ID (test) | 1350960551434587 |
| Jeton d'accès | Non généré à ce stade — à générer au déploiement, uniquement via flux sécurisé (jamais dans le chat ni dans un fichier) |

## Règles
- Le vrai numéro +223 73255873 n'est PAS encore branché comme numéro WhatsApp API.
  Il ne le sera qu'après tests avec le numéro de test ET vérification du mode
  coexistence (le propriétaire garde WhatsApp Business sur son téléphone,
  ses catalogues et un accès complet).
- Aucun moyen de paiement Meta ajouté.

## Étapes restantes
1. Déployer sur Render (gratuit) : repo GitHub privé → Web Service →
   env `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN`, `INLINE_WORKER=1`.
2. Webhook Meta → `https://<nom>.onrender.com/webhook`, s'abonner aux messages.
3. Tester de bout en bout avec le numéro de test (+1 555…) : envoyer un message
   WhatsApp au numéro de test depuis le téléphone du propriétaire.
4. Brancher les notifications (commandes `a_encaisser`, leads CM, escalades).
5. Remplir `autres_catalogues` (catalog.json) avec les catalogues WhatsApp Business.
6. Brancher le vrai numéro après validation du propriétaire (mode coexistence vérifié).
