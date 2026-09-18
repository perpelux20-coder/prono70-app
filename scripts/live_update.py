#!/usr/bin/env python3
"""
ProLab — moteur d'analyse EN DIRECT.

Source de données live : API-Football (https://www.api-football.com/), un seul
appel "fixtures?live=all" par exécution récupère TOUS les matchs en direct dans
le monde (le plan gratuit = 100 requêtes/jour permet un rafraîchissement toutes
les ~15 min ; un plan payant permet une cadence proche de la minute).

Aucune IA générative : la probabilité en direct est recalculée à chaque appel
avec un modèle de Poisson tronqué, ajusté au score courant et au temps restant,
lui-même ancré sur les statistiques pré-match déjà calculées par
scripts/build_data.py (Elo + forme).

Le résultat est écrit dans Supabase (table live_matches), protégée par une
politique RLS qui ne la rend visible qu'aux comptes activés "ProLab" par
l'administrateur. Ce script utilise la clé service_role, qui contourne les
politiques RLS en écriture — elle ne doit JAMAIS être exposée côté frontend.
"""

import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Données pré-match générées par build_data.py, utilisées comme "ancre" du modèle live
TODAY_JSON_URL = "https://raw.githubusercontent.com/{repo}/main/docs/data/today.json"
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")  # fourni automatiquement par GitHub Actions

HTTP_TIMEOUT = 20
LEAGUE_AVG_GOALS = 1.35  # valeur par défaut si aucune donnée pré-match dispo pour un match


def http_get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_pre_match_lookup():
    """Récupère les pronostics pré-match du jour pour ancrer le modèle live."""
    if not GITHUB_REPOSITORY:
        return {}
    url = TODAY_JSON_URL.format(repo=GITHUB_REPOSITORY)
    try:
        data = http_get_json(url)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] impossible de charger today.json ({e}) — utilisation de valeurs neutres", file=sys.stderr)
        return {}
    lookup = {}
    for m in data.get("matches", []):
        key = f"{m['home'].lower()}|{m['away'].lower()}"
        lookup[key] = m
    return lookup


def fetch_live_fixtures():
    if not API_FOOTBALL_KEY:
        print("[erreur] API_FOOTBALL_KEY manquante — impossible de récupérer les matchs en direct.", file=sys.stderr)
        return []
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    try:
        data = http_get_json(url, headers=headers)
    except urllib.error.HTTPError as e:
        print(f"[erreur] API-Football a répondu {e.code} : {e.read()[:300]}", file=sys.stderr)
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[erreur] appel API-Football échoué : {e}", file=sys.stderr)
        return []
    return data.get("response", [])


# ---------------------------------------------------------------------------
# Modèle statistique en direct : Poisson tronqué sur les buts restants
# ---------------------------------------------------------------------------
def poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def outcome_probs_from_lambdas(lam_home, lam_away, cur_h, cur_a, max_extra=6):
    """Distribue les buts restants selon Poisson et additionne les scénarios
    qui donnent victoire domicile / nul / victoire extérieur."""
    p_home = p_draw = p_away = 0.0
    for extra_h in range(max_extra + 1):
        for extra_a in range(max_extra + 1):
            p = poisson_pmf(extra_h, lam_home) * poisson_pmf(extra_a, lam_away)
            final_h, final_a = cur_h + extra_h, cur_a + extra_a
            if final_h > final_a:
                p_home += p
            elif final_h < final_a:
                p_away += p
            else:
                p_draw += p
    total = p_home + p_draw + p_away
    if total == 0:
        return 1 / 3, 1 / 3, 1 / 3
    return p_home / total, p_draw / total, p_away / total


def estimate_pre_match_lambdas(pre):
    """Convertit les stats pré-match (forme) en buts attendus par équipe sur 90 min."""
    if not pre:
        return LEAGUE_AVG_GOALS, LEAGUE_AVG_GOALS
    fh, fa = pre["prediction"]["form_home"], pre["prediction"]["form_away"]
    lam_home = max(0.3, (fh.get("avg_gf", LEAGUE_AVG_GOALS) + fa.get("avg_ga", LEAGUE_AVG_GOALS)) / 2)
    lam_away = max(0.3, (fa.get("avg_gf", LEAGUE_AVG_GOALS) + fh.get("avg_ga", LEAGUE_AVG_GOALS)) / 2)
    return lam_home, lam_away


