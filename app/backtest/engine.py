"""Simulation : rejoue la stratégie de production sur un historique, bougie par bougie, sans
regarder le futur.

Le moteur de décision n'est **pas** recopié : c'est le `Scanner` de production, branché sur un
marché simulé
(`SimMarket`) qui ne montre que les bougies déjà fermées au moment simulé, et sur un « stream » qui
collecte les
signaux au lieu de les envoyer à Redis. Le backtest teste donc ce qui tourne vraiment (indicateurs,
stratégie,
confirmation sur plusieurs bougies, état par symbole, validation du message).
"""

import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.backtest.history import HOUR_MS
from app.backtest.metrics import Metrics, compute_metrics
from app.backtest.portfolio import Portfolio, Trade
from app.market import Candles
from app.scanner import Scanner, ScanSettings
from app.signals import SignalPublisher

TIMEFRAME_HOURS = {"1h": 1, "4h": 4}


@dataclass(frozen=True)
class BacktestConfig:
    capital: float = 10_000.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    #: Réglages du portefeuille de l'API (valeurs par défaut de RiskSettings).
    max_exposure_pct: float = 5.0
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 6.0
    #: Réglages du moteur (valeurs de production). Bougies de décision : « 1h » ou « 4h » ; les
    #: ordres, les stops et la valorisation restent à l'heure dans tous les cas.
    timeframe: str = "1h"
    candles_limit: int = 200
    emit_min_score: float = 0.6
    confirm_candles: int = 3
    #: Options du scanner, désactivées par défaut (voir `app/scanner.py`).
    cooldown_seconds: int = 0
    regime_filter: bool = False
    #: Stops en multiples de l'ATR (voir `Portfolio`) ; `None` : pourcentages fixes.
    atr_stop_multiple: float | None = None
    atr_take_profit_multiple: float | None = None

    @property
    def hours_per_candle(self) -> int:
        return TIMEFRAME_HOURS[self.timeframe]


class MemoryState:
    """État par symbole du scanner, en mémoire (en production : Redis)."""

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}

    def hget(self, name: str, key: str) -> str | None:
        return self._hashes.get(name, {}).get(key)

    def hset(self, name: str, key: str, value: str) -> int:
        self._hashes.setdefault(name, {})[key] = value
        return 1


class CollectingStream:
    """Remplace Redis pour la publication : garde les signaux (déjà validés par `Signal`) au lieu
    de les envoyer."""

    def __init__(self) -> None:
        self.signals: list[dict] = []

    def xadd(self, name, fields, id="*", maxlen=None, approximate=True):  # noqa: A002
        self.signals.append(json.loads(fields["data"]))
        return f"{len(self.signals)}-0"


