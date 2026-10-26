# Protocole de test de la stratégie

**Ce document est écrit et commité AVANT d'avoir calculé le moindre résultat de variante.** Il fixe les hypothèses, les valeurs testées, les périodes et les critères de réussite. Les résultats viennent plus tard, dans `docs/BACKTEST.md` ; toute entorse à ce protocole y sera signalée.

## Pourquoi

Le backtest de l'étape 14 a montré que la stratégie actuelle perd de l'argent (−19,8 % sur 12 mois) et n'a pas d'avantage brut, même sans frais (voir `docs/BACKTEST.md`). Question de cette étape : **existe-t-il une version de la stratégie qui a un avantage réel après frais, démontré sur des données qui n'ont pas servi à la choisir ?** La réponse « non » est acceptable : on s'arrêtera alors, au lieu d'ajuster jusqu'à obtenir un joli chiffre.

## Données et périodes

- **36 mois de bougies 1 h** Binance publiques, mêmes 12 actifs que l'étape 14 (BTC, ETH, BNB, SOL, XRP, DOGE, ADA, LINK, TRX, AVAX, DOT, LTC), fenêtre finissant à la date de téléchargement, plus 200 bougies de chauffe.
- **Trois blocs chronologiques, sans chevauchement :**
  1. **Apprentissage** : tout sauf les 10 derniers mois (environ 26 mois). On y observe et compare les variantes.
  2. **Validation** : les 6 mois suivants (4 380 bougies). Sert à départager les variantes retenues.
  3. **Test final** : les **4 derniers mois** (2 920 bougies). **Consulté une seule fois**, à la toute fin, pour une seule variante, et seulement s'il en reste une. Aucune décision ne dépend de ses chiffres : ils servent à confirmer ou infirmer.
- **Contamination à signaler** : le test final recouvre les 4 derniers mois du backtest de l'étape 14, où la stratégie **de base** a déjà été observée (−2,5 % contre +5,2 % pour la détention à exposition égale, marché haussier). Aucune variante n'y a été essayée, mais je connais ce résultat de base : c'est une connaissance, pas un réglage, et elle est écrite ici pour que le lecteur en juge.

## Variantes (liste fermée, valeurs fixées a priori)

Quatre hypothèses, **une valeur chacune, choisie avant de regarder quoi que ce soit** (aucun balayage de paramètres) :

| Code | Hypothèse | Valeur testée | Ce qu'elle cible |
|---|---|---|---|
| **H1** | Décider sur des bougies de **4 h** au lieu d'1 h (exécution et stops restent à l'heure) | décisions toutes les 4 h ; EMA, RSI, MACD, Bollinger calculés sur les bougies de 4 h | Trop d'opérations (56 fois le capital échangé) |
| **H2** | **Délai de carence** après un signal sur un symbole | 24 bougies d'1 h (1 jour) sans nouveau signal sur ce symbole | Les allers-retours rapprochés |
| **H3** | **Stops proportionnels à la volatilité** : stop-loss à 2,5 × ATR(14), take-profit à 5 × ATR(14) (même rapport 1 : 2 que 3 % / 6 %), ATR de la bougie de décision, fixés à l'entrée | multiples 2,5 et 5 | 52 % des sorties étaient des stop-loss à 3 % fixes |
| **H4** | **Filtre de régime** : n'acheter que si BTC est en tendance haussière (EMA 50 au-dessus de l'EMA 200 sur les mêmes bougies) ; les ventes ne sont jamais filtrées | EMA 50 / EMA 200 | Les pertes concentrées en marché baissier |

**9 variantes au total** (V0 est la stratégie actuelle, sans changement) :

| V0 | V1 | V2 | V3 | V4 | V5 | V6 | V7 | V8 |
|---|---|---|---|---|---|---|---|---|
| base | H1 | H2 | H3 | H4 | H1 + H4 | H1 + H3 | H1 + H3 + H4 | H2 + H4 |

**Nombre de variantes essayées : 9, déclaré d'avance.** Plus on essaie de variantes, plus l'une d'elles réussit par hasard : c'est pourquoi la liste est fermée, et pourquoi le test final existe. **Si j'en ajoute une en cours de route, je le dirai et je compterai.**

## Hypothèses d'exécution (identiques à l'étape 14, non modifiées)

Frais 0,10 % et glissement 0,05 % par ordre ; ordre exécuté à l'ouverture de la bougie suivante ; 5 % du capital par position au plus ; achat proportionnel au score ; vente de toute la position ; stop-loss et take-profit contrôlés sur chaque bougie d'1 h (le stop l'emporte si les deux sont touchés) ; confirmation sur 3 bougies de décision. **Les poids de la stratégie (`app/strategy.py`) ne sont pas modifiés.**

## Critères de réussite (fixés maintenant)

Une variante **réussit** sur un bloc si, après frais et glissement, **les quatre conditions** sont vraies :

1. **(a)** son rendement est supérieur à celui de la **détention à exposition égale** (achat-conservation à parts égales, ramené à l'exposition moyenne de la variante) ;
2. **(b)** sa perte maximale est **inférieure à 15 %** ;
3. **(c)** son facteur de profit est **supérieur à 1,1** ;
4. **(d)** elle compte **au moins 60 opérations** sur l'apprentissage (**20** sur la validation et sur le test final), faute de quoi le résultat n'a pas de signification statistique.

## Règle de décision

1. Toutes les variantes sont simulées sur l'**apprentissage** et la **validation**, **sans toucher au test final**.
2. Une variante est **retenue** si elle réussit **sur l'apprentissage ET sur la validation**, chacun pris séparément. (Réussir sur un seul bloc ne suffit pas : c'est exactement ce que le hasard produit.)
3. S'il y a plusieurs variantes retenues, on prend celle dont la **perte maximale cumulée** (pire des deux blocs) est la plus faible ; à égalité, la plus simple (moins d'hypothèses combinées).
4. **Si aucune variante n'est retenue : on s'arrête.** Le test final n'est pas ouvert. Le verdict est « la stratégie à indicateurs n'a pas d'avantage démontrable » et on propose d'abandonner cette famille de stratégies plutôt que de l'ajuster.
5. **Sinon, la variante retenue est simulée UNE FOIS sur le test final.** Elle y réussit aux quatre critères, ou elle est **rejetée**, sans seconde chance ni nouvelle variante. Dans les deux cas le résultat est publié tel quel.
6. **Seule une variante qui a réussi le test final** peut être proposée comme nouvelle stratégie de production. Même alors : essai testnet de l'API, puis LIVE avec des montants réduits (voir les conditions dans `docs/BACKTEST.md`). Le critère (b) de 15 % de perte maximale n'est pas une garantie sur l'avenir.

## Ce que ce test ne prouve pas, même s'il est réussi

Le biais des survivants (12 actifs connus aujourd'hui), une seule fenêtre de 36 mois, des régimes de marché qui ne se répéteront pas à l'identique, un glissement fixe. Réussir ce protocole rend un avantage **plausible**, pas **certain**.
