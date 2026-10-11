# Painel de Esportes

Tabelas de Premier League, La Liga, Champions League, Brasileirão e F1, com resumo gerado a cada rodada/corrida. Site estático no GitHub Pages, atualizado por GitHub Actions a cada 3 horas.

## Como funciona
1. `scripts/fetch_data.py` busca tabelas e jogos no football-data.org e F1 no Jolpica, e grava JSON em `site/data/`. Só grava se algo mudou.
2. Detecta rodada nova (todos os jogos da rodada encerrados) ou corrida nova e calcula subida/queda de posição contra o fechamento da rodada anterior, zebras (vencedor 8+ posições abaixo) e goleadas (saldo 4+).
3. `scripts/summarize.py` chama o modelo só quando há rodada/corrida nova (cerca de 5 chamadas por semana).
4. `site/index.html` lê os JSON e mostra uma aba por campeonato.

## Configuração (uma vez)
1. Crie um repositório no GitHub e suba esta pasta.
2. Em Settings → Secrets and variables → Actions, crie:
   - `FOOTBALL_DATA_API_KEY`: chave gratuita em football-data.org/client/register
   - `OPENROUTER_API_KEY`: opcional; sem ela o painel mostra só tabelas
3. Em Settings → Pages, escolha Source: GitHub Actions.
4. Em Actions, rode "Atualizar dados e publicar" manualmente na primeira vez.

## Testes
`python3 tests/test_pipeline.py` roda a coleta contra APIs simuladas (rodada nova, movimentos, zebras, falha parcial, resumos sem repetir).

## Limites conhecidos
- O plano gratuito do football-data.org traz placar e tabela, sem estatística de jogadores. O resumo fala de tabela e resultados.
- Jogos adiados no Brasileirão não travam a rodada (adiado conta como encerrado), mas a tabela inclui os jogos já disputados.
- A rodada só é "assentada" (movimentos e resumo gerados) quando nenhum jogo da rodada seguinte começou. Se as rodadas se sobrepõem por muitos dias, o resumo espera a janela livre. Enquanto isso a tabela continua ao vivo.
- Modelo do resumo: `deepseek/deepseek-v4.1-flash` no OpenRouter, com raciocínio desligado. Para trocar, defina a variável `SUMMARY_MODEL`.
