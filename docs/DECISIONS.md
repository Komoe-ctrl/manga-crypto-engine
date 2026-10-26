# Décisions du moteur d'analyse (crypto-engine)

Le pourquoi des choix. Le contrat des signaux est dans `../crypto-api/docs/SPEC.md` section 7 (source de vérité) ;
le mode d'emploi est dans `docs/FONCTIONNEMENT.md`.

## Étape 13 — Le moteur publie ses signaux

### E1. Aucune dépendance ajoutée
Tout ce qu'il faut est déjà dans la stack de `CLAUDE.md` : `ccxt` (données publiques), `pandas` et `TA-Lib` (indicateurs), `redis-py` (stream), `pydantic` (validation du message). Les indicateurs sont calculés par TA-Lib, pas à la main : c'est la référence du métier et elle est testée par d'autres.
Outillage : `uv` n'était pas installé sur la machine ; il a été installé au niveau utilisateur (`pip install --user uv`), sans toucher au système. Il télécharge lui-même Python 3.12 (`.python-version`).

### E2. Univers : les 20 actifs au plus gros volume en USDT, spot, hors stablecoins
Classement par `quoteVolume` (24 h) des paires `*/USDT` actives, via un seul `fetch_tickers`. Sont exclus les stablecoins et monnaies fiat (l'API les refuserait), et les jetons à effet de levier (`BTCUP`, `ETHBEAR`…). `SYMBOLS=BTC,ETH,…` fixe la liste à la place du classement.
Ce classement renvoie parfois des actifs que l'on n'attend pas dans un « top crypto » (le 26/10/2026 : `XAUT`, jeton adossé à l'or, et deux jetons dont le nom évoque des produits cotés). Ils sont liquides et négociables, donc retenus ; si l'on n'en veut pas, `SYMBOLS` est la réponse (voir E13).

### E3. Bougies d'une heure, uniquement fermées
200 bougies `1h` (EMA 50 a besoin d'une soixantaine de bougies pour se stabiliser). **La dernière bougie, encore en formation, n'est jamais analysée** : ses valeurs bougent, un signal fondé dessus pourrait disparaître une minute plus tard. Le moteur tourne toutes les 60 s mais ne change d'avis qu'à la fermeture d'une bougie.

### E4. Stratégie : un score fait de confirmations, une tendance claire obligatoire
Règle simple, lisible, testable (`app/strategy.py`). Pour un **achat** :

| Confirmation | Poids |
|---|---|
| Tendance : EMA20 au-dessus de l'EMA50 d'au moins 0,2 % (**obligatoire**) | 0,35 |
| Croisement haussier des EMA dans les 3 dernières bougies | 0,15 |
| MACD positif | 0,10 |
| MACD en hausse | 0,10 |
| RSI entre 40 et 65 (« sain ») ; sous 40 : repli, demi-poids | 0,15 |
| Prix sous la bande haute de Bollinger | 0,10 |
| Volatilité calme (≤ 3 % par bougie) | 0,05 |

Pas d'achat si le RSI est à 70 ou plus (surachat). Une volatilité élevée **réduit** le score de 20 %. La **vente est le miroir exact** : on évalue l'achat sur les indicateurs, la vente sur les indicateurs inversés, donc pas d'achat sur une tendance haussière surachetée, et pas de vente en survente (on ne vend pas le creux). Le score va de 0 à 1 ; l'API ne réagit qu'au-dessus de 0,6, et dimensionne l'achat proportionnellement.
**Ce n'est pas une promesse de rentabilité** : la règle sert à faire tourner la chaîne complète et à être comprise de l'utilisateur (chaque signal porte sa raison en clair). Voir E12.

### E5. Un signal seulement quand la position recommandée change, après confirmation de 3 bougies
- **Position recommandée** = BUY, SELL ou HOLD (un HOLD, ou un signal sous 0,6, équivaut à « ne rien faire »). Le moteur n'émet que lorsqu'elle **change** : une hausse qui dure dix heures donne un achat, pas dix. L'état est gardé dans Redis (`engine:stance`), donc un redémarrage ne republie rien.
- **Confirmation** : le changement n'est retenu que s'il se vérifie sur `CONFIRM_CANDLES` bougies fermées consécutives (3 par défaut). Mesure sur 24 marches aléatoires de 441 bougies (dérive haussière, nulle, baissière) : **25 signaux sans confirmation, 15 avec 2 bougies, 12 avec 3, 9,5 avec 4**. Trois bougies divisent par deux les va-et-vient (chacun coûte des frais d'échange) pour trois heures de retard, acceptable pour une stratégie à l'heure. Aucune bascule directe BUY → SELL n'a été observée : on passe par HOLD.
- **Contrepartie assumée** : un BUY émis pendant un kill switch de l'utilisateur est perdu (la position recommandée est déjà « BUY »), il ne sera pas rejoué quand le kill switch sera levé. C'est voulu : on n'achète pas plus tard au prix d'un signal périmé.

### E6. Premier passage : enregistrer l'état, ne rien publier
Au tout premier cycle sur un symbole (ou après avoir vidé Redis), le moteur note la position recommandée **sans publier**. Sinon, le premier démarrage sur un marché haussier achèterait d'un coup tout ce qui monte (jusqu'à 20 positions). `EMIT_ON_FIRST_SCAN=true` désactive cette protection, pour les essais.

### E7. Identifiant déterministe : `SYMBOLE-1h-<ouverture de la bougie en ms>-ACTION`
Une même bougie et une même action donnent toujours le même `id` (ex. `BTC-1h-1793001600000-BUY`, 64 caractères au plus). Un redémarrage, une double exécution ou deux instances ne peuvent donc jamais produire deux ordres : l'API déduplique par `id` (D93 de l'API). C'est la seconde ligne de défense, après l'état Redis de E5.

### E8. Prix du signal : le prix du moment
Le champ `price` est le dernier prix du ticker, **pas** la clôture de la bougie fermée (vieille de plusieurs dizaines de minutes) : l'API ignore un achat si le prix a bougé de plus de 2 % depuis `price`, donc un prix périmé ferait rejeter des achats légitimes. Écrit en chaîne décimale sans notation scientifique (`PEPE` : `0.00000441`, jamais `4.41e-06`, que l'API refuserait).

### E9. Le message est validé avant d'être publié
`Signal` (pydantic) applique les mêmes règles que l'API (id, symbole, score, prix, raison, 2 Ko d'indicateurs, 8 Ko au total, `ts` en UTC) : un signal que l'API refuserait ne part jamais (il finirait dans `signals:dead`). Deux défauts attrapés par les tests : un prix `"abc"` levait `InvalidOperation` au lieu d'une erreur de validation, et un `NaN` dans `indicators` devenait silencieusement `null` dans le JSON (maintenant refusé).

