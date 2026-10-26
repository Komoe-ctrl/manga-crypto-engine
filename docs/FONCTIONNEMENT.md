# Fonctionnement du moteur

Le moteur lit des données de marché **publiques** (aucune clé), calcule des indicateurs, et publie des signaux `BUY` / `SELL` dans le Redis Stream `signals`. Il ne passe aucun ordre et n'a accès à aucune clé : c'est le rôle exclusif de `crypto-api`, qui consomme le stream (contrat : `../crypto-api/docs/SPEC.md` section 7). Les choix sont expliqués dans `docs/DECISIONS.md` (E1 à E13).

## Chaîne de traitement

```
Binance (public) ──► univers (top 20 USDT) ──► bougies 1h fermées ──► indicateurs (TA-Lib)
                                                                         │
 Redis stream `signals` ◄── validation du contrat ◄── décision + score ◄─┘
        │                                  ▲
        └─► crypto-api (risques, ordres)   └── état `engine:stance` (Redis) : n'émet qu'au changement,
                                               après 3 bougies concordantes
```

| Fichier | Rôle |
|---|---|
| `app/market.py` | Données publiques : univers, prix, bougies fermées (CCXT) |
| `app/indicators.py` | EMA 20/50, RSI, MACD, Bollinger, volatilité, croisement récent (TA-Lib) |
| `app/strategy.py` | Décision BUY / SELL / HOLD, score 0 à 1, raison en clair |
| `app/signals.py` | Message du contrat, validation, publication (`XADD … MAXLEN ~ 10000`) |
| `app/scanner.py` | Un cycle complet, état par symbole, confirmation, publication |
| `app/main.py` | Service FastAPI : boucle de fond, `/health`, `/status` |

## Lancer

```bash
uv sync                                          # installe l'environnement
npm run db:up                                    # (dans crypto-api) PostgreSQL + Redis
uv run uvicorn app.main:app --port 8001          # le moteur analyse toutes les 60 s
curl http://localhost:8001/status                # dernier cycle, dernière erreur
```
Le **premier cycle ne publie rien** : il enregistre la position recommandée de chaque symbole (E6). Les signaux partent aux changements suivants. Pour voir passer des signaux tout de suite (essai) : `EMIT_ON_FIRST_SCAN=true`.

## Réglages (variables d'environnement, voir `.env.example`)

| Variable | Défaut | Rôle |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis (stream et état) |
| `SIGNALS_STREAM` | `signals` | Nom du stream |
| `MARKET_EXCHANGE` | `binance` | Plateforme des données publiques |
| `SCAN_INTERVAL_SECONDS` | `60` | Pause entre deux cycles |
| `SCANNER_ENABLED` | `true` | `false` coupe la boucle (tests) |
| `UNIVERSE_SIZE` | `20` | Nombre d'actifs analysés (top par volume) |
| `SYMBOLS` | vide | Liste fixe (`BTC,ETH,SOL`) à la place du classement |
| `SIGNAL_TIMEFRAME` | `1h` | Durée des bougies |
| `CANDLES_LIMIT` | `200` | Bougies lues par symbole |
| `CONFIRM_CANDLES` | `3` | Bougies consécutives qui doivent concorder avant de publier |
| `EMIT_MIN_SCORE` | `0.6` | Score minimal pour publier (l'API ignore de toute façon sous 0,6) |
| `EMIT_ON_FIRST_SCAN` | `false` | Publier dès le premier cycle (essais) |
| `PUBLISH_HOLD` | `false` | Publier aussi les HOLD (bruyant) |
| `STREAM_MAXLEN` | `10000` | Taille approximative du stream |

## Lire un signal

Chaque message du stream porte sa raison. Exemple réel :
`{"v":1,"id":"ETH-1h-1793001600000-SELL","action":"SELL","score":0.75,"price":"2334.37","reason":"Baissier : EMA20 < EMA50 (-0.68 %), MACD négatif, RSI sain (46), prix au-dessus de la bande basse", "indicators":{…}}`

## Diagnostiquer

- **Aucun signal** : normal au premier cycle (E6) et tant qu'aucune position recommandée ne change. `GET /status` indique le dernier cycle (`baseline`, `published`, `errors`).
- **Voir l'état** : `redis-cli HGETALL engine:stance` (position recommandée par symbole). `HDEL engine:stance BTC` fait repartir un symbole du premier passage.
- **Un symbole en erreur** : `last_cycle.errors` de `/status` (`insufficient_data` : moins de 60 bougies fermées ; sinon le type de l'exception).
- **Signaux refusés par l'API** : `XRANGE signals:dead - +` côté Redis.
- **Les tests** : `uv run pytest` ; `uv run ruff check .` ; `uv run ruff format .`.

## Limites

Pas de backtest : la stratégie n'a pas été éprouvée sur l'historique (E12). Une seule instance conseillée. Le moteur ne sait pas ce que l'API a fait de ses signaux.

## Backtest

`uv run python -m app.backtest` rejoue la stratégie sur 12 mois d'historique Binance public (périodes d'optimisation et de validation séparées). Résultats et mode d'emploi détaillé : `docs/BACKTEST.md`. **À lire avant tout passage en LIVE** : la stratégie actuelle y perd de l'argent.

### Étude de la stratégie (étape 16)

`uv run python -m app.backtest.study` applique le protocole de `docs/STRATEGIE.md` (9 variantes, apprentissage et validation, critères évalués par le code). Il n'ouvre jamais le test final ; `--final VARIANTE` ne l'accepte que pour la variante sélectionnée, une seule fois. **Verdict actuel : aucune variante ne réussit** (`docs/BACKTEST.md`). Avec 4 processeurs, l'étude complète prend une vingtaine de minutes. Le scanner accepte deux options, désactivées par défaut : `cooldown_seconds` (carence entre deux signaux) et `regime_filter` (pas d'achat si BTC n'est pas en tendance haussière).
