# crypto-engine — Moteur d'analyse Python

Projet « Veille ». Spécification complète : `../crypto-api/docs/SPEC.md`.

## Rôle
- Scanner le top 20 des cryptos les plus liquides (données de marché PUBLIQUES via CCXT).
- Calculer les indicateurs (EMA 20/50, RSI, MACD, Bollinger, volatilité) avec Pandas + TA-Lib.
- Publier des signaux BUY / SELL / HOLD (score + justification) dans le Redis Stream `signals`.

## Interdits
- Aucun accès aux clés API d'échange, aucun ordre passé ici : c'est le rôle exclusif de crypto-api.
- Pas de dépendance hors stack sans la proposer d'abord.

## Stack
Python 3.12, uv, FastAPI, CCXT, Pandas 3, TA-Lib 0.8 (wheel précompilé, pas d'installation C nécessaire), redis-py, pydantic-settings, pytest, ruff.

## Commandes
- `uv sync` — installe l'environnement
- `uv run uvicorn app.main:app --reload --port 8001` — démarre le moteur
- `uv run pytest` · `uv run ruff check .` · `uv run ruff format .`

## Règles
- Commits et PR : aucune mention d'attribution (« Generated with Claude », Co-Authored-By).
