"""Un cycle d'analyse : univers, bougies fermées, indicateurs, décision, publication.

Le moteur ne publie un signal que lorsque la **position recommandée change** pour un symbole
(HOLD → BUY, BUY → SELL…), pas à chaque bougie : une tendance haussière qui dure dix heures donne
un achat, pas dix. Cet état est gardé dans Redis (`engine:stance`), donc un redémarrage ne
republie rien.

Un changement n'est retenu que s'il se confirme sur plusieurs bougies fermées consécutives
(`confirm_candles`) : un frémissement d'une seule bougie ne déclenche pas d'ordre.

Deux options, **désactivées par défaut** (elles ne changent rien au comportement de production) :
- `cooldown_seconds` : délai de carence entre deux signaux sur un même symbole ;
- `regime_filter` : pas d'achat tant que le marché d'ensemble (BTC) n'est pas en tendance haussière.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import pandas as pd

from app.indicators import Indicators, InsufficientDataError, compute_indicators, trend_is_up
from app.market import MarketData
from app.signals import Signal, SignalPublisher, decimal_string, signal_id, utc_now
from app.strategy import Decision, decide

logger = logging.getLogger("engine.scanner")

STANCE_KEY = "engine:stance"
#: Ouverture (ms) de la bougie du dernier signal publié, par symbole : sert au délai de carence.
LAST_EMIT_KEY = "engine:last_emit"
#: Analyses gardées en mémoire par symbole : de quoi couvrir la fenêtre de confirmation.
CACHE_PER_SYMBOL = 8


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
    #: 0 : pas de délai de carence.
    cooldown_seconds: int = 0
    regime_filter: bool = False
    regime_symbol: str = "BTC"
    #: Bougies lues pour juger le régime (l'EMA 200 en demande plus de 200).
    regime_candles: int = 300


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
        self._regime_up = True
        self._cache: dict[str, dict[tuple, tuple[Decision, Indicators]]] = {}

    def run_once(self) -> CycleReport:
        report = CycleReport(started_at=utc_now())
        symbols = self._market.universe(self._settings.symbols, self._settings.universe_size)
        report.symbols = symbols
        prices = self._market.last_prices(symbols)
        self._update_regime(report)

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

    def _update_regime(self, report: CycleReport) -> None:
        """Marché d'ensemble haussier ? En cas de doute (données absentes), on n'achète pas."""
        s = self._settings
        if not s.regime_filter:
            return
        try:
            candles = self._market.candles(s.regime_symbol, s.timeframe, s.regime_candles)
            self._regime_up = trend_is_up(candles.frame)
        except Exception as error:
            self._regime_up = False
            report.errors["regime"] = f"{type(error).__name__}: {str(error)[:120]}"

    def _scan_symbol(self, base: str, live_price: float | None, report: CycleReport) -> None:
        s = self._settings
        candles = self._market.candles(base, s.timeframe, s.candles_limit)
        decision, indicators = self._analyse(base, candles.frame, back=0)

        # Position recommandée sur chacune des dernières bougies fermées (la plus récente d'abord).
        recent = [self._stance_of(decision)]
        for back in range(1, max(s.confirm_candles, 1)):
            recent.append(self._stance_of(self._analyse(base, candles.frame, back)[0]))
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

        # Délai de carence : pas de nouveau signal trop tôt après le précédent. L'état n'est pas
        # modifié : le changement est réexaminé à la bougie suivante, et publié si le délai est
        # écoulé.
        if self._in_cooldown(base, candles.last_open_ms):
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
        self._state.hset(LAST_EMIT_KEY, base, str(candles.last_open_ms))
        report.published.append(f"{base}:{stance}")
        logger.info("Signal %s %s (score %.2f) : %s", stance, base, decision.score, decision.reason)

    def _analyse(self, base: str, frame: pd.DataFrame, back: int) -> tuple[Decision, Indicators]:
        """Indicateurs et décision pour la bougie fermée située `back` bougies avant la dernière.

        Mémorisés : une même bougie fermée donne toujours le même résultat, et le moteur tourne
        toutes les minutes. La clé comprend le contenu de la bougie (pas seulement son horodatage),
        donc une série qui change sous le même horodatage ne peut jamais servir une réponse périmée.
        """
        end = len(frame) - back
        key = None
        if "open_ms" in frame.columns:
            last = frame.iloc[end - 1]
            key = (
                int(last["open_ms"]),
                float(last["close"]),
                float(last["high"]),
                float(last["low"]),
            )
            cached = self._cache.get(base, {}).get(key)
            if cached is not None:
                return cached
        indicators = compute_indicators(frame.iloc[:end])
        result = (decide(indicators), indicators)
        if key is not None:
            entries = self._cache.setdefault(base, {})
            entries[key] = result
            while len(entries) > CACHE_PER_SYMBOL:
                del entries[min(entries)]
        return result

    def _in_cooldown(self, base: str, candle_open_ms: int) -> bool:
        cooldown_ms = self._settings.cooldown_seconds * 1000
        if cooldown_ms <= 0:
            return False
        raw = self._state.hget(LAST_EMIT_KEY, base)
        if raw is None:
            return False
        last = int(raw.decode() if isinstance(raw, bytes) else raw)
        return candle_open_ms - last < cooldown_ms

    def _stance_of(self, decision: Decision) -> str:
        """Un HOLD, ou un signal trop faible, équivaut à « ne rien faire »."""
        if not self._is_actionable(decision):
            return "HOLD"
        # Les ventes ne sont jamais filtrées : sortir doit toujours rester possible.
        if decision.action == "BUY" and self._settings.regime_filter and not self._regime_up:
            return "HOLD"
        return decision.action

    def _is_actionable(self, decision: Decision) -> bool:
        return decision.action != "HOLD" and decision.score >= self._settings.emit_min_score

    def _previous_stance(self, base: str) -> str | None:
        raw = self._state.hget(STANCE_KEY, base)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
