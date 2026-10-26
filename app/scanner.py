"""Un cycle d'analyse : univers, bougies fermées, indicateurs, décision, publication.

Le moteur ne publie un signal que lorsque la **position recommandée change** pour un symbole
(HOLD → BUY, BUY → SELL…), pas à chaque bougie : une tendance haussière qui dure dix heures donne
un achat, pas dix. Cet état est gardé dans Redis (`engine:stance`), donc un redémarrage ne
republie rien.

Un changement n'est retenu que s'il se confirme sur plusieurs bougies fermées consécutives
(`confirm_candles`) : un frémissement d'une seule bougie ne déclenche pas d'ordre.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import pandas as pd

from app.indicators import InsufficientDataError, compute_indicators
from app.market import MarketData
from app.signals import Signal, SignalPublisher, decimal_string, signal_id, utc_now
from app.strategy import Decision, decide

logger = logging.getLogger("engine.scanner")

STANCE_KEY = "engine:stance"


class StateStore(Protocol):
    def hget(self, name: str, key: str) -> bytes | str | None: ...

    def hset(self, name: str, key: str, value: str) -> object: ...


@dataclass
class ScanSettings:
    universe_size: int = 20
    symbols: list[str] = field(default_factory=list)
    timeframe: str = "1h"
    candles_limit: int = 200
    emit_min_score: float = 0.6
    confirm_candles: int = 3
    emit_on_first_scan: bool = False
    publish_hold: bool = False


@dataclass
class CycleReport:
    started_at: datetime
    symbols: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    baseline: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        return {
            "started_at": self.started_at.isoformat(),
            "symbols": len(self.symbols),
            "published": self.published,
            "baseline": len(self.baseline),
            "errors": self.errors,
        }


class Scanner:
    def __init__(
        self,
        market: MarketData,
        state: StateStore,
        publisher: SignalPublisher,
        settings: ScanSettings,
    ) -> None:
        self._market = market
        self._state = state
        self._publisher = publisher
        self._settings = settings

    def run_once(self) -> CycleReport:
        report = CycleReport(started_at=utc_now())
        symbols = self._market.universe(self._settings.symbols, self._settings.universe_size)
        report.symbols = symbols
        prices = self._market.last_prices(symbols)

        for base in symbols:
            # Un symbole en échec (réseau, données manquantes) ne doit jamais arrêter l'analyse des
            # autres.
            try:
                self._scan_symbol(base, prices.get(base), report)
            except InsufficientDataError as error:
                report.errors[base] = f"insufficient_data: {error}"
            except Exception as error:
                report.errors[base] = f"{type(error).__name__}: {str(error)[:120]}"
                logger.warning("Analyse de %s impossible : %s", base, type(error).__name__)
        logger.info(
            "Cycle : %d symboles, %d signaux publiés, %d erreurs",
            len(symbols),
            len(report.published),
            len(report.errors),
        )
        return report

    def _scan_symbol(self, base: str, live_price: float | None, report: CycleReport) -> None:
        s = self._settings
        candles = self._market.candles(base, s.timeframe, s.candles_limit)
        indicators = compute_indicators(candles.frame)
        decision = decide(indicators)

        # Position recommandée sur chacune des dernières bougies fermées (la plus récente d'abord).
        recent = self._recent_stances(candles.frame, decision)
        latest = recent[0]
        # Confirmée : la même sur toutes les dernières bougies. Un frémissement d'une seule bougie
        # ne publie rien.
        confirmed = latest if all(stance == latest for stance in recent) else None
        previous = self._previous_stance(base)

        if previous is None:
            self._state.hset(STANCE_KEY, base, confirmed or latest)
            if not (s.emit_on_first_scan and confirmed not in (None, "HOLD")):
                report.baseline.append(base)
                return
            stance = confirmed
        elif confirmed is None or confirmed == previous:
            return
        else:
            stance = confirmed

        if stance == "HOLD" and not s.publish_hold:
            self._state.hset(STANCE_KEY, base, stance)
            return

        signal = Signal(
            id=signal_id(base, s.timeframe, candles.last_open_ms, stance),
            ts=utc_now(),
            symbol=base,
            action=stance,
            score=decision.score,
            # Prix du moment (et non celui de la bougie fermée, vieux de plusieurs dizaines de
            # minutes) :
            # l'API ignore un achat si le prix a bougé de plus de 2 % depuis.
            price=decimal_string(live_price if live_price else indicators.price),
            reason=decision.reason,
            indicators=indicators.as_dict(),
        )
        self._publisher.publish(signal)
        # L'état n'est enregistré qu'après la publication : un échec Redis est retenté au cycle
        # suivant.
        self._state.hset(STANCE_KEY, base, stance)
        report.published.append(f"{base}:{stance}")
        logger.info("Signal %s %s (score %.2f) : %s", stance, base, decision.score, decision.reason)

    def _recent_stances(self, frame: pd.DataFrame, latest: Decision) -> list[str]:
        """Positions recommandées sur les `confirm_candles` dernières bougies fermées, la plus
        récente d'abord."""
        stances = [self._stance_of(latest)]
        for back in range(1, max(self._settings.confirm_candles, 1)):
            stances.append(
                self._stance_of(decide(compute_indicators(frame.iloc[: len(frame) - back])))
            )
        return stances

    def _stance_of(self, decision: Decision) -> str:
        """Un HOLD, ou un signal trop faible, équivaut à « ne rien faire »."""
        return decision.action if self._is_actionable(decision) else "HOLD"

    def _is_actionable(self, decision: Decision) -> bool:
        return decision.action != "HOLD" and decision.score >= self._settings.emit_min_score

    def _previous_stance(self, base: str) -> str | None:
        raw = self._state.hget(STANCE_KEY, base)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
