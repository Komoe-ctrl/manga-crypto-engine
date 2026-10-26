"""Calcul des indicateurs techniques (EMA 20/50, RSI, MACD, Bollinger, volatilité) avec TA-Lib.

Fonction pure : un DataFrame de bougies **fermées** en entrée, un instantané d'indicateurs en
sortie.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
import talib

# EMA 50 : la valeur n'est fiable qu'après quelques dizaines de bougies de chauffe.
MIN_CANDLES = 60
# Un croisement des EMA compte comme « récent » pendant ce nombre de bougies.
CROSS_LOOKBACK = 3
VOLATILITY_WINDOW = 20


class InsufficientDataError(Exception):
    """Pas assez de bougies (ou des valeurs manquantes) pour calculer les indicateurs."""


@dataclass(frozen=True)
class Indicators:
    price: float
    ema20: float
    ema50: float
    rsi: float
    macd_hist: float
    macd_hist_prev: float
    #: Position du prix dans les bandes de Bollinger : 0 = bande basse, 1 = bande haute.
    bb_pct: float
    #: Écart-type des variations de prix sur 20 bougies, en %.
    volatility_pct: float
    #: « UP » / « DOWN » si l'EMA 20 a croisé l'EMA 50 dans les dernières bougies, sinon None.
    recent_cross: str | None

    def as_dict(self) -> dict[str, float | str | None]:
        """Valeurs arrondies, pour le champ `indicators` du signal."""
        return {
            "price": round(self.price, 8),
            "ema20": round(self.ema20, 8),
            "ema50": round(self.ema50, 8),
            "rsi": round(self.rsi, 2),
            "macd_hist": round(self.macd_hist, 8),
            "bb_pct": round(self.bb_pct, 3),
            "volatility_pct": round(self.volatility_pct, 3),
            "recent_cross": self.recent_cross,
        }


def compute_indicators(candles: pd.DataFrame) -> Indicators:
    """`candles` : colonnes `high`, `low`, `close` (bougies fermées, de la plus ancienne à la plus
    récente)."""
    if len(candles) < MIN_CANDLES:
        raise InsufficientDataError(f"{len(candles)} bougies, {MIN_CANDLES} requises")

    close = candles["close"].to_numpy(dtype=np.float64)
    if not np.isfinite(close[-MIN_CANDLES:]).all() or (close[-MIN_CANDLES:] <= 0).any():
        raise InsufficientDataError("prix manquants ou invalides")

    ema20 = talib.EMA(close, timeperiod=20)
    ema50 = talib.EMA(close, timeperiod=50)
    rsi = talib.RSI(close, timeperiod=14)
    _, _, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    upper, _, lower = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2)

    last = {
        "ema20": ema20[-1],
        "ema50": ema50[-1],
        "rsi": rsi[-1],
        "macd_hist": macd_hist[-1],
        "macd_hist_prev": macd_hist[-2],
        "upper": upper[-1],
        "lower": lower[-1],
    }
    if not all(np.isfinite(v) for v in last.values()):
        raise InsufficientDataError("indicateur non calculable")

    width = last["upper"] - last["lower"]
    # Bandes écrasées (prix plat) : le prix est « au milieu », sans information.
    bb_pct = 0.5 if width <= 0 else float((close[-1] - last["lower"]) / width)

    returns = np.diff(close[-(VOLATILITY_WINDOW + 1) :]) / close[-(VOLATILITY_WINDOW + 1) : -1]
    volatility_pct = float(np.std(returns) * 100)

    return Indicators(
        price=float(close[-1]),
        ema20=float(last["ema20"]),
        ema50=float(last["ema50"]),
        rsi=float(last["rsi"]),
        macd_hist=float(last["macd_hist"]),
        macd_hist_prev=float(last["macd_hist_prev"]),
        bb_pct=min(max(bb_pct, -0.5), 1.5),
        volatility_pct=volatility_pct,
        recent_cross=_recent_cross(ema20, ema50),
    )


def _recent_cross(ema20: np.ndarray, ema50: np.ndarray) -> str | None:
    """Croisement de l'EMA 20 et de l'EMA 50 sur les dernières bougies (le plus récent
    l'emporte)."""
    diff = ema20 - ema50
    for i in range(1, CROSS_LOOKBACK + 1):
        before, after = diff[-i - 1], diff[-i]
        if not (np.isfinite(before) and np.isfinite(after)):
            continue
        if before <= 0 < after:
            return "UP"
        if before >= 0 > after:
            return "DOWN"
    return None