### E10. Boucle de fond dans le service FastAPI, résiliente
La boucle démarre avec le service (`lifespan`) : un cycle, une pause (`SCAN_INTERVAL_SECONDS`), et ainsi de suite. CCXT et redis-py sont synchrones : le cycle tourne dans un thread (`asyncio.to_thread`) pour ne pas bloquer `/health`. **Une erreur de cycle ne l'arrête jamais** (journalisée, remise à zéro au cycle suivant) ; **un symbole en échec n'arrête pas les autres** ; un échec de publication n'enregistre pas l'état, donc le signal est retenté. `GET /status` expose le dernier cycle et la dernière erreur, sans donnée sensible. `SCANNER_ENABLED=false` coupe la boucle (les tests).

### E11. Vérification sur Binance, Redis et l'API réels, et le défaut qu'elle a trouvé
Moteur réel (top 20 de Binance, bougies 1h réelles) + Redis + API complète (PostgreSQL temporaire) : 20 symboles analysés, 0 erreur, 12 signaux publiés (11 `SELL`, 1 `BUY`) ; l'API a consommé tout le stream sans message en attente ni dans `signals:dead` ; le `BUY` (ENA) est devenu un ordre PAPER exécuté d'environ 325 USDT, proportionnel au score (0,65) ; les `SELL` n'ont donné aucun ordre (aucune position déclarée, comportement attendu) ; **un second cycle ne publie rien** ; le service FastAPI complet tourne, `/status` renvoie le dernier cycle. Aux tests unitaires seuls, deux choses ont échappé :
- **Raisons des ventes fausses** : une vente se justifiait par « EMA20 > EMA50 », « MACD positif », « RSI 54 », parce que la vente est évaluée sur les indicateurs inversés et que les libellés venaient du calcul inversé. Ces textes sont enregistrés avec l'ordre dans l'API et montrés à l'utilisateur : ils auraient menti. Les libellés décrivent maintenant le marché réel (« EMA20 < EMA50 (-0,38 %), MACD négatif, RSI sain (46) ») et un test l'impose.
- **Séries de test trop lisses** : une dérive de 0,4 % par bougie colle le RSI entre 70 et 100, donc la règle anti-surachat bloque tout. Les tests utilisent des marches aléatoires à graine fixe (déterministes, réalistes).
*Non couvert :* aucun ordre LIVE (voir l'API) ; pas de marché baissier violent ni de krach ; pas de Binance indisponible en conditions réelles (simulé par les tests).

### E12. Limites
- **Aucun backtest** : la règle n'a pas été éprouvée sur l'historique ; il faut en faire un avant tout usage LIVE.
- **Position déclarative côté API** : un signal `SELL` vend toute la position déclarée du symbole, y compris ce que l'utilisateur a acheté lui-même.
- **Une instance du moteur** conseillée : deux instances partagent l'état Redis et leurs `id` sont identiques (l'API ne passe qu'un ordre), mais elles feraient le travail en double.
- **Données publiques seulement** : un seul appel `fetch_tickers` par cycle et un `fetch_ohlcv` par symbole, bien sous les limites de Binance ; si la plateforme répond lentement, le cycle s'allonge (9 s pour 20 symboles lors de l'essai).

