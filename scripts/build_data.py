#!/usr/bin/env python3
"""
Moteur de récupération + prédiction pour l'application de pronostics football.

Sources 100% gratuites, sans clé API :
  1. openfootball/football.json   -> calendriers + résultats (ligues majeures)
  2. openfootball/worldcup.json   -> compétitions internationales (bonus)
  3. luukhopman/football-logos    -> logos des équipes (+ validation des noms)

Aucune IA générative n'est utilisée : les pronostics viennent d'un modèle
statistique (Elo + forme récente + confrontations directes) calculé
uniquement à partir des données ci-dessus.

Sortie :
  docs/data/today.json       -> les meilleures opportunités du jour (max 70)
  docs/data/history.json     -> historique des pronostics + vérification
  docs/data/meta.json        -> infos de build (horodatage, sources, précision globale)
"""

import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "football-app-data-builder/1.0 (+https://github.com/)"
HTTP_TIMEOUT = 20
MAX_MATCHES = 70

# ---------------------------------------------------------------------------
# Ligues suivies sur openfootball/football.json
# (le script ignore silencieusement les codes qui n'existent pas / 404)
# ---------------------------------------------------------------------------
LEAGUES = [
    ("en.1", "Angleterre - Premier League"),
    ("en.2", "Angleterre - Championship"),
    ("en.3", "Angleterre - League One"),
    ("de.1", "Allemagne - Bundesliga"),
    ("de.2", "Allemagne - 2. Bundesliga"),
    ("es.1", "Espagne - La Liga"),
    ("es.2", "Espagne - Segunda División"),
    ("it.1", "Italie - Serie A"),
    ("it.2", "Italie - Serie B"),
    ("fr.1", "France - Ligue 1"),
    ("fr.2", "France - Ligue 2"),
    ("nl.1", "Pays-Bas - Eredivisie"),
    ("pt.1", "Portugal - Primeira Liga"),
    ("sco.1", "Écosse - Premiership"),
    ("tr.1", "Turquie - Süper Lig"),
    ("be.1", "Belgique - Pro League"),
]

LOGOS_API = "https://api.github.com/repos/luukhopman/football-logos/git/trees/master?recursive=1"
LOGOS_RAW_BASE = "https://raw.githubusercontent.com/luukhopman/football-logos/master/"


# ---------------------------------------------------------------------------
# Utilitaires HTTP
# ---------------------------------------------------------------------------
def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def try_get_json(url):
    try:
        return http_get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"  [warn] HTTP {e.code} pour {url}", file=sys.stderr)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] échec {url}: {e}", file=sys.stderr)
        return None


def current_season_str(today=None):
    today = today or datetime.now(timezone.utc)
    y = today.year
    if today.month >= 7:
        return f"{y}-{str(y + 1)[2:]}"
    return f"{y - 1}-{str(y)[2:]}"


def season_candidates(today=None):
    """Essaie la saison en cours, puis la précédente en secours (début de saison / données pas encore publiées)."""
    cur = current_season_str(today)
    y = int(cur.split("-")[0])
    prev = f"{y - 1}-{str(y)[2:]}"
    return [cur, prev]


