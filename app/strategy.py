"""Stratégie : décision BUY / SELL / HOLD à partir d'un instantané d'indicateurs, avec un score.

Le score est une somme de **confirmations** (chacune a un poids, le total vaut au plus 1). Une
tendance claire est obligatoire ; le reste (croisement récent, MACD, RSI, Bollinger, volatilité)
la confirme ou l'affaiblit. La vente est le miroir exact de l'achat : on évalue l'achat sur les
indicateurs, la vente sur les indicateurs inversés.

Ce n'est pas une promesse de rentabilité : c'est une règle simple, lisible et testable
(voir docs/DECISIONS.md).
"""

from dataclasses import dataclass
from typing import Literal

from app.indicators import Indicators

Action = Literal["BUY", "SELL", "HOLD"]

# Écart relatif EMA20/EMA50 à partir duquel la tendance est jugée « claire » : en dessous, pas de
# confirmation.
MIN_TREND_GAP = 0.002
# RSI au-delà duquel on n'achète pas (surachat) ; son miroir (100 - seuil) interdit de vendre en
# survente.
RSI_VETO = 70.0
# Volatilité (% par bougie) au-delà de laquelle le score est réduit.
HIGH_VOLATILITY_PCT = 3.0
HIGH_VOLATILITY_FACTOR = 0.8

W_TREND = 0.35
W_CROSS = 0.15
W_MACD_SIGN = 0.10
W_MACD_RISING = 0.10
W_RSI = 0.15
W_BOLLINGER = 0.10
W_CALM = 0.05


@dataclass(frozen=True)
class Decision:
    action: Action
    #: Confiance de 0 à 1 (pour HOLD : la meilleure confirmation trouvée, informative).
    score: float
    #: Justification lisible, en français.
    reason: str


def decide(ind: Indicators) -> Decision:
    buy_score, buy_notes = _bullish_score(ind, bearish=False)
    sell_score, sell_notes = _bullish_score(_mirror(ind), bearish=True)

    if buy_score > 0 and buy_score >= sell_score:
        return Decision("BUY", buy_score, _reason("Haussier", ind, buy_notes))
    if sell_score > 0:
        return Decision("SELL", sell_score, _reason("Baissier", ind, sell_notes))
    return Decision("HOLD", 0.0, _reason("Pas de tendance claire", ind, []))


def _bullish_score(ind: Indicators, bearish: bool) -> tuple[float, list[str]]:
    """Score d'un achat (0 si les conditions obligatoires ne sont pas réunies) et les confirmations
    trouvées.

    `ind` est vu à l'endroit pour un achat, **inversé** pour une vente (`bearish`) : le calcul est
    le même, mais les libellés doivent décrire le marché réel, pas le marché inversé (sinon une
    vente se justifierait par « EMA20 > EMA50 »).
    """
    gap = (ind.ema20 - ind.ema50) / ind.ema50
    # Conditions obligatoires : tendance claire, pas de surachat (ni de survente pour une vente).
    if gap < MIN_TREND_GAP or ind.rsi >= RSI_VETO:
        return 0.0, []

    real_rsi = 100.0 - ind.rsi if bearish else ind.rsi
    score = W_TREND
    notes = [
        f"EMA20 < EMA50 (-{gap * 100:.2f} %)" if bearish else f"EMA20 > EMA50 (+{gap * 100:.2f} %)"
    ]
    if ind.recent_cross == "UP":
        score += W_CROSS
        notes.append("croisement baissier récent" if bearish else "croisement haussier récent")
    if ind.macd_hist > 0:
        score += W_MACD_SIGN
        notes.append("MACD négatif" if bearish else "MACD positif")
        if ind.macd_hist > ind.macd_hist_prev:
            score += W_MACD_RISING
            notes.append("MACD en baisse" if bearish else "MACD en hausse")
    if 40 <= ind.rsi < RSI_VETO - 5:
        score += W_RSI
        notes.append(f"RSI sain ({real_rsi:.0f})")
    elif ind.rsi < 40:
        score += W_RSI * 2 / 3
        notes.append(
            f"RSI haut, rebond ({real_rsi:.0f})" if bearish else f"RSI bas, repli ({real_rsi:.0f})"
        )
    if ind.bb_pct < 0.95:
        score += W_BOLLINGER
        notes.append("prix au-dessus de la bande basse" if bearish else "prix sous la bande haute")
    if ind.volatility_pct <= HIGH_VOLATILITY_PCT:
        score += W_CALM
    else:
        score *= HIGH_VOLATILITY_FACTOR
        notes.append(f"volatilité élevée ({ind.volatility_pct:.1f} %)")
    return round(min(score, 1.0), 3), notes


def _mirror(ind: Indicators) -> Indicators:
    """Les mêmes indicateurs vus à l'envers : une vente est un achat sur le marché inversé."""
    return Indicators(
        price=ind.price,
        ema20=ind.ema50,
        ema50=ind.ema20,
        rsi=100.0 - ind.rsi,
        macd_hist=-ind.macd_hist,
        macd_hist_prev=-ind.macd_hist_prev,
        bb_pct=1.0 - ind.bb_pct,
        volatility_pct=ind.volatility_pct,
        recent_cross={"UP": "DOWN", "DOWN": "UP"}.get(ind.recent_cross or ""),
    )


def _reason(head: str, ind: Indicators, notes: list[str]) -> str:
    detail = (
        ", ".join(notes)
        if notes
        else f"EMA20 {ind.ema20:.4g}, EMA50 {ind.ema50:.4g}, RSI {ind.rsi:.0f}"
    )
    return f"{head} : {detail}"[:500]
