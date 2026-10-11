#!/usr/bin/env python3
"""Gera um resumo curto por rodada/corrida via OpenRouter (padrão: DeepSeek).

Só chama o modelo quando a rodada (ou corrida) é nova em relação a summaries.json.
Sem OPENROUTER_API_KEY, não faz nada e o painel mostra só as tabelas.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_data import DATA, load_json, write_json_if_changed  # noqa: E402

MODEL = os.environ.get("SUMMARY_MODEL", "deepseek/deepseek-v4.1-flash")

RULES = (
    "Escreva em português do Brasil, em até 4 frases, sem emojis. "
    "Use somente os fatos abaixo; não invente nada. "
)


def facts_football(c):
    t = c["table"]
    done = [m for m in c["last_round"] if m["status"] == "FINISHED"]
    lines = [f'{c["name"]}, rodada {c["matchday"]}.']
    lines.append("Resultados: " + "; ".join(f'{m["home"]} {m["hg"]}-{m["ag"]} {m["away"]}' for m in done))
    lines.append("Topo: " + "; ".join(f'{r["position"]}º {r["team"]} {r["points"]} pts' for r in t[:5]))
    lines.append("Base: " + "; ".join(f'{r["position"]}º {r["team"]} {r["points"]} pts' for r in t[-4:]))
    movers = [r for r in t if abs(r["move"]) >= 2]
    if movers:
        lines.append("Mudanças de posição: " + "; ".join(
            f'{r["team"]} {r["move"]:+d} (agora {r["position"]}º)' for r in movers))
    z = c["highlights"]["zebras"]
    if z:
        lines.append("Zebras: " + "; ".join(
            f'{x["winner"]} ({x["winner_pos"]}º) venceu {x["loser"]} ({x["loser_pos"]}º) por {x["score"]}' for x in z))
    g = c["highlights"]["goleadas"]
    if g:
        lines.append("Goleadas: " + "; ".join(f'{x["winner"]} {x["score"]} {x["loser"]}' for x in g))
    return "\n".join(lines)


def facts_f1(f):
    race = f["last_race"]
    d = f["drivers"]
    lines = [f'GP {race["name"]} ({race["country"]}), etapa {race["round"]} de {f["total_races"]}.']
    lines.append("Resultado: " + "; ".join(
        f'{r["position"]}º {r["name"]} ({r["team"]})' for r in race["results"][:6]))
    lines.append("Campeonato de pilotos: " + "; ".join(
        f'{r["position"]}º {r["name"]} {r["points"]} pts ({r["move"]:+d})' for r in d[:6]))
    if len(d) > 1:
        lines.append(f'Diferença entre 1º e 2º: {d[0]["points"] - d[1]["points"]} pts. '
                     f'Etapas restantes: {f["total_races"] - race["round"]}.')
    return "\n".join(lines)


def call_llm(prompt, key):
    """OpenRouter (API compatível com OpenAI). Raciocínio desligado: o resumo é curto e factual."""
    body = json.dumps({"model": MODEL, "max_tokens": 600, "temperature": 0.3,
                       "reasoning": {"enabled": False},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    return (data["choices"][0]["message"].get("content") or "").strip()


def main():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY ausente: sem resumos", file=sys.stderr)
        return False
    summaries = load_json(DATA / "summaries.json", {})
    football = load_json(DATA / "football.json", {})
    f1 = load_json(DATA / "f1.json", {})
    jobs = []
    for code, c in football.items():
        if c.get("matchday") is not None and summaries.get(code, {}).get("round") != c["matchday"]:
            jobs.append((code, c["matchday"], facts_football(c)))
    if f1.get("last_race") and summaries.get("F1", {}).get("round") != f1["last_race"]["round"]:
        jobs.append(("F1", f1["last_race"]["round"], facts_f1(f1)))

    for code, rnd, facts in jobs:
        try:
            text = call_llm(f"Você é editor esportivo. {RULES}\n\nFatos:\n{facts}", key)
        except Exception as e:  # mantém o resumo anterior
            print(f"[{code}] resumo falhou: {e}", file=sys.stderr)
            continue
        if text:
            summaries[code] = {"round": rnd, "text": text,
                               "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return write_json_if_changed(DATA / "summaries.json", summaries)


if __name__ == "__main__":
    main()
