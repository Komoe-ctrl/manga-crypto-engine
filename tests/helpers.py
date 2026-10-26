"""Séries de prix et faux de test partagés."""

import numpy as np
import pandas as pd

from app.indicators import Indicators
from app.market import Candles


def frame(closes: list[float] | np.ndarray) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"close": closes, "high": closes * 1.001, "low": closes * 0.999})


def walk(drift: float, n: int = 120, sigma: float = 0.008, seed: int = 2) -> np.ndarray:
    """Marche aléatoire à graine fixe (déterministe) : prix réalistes, avec de vrais replis."""
    returns = np.random.default_rng(seed).normal(drift, sigma, n)
    return 100 * np.cumprod(1 + returns)


def uptrend(n: int = 120) -> np.ndarray:
    return walk(0.0008, n)


def downtrend(n: int = 120) -> np.ndarray:
    return walk(-0.0008, n)


def _find_run(series: np.ndarray, action: str, run: int, start: int = 60) -> np.ndarray:
    """Série tronquée à la première bougie où la stratégie décide `action` `run` bougies de suite.

    Cherche sur la vraie stratégie : si elle ne décidait jamais BUY ou SELL, ce serait un défaut
    visible ici.
    """
    from app.indicators import compute_indicators
    from app.strategy import decide

    streak = 0
    for n in range(start, len(series) + 1):
        decision = decide(compute_indicators(frame(series[:n])))
        actionable = decision.action == action and decision.score >= 0.6
        streak = streak + 1 if actionable else 0
        if streak == run:
            return series[:n]
    raise AssertionError(f"la stratégie ne décide jamais {action} {run} bougies de suite")


# `BUY_SERIES[:-1]` et `BUY_SERIES` donnent BUY sur leurs 3 dernières bougies (confirmation par
# défaut) ;
# `FIRST_BUY` : la toute première bougie BUY, précédée d'une autre décision (donc non confirmée).
BUY_SERIES = _find_run(uptrend(260), "BUY", 4)
SELL_SERIES = _find_run(downtrend(260), "SELL", 4)
FIRST_BUY = _find_run(uptrend(260), "BUY", 1)


def indicators(**over: object) -> Indicators:
    """Instantané neutre, modifiable champ par champ."""
    base = {
        "price": 100.0,
        "ema20": 100.0,
        "ema50": 100.0,
        "rsi": 50.0,
        "macd_hist": 0.0,
        "macd_hist_prev": 0.0,
        "bb_pct": 0.5,
        "volatility_pct": 1.0,
        "recent_cross": None,
    }
    base.update(over)
    return Indicators(**base)  # type: ignore[arg-type]


class FakeState:
    """Faux Redis pour hget / hset."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    def hset(self, name: str, key: str, value: str) -> int:
        self.hashes.setdefault(name, {})[key] = value
        return 1


class FakeStream:
    """Faux Redis pour xadd : garde les entrées publiées."""

    def __init__(self, fail: bool = False) -> None:
        self.entries: list[tuple[str, dict[str, str], dict[str, object]]] = []
        self.fail = fail

    def xadd(self, name, fields, id="*", maxlen=None, approximate=True):
        if self.fail:
            raise ConnectionError("redis indisponible")
        self.entries.append((name, fields, {"maxlen": maxlen, "approximate": approximate}))
        return f"{len(self.entries)}-0"


class FakeMarket:
    """Marché simulé : un univers, des bougies par symbole (ou une erreur à lever)."""

    def __init__(
        self, series: dict[str, np.ndarray | Exception], prices: dict[str, float] | None = None
    ):
        self.series = series
        self.prices = prices or {}
        self.universe_calls: list[tuple[list[str], int]] = []

    def universe(self, configured: list[str], limit: int) -> list[str]:
        self.universe_calls.append((configured, limit))
        return configured or list(self.series)[:limit]

    def last_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: self.prices[s] for s in symbols if s in self.prices}

    def candles(self, symbol: str, timeframe: str, limit: int) -> Candles:
        data = self.series[symbol]
        if isinstance(data, Exception):
            raise data
        return Candles(frame=frame(data), last_open_ms=1_793_000_000_000 + len(data) * 3_600_000)
