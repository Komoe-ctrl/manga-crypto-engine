"""Données de marché PUBLIQUES (aucune clé API) : univers des cryptos liquides, prix et bougies."""

import re
from dataclasses import dataclass
from typing import Protocol

import ccxt
import pandas as pd

QUOTE = "USDT"
# Stablecoins et monnaies fiat : ne se négocient pas (l'API les refuserait de toute façon).
EXCLUDED_BASES = frozenset(
    {
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "USDP",
        "DAI",
        "BUSD",
        "USDE",
        "USD1",
        "PYUSD",
        "USDS",
        "EUR",
        "EURI",
        "AEUR",
        "GBP",
        "TRY",
        "BRL",
        "UST",
        "XUSD",
        "BFUSD",
        "RLUSD",
    }  # fmt: skip
)
# Jetons à effet de levier (« BTCUP », « ETHBEAR ») : produits dérivés, hors périmètre.
_LEVERAGED = re.compile(r"^[A-Z0-9]+(UP|DOWN|BULL|BEAR)$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,12}$")


@dataclass(frozen=True)
class Candles:
    """Bougies **fermées** d'un symbole, de la plus ancienne à la plus récente."""

    frame: pd.DataFrame
    #: Ouverture (ms) de la dernière bougie fermée : identifie la bougie analysée.
    last_open_ms: int


class MarketData(Protocol):
    def universe(self, configured: list[str], limit: int) -> list[str]: ...

    def last_prices(self, symbols: list[str]) -> dict[str, float]: ...

    def candles(self, symbol: str, timeframe: str, limit: int) -> Candles: ...


def is_tradable_base(base: str) -> bool:
    return bool(_SYMBOL.match(base)) and base not in EXCLUDED_BASES and not _LEVERAGED.match(base)


class CcxtMarketData:
    """Lecture seule via CCXT : instance sans clé, jamais d'appel signé."""

    def __init__(self, exchange_id: str) -> None:
        self._exchange: ccxt.Exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
        self._tickers: dict[str, dict] = {}

    def universe(self, configured: list[str], limit: int) -> list[str]:
        """Symboles à analyser et, au passage, leurs derniers prix.

        Liste configurée : ces symboles (hors stablecoins). Sinon : les `limit` actifs au plus gros
        volume.
        """
        self._exchange.load_markets()
        if configured:
            bases = [b for b in dict.fromkeys(configured) if is_tradable_base(b)]
            self._tickers = self._exchange.fetch_tickers([f"{b}/{QUOTE}" for b in bases])
            return bases
        return self._top_by_volume(limit)

    def _top_by_volume(self, limit: int) -> list[str]:
        """Les `limit` actifs au plus gros volume échangé en USDT (24 h), spot, hors stablecoins."""
        self._tickers = self._exchange.fetch_tickers()
        ranked: list[tuple[float, str]] = []
        for pair, ticker in self._tickers.items():
            market = self._exchange.markets.get(pair)
            if not market or not market.get("spot") or not market.get("active"):
                continue
            if market.get("quote") != QUOTE or not is_tradable_base(market["base"]):
                continue
            volume = ticker.get("quoteVolume")
            if volume:
                ranked.append((float(volume), market["base"]))
        ranked.sort(reverse=True)
        return [base for _, base in ranked[:limit]]

    def last_prices(self, symbols: list[str]) -> dict[str, float]:
        """Derniers prix, issus du dernier `fetch_tickers` (un seul appel par cycle)."""
        prices: dict[str, float] = {}
        for base in symbols:
            last = self._tickers.get(f"{base}/{QUOTE}", {}).get("last")
            if last:
                prices[base] = float(last)
        return prices

    def candles(self, symbol: str, timeframe: str, limit: int) -> Candles:
        raw = self._exchange.fetch_ohlcv(f"{symbol}/{QUOTE}", timeframe, limit=limit)
        frame = pd.DataFrame(raw, columns=["open_ms", "open", "high", "low", "close", "volume"])
        period_ms = int(self._exchange.parse_timeframe(timeframe) * 1000)
        now_ms = int(self._exchange.milliseconds())
        # La dernière bougie est en cours de formation : ses valeurs bougent, on ne l'analyse pas.
        frame = frame[frame["open_ms"] + period_ms <= now_ms].reset_index(drop=True)
        if frame.empty:
            raise ValueError(f"aucune bougie fermée pour {symbol}")
        return Candles(frame=frame, last_open_ms=int(frame["open_ms"].iloc[-1]))
