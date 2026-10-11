"""Teste offline: simula as APIs (formato real) e confere coleta, rodada nova, movimentos e resumos."""
import json
import os
import pathlib
import sys
import tempfile

TMP = os.environ.get("TEST_DATA_DIR") or tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, FD_SLEEP="0", FOOTBALL_DATA_API_KEY="x", OPENROUTER_API_KEY="y")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import fetch_data  # noqa: E402
import summarize  # noqa: E402

TEAMS = [f"T{i}" for i in range(1, 21)]
STATE = {"round": 5, "f1": 16, "order": TEAMS[:]}


def match(md, i, status):
    home, away = TEAMS[i], TEAMS[19 - i]
    if md == 6 and i == 0:
        home, away = TEAMS[19], TEAMS[0]  # zebra: T20 vence T1
    score = {"fullTime": {"home": 2, "away": 1}} if status == "FINISHED" else {"fullTime": {"home": None, "away": None}}
    return {"matchday": md, "status": status, "utcDate": f"2026-10-{md:02d}T15:00:00Z",
            "homeTeam": {"shortName": home}, "awayTeam": {"shortName": away}, "score": score}


def fd_matches():
    ms = [match(md, i, "FINISHED") for md in range(1, 6) for i in range(10)]
    ms += [match(6, i, "FINISHED" if STATE["round"] >= 6 else "TIMED") for i in range(10)]
    if STATE.get("partial7"):  # rodada seguinte já começou (1 jogo encerrado)
        ms += [match(7, i, "FINISHED" if i == 0 else "TIMED") for i in range(10)]
    return {"matches": ms}


def fd_standings():
    table = [{"position": p + 1, "team": {"shortName": t}, "playedGames": STATE["round"], "won": 3, "draw": 1,
              "lost": 1, "goalsFor": 9, "goalsAgainst": 5, "goalDifference": 4, "points": 40 - p}
             for p, t in enumerate(STATE["order"])]
    return {"standings": [{"type": "TOTAL", "table": table}]}


def drv(pos, code, given, fam, team, pts, wins):
    return {"position": str(pos), "points": str(pts), "wins": str(wins),
            "Driver": {"driverId": code.lower(), "code": code, "givenName": given, "familyName": fam},
            "Constructors": [{"name": team}]}


def jolpica(url):
    r = STATE["f1"]
    if url.endswith("/driverStandings.json"):
        d = [drv(1, "ANT", "Kimi", "Antonelli", "Mercedes", 320 + (r - 16) * 25, 8),
             drv(2, "RUS", "George", "Russell", "Mercedes", 236, 3),
             drv(3, "HAM", "Lewis", "Hamilton", "Ferrari", 214, 1)]
        return {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": d}]}}}
    if url.endswith("/constructorStandings.json"):
        c = [{"position": "1", "points": "556", "wins": "11", "Constructor": {"name": "Mercedes"}},
             {"position": "2", "points": "405", "wins": "2", "Constructor": {"name": "Ferrari"}}]
        return {"MRData": {"StandingsTable": {"StandingsLists": [{"ConstructorStandings": c}]}}}
    if url.endswith("/last/results.json"):
        res = [{"position": "1", "grid": "2", "status": "Finished", "points": "25", "Time": {"time": "1:30:00.1"},
                "Driver": {"code": "ANT", "givenName": "Kimi", "familyName": "Antonelli"},
                "Constructor": {"name": "Mercedes"}, "FastestLap": {"rank": "1"}},
               {"position": "2", "grid": "1", "status": "+3.2s", "points": "18",
                "Driver": {"code": "RUS", "givenName": "George", "familyName": "Russell"},
                "Constructor": {"name": "Mercedes"}}]
        race = {"season": "2026", "round": str(r), "raceName": "GP Teste", "date": "2026-10-04",
                "Circuit": {"circuitName": "Circuito X", "Location": {"country": "Japan"}}, "Results": res}
        return {"MRData": {"RaceTable": {"Races": [race]}}}
    if url.endswith("/current.json"):
        races = [{"round": str(i), "raceName": f"GP {i}", "date": f"2026-{(i % 12) + 1:02d}-15",
                  "Circuit": {"Location": {"country": "Brasil"}}} for i in range(1, 25)]
        return {"MRData": {"RaceTable": {"Races": races}}}
    raise AssertionError(url)


def fake_http(url, headers=None, retries=3):
    if "football-data.org" in url:
        return fd_standings() if url.endswith("/standings") else fd_matches()
    return jolpica(url)


fetch_data.http_get_json = fake_http
calls = []
summarize.call_llm = lambda prompt, key: (calls.append(prompt), "Resumo de teste.")[1]
read = lambda n: json.loads((pathlib.Path(TMP) / n).read_text(encoding="utf-8"))

# --- rodada 1 de coleta ---
assert fetch_data.main() is True
fb = read("football.json")
assert set(fb) == {"PL", "PD", "CL", "BSA"}
pl = fb["PL"]
assert pl["matchday"] == 5 and pl["settled"] is True and len(pl["table"]) == 20
assert all(r["move"] == 0 for r in pl["table"]), "primeira coleta não tem base de comparação"
assert pl["next_round"] and len(pl["next_round"]) == 10
f1 = read("f1.json")
assert f1["last_race"]["round"] == 16 and f1["total_races"] == 24 and f1["next_race"]["round"] == 17
assert f1["last_race"]["results"][0]["fastest"] is True
assert f1["drivers"][0]["points"] == 320
assert summarize.main() is True and len(calls) == 5, len(calls)
assert read("summaries.json")["PL"]["round"] == 5

# --- sem mudança: nada é regravado e nenhum resumo é gerado ---
assert fetch_data.main() is False
n = len(calls)
assert summarize.main() is False and len(calls) == n

# --- rodada nova: T3 sobe ao 1º, T1 e T2 descem; F1 etapa 17 ---
STATE.update(round=6, f1=17, order=["T3", "T1", "T2"] + TEAMS[3:])
assert fetch_data.main() is True
pl = read("football.json")["PL"]
mv = {r["team"]: r["move"] for r in pl["table"]}
assert pl["matchday"] == 6
assert mv["T3"] == 2 and mv["T1"] == -1 and mv["T2"] == -1, mv
z = pl["highlights"]["zebras"]
assert z and z[0]["winner"] == "T20" and z[0]["loser"] == "T1", z
f1 = read("f1.json")
assert f1["last_race"]["round"] == 17 and f1["drivers"][0]["points"] == 345
assert summarize.main() is True and len(calls) == n + 5
assert read("summaries.json")["F1"]["round"] == 17
assert "Zebras" in calls[-5] or any("Zebras" in c for c in calls[-5:])

# --- rodada seguinte começou: não assenta, não gera resumo, não mexe nos movimentos ---
STATE["partial7"] = True
n = len(calls)
fetch_data.main()
pl = read("football.json")["PL"]
assert pl["matchday"] == 6 and pl["settled"] is False
assert {r["team"]: r["move"] for r in pl["table"]}["T3"] == 2, "movimentos seguem a rodada assentada"
assert summarize.main() is False and len(calls) == n
STATE["partial7"] = False

# --- competição que falha mantém o dado anterior ---
orig = fetch_data.http_get_json
fetch_data.http_get_json = lambda url, headers=None, retries=3: (_ for _ in ()).throw(OSError("x")) if "/PD/" in url else orig(url, headers)
fetch_data.main()
assert read("football.json")["PD"]["matchday"] == 6
print("OK")
