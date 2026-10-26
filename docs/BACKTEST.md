# Backtest de la stratégie

**Conclusion, en premier : la stratégie actuelle ne doit pas passer en LIVE.** Rejouée sur 12 mois d'historique Binance, elle perd de l'argent, et elle fait **moins bien qu'une simple détention à exposition égale** dans les deux périodes testées, même sans frais. Les résultats ci-dessous sont ceux de la première exécution, sans aucun réglage : rien n'a été modifié pour les améliorer.

## Ce qui a été mesuré

La stratégie **de production** (le `Scanner` : indicateurs, score, confirmation sur 3 bougies, état par symbole, validation du message) est rejouée bougie par bougie sur un marché simulé qui ne montre que les bougies déjà fermées. Les signaux alimentent un portefeuille simulé qui applique les règles de l'API.

| Élément | Choix |
|---|---|
| Données | Bougies 1 h Binance publiques, 12 mois, fenêtre finissant le 2026-10-26 10:00 UTC (`--until-ms 1793008800000` pour la rejouer), plus 200 bougies de chauffe |
| Symboles | BTC, ETH, BNB, SOL, XRP, DOGE, ADA, LINK, TRX, AVAX, DOT, LTC (12 actifs anciens et liquides, 8 960 bougies alignées chacun) |
| Capital | 10 000 USDT |
| Exécution | À l'**ouverture de la bougie suivante** le signal (jamais au prix qui l'a déclenché) |
| Frais, glissement | 0,10 % et 0,05 % par ordre |
| Portefeuille | Comme l'API : 5 % du capital par position au maximum, achat proportionnel au score, vente de toute la position, stop-loss 3 % et take-profit 6 % |
| Stop et take-profit | Contrôlés sur chaque bougie ; les deux dans la même bougie : stop-loss (hypothèse prudente) ; ouverture au-delà du niveau : sortie à l'ouverture |
| Périodes | **1. optimisation** (8 mois, bougies 200 à 6040) et **2. validation** (4 mois, 6040 à 8960), simulées séparément, capital remis à zéro |

Commande : `uv run python -m app.backtest` (options : `--months`, `--validation-months`, `--symbols`, `--until-ms`, `--fee-rate`, `--slippage-rate`, `--only-total`). Les bougies sont mises en cache dans `data/history/` (hors git).

## Résultats

| Mesure | Optimisation (243 j) | Validation (122 j) | Ensemble (365 j) |
|---|---|---|---|
| **Rendement de la stratégie** | **−17,64 %** | **−2,54 %** | **−19,83 %** |
| Achat-conservation à parts égales, 100 % investi | −55,89 % | +30,26 % | −43,85 % |
| …ramené à la même exposition moyenne (15 à 17 %) | −8,34 % | +5,17 % | −6,87 % |
| Achat-conservation BTC seul | −46,82 % | +33,78 % | −28,64 % |
| Perte maximale (drawdown) | 18,01 % | 5,33 % | 20,34 % |
| Opérations terminées (signaux émis) | 511 (1 725) | 313 (863) | 825 (2 589) |
| Taux de réussite | 21,1 % | 31,3 % | 25,0 % |
| Gain moyen / perte moyenne | +19,19 / −9,52 | +19,38 / −10,02 | +17,64 / −9,07 |
| Facteur de profit | 0,54 | 0,88 | 0,65 |
| Frais payés | 359,65 | 241,79 | 558,76 |
| Sorties : take-profit / stop-loss / vente sur signal | 95 / 289 / 127 | 86 / 139 / 88 | 181 / 429 / 215 |

Résultat net par symbole sur l'ensemble : tous négatifs sauf TRX (+15), de DOT (−311) à ADA (−50).

## Comment lire ces chiffres

