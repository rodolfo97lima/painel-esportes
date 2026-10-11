#!/usr/bin/env python3
"""Coleta tabelas e resultados (football-data.org + Jolpica F1) e grava JSON em site/data/.

Só usa a biblioteca padrão. Variáveis de ambiente:
  FOOTBALL_DATA_API_KEY  chave gratuita do football-data.org (sem ela o futebol é pulado)
  DATA_DIR               pasta de saída (padrão: site/data)
  FD_SLEEP               pausa entre chamadas ao football-data.org (padrão 7s; limite grátis = 10/min)
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "site" / "data"))
SLEEP = float(os.environ.get("FD_SLEEP", "7"))
FD_BASE = "https://api.football-data.org/v4"
JOLPICA = "https://api.jolpi.ca/ergast/f1"

COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "CL": "Champions League",
    "BSA": "Brasileirão Série A",
}
CLOSED = {"FINISHED", "POSTPONED", "CANCELLED", "SUSPENDED", "AWARDED"}
OPEN = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}
ZEBRA_GAP = 8  # vencedor ao menos 8 posições abaixo do perdedor
BIG_WIN = 4    # saldo mínimo para destacar goleada


# ---------- utilidades ----------

def http_get_json(url, headers=None, retries=3):
    req = urllib.request.Request(url, headers={"User-Agent": "sports-dashboard/1.0", **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(30 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json_if_changed(path, obj):
    """Grava só se o conteúdo mudou (evita commits vazios). Retorna True se gravou."""
    text = json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True)
    path = Path(path)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def apply_moves(rows, key, st, tag, new_round):
    """Calcula subida/queda de posição contra o fechamento da rodada anterior.

    st guarda dois retratos: <tag>_cur (fechamento da última rodada) e <tag>_prev (da anterior).
    move > 0 = subiu; < 0 = caiu.
    """
    cur = {r[key]: r["position"] for r in rows}
    if new_round:
        st[tag + "_prev"] = st.get(tag + "_cur")
        st[tag + "_cur"] = cur
    elif tag + "_cur" not in st:
        st[tag + "_cur"] = cur
    prev = st.get(tag + "_prev") or {}
    for r in rows:
        p = prev.get(r[key])
        r["move"] = (p - r["position"]) if p else 0


def num(x):
    x = float(x)
    return int(x) if x.is_integer() else x


# ---------- futebol ----------

def short(team):
    t = team or {}
    return t.get("shortName") or t.get("name") or "A definir"


def parse_table(st):
    groups = st.get("standings", [])
    total = [g for g in groups if g.get("type") == "TOTAL"] or groups
    if not total:
        return []
    return [
        {
            "position": r["position"], "team": short(r["team"]),
            "played": r["playedGames"], "won": r["won"], "draw": r["draw"], "lost": r["lost"],
            "gf": r["goalsFor"], "ga": r["goalsAgainst"], "gd": r["goalDifference"],
            "points": r["points"],
        }
        for r in total[0]["table"]
    ]


def fmt_match(m):
    ft = (m.get("score") or {}).get("fullTime") or {}
    return {
        "date": m.get("utcDate"), "home": short(m.get("homeTeam")), "away": short(m.get("awayTeam")),
        "hg": ft.get("home"), "ag": ft.get("away"), "status": m.get("status"),
    }


def closed_matchday(matches):
    """Maior rodada em que nenhum jogo está pendente e ao menos um terminou."""
    by_md = {}
    for m in matches:
        if m.get("matchday") is not None:
            by_md.setdefault(m["matchday"], []).append(m)
    closed = [
        md for md, ms in by_md.items()
        if all(x["status"] in CLOSED for x in ms) and any(x["status"] == "FINISHED" for x in ms)
    ]
    return (max(closed) if closed else None), by_md


def highlights(round_matches, table):
    pos = {r["team"]: r["position"] for r in table}
    zebras, goleadas = [], []
    for m in round_matches:
        if m["status"] != "FINISHED" or m["hg"] is None or m["hg"] == m["ag"]:
            continue
        win, lose = (m["home"], m["away"]) if m["hg"] > m["ag"] else (m["away"], m["home"])
        score = f'{max(m["hg"], m["ag"])}-{min(m["hg"], m["ag"])}'
        if win in pos and lose in pos and pos[win] - pos[lose] >= ZEBRA_GAP:
            zebras.append({"winner": win, "loser": lose, "score": score,
                           "winner_pos": pos[win], "loser_pos": pos[lose]})
        if abs(m["hg"] - m["ag"]) >= BIG_WIN:
            goleadas.append({"winner": win, "loser": lose, "score": score})
    return {"zebras": zebras, "goleadas": goleadas}


def build_football(code, name, standings_json, matches_json, cs):
    table = parse_table(standings_json)
    matches = matches_json.get("matches", [])
    md, by_md = closed_matchday(matches)
    new_round = md is not None and cs.get("matchday") != md
    apply_moves(table, "team", cs, "table", new_round)
    if new_round:
        cs["matchday"] = md
    last = sorted((fmt_match(m) for m in by_md.get(md, [])), key=lambda x: x["date"] or "")
    pending = [m for m in matches if m["status"] in OPEN and m.get("matchday") is not None]
    nxt = []
    if pending:
        nmd = min(m["matchday"] for m in pending)
        nxt = sorted((fmt_match(m) for m in pending if m["matchday"] == nmd), key=lambda x: x["date"] or "")
    return {
        "code": code, "name": name, "matchday": md, "table": table,
        "last_round": last, "next_round": nxt, "highlights": highlights(last, table),
    }


def fetch_competition(code, token):
    h = {"X-Auth-Token": token}
    st = http_get_json(f"{FD_BASE}/competitions/{code}/standings", h)
    time.sleep(SLEEP)
    ms = http_get_json(f"{FD_BASE}/competitions/{code}/matches", h)
    return st, ms


# ---------- F1 ----------

def first(lst):
    return lst[0] if lst else None


def build_f1(sf1):
    ds = http_get_json(f"{JOLPICA}/current/driverStandings.json")
    time.sleep(0.5)
    cs = http_get_json(f"{JOLPICA}/current/constructorStandings.json")
    time.sleep(0.5)
    last = http_get_json(f"{JOLPICA}/current/last/results.json")
    time.sleep(0.5)
    sched = http_get_json(f"{JOLPICA}/current.json")

    dl = first(ds["MRData"]["StandingsTable"]["StandingsLists"]) or {}
    drivers = [
        {
            "position": int(x["position"]), "code": x["Driver"].get("code") or x["Driver"]["driverId"],
            "name": f'{x["Driver"]["givenName"]} {x["Driver"]["familyName"]}',
            "team": " / ".join(c["name"] for c in x.get("Constructors", [])),
            "points": num(x["points"]), "wins": int(x["wins"]),
        }
        for x in dl.get("DriverStandings", [])
    ]
    cl = first(cs["MRData"]["StandingsTable"]["StandingsLists"]) or {}
    teams = [
        {
            "position": int(x["position"]), "name": x["Constructor"]["name"],
            "points": num(x["points"]), "wins": int(x["wins"]),
        }
        for x in cl.get("ConstructorStandings", [])
    ]

    race = first(last["MRData"]["RaceTable"]["Races"])
    races = sched["MRData"]["RaceTable"]["Races"]
    if not race:
        return None
    rnd = int(race["round"])
    results = [
        {
            "position": int(r["position"]), "code": r["Driver"].get("code") or r["Driver"]["driverId"],
            "name": f'{r["Driver"]["givenName"]} {r["Driver"]["familyName"]}',
            "team": r["Constructor"]["name"], "grid": int(r.get("grid", 0)),
            "status": r.get("status", ""), "time": (r.get("Time") or {}).get("time"),
            "points": num(r.get("points", 0)),
            "fastest": (r.get("FastestLap") or {}).get("rank") == "1",
        }
        for r in race.get("Results", [])
    ]
    upcoming = [x for x in races if int(x["round"]) > rnd]
    nxt = None
    if upcoming:
        u = upcoming[0]
        nxt = {"round": int(u["round"]), "name": u["raceName"], "date": u["date"],
               "country": u["Circuit"]["Location"]["country"]}

    new_round = sf1.get("round") != rnd
    apply_moves(drivers, "code", sf1, "drivers", new_round)
    apply_moves(teams, "name", sf1, "teams", new_round)
    sf1["round"] = rnd
    return {
        "season": race["season"], "total_races": len(races),
        "last_race": {
            "round": rnd, "name": race["raceName"], "date": race["date"],
            "circuit": race["Circuit"]["circuitName"], "country": race["Circuit"]["Location"]["country"],
            "results": results,
        },
        "next_race": nxt, "drivers": drivers, "constructors": teams,
    }


# ---------- principal ----------

def main():
    DATA.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("FOOTBALL_DATA_API_KEY")
    state = load_json(DATA / "state.json", {})
    football = load_json(DATA / "football.json", {})
    f1 = load_json(DATA / "f1.json", {})
    ok = 0

    if token:
        for code, name in COMPETITIONS.items():
            try:
                st, ms = fetch_competition(code, token)
                football[code] = build_football(code, name, st, ms, state.setdefault("football", {}).setdefault(code, {}))
                ok += 1
            except Exception as e:  # mantém o dado anterior dessa competição
                print(f"[{code}] falhou: {e}", file=sys.stderr)
            time.sleep(SLEEP)
    else:
        print("FOOTBALL_DATA_API_KEY ausente: pulando futebol", file=sys.stderr)

    try:
        res = build_f1(state.setdefault("f1", {}))
        if res:
            f1 = res
            ok += 1
    except Exception as e:
        print(f"[F1] falhou: {e}", file=sys.stderr)

    changed = False
    changed |= write_json_if_changed(DATA / "football.json", football)
    changed |= write_json_if_changed(DATA / "f1.json", f1)
    changed |= write_json_if_changed(DATA / "state.json", state)
    if changed:
        write_json_if_changed(DATA / "meta.json", {
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "competitions": list(COMPETITIONS),
        })
    if ok == 0:
        print("Nenhuma fonte respondeu.", file=sys.stderr)
        sys.exit(1)
    return changed


if __name__ == "__main__":
    main()
