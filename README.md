# Prono70 — Pronostics football automatiques

Application qui affiche chaque jour les **70 meilleures opportunités** de matchs de
football, avec un pronostic calculé par un **modèle statistique** (Elo + forme
récente + confrontations directes) — **aucune IA générative**, **aucune clé API**.

## Sources de données (100% gratuites, sans clé)

| Source | Rôle |
|---|---|
| [openfootball/football.json](https://github.com/openfootball/football.json) | Calendriers + résultats des grandes ligues (auto-mis à jour chaque jour) |
| [openfootball/worldcup.json](https://github.com/openfootball/worldcup.json) | Compétitions internationales quand elles sont actives |
| [luukhopman/football-logos](https://github.com/luukhopman/football-logos) | Logos des équipes + validation des noms |

## Comment ça marche

1. Un script Python (`scripts/build_data.py`) récupère les calendriers et résultats,
   calcule un rating Elo pour chaque équipe à partir des résultats réels, puis
   génère un pronostic (1 / Nul / 2 + Over/Under + BTTS) avec un score de confiance.
2. Il sélectionne les 70 matchs du jour avec la plus forte confiance et écrit
   `docs/data/today.json`.
3. Il compare aussi automatiquement les pronostics passés au score final réel une
   fois les matchs joués, et met à jour `docs/data/history.json` (taux de réussite
   honnête, pas de triche possible : tout est recalculé depuis les résultats bruts).
4. Un workflow **GitHub Actions** exécute ce script chaque jour à 6h00 UTC et publie
   les fichiers JSON mis à jour.
5. La page `docs/index.html` (servie gratuitement par **GitHub Pages**) lit ces
   fichiers JSON et affiche l'interface (onglets Prono / Historique, cartes 3D,
   vue tableau, analyse détaillée par match).

Aucun serveur à payer, aucune clé à gérer : tout tourne sur l'infrastructure
gratuite de GitHub.

## Déploiement (10 minutes)

1. **Crée un nouveau dépôt GitHub** (public ou privé), et mets-y tout le contenu
   de ce dossier (glisser-déposer sur github.com fonctionne, ou `git push`).
2. Va dans **Settings → Pages** de ton dépôt :
   - Source : "Deploy from a branch"
   - Branch : `main`, dossier `/docs`
   - Sauvegarde. Ton site sera disponible sous quelques minutes à une adresse du
     type `https://<ton-pseudo>.github.io/<nom-du-depot>/`.
3. Va dans l'onglet **Actions** de ton dépôt, ouvre le workflow
   "Mise à jour quotidienne des pronostics", clique sur **Run workflow** pour le
   lancer une première fois manuellement (sinon il attend son horaire de 6h UTC).
4. Une fois le workflow terminé (1-2 minutes), rafraîchis ton site : les 70
   meilleures opportunités du jour doivent apparaître.

Ensuite, plus rien à faire : le workflow s'exécute automatiquement chaque jour et
committe les nouvelles données. Le site les affiche toujours à jour, sans que tu
aies besoin de revenir dans cette conversation.

## Pour aller plus loin

- **Changer l'heure de mise à jour** : modifie la ligne `cron` dans
  `.github/workflows/update.yml` (l'heure est en UTC).
- **Ajouter des ligues** : complète la liste `LEAGUES` en haut de
  `scripts/build_data.py` avec d'autres codes `pays.division` disponibles sur
  openfootball/football.json.

---

## ProLab — l'espace payant en direct (activation admin)

ProLab ajoute : des comptes utilisateurs (email/mot de passe), un accès activé
manuellement par toi (l'admin), et une analyse **en direct** des matchs en cours
(score, minute, probabilités recalculées en continu par un modèle statistique
de Poisson — toujours sans IA générative).

### 1. Créer le projet Supabase (gratuit)

1. Va sur [supabase.com](https://supabase.com) → crée un compte → **New project** (gratuit).
2. Une fois le projet créé : **SQL Editor** → colle le contenu de `sql/schema.sql`
   → **Run**. Ça crée les tables `profiles` (comptes + drapeau ProLab) et
   `live_matches` (les données en direct), avec des règles de sécurité (RLS) qui
   garantissent qu'un utilisateur ne voit les matchs en direct QUE si tu as
   activé son compte.
3. **Project Settings → API** : récupère le **Project URL** et la clé **anon public**.
   Colle-les dans `docs/index.html`, en haut du dernier `<script>`, aux lignes :
   ```js
   const SUPABASE_URL = 'https://VOTRE-PROJET.supabase.co';
   const SUPABASE_ANON_KEY = 'VOTRE_CLE_ANON_PUBLIC';