def resample(frame: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Bougies de `hours` heures bâties sur des bougies d'1 h, alignées sur les heures UTC.

    Pour 4 h : groupes 00-04, 04-08, 08-12…

    Un groupe incomplet (début d'historique, trou) est écarté : une bougie de 4 h bâtie sur 3 heures
    serait fausse. `open_ms` est l'ouverture du groupe.
    """
    period = hours * HOUR_MS
    grouped = frame.groupby(frame["open_ms"] // period)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        count=("open_ms", "count"),
        first=("open_ms", "min"),
        last=("open_ms", "max"),
    )
    complete = (out["count"] == hours) & (out["last"] - out["first"] == (hours - 1) * HOUR_MS)
    out = out[complete].copy()
    out["open_ms"] = out.index.to_numpy() * period
    return out[["open_ms", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


class SimMarket:
    """Marché rejoué : à l'instant `index` (une heure), seules les bougies **fermées** existent.

    Avec des bougies de 4 h, une bougie de 4 h n'apparaît qu'une fois ses quatre heures écoulées : à
    10 h, la bougie 08 h-12 h est en formation et n'est pas visible.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], hours_per_candle: int = 1) -> None:
        self._frames = frames
        self._hours = hours_per_candle
        self._open = {s: f["open_ms"].to_numpy() for s, f in frames.items()}
        self._close = {s: f["close"].to_numpy(dtype=float) for s, f in frames.items()}
        self._resampled = (
            {s: resample(f, hours_per_candle) for s, f in frames.items()}
            if hours_per_candle > 1
            else {}
        )
        self._resampled_open = {s: f["open_ms"].to_numpy() for s, f in self._resampled.items()}
        self.index = 0

    def universe(self, configured: list[str], limit: int) -> list[str]:
        return configured or list(self._frames)[:limit]

    def last_prices(self, symbols: list[str]) -> dict[str, float]:
        return {s: float(self._close[s][self.index]) for s in symbols}

    def candles(self, symbol: str, timeframe: str, limit: int) -> Candles:
        if self._hours == 1:
            window = self._frames[symbol].iloc[max(0, self.index + 1 - limit) : self.index + 1]
            return Candles(frame=window, last_open_ms=int(window["open_ms"].iloc[-1]))
        # Bougies de plusieurs heures : fermées une fois leur dernière heure (indice <= index)
        # écoulée.
        open_now = int(self._open[symbol][self.index])
        closed = int(
            np.searchsorted(
                self._resampled_open[symbol], open_now - (self._hours - 1) * HOUR_MS, side="right"
            )
        )
        if closed == 0:
            raise ValueError(f"aucune bougie fermée pour {symbol}")
        window = self._resampled[symbol].iloc[max(0, closed - limit) : closed]
        return Candles(frame=window, last_open_ms=int(window["open_ms"].iloc[-1]))


@dataclass
class BacktestResult:
    metrics: Metrics
    equity: list[float]
    trades: list[Trade]
    signals: int
    #: Positions encore ouvertes à la fin (valorisées au dernier cours, hors des opérations
    # terminées).
    open_positions: list[str] = field(default_factory=list)
    #: Rendement d'un achat-conservation à parts égales des mêmes symboles, frais compris.
    benchmark_return_pct: float = 0.0
    btc_return_pct: float | None = None
    by_symbol: dict[str, float] = field(default_factory=dict)


def align(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Restreint tous les symboles aux mêmes bougies (intersection), indexées de 0 à N-1."""
    common = set.intersection(*(set(f["open_ms"]) for f in frames.values()))
    return {
        symbol: f[f["open_ms"].isin(common)].sort_values("open_ms").reset_index(drop=True)
        for symbol, f in frames.items()
    }


def run_backtest(
    frames: dict[str, pd.DataFrame],
    start: int,
    end: int,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Simule les bougies d'indice `start` à `end - 1`. Les bougies avant `start` ne servent qu'au
    calcul des
    indicateurs (chauffe) : rien n'est négocié dessus."""
    config = config or BacktestConfig()
    symbols = list(frames)
    market = SimMarket(frames, config.hours_per_candle)
    stream = CollectingStream()
    scanner = Scanner(
        market,
        MemoryState(),
        SignalPublisher(stream, "signals", 10_000),
        ScanSettings(
            symbols=symbols,
            timeframe=config.timeframe,
            candles_limit=config.candles_limit,
            emit_min_score=config.emit_min_score,
            confirm_candles=config.confirm_candles,
            cooldown_seconds=config.cooldown_seconds,
            regime_filter=config.regime_filter,
        ),
    )
    portfolio = Portfolio(
        cash=config.capital,
        fee_rate=config.fee_rate,
        slippage_rate=config.slippage_rate,
        max_exposure_pct=config.max_exposure_pct,
        stop_loss_pct=config.stop_loss_pct,
        take_profit_pct=config.take_profit_pct,
        atr_stop_multiple=config.atr_stop_multiple,
        atr_take_profit_multiple=config.atr_take_profit_multiple,
    )
    hours = config.hours_per_candle
    open_hours = next(iter(frames.values()))["open_ms"].to_numpy() // HOUR_MS
    bars = {
        s: {k: f[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close")}
        for s, f in frames.items()
    }

    equity: list[float] = []
    exposure: list[float] = []
    pending: list[dict] = []
    total_signals = 0

    for i in range(start, end):
        opens = {s: bars[s]["open"][i] for s in symbols}
        # 1. Les ordres décidés à la clôture de la bougie précédente partent à l'ouverture de
        # celle-ci.
        for signal in pending:
            symbol = signal["symbol"]
            if signal["action"] == "BUY":
                atr_pct = float((signal.get("indicators") or {}).get("atr_pct") or 0.0)
                portfolio.buy(symbol, signal["score"], opens[symbol], i, opens, atr_pct)
            elif signal["action"] == "SELL":
                portfolio.sell_all(symbol, opens[symbol], i, "SELL")
        pending = []
        # 2. Pendant la bougie : stop-loss et take-profit.
        portfolio.check_exits(
            i,
            {s: {k: bars[s][k][i] for k in ("open", "high", "low")} for s in symbols},
        )
        # 3. À la clôture : valorisation, puis analyse sur les bougies fermées (indices <= i).
        closes = {s: bars[s]["close"][i] for s in symbols}
        value = portfolio.equity(closes)
        equity.append(value)
        exposure.append(portfolio.invested(closes) / value)
        # On décide quand une bougie de décision vient de se fermer (toutes les heures en 1 h, à
        # 03 h, 07 h, 11 h… en 4 h) ; entre deux, le marché visible ne change pas.
        decision_closed = hours == 1 or int(open_hours[i]) % hours == hours - 1
        if i < end - 1 and decision_closed:
            market.index = i
            stream.signals.clear()
            scanner.run_once()
            pending = list(stream.signals)
            total_signals += len(pending)

    by_symbol: dict[str, float] = {}
    for trade in portfolio.trades:
        by_symbol[trade.symbol] = by_symbol.get(trade.symbol, 0.0) + trade.pnl
    return BacktestResult(
        metrics=compute_metrics(equity, exposure, portfolio.trades, portfolio.fees_paid),
        equity=equity,
        trades=portfolio.trades,
        signals=total_signals,
        open_positions=sorted(portfolio.positions),
        benchmark_return_pct=buy_and_hold_return_pct(bars, start, end, config),
        btc_return_pct=_single_return_pct(bars["BTC"], start, end, config)
        if "BTC" in bars
        else None,
        by_symbol=dict(sorted(by_symbol.items(), key=lambda kv: kv[1])),
    )


def _single_return_pct(
    bar: dict[str, np.ndarray], start: int, end: int, config: BacktestConfig
) -> float:
    """Achat à l'ouverture de la première bougie, vente à la clôture de la dernière, frais et
    glissement compris."""
    entry = bar["open"][start] * (1 + config.slippage_rate) * (1 + config.fee_rate)
    exit_ = bar["close"][end - 1] * (1 - config.slippage_rate) * (1 - config.fee_rate)
    return (exit_ / entry - 1) * 100


def buy_and_hold_return_pct(
    bars: dict[str, dict[str, np.ndarray]], start: int, end: int, config: BacktestConfig
) -> float:
    """Achat-conservation à parts égales de tous les symboles (entièrement investi), pour
    comparaison."""
    returns = [_single_return_pct(bar, start, end, config) for bar in bars.values()]
    return sum(returns) / len(returns)