- **Le marché a beaucoup baissé** (−44 % en détention à parts égales) : une stratégie qui n'achète que « long » perd naturellement dans ce cas. C'est pourquoi la comparaison honnête est celle **à exposition égale** (la stratégie n'est investie qu'à 15 % en moyenne). Elle est défavorable : −17,6 % contre −8,3 % en optimisation, −2,5 % contre +5,2 % en validation. **Elle fait donc pire que de tenir le même montant, dans un marché baissier comme dans un marché haussier.**
- **Garder son argent en USDT** (0 %) aurait fait mieux que la stratégie sur l'année (−19,8 %).
- **Diagnostic sans frais ni glissement** (même fenêtre, ensemble) : −11,13 % contre −6,89 % pour la détention à exposition égale, facteur de profit 0,79. **La stratégie n'a pas d'avantage brut** : les frais aggravent (de −11,1 % à −19,8 %, soit −8,7 points) mais ne sont pas la cause première.
- **Beaucoup trop d'opérations** : 825 en un an, soit 56 fois le capital échangé ; chaque aller-retour coûte environ 0,3 % (frais et glissement).
- **Plus d'une sortie sur deux est un stop-loss** (429 sur 825) : un stop à 3 % sur des bougies d'une heure est probablement du bruit, pas un signal. Le taux de réussite de 25 % est sous le seuil de rentabilité de ce profil de gains (gain moyen 2,2 fois la perte moyenne : il faudrait environ 31 % de réussite pour être à l'équilibre sans frais).
- **La période de validation n'est pas une confirmation** : elle est « moins mauvaise » parce que le marché est monté (+30 %), pas parce que la stratégie est meilleure. Les deux périodes sont de régimes opposés ; 12 mois ne suffisent pas à les comparer.

## Limites de ce test

- **Biais des survivants** : les 12 symboles sont ceux que l'on connaît encore aujourd'hui. L'univers réel du moteur (top 20 par volume du moment) changerait au fil du temps ; un backtest fidèle aurait besoin des classements passés.
- **Un seul portefeuille de 12 symboles, un seul pas de temps** (1 h), une seule fenêtre de 12 mois : les résultats ne se généralisent pas à d'autres marchés ou périodes.
- **Hypothèses d'exécution** : glissement fixe de 0,05 % et aucun impact de marché ; en réalité le stop-loss de l'API est contrôlé chaque minute (les prix à la minute peuvent sortir plus tôt ou plus mal que sur des bougies d'une heure). La pause sur perte journalière de l'API n'est pas simulée.
- **Écarts avec la production** : la confirmation, l'état et les scores sont ceux de la production, mais le prix du signal est la clôture de la bougie (en production, le prix du moment) ; l'API ne reçoit pas de signal pendant un kill switch (non simulé).

## Pistes, sans les avoir appliquées (c'est une décision à prendre ensemble)

Aucune n'est un réglage : ce sont des hypothèses à tester **avec une méthode** (régler sur une période, valider sur une autre, sur plus d'historique).

1. **Moins trader** : bougies de 4 h ou d'1 jour, confirmation plus longue ou délai de carence après un signal. C'est la piste qui agit directement sur le plus gros défaut mesuré (56 fois le capital échangé).
2. **Stops adaptés à la volatilité** (par exemple en multiples de l'ATR) plutôt que 3 % fixes : plus de la moitié des sorties sont des stops.
3. **Filtre de régime** : n'acheter que lorsque le marché d'ensemble (BTC) est lui-même en tendance haussière. Les pertes sont concentrées dans la période baissière.
4. **Plus d'historique** (24 à 36 mois, plusieurs régimes) avant tout réglage, sinon l'on optimise sur un seul type de marché.
5. **Revoir la logique d'ensemble** si les pistes 1 à 3 ne suffisent pas : le test sans frais montre que le signal brut n'a pas d'avantage.

## Ce qu'il faut pour envisager le LIVE

Un backtest **positif après frais, sur plusieurs régimes et sur une période de validation jamais utilisée pour régler quoi que ce soit**, puis l'essai sur le testnet Binance (voir l'API, D87), puis un passage en LIVE avec des montants réduits. Aucune de ces conditions n'est remplie aujourd'hui.