### E13. À prévoir
| Sujet | Pourquoi |
|---|---|
| Backtest de la stratégie | Voir E12. |
| Liste d'exclusion (`EXCLUDED_SYMBOLS`) | Voir E2 : écarter les jetons exotiques sans fixer toute la liste. |
| Plusieurs horizons (15 m, 4 h) | Un `id` par horizon est déjà prévu (`1h` dans l'identifiant). |
| Indicateurs de volume | Le volume n'entre pas encore dans le score. |

---

## Étape 14 — Backtest de la stratégie

Résultats, méthode, limites et pistes : `docs/BACKTEST.md`. Résumé : **la stratégie actuelle perd de l'argent et ne doit pas passer en LIVE** (−19,8 % sur 12 mois, et moins bien qu'une détention à exposition égale, même sans frais).

### E14. Le backtest rejoue la stratégie de production, il ne la recopie pas
Le `Scanner` réel est branché sur un marché simulé (`SimMarket`, qui ne montre que les bougies déjà fermées) et sur un « stream » qui collecte les signaux au lieu de les envoyer à Redis. Les signaux passent donc par la vraie validation (`Signal`). Si la stratégie de production change, le backtest la suit sans qu'on y pense, et il ne peut pas valider autre chose que ce qui tourne.

### E15. Exécution réaliste : ouverture de la bougie suivante, frais, glissement
Un ordre décidé à la clôture part à l'**ouverture de la bougie d'après** (jamais au prix qui l'a déclenché, ce qui serait une triche). Frais de 0,1 % et glissement de 0,05 % par ordre. Stop-loss et take-profit sont contrôlés sur chaque bougie ; si les deux sont touchés dans la même, le stop-loss l'emporte (on ne sait pas lequel est venu en premier) ; si la bougie ouvre déjà au-delà du niveau, la sortie se fait à l'ouverture (un trou de prix n'est pas gommé).

### E16. Mêmes règles que l'API pour le portefeuille
5 % du capital par position au maximum, achat proportionnel au score, vente de toute la position, stop-loss 3 % et take-profit 6 % (les valeurs par défaut de l'API). Le portefeuille simulé n'invente pas de règle : un résultat obtenu avec d'autres règles que la production n'aurait aucun sens.

### E17. Deux périodes séparées, et aucun réglage pendant cette étape
Optimisation (8 mois) et validation (4 mois), simulées séparément. **Rien n'a été réglé** : les résultats sont ceux de la première exécution. La séparation sert aux étapes suivantes : si l'on règle quelque chose, ce sera sur la première période et on le jugera sur la seconde. Ici les deux périodes sont de régimes opposés (marché −56 % puis +30 %), ce qui limite la valeur de la validation (voir BACKTEST.md).

### E18. Garde-fou contre le regard sur le futur
Un test réécrit tout l'avenir (krach de 80 % après une bougie donnée) et vérifie que l'équité et les opérations antérieures sont strictement identiques. Un autre vérifie que le marché simulé ne montre que les bougies d'indice inférieur ou égal à l'instant simulé, qu'aucun ordre ne part pendant la chauffe, et que le dernier signal (sans bougie suivante) n'est jamais exécuté.

### E19. Diagnostic sans frais ni glissement
Pour savoir si la perte vient du coût des échanges ou du signal lui-même, la même simulation est rejouée avec `--fee-rate 0 --slippage-rate 0` sur la même fenêtre (`--until-ms` la fige). Résultat : −11,1 % sans frais, contre −6,9 % pour la détention à exposition égale : **le signal brut n'a pas d'avantage**, les frais aggravent (−8,7 points).

### E20. Limites et suites
Biais des survivants, une seule fenêtre de 12 mois, un seul pas de temps, glissement fixe (voir BACKTEST.md). Suites à décider ensemble, dans cet ordre : plus d'historique (24 à 36 mois), moins trader (bougies plus longues, carence), stops adaptés à la volatilité, filtre de régime ; puis l'essai testnet de l'API. Aucun passage en LIVE tant qu'un backtest positif après frais, sur une validation jamais utilisée pour régler, n'existe.
