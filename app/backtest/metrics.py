"""Mesures de performance d'une simulation."""

import math
from dataclasses import dataclass

from app.backtest.portfolio import Trade

HOURS_PER_YEAR = 8760


@dataclass(frozen=True)
class Metrics:
    return_pct: float
    max_drawdown_pct: float
    #: Rendement annualisé du ratio rendement/risque horaire (0 si l'équité ne bouge pas).
    sharpe: float
    trades: int
    win_rate_pct: float
    #: Somme des gains / somme des pertes (None : aucune perte).
    profit_factor: float | None
    avg_win: float
    avg_loss: float
    fees_paid: float
    avg_exposure_pct: float
    by_reason: dict[str, int]


def max_drawdown_pct(equity: list[float]) -> float:
    """Pire baisse, en % du plus haut précédent."""
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak)
    return worst * 100


def sharpe(equity: list[float]) -> float:
    returns = [b / a - 1 for a, b in zip(equity, equity[1:], strict=False) if a > 0]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return 0.0 if variance == 0 else mean / math.sqrt(variance) * math.sqrt(HOURS_PER_YEAR)


def compute_metrics(
    equity: list[float],
    exposure: list[float],
    trades: list[Trade],
    fees_paid: float,
) -> Metrics:
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    reasons: dict[str, int] = {}
    for trade in trades:
        reasons[trade.reason] = reasons.get(trade.reason, 0) + 1
    gross_loss = -sum(losses)
    return Metrics(
        return_pct=(equity[-1] / equity[0] - 1) * 100,
        max_drawdown_pct=max_drawdown_pct(equity),
        sharpe=sharpe(equity),
        trades=len(trades),
        win_rate_pct=len(wins) / len(trades) * 100 if trades else 0.0,
        profit_factor=sum(wins) / gross_loss if gross_loss > 0 else None,
        avg_win=sum(wins) / len(wins) if wins else 0.0,
        avg_loss=sum(losses) / len(losses) if losses else 0.0,
        fees_paid=fees_paid,
        avg_exposure_pct=sum(exposure) / len(exposure) * 100 if exposure else 0.0,
        by_reason=reasons,
    )