def normalize_name(name):
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    name = name.lower()
    name = re.sub(r"\(.*?\)", "", name)  # enlève "(ENG)" etc.
    for suffix in [
        " football club", " futbol club", " fc", " cf", " afc", " sc", " ac",
        " calcio", " club", " 1899", " 1900", " 1904", " 1909", " 1913",
        " ss", " ssd", " ssc", " as", " us", " cd", " ud", " rc", " sv",
        " vfl", " vfb", " tsg", " bvb", " sk", " ks",
    ]:
        name = re.sub(rf"\b{suffix.strip()}\b", "", name)
    name = re.sub(r"[^a-z0-9 ]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# ---------------------------------------------------------------------------
# 1) Récupération des calendriers / résultats (openfootball/football.json)
# ---------------------------------------------------------------------------
def fetch_league_matches(code, label, seasons):
    for season in seasons:
        url = f"https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"
        data = try_get_json(url)
        if data and data.get("matches"):
            for m in data["matches"]:
                m["_league_code"] = code
                m["_league_label"] = label
                m["_season"] = season
            return data["matches"]
    return []


def fetch_worldcup_matches():
    year = datetime.now(timezone.utc).year
    out = []
    for y in {year, year + 1}:
        url = f"https://raw.githubusercontent.com/openfootball/worldcup.json/master/{y}/worldcup.json"
        data = try_get_json(url)
        if data and data.get("matches"):
            for m in data["matches"]:
                m["_league_code"] = "wc"
                m["_league_label"] = data.get("name", "Compétition internationale")
                m["_season"] = str(y)
            out.extend(data["matches"])
    return out


def fetch_all_matches():
    seasons = season_candidates()
    all_matches = []
    print(f"Saisons essayées : {seasons}")
    for code, label in LEAGUES:
        matches = fetch_league_matches(code, label, seasons)
        print(f"  {label} ({code}) -> {len(matches)} matchs")
        all_matches.extend(matches)
        time.sleep(0.15)  # gentil avec l'API GitHub
    wc = fetch_worldcup_matches()
    if wc:
        print(f"  Compétitions internationales -> {len(wc)} matchs")
        all_matches.extend(wc)
    return all_matches


# ---------------------------------------------------------------------------
# 2) Logos (luukhopman/football-logos)
# ---------------------------------------------------------------------------
def fetch_logo_index():
    tree = try_get_json(LOGOS_API)
    index = {}
    if not tree or "tree" not in tree:
        print("  [warn] impossible de récupérer l'arborescence des logos", file=sys.stderr)
        return index
    for entry in tree["tree"]:
        path = entry.get("path", "")
        if not path.startswith("logos/") or not path.lower().endswith(".png"):
            continue
        filename = path.rsplit("/", 1)[-1][:-4]  # sans ".png"
        key = normalize_name(filename)
        if key:
            index[key] = LOGOS_RAW_BASE + urllib.parse.quote(path)
    print(f"  Index logos : {len(index)} équipes")
    return index


def find_logo(index, team_name):
    key = normalize_name(team_name)
    if key in index:
        return index[key]
    # correspondance partielle (ex: "Manchester United FC" vs "Manchester Utd")
    for k, v in index.items():
        if key and (key in k or k in key):
            return v
    return None


# ---------------------------------------------------------------------------
# 3) Modèle statistique : Elo + forme + confrontations
# ---------------------------------------------------------------------------
HOME_ADV = 70
K_FACTOR = 22
BASE_ELO = 1500


def build_team_stats(all_matches):
    """Parcourt tous les matchs joués (score connu), dans l'ordre chronologique,
    pour calculer Elo, forme récente et stats de buts par équipe."""
    played = [m for m in all_matches if m.get("score", {}).get("ft")]
    played.sort(key=lambda m: m.get("date", ""))

    elo = {}
    history = {}  # team -> liste de dicts {date, gf, ga, result, opponent}

    def get_elo(t):
        return elo.get(t, BASE_ELO)

    for m in played:
        home, away = m["team1"], m["team2"]
        ft = m["score"]["ft"]
        if not isinstance(ft, list) or len(ft) != 2:
            continue
        gh, ga = ft
        eh, ea = get_elo(home), get_elo(away)
        exp_home = 1 / (1 + 10 ** (-((eh + HOME_ADV) - ea) / 400))
        if gh > ga:
            score_home = 1.0
        elif gh < ga:
            score_home = 0.0
        else:
            score_home = 0.5
        elo[home] = eh + K_FACTOR * (score_home - exp_home)
        elo[away] = ea + K_FACTOR * ((1 - score_home) - (1 - exp_home))

        for team, gf, ga_, opp, is_home in (
            (home, gh, ga, away, True),
            (away, ga, gh, home, False),
        ):
            result = "W" if gf > ga_ else ("D" if gf == ga_ else "L")
            history.setdefault(team, []).append(
                {"date": m.get("date", ""), "gf": gf, "ga": ga_, "result": result,
                 "opponent": opp, "home": is_home}
            )

    return elo, history


def recent_form(history, team, n=5):
    games = history.get(team, [])[-n:]
    if not games:
        return {"points": 0, "played": 0, "avg_gf": 0.0, "avg_ga": 0.0, "form_str": ""}
    pts = sum(3 if g["result"] == "W" else (1 if g["result"] == "D" else 0) for g in games)
    form_str = "".join(g["result"] for g in games)
    return {
        "points": pts,
        "played": len(games),
        "avg_gf": round(sum(g["gf"] for g in games) / len(games), 2),
        "avg_ga": round(sum(g["ga"] for g in games) / len(games), 2),
        "form_str": form_str,
    }


def head_to_head(history, home, away, n=5):
    games = [g for g in history.get(home, []) if g["opponent"] == away][-n:]
    if not games:
        return {"played": 0, "home_wins": 0, "draws": 0, "away_wins": 0}
    home_wins = sum(1 for g in games if g["result"] == "W")
    draws = sum(1 for g in games if g["result"] == "D")
    away_wins = sum(1 for g in games if g["result"] == "L")
    return {"played": len(games), "home_wins": home_wins, "draws": draws, "away_wins": away_wins}


def predict_match(elo, history, home, away):
    eh = elo.get(home, BASE_ELO)
    ea = elo.get(away, BASE_ELO)
    elo_diff = (eh + HOME_ADV) - ea
    p_home_raw = 1 / (1 + 10 ** (-elo_diff / 400))

    # Probabilité de nul : plus l'écart Elo est faible, plus le nul est probable
    p_draw = max(0.18, min(0.30, 0.30 - abs(elo_diff) / 1400))
    p_home = p_home_raw * (1 - p_draw)
    p_away = (1 - p_home_raw) * (1 - p_draw)
    total = p_home + p_draw + p_away
    p_home, p_draw, p_away = p_home / total, p_draw / total, p_away / total

    form_h = recent_form(history, home)
    form_a = recent_form(history, away)
    h2h = head_to_head(history, home, away)

    games_played = min(form_h["played"], form_a["played"])
    data_quality = "solide" if games_played >= 4 else ("limitée" if games_played >= 1 else "insuffisante")

    outcomes = [("1", p_home, f"Victoire {home}"), ("X", p_draw, "Match nul"), ("2", p_away, f"Victoire {away}")]
    pick_code, pick_prob, pick_label = max(outcomes, key=lambda o: o[1])

    avg_goals = form_h["avg_gf"] + form_h["avg_ga"] + form_a["avg_gf"] + form_a["avg_ga"]
    avg_goals_match = round((form_h["avg_gf"] + form_a["avg_gf"] + form_h["avg_ga"] + form_a["avg_ga"]) / 2, 2)
    over_under_pick = "Plus de 2.5 buts" if avg_goals_match > 2.6 else "Moins de 2.5 buts"
    btts_pick = "Oui" if (form_h["avg_gf"] > 0.9 and form_a["avg_gf"] > 0.9) else "Non"

    confidence = round(pick_prob * 100, 1)
    if games_played < 3:
        confidence = round(confidence * 0.85, 1)  # on réduit la confiance si peu de données

    return {
        "elo_home": round(eh, 1),
        "elo_away": round(ea, 1),
        "prob_home": round(p_home * 100, 1),
        "prob_draw": round(p_draw * 100, 1),
        "prob_away": round(p_away * 100, 1),
        "pick_code": pick_code,
        "pick_label": pick_label,
        "confidence": confidence,
        "form_home": form_h,
        "form_away": form_a,
        "head_to_head": h2h,
        "over_under_pick": over_under_pick,
        "btts_pick": btts_pick,
        "avg_goals_expected": avg_goals_match,
        "data_quality": data_quality,
    }


# ---------------------------------------------------------------------------
# 4) Sélection du jour + assemblage du JSON de sortie
# ---------------------------------------------------------------------------
def match_key(m):
    return f"{m.get('date')}|{m.get('team1')}|{m.get('team2')}"


def build_today_payload(all_matches, elo, history, logo_index, target_date):
    todays = [m for m in all_matches if m.get("date") == target_date]
    results = []
    for m in todays:
        home, away = m["team1"], m["team2"]
        pred = predict_match(elo, history, home, away)
        results.append({
            "id": match_key(m),
            "date": m.get("date"),
            "time": m.get("time", "--:--"),
            "league": m.get("_league_label", ""),
            "round": m.get("round", ""),
            "home": home,
            "away": away,
            "home_logo": find_logo(logo_index, home),
            "away_logo": find_logo(logo_index, away),
            "prediction": pred,
        })
    results.sort(key=lambda r: r["prediction"]["confidence"], reverse=True)
    return results[:MAX_MATCHES]


def update_history_log(all_matches, elo, history, target_date):
    """Journal persistant : on enregistre les pronostics émis, puis on vérifie
    les résultats une fois les matchs joués."""
    hist_path = DATA_DIR / "history.json"
    log = {"entries": [], "accuracy": {"overall": 0.0, "graded": 0, "correct": 0}}
    if hist_path.exists():
        try:
            log = json.loads(hist_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    existing_ids = {e["id"] for e in log["entries"]}

    matches_by_key = {match_key(m): m for m in all_matches}

    # 1) ajouter les nouveaux pronostics du jour
    for m in all_matches:
        if m.get("date") != target_date:
            continue
        mid = match_key(m)
        if mid in existing_ids:
            continue
        home, away = m["team1"], m["team2"]
        pred = predict_match(elo, history, home, away)
        log["entries"].append({
            "id": mid,
            "date": m.get("date"),
            "league": m.get("_league_label", ""),
            "home": home,
            "away": away,
            "pick_label": pred["pick_label"],
            "pick_code": pred["pick_code"],
            "confidence": pred["confidence"],
            "status": "en_attente",
            "actual_score": None,
            "correct": None,
        })

    # 2) vérifier les pronostics passés dont le score est maintenant connu
    for entry in log["entries"]:
        if entry["status"] == "verifie":
            continue
        src = matches_by_key.get(entry["id"])
        if not src:
            continue
        ft = src.get("score", {}).get("ft")
        if not ft or not isinstance(ft, list):
            continue
        gh, ga = ft
        actual_code = "1" if gh > ga else ("2" if gh < ga else "X")
        entry["actual_score"] = f"{gh}-{ga}"
        entry["correct"] = (actual_code == entry["pick_code"])
        entry["status"] = "verifie"

    # 3) ne garder que les 60 derniers jours pour ne pas gonfler le fichier indéfiniment
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
    log["entries"] = [e for e in log["entries"] if e["date"] >= cutoff]
    log["entries"].sort(key=lambda e: e["date"], reverse=True)

    graded = [e for e in log["entries"] if e["status"] == "verifie"]
    correct = [e for e in graded if e["correct"]]
    log["accuracy"] = {
        "overall": round(100 * len(correct) / len(graded), 1) if graded else 0.0,
        "graded": len(graded),
        "correct": len(correct),
    }
    return log


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    target_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== Build du {target_date} (UTC) ===")

    print("1) Récupération des calendriers / résultats...")
    all_matches = fetch_all_matches()
    all_matches = [m for m in all_matches if isinstance(m, dict)]
    for m in all_matches:
        if not isinstance(m.get("score"), dict):
            m["score"] = {}
    print(f"   Total matchs récupérés : {len(all_matches)}")

    print("2) Récupération des logos...")
    logo_index = fetch_logo_index()

    print("3) Calcul Elo / forme / confrontations...")
    elo, history = build_team_stats(all_matches)
    print(f"   Équipes notées : {len(elo)}")

    print("4) Sélection des meilleures opportunités du jour...")
    today_payload = build_today_payload(all_matches, elo, history, logo_index, target_date)
    print(f"   Matchs retenus aujourd'hui : {len(today_payload)}")

    print("5) Mise à jour de l'historique + vérification...")
    history_log = update_history_log(all_matches, elo, history, target_date)

    (DATA_DIR / "today.json").write_text(
        json.dumps({"date": target_date, "matches": today_payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DATA_DIR / "history.json").write_text(
        json.dumps(history_log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (DATA_DIR / "meta.json").write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target_date": target_date,
            "total_matches_scanned": len(all_matches),
            "matches_today": len(today_payload),
            "teams_rated": len(elo),
            "sources": [
                "https://github.com/openfootball/football.json",
                "https://github.com/openfootball/worldcup.json",
                "https://github.com/luukhopman/football-logos",
            ],
            "accuracy_overall": history_log["accuracy"]["overall"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("Terminé.")


if __name__ == "__main__":
    main()