def live_prediction(minute, status_short, home_goals, away_goals, pre):
    minute = minute or 0
    elapsed_fraction = min(max(minute, 0), 90) / 90
    remaining_fraction = max(1 - elapsed_fraction, 0.02)  # ne jamais tomber à 0 pile (mi-temps, arrêts de jeu...)

    lam_home_90, lam_away_90 = estimate_pre_match_lambdas(pre)
    lam_home_remaining = lam_home_90 * remaining_fraction
    lam_away_remaining = lam_away_90 * remaining_fraction

    p_home, p_draw, p_away = outcome_probs_from_lambdas(
        lam_home_remaining, lam_away_remaining, home_goals or 0, away_goals or 0
    )

    outcomes = [("1", p_home, "Victoire domicile"), ("X", p_draw, "Match nul"), ("2", p_away, "Victoire extérieur")]
    pick_code, pick_prob, pick_label = max(outcomes, key=lambda o: o[1])

    note = f"Modèle Poisson ancré sur {round(lam_home_90,2)}/{round(lam_away_90,2)} buts attendus pré-match"
    if not pre:
        note = "Aucune donnée pré-match disponible pour ce match — estimation à partir d'une moyenne générique"

    return {
        "prob_home": round(p_home * 100, 1),
        "prob_draw": round(p_draw * 100, 1),
        "prob_away": round(p_away * 100, 1),
        "pick_label": pick_label,
        "confidence": round(pick_prob * 100, 1),
        "note": note,
    }


# ---------------------------------------------------------------------------
# Écriture dans Supabase (REST, clé service_role — écriture protégée)
# ---------------------------------------------------------------------------
def upsert_live_matches(rows):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("[erreur] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY manquants.", file=sys.stderr)
        return
    if not rows:
        print("Aucun match en direct actuellement — rien à envoyer.")
        return
    url = f"{SUPABASE_URL}/rest/v1/live_matches"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    body = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            print(f"Supabase : {len(rows)} match(s) envoyé(s), statut {resp.status}")
    except urllib.error.HTTPError as e:
        print(f"[erreur] Supabase a répondu {e.code} : {e.read()[:400]}", file=sys.stderr)


def cleanup_finished_matches(active_ids):
    """Supprime de la table les matchs qui ne sont plus en direct (terminés, reportés...)
    pour que ProLab n'affiche jamais un match figé de la veille."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    url = f"{SUPABASE_URL}/rest/v1/live_matches"
    if active_ids:
        ids_list = ",".join(f'"{i}"' for i in active_ids)
        url += f"?id=not.in.({ids_list})"
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
    except urllib.error.HTTPError as e:
        print(f"[warn] nettoyage Supabase : {e.code}", file=sys.stderr)


def main():
    print(f"=== ProLab live update — {datetime.now(timezone.utc).isoformat()} ===")
    pre_lookup = fetch_pre_match_lookup()
    fixtures = fetch_live_fixtures()
    print(f"Matchs en direct récupérés : {len(fixtures)}")

    rows = []
    for fx in fixtures:
        try:
            fixture = fx["fixture"]
            teams = fx["teams"]
            goals = fx["goals"]
            league = fx["league"]

            home_name, away_name = teams["home"]["name"], teams["away"]["name"]
            key = f"{home_name.lower()}|{away_name.lower()}"
            pre = pre_lookup.get(key)

            minute = (fixture.get("status") or {}).get("elapsed") or 0
            status_short = (fixture.get("status") or {}).get("short") or ""
            home_goals, away_goals = goals.get("home") or 0, goals.get("away") or 0

            pred = live_prediction(minute, status_short, home_goals, away_goals, pre)

            rows.append({
                "id": str(fixture["id"]),
                "league": league.get("name", ""),
                "home": home_name,
                "away": away_name,
                "home_logo": teams["home"].get("logo") or (pre["home_logo"] if pre else None),
                "away_logo": teams["away"].get("logo") or (pre["away_logo"] if pre else None),
                "minute": minute,
                "status": status_short,
                "home_goals": home_goals,
                "away_goals": away_goals,
                "prob_home": pred["prob_home"],
                "prob_draw": pred["prob_draw"],
                "prob_away": pred["prob_away"],
                "pick_label": pred["pick_label"],
                "confidence": pred["confidence"],
                "note": pred["note"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:  # noqa: BLE001
            print(f"[warn] match ignoré (données inattendues) : {e}", file=sys.stderr)

    upsert_live_matches(rows)
    cleanup_finished_matches([r["id"] for r in rows])
    print("Terminé.")


if __name__ == "__main__":
    main()
