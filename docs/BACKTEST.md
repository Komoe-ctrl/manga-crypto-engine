# Backtest de la stratégie

> **Mise à jour de l'étape 16 : sur 36 mois, aucune des 9 variantes testées ne réussit le protocole.** La famille « indicateurs techniques (EMA, RSI, MACD, Bollinger) en achat seul » n'a pas d'avantage démontrable. Le test final n'a **jamais été ouvert**, conformément à la règle d'arrêt écrite avant les résultats. Voir la section « Étape 16 » en bas de ce document.

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

---

## Étape 16 : protocole en trois blocs, 9 variantes, aucune retenue

Protocole : `docs/STRATEGIE.md`, **écrit et commité avant tout résultat** (commit `11777e3`, il n'a pas été modifié depuis). Il fixe les hypothèses, leurs valeurs, les blocs, les critères et la règle d'arrêt. Programme : `uv run python -m app.backtest.study`.

### Ce qui a été fait

- **36 mois** de bougies 1 h (27 480 bougies alignées par actif, dont 1 200 de chauffe), les mêmes 12 actifs, fenêtre finissant le 26/10/2026 10:00 UTC.
- **Trois blocs chronologiques** : apprentissage (18 980 h, environ 26 mois), validation (4 380 h, 6 mois) et test final (2 920 h, 4 mois). **Le test final n'a pas été simulé** : `data/final-test.json` n'existe pas, et le programme refuse de l'ouvrir sans variante sélectionnée.
- **9 variantes déclarées d'avance** (aucune ajoutée ensuite) : V0 (stratégie actuelle), V1 (bougies de 4 h), V2 (carence de 24 h), V3 (stops à 2,5 × ATR et 5 × ATR), V4 (filtre de régime BTC), puis les combinaisons V5 (4 h + régime), V6 (4 h + ATR), V7 (4 h + ATR + régime), V8 (carence + régime). Une valeur par hypothèse, aucun balayage de paramètres.
- Mêmes hypothèses d'exécution que l'étape 14 (frais 0,10 %, glissement 0,05 %, ordre à l'ouverture suivante, 5 % par position, vente de toute la position). Les poids de la stratégie n'ont pas été touchés.

### Résultats : apprentissage (26 mois)

| Variante | Rendement | Détention à même exposition | Perte max | Facteur de profit | Opérations | Frais (USDT) | (a) | (b) | (c) | (d) |
|---|---|---|---|---|---|---|---|---|---|---|
| V0 base | −13,15 % | +16,21 % | 20,0 % | 0,91 | 2 152 | 1 606 | non | non | non | oui |
| V1 4 h | −7,52 % | +7,03 % | 9,5 % | 0,87 | 756 | 535 | non | oui | non | oui |
| V2 carence | −11,97 % | +15,62 % | 17,9 % | 0,92 | 2 076 | 1 526 | non | non | non | oui |
| V3 stops ATR | −20,79 % | +14,63 % | 30,0 % | 0,87 | 2 354 | 1 702 | non | non | non | oui |
| V4 régime | −11,00 % | +12,21 % | 15,7 % | 0,91 | 1 665 | 1 214 | non | non | non | oui |
| V5 4 h + régime | −4,66 % | +4,95 % | 7,2 % | 0,89 | 567 | 394 | non | oui | non | oui |
| V6 4 h + ATR | −11,69 % | +13,65 % | 20,8 % | 0,87 | 681 | 495 | non | non | non | oui |
| V7 4 h + ATR + régime | +0,68 % | +10,12 % | 15,4 % | 1,01 | 506 | 378 | non | non | non | oui |
| V8 carence + régime | −11,56 % | +11,80 % | 16,2 % | 0,90 | 1 610 | 1 151 | non | non | non | oui |

### Résultats : validation (6 mois)

| Variante | Rendement | Détention à même exposition | Perte max | Facteur de profit | Opérations | Frais (USDT) | (a) | (b) | (c) | (d) |
|---|---|---|---|---|---|---|---|---|---|---|
| V0 base | −11,88 % | −6,34 % | 14,8 % | 0,60 | 389 | 289 | non | oui | non | oui |
| V1 4 h | −1,53 % | −3,27 % | 4,3 % | 0,87 | 141 | 107 | oui | oui | non | oui |
| V2 carence | −8,97 % | −6,26 % | 12,0 % | 0,67 | 364 | 273 | non | oui | non | oui |
| V3 stops ATR | −16,67 % | −4,41 % | 18,8 % | 0,47 | 499 | 343 | non | non | non | oui |
| V4 régime | −4,05 % | −4,60 % | 7,7 % | 0,80 | 269 | 206 | oui | oui | non | oui |
| V5 4 h + régime | −1,37 % | −2,16 % | 4,0 % | 0,79 | 80 | 59 | oui | oui | non | oui |
| V6 4 h + ATR | −5,80 % | −4,46 % | 8,3 % | 0,62 | 139 | 103 | non | oui | non | oui |
| V7 4 h + ATR + régime | −4,03 % | −2,68 % | 6,9 % | 0,53 | 78 | 57 | non | oui | non | oui |
| V8 carence + régime | −2,90 % | −4,54 % | 6,7 % | 0,84 | 256 | 196 | oui | oui | non | oui |

(a) bat la détention à exposition égale, (b) perte maximale sous 15 %, (c) facteur de profit au-dessus de 1,1, (d) assez d'opérations. **Les quatre sont obligatoires, sur chaque bloc.**

### Verdict

**Aucune variante n'est retenue** : aucune ne réussit à la fois sur l'apprentissage et sur la validation. Règle 4 du protocole : **on s'arrête**. Le test final n'est pas ouvert, et aucune variante supplémentaire n'a été essayée.

### Ce que disent les chiffres

- **Le critère (c) échoue partout, dans les 18 cases** : le meilleur facteur de profit est **1,01** (V7, apprentissage) et **0,87** en validation. Une stratégie qui gagne de l'argent a un facteur de profit supérieur à 1 après frais, avec de la marge ; aucune de ces neuf versions n'y arrive de façon stable. C'est la signature d'**un signal sans avantage**, qui confirme le diagnostic de l'étape 14 (même sans frais, −11 %).
- **La stratégie de base détruit de la valeur dans un marché qui montait.** Sur l'apprentissage, détenir les mêmes actifs à la même exposition rapportait **+16 %** ; V0 perd **13 %** (et paie **1 606 USDT de frais, 16 % du capital**).
- **Trader moins fait perdre moins, sans faire gagner.** V1 (bougies de 4 h) : −7,5 % au lieu de −13,2 % ; V5 : −4,7 %. L'effet est celui des frais (2 152 opérations contre 756, 506 ou 567), pas celui d'un signal meilleur : le facteur de profit reste sous 1.
- **Les quatre « oui » du critère (a) en validation (V1, V4, V5, V8) ne sont pas des succès.** Le marché a baissé (−2 % à −5 % à exposition égale) et ces variantes sont peu investies : elles perdent **moins** que la détention, mais elles perdent (facteur de profit entre 0,79 et 0,87).
- **Les stops en multiples de l'ATR (H3) aggravent** : V3 fait −20,8 % et −16,7 %, pire que V0 sur les deux blocs. Des stops plus larges laissent courir des pertes plus grosses sans que les gains augmentent.
- **Le filtre de régime (H4) réduit le risque, pas la perte d'avantage** : V4 perd 11,0 % au lieu de 13,2 % (apprentissage) et 4,1 % au lieu de 11,9 % (validation), parce qu'il évite d'acheter en marché baissier ; le facteur de profit reste à 0,91 en apprentissage et passe de 0,60 à 0,80 en validation : mieux, mais toujours sous 1.
- **La quasi-réussite à ne pas retenir : V7** gagne +0,68 % sur l'apprentissage, mais avec un facteur de profit de 1,01, une perte maximale de 15,4 % (critère (b) manqué) et un rendement très inférieur à la détention (+10,1 %) ; en validation elle perd 4,0 % avec un facteur de profit de 0,53. Un seul chiffre positif parmi dix-huit n'est pas un signal : avec neuf variantes essayées, c'est ce que le hasard produit.

### Écart de la stratégie de base avec l'étape 14

La stratégie de base de cette étape (V0) n'est pas strictement identique à celle de l'étape 14 : le scanner mémorise maintenant l'analyse de chaque bougie fermée (voir `docs/DECISIONS.md`, E23). Mesure : rejouée sur la fenêtre de l'étape 14, elle donne **−19,83 %, 825 opérations, 20,34 % de perte maximale** (mêmes chiffres), avec **2 590 signaux au lieu de 2 589** ; **sans le cache, elle redonne exactement 2 589**, ce qui établit la cause. Les 9 variantes utilisent toutes le même scanner : la comparaison entre elles est homogène.

### Ce que cela implique

1. **Cette stratégie ne passe pas en LIVE**, ni aucune de ses variantes. La règle 6 du protocole l'interdit : seule une variante qui réussit le test final peut être proposée.
2. **Il faut arrêter d'ajuster cette famille.** Dix variantes de plus ne changeraient pas le diagnostic (facteur de profit < 1,1 partout) et finiraient par produire un faux positif par hasard. Le protocole prévoit cet arrêt, et c'est ce qui le rend crédible.
3. **Le bloc de test final reste intact** (4 derniers mois, jamais simulé par aucune stratégie) : il peut servir de test final à une **nouvelle famille** de stratégies, sous un **nouveau protocole écrit avant ses résultats**, à condition que sa conception ne l'ait pas regardé.
4. **Décision de produit à prendre** (voir la proposition faite à la fin de l'étape) : abandonner le trading automatique par signaux, ou en tester une famille différente avec le même sérieux.

### Limites de ce test

Biais des survivants (12 actifs connus aujourd'hui), une seule fenêtre de 36 mois et un seul pas de temps de décision (1 h ou 4 h), glissement fixe, achat seul (pas de vente à découvert), 5 % maximum par position, pause sur perte journalière non simulée. Ces limites rendent un résultat positif **moins** crédible qu'il n'en a l'air ; elles ne rendent pas un résultat négatif cohérent sur 18 cases plus douteux.
