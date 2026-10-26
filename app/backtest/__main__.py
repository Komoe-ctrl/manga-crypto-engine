"""Commande `uv run python -m app.backtest` : rejoue la stratégie sur l'historique Binance public.

Deux périodes **séparées** : l'optimisation (la première partie) et la validation (les derniers
mois). Le backtest ne règle aucun paramètre : il mesure la stratégie telle qu'elle tourne en
production. La validation sert à ne pas se faire plaisir avec un résultat obtenu sur la période
même où l'on aurait réglé quelque chose.
"""

import argparse
import time
from pathlib import Path

import ccxt

from app.backtest.engine import BacktestConfig, BacktestResult, align, run_backtest
from app.backtest.history import CACHE_DIR, HOUR_MS, load_history
from app.config import settings

# Actifs anciens et liquides : tous ont plus d'un an d'historique sur Binance.
# Biais assumé (« survivants ») : on les connaît parce qu'ils sont toujours là aujourd'hui.
DEFAULT_SYMBOLS = "BTC,ETH,BNB,SOL,XRP,DOGE,ADA,LINK,TRX,AVAX,DOT,LTC"
HOURS_PER_MONTH = 730


def _fmt(value: float | None, suffix: str = "", digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}{suffix}"


def print_result(title: str, result: BacktestResult, config: BacktestConfig) -> None:
    m = result.metrics
    hours = len(result.equity)
    scaled = result.benchmark_return_pct * m.avg_exposure_pct / 100
    rows = [
        ("Rendement de la stratégie", f"**{m.return_pct:+.2f} %** sur {config.capital:.0f} USDT"),
        (
            "Achat-conservation à parts égales (100 % investi)",
            f"{result.benchmark_return_pct:+.2f} %",
        ),
        (f"…ramené à la même exposition moyenne ({m.avg_exposure_pct:.1f} %)", f"{scaled:+.2f} %"),
        ("Achat-conservation BTC seul", _fmt(result.btc_return_pct, " %")),
        ("Perte maximale (drawdown)", f"{m.max_drawdown_pct:.2f} %"),
        ("Ratio de Sharpe (annualisé, indicatif)", f"{m.sharpe:.2f}"),
        ("Opérations terminées", f"{m.trades} (signaux émis : {result.signals})"),
        ("Taux de réussite", f"{m.win_rate_pct:.1f} %"),
        ("Gain moyen / perte moyenne", f"{m.avg_win:+.2f} / {m.avg_loss:+.2f} USDT"),
        ("Facteur de profit", _fmt(m.profit_factor)),
        ("Frais payés", f"{m.fees_paid:.2f} USDT"),
        ("Sorties par motif", str(m.by_reason)),
        ("Positions ouvertes à la fin", str(result.open_positions or "aucune")),
    ]
    print(f"\n### {title} ({hours} bougies, soit {hours / 24:.0f} jours)")
    print("| Mesure | Valeur |\n|---|---|")
    for label, value in rows:
        print(f"| {label} | {value} |")
    if result.by_symbol:
        detail = ", ".join(f"{s} {v:+.1f}" for s, v in result.by_symbol.items())
        print(f"\nRésultat net par symbole (USDT) : {detail}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest de la stratégie du moteur")
    parser.add_argument("--months", type=int, default=12, help="durée totale d'historique")
    parser.add_argument(
        "--validation-months", type=int, default=4, help="derniers mois réservés à la validation"
    )
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--until-ms", type=int, default=None, help="fin de la fenêtre, en ms")
    parser.add_argument("--fee-rate", type=float, default=None, help="frais par ordre (0.001)")
    parser.add_argument("--slippage-rate", type=float, default=None, help="glissement par ordre")
    parser.add_argument("--only-total", action="store_true", help="ne simuler que l'ensemble")
    args = parser.parse_args()

    defaults = BacktestConfig()
    config = BacktestConfig(
        capital=args.capital,
        fee_rate=defaults.fee_rate if args.fee_rate is None else args.fee_rate,
        slippage_rate=defaults.slippage_rate if args.slippage_rate is None else args.slippage_rate,
        timeframe=settings.signal_timeframe,
        candles_limit=settings.candles_limit,
        emit_min_score=settings.emit_min_score,
        confirm_candles=settings.confirm_candles,
    )
    exchange = getattr(ccxt, settings.market_exchange)({"enableRateLimit": True})
    until_ms = args.until_ms or int(time.time() * 1000) // HOUR_MS * HOUR_MS
    since_ms = until_ms - args.months * HOURS_PER_MONTH * HOUR_MS
    # Les `candles_limit` bougies d'avant le début ne servent qu'à chauffer les indicateurs.
    fetch_since = since_ms - config.candles_limit * HOUR_MS

    frames = {}
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    for base in symbols:
        frame = load_history(
            exchange, base, config.timeframe, fetch_since, until_ms, args.cache_dir
        )
        if frame.empty or int(frame["open_ms"].iloc[0]) > fetch_since + 24 * HOUR_MS:
            print(f"! {base} écarté : historique trop court")
            continue
        frames[base] = frame
        print(f"{base}: {len(frame)} bougies")

    frames = align(frames)
    total = len(next(iter(frames.values())))
    start = config.candles_limit  # les premières bougies ne servent qu'à la chauffe
    split = total - args.validation_months * HOURS_PER_MONTH
    print(f"\n{len(frames)} symboles, {total} bougies alignées (dont {start} de chauffe).")
    window_end = int(next(iter(frames.values()))["open_ms"].iloc[-1]) + HOUR_MS
    window_date = time.strftime("%Y-%m-%d %H:%M", time.gmtime(window_end / 1000))
    print(f"Fenêtre : fin à {window_end} ms ({window_date} UTC).")
    print(f"Période 1 « optimisation » : bougies {start} à {split}.")
    print(f"Période 2 « validation » : bougies {split} à {total}.")
    print(
        f"Hypothèses : frais {config.fee_rate:.2%}, glissement {config.slippage_rate:.2%}, "
        f"exposition max {config.max_exposure_pct:g} % par position, "
        f"stop-loss {config.stop_loss_pct:g} %, take-profit {config.take_profit_pct:g} %, "
        f"confirmation sur {config.confirm_candles} bougies."
    )

    periods = (
        ("Période 1 : optimisation", start, split),
        ("Période 2 : validation", split, total),
        ("Ensemble", start, total),
    )
    for title, lo, hi in periods[2:] if args.only_total else periods:
        print_result(title, run_backtest(frames, lo, hi, config), config)


if __name__ == "__main__":
    main()
