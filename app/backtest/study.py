"""Étude de la stratégie selon `docs/STRATEGIE.md` : variantes, blocs, critères, décision.

Commande : `uv run python -m app.backtest.study`

Le protocole est appliqué par du code, pas de mémoire :
- la commande normale ne simule QUE l'apprentissage et la validation ; le test final n'est jamais
  calculé ;
- `--final VARIANTE` ouvre le test final, une seule fois, pour la seule variante sélectionnée ; un
  second essai est refusé tant que `data/final-test.json` existe.
"""

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

import ccxt
import pandas as pd

from app.backtest.__main__ import DEFAULT_SYMBOLS, HOURS_PER_MONTH
from app.backtest.engine import BacktestConfig, BacktestResult, align, run_backtest
from app.backtest.history import CACHE_DIR, HOUR_MS, load_history
from app.config import settings

#: Commit du protocole (`docs/STRATEGIE.md`), écrit avant tout résultat de variante.
PROTOCOL_COMMIT = "11777e3"

RESULTS_PATH = Path("data/study-results.json")
FINAL_PATH = Path("data/final-test.json")

# Hypothèses du protocole : une valeur chacune, fixée avant de regarder quoi que ce soit.
HYPOTHESES: dict[str, dict] = {
    "H1": {"timeframe": "4h"},
    "H2": {"cooldown_seconds": 24 * 3600},
    "H3": {"atr_stop_multiple": 2.5, "atr_take_profit_multiple": 5.0},
    "H4": {"regime_filter": True},
}
#: Les 9 variantes du protocole (liste fermée).
VARIANTS: dict[str, tuple[str, ...]] = {
    "V0": (),
    "V1": ("H1",),
    "V2": ("H2",),
    "V3": ("H3",),
    "V4": ("H4",),
    "V5": ("H1", "H4"),
    "V6": ("H1", "H3"),
    "V7": ("H1", "H3", "H4"),
    "V8": ("H2", "H4"),
}

# Critères de réussite du protocole.
MAX_DRAWDOWN_PCT = 15.0
MIN_PROFIT_FACTOR = 1.1
MIN_TRADES = {"learning": 60, "validation": 20, "final": 20}


def variant_config(variant: str) -> BacktestConfig:
    overrides: dict = {}
    for hypothesis in VARIANTS[variant]:
        overrides.update(HYPOTHESES[hypothesis])
    return BacktestConfig(
        timeframe=overrides.pop("timeframe", "1h"),
        candles_limit=settings.candles_limit,
        emit_min_score=settings.emit_min_score,
        confirm_candles=settings.confirm_candles,
        **overrides,
    )


@dataclass(frozen=True)
class BlockSummary:
    variant: str
    block: str
    return_pct: float
    #: Détention à parts égales, ramenée à l'exposition moyenne de la variante.
    benchmark_scaled_pct: float
    max_drawdown_pct: float
    profit_factor: float | None
    trades: int
    avg_exposure_pct: float
    fees_paid: float
    win_rate_pct: float
    signals: int
    by_reason: dict[str, int]

    @classmethod
    def of(cls, variant: str, block: str, result: BacktestResult) -> "BlockSummary":
        m = result.metrics
        return cls(
            variant=variant,
            block=block,
            return_pct=m.return_pct,
            benchmark_scaled_pct=result.benchmark_return_pct * m.avg_exposure_pct / 100,
            max_drawdown_pct=m.max_drawdown_pct,
            profit_factor=m.profit_factor,
            trades=m.trades,
            avg_exposure_pct=m.avg_exposure_pct,
            fees_paid=m.fees_paid,
            win_rate_pct=m.win_rate_pct,
            signals=result.signals,
            by_reason=m.by_reason,
        )


def criteria(summary: BlockSummary) -> dict[str, bool]:
    """Les quatre critères du protocole, un par un (a, b, c, d)."""
    profit_factor_ok = (
        summary.trades > 0
        if summary.profit_factor is None  # aucune perte du tout
        else summary.profit_factor > MIN_PROFIT_FACTOR
    )
    return {
        "a_bat_la_detention": summary.return_pct > summary.benchmark_scaled_pct,
        "b_perte_max_sous_15": summary.max_drawdown_pct < MAX_DRAWDOWN_PCT,
        "c_facteur_de_profit": profit_factor_ok,
        "d_assez_d_operations": summary.trades >= MIN_TRADES[summary.block],
    }


def passes(summary: BlockSummary) -> bool:
    return all(criteria(summary).values())


def retained(summaries: dict[str, dict[str, BlockSummary]]) -> list[str]:
    """Variantes qui réussissent sur l'apprentissage ET sur la validation, bloc par bloc."""
    return [
        variant
        for variant, blocks in summaries.items()
        if passes(blocks["learning"]) and passes(blocks["validation"])
    ]


def select(summaries: dict[str, dict[str, BlockSummary]], candidates: list[str]) -> str | None:
    """Parmi les variantes retenues : pire perte maximale (sur les deux blocs) la plus faible ; à
    égalité, la plus simple (le moins d'hypothèses combinées)."""
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda v: (
            max(
                summaries[v]["learning"].max_drawdown_pct,
                summaries[v]["validation"].max_drawdown_pct,
            ),
            len(VARIANTS[v]),
        ),
    )


class FinalTestRefused(Exception):
    """Le test final ne peut pas être ouvert (protocole)."""


def check_final_allowed(
    variant: str, results_path: Path = RESULTS_PATH, final_path: Path = FINAL_PATH
) -> None:
    """Le test final n'existe que pour la variante sélectionnée, et une seule fois."""
    if final_path.exists():
        raise FinalTestRefused(
            f"Le test final a déjà été consulté ({final_path}). Il ne se rejoue pas : "
            "ce serait en faire une période de réglage."
        )
    if not results_path.exists():
        raise FinalTestRefused("Lancez d'abord l'étude (apprentissage et validation).")
    selected = json.loads(results_path.read_text(encoding="utf-8")).get("selected")
    if selected is None:
        raise FinalTestRefused(
            "Aucune variante retenue : le protocole s'arrête, le test final n'est pas ouvert."
        )
    if variant != selected:
        raise FinalTestRefused(
            f"Seule la variante sélectionnée ({selected}) peut passer le test final."
        )


# ── Exécution ────────────────────────────────────────────────────────────────────────────────

_frames: dict[str, pd.DataFrame] = {}


def _init_worker(frames: dict[str, pd.DataFrame]) -> None:
    global _frames
    _frames = frames


def _run_task(variant: str, block: str, lo: int, hi: int) -> BlockSummary:
    return BlockSummary.of(variant, block, run_backtest(_frames, lo, hi, variant_config(variant)))


def load_frames(args: argparse.Namespace) -> dict[str, pd.DataFrame]:
    exchange = getattr(ccxt, settings.market_exchange)({"enableRateLimit": True})
    until_ms = args.until_ms or int(time.time() * 1000) // HOUR_MS * HOUR_MS
    since_ms = until_ms - args.months * HOURS_PER_MONTH * HOUR_MS
    fetch_since = since_ms - args.warmup_hours * HOUR_MS
    frames = {}
    for base in (s.strip().upper() for s in args.symbols.split(",") if s.strip()):
        frame = load_history(exchange, base, "1h", fetch_since, until_ms, args.cache_dir)
        if frame.empty or int(frame["open_ms"].iloc[0]) > fetch_since + 24 * HOUR_MS:
            raise SystemExit(f"{base} : historique trop court pour {args.months} mois")
        frames[base] = frame
        print(f"{base}: {len(frame)} bougies", flush=True)
    return align(frames)


def blocks_of(total: int, args: argparse.Namespace) -> dict[str, tuple[int, int]]:
    final_start = total - args.final_months * HOURS_PER_MONTH
    validation_start = final_start - args.validation_months * HOURS_PER_MONTH
    return {
        "learning": (args.warmup_hours, validation_start),
        "validation": (validation_start, final_start),
        "final": (final_start, total),
    }


def fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def print_summaries(summaries: dict[str, dict[str, BlockSummary]], blocks: list[str]) -> None:
    for block in blocks:
        print(f"\n### Bloc « {block} »")
        print(
            "| Variante | Hypothèses | Rendement | Détention (même expo.) | Perte max | "
            "Facteur de profit | Opérations | Expo. moy. | Frais | (a) | (b) | (c) | (d) |"
        )
        print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for variant, by_block in summaries.items():
            s = by_block[block]
            c = criteria(s)
            marks = " | ".join("oui" if ok else "non" for ok in c.values())
            print(
                f"| {variant} | {'+'.join(VARIANTS[variant]) or 'base'} | {s.return_pct:+.2f} % | "
                f"{s.benchmark_scaled_pct:+.2f} % | {s.max_drawdown_pct:.1f} % | "
                f"{fmt(s.profit_factor)} | {s.trades} | {s.avg_exposure_pct:.1f} % | "
                f"{s.fees_paid:.0f} | {marks} |"
            )


def run_study(args: argparse.Namespace) -> None:
    frames = load_frames(args)
    total = len(next(iter(frames.values())))
    blocks = blocks_of(total, args)
    print(f"\n{len(frames)} symboles, {total} bougies alignées.", flush=True)
    for name, (lo, hi) in blocks.items():
        print(f"Bloc {name} : bougies {lo} à {hi} ({hi - lo} h).")
    print(f"Protocole : commit {PROTOCOL_COMMIT}.", flush=True)
    print("Le bloc « final » n'est PAS simulé ici.", flush=True)

    variants = [v for v in VARIANTS if not args.only or v in args.only.split(",")]
    tasks = [(v, b) for v in variants for b in ("learning", "validation")]
    summaries: dict[str, dict[str, BlockSummary]] = {v: {} for v in variants}
    started = time.time()
    with ProcessPoolExecutor(args.workers, initializer=_init_worker, initargs=(frames,)) as pool:
        futures = {pool.submit(_run_task, v, b, *blocks[b]): (v, b) for v, b in tasks}
        for future in as_completed(futures):
            summary = future.result()
            summaries[summary.variant][summary.block] = summary
            print(
                f"  {summary.variant} / {summary.block} : {summary.return_pct:+.2f} % "
                f"({summary.trades} opérations, {time.time() - started:.0f} s)",
                flush=True,
            )

    print_summaries(summaries, ["learning", "validation"])
    kept = retained(summaries)
    chosen = select(summaries, kept)
    print("\nVariantes retenues (réussite sur l'apprentissage ET la validation) :")
    print(f"  {kept or 'aucune'}")
    stop = "aucune : le protocole s'arrête ici"
    print(f"Variante sélectionnée : {chosen or stop}", flush=True)

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "protocol_commit": PROTOCOL_COMMIT,
                "variants_tried": len(variants),
                "blocks": {k: list(v) for k, v in blocks.items()},
                "summaries": {
                    v: {b: asdict(s) for b, s in by_block.items()}
                    for v, by_block in summaries.items()
                },
                "retained": kept,
                "selected": chosen,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def run_final(args: argparse.Namespace) -> None:
    check_final_allowed(args.final)
    frames = load_frames(args)
    total = len(next(iter(frames.values())))
    lo, hi = blocks_of(total, args)["final"]
    print(f"\nTEST FINAL, variante {args.final}, bougies {lo} à {hi}.", flush=True)
    print("Une seule consultation : le résultat est enregistré et ne se rejoue pas.", flush=True)
    result = run_backtest(frames, lo, hi, variant_config(args.final))
    summary = BlockSummary.of(args.final, "final", result)
    verdict = criteria(summary)
    FINAL_PATH.write_text(
        json.dumps(
            {
                "variant": args.final,
                "summary": asdict(summary),
                "criteria": verdict,
                "passes": all(verdict.values()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print_summaries({args.final: {"final": summary}}, ["final"])
    print(f"\nVerdict : {'RÉUSSI' if all(verdict.values()) else 'REJETÉ'} ({verdict})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Étude de la stratégie (docs/STRATEGIE.md)")
    parser.add_argument("--months", type=int, default=36)
    parser.add_argument("--validation-months", type=int, default=6)
    parser.add_argument("--final-months", type=int, default=4)
    parser.add_argument("--warmup-hours", type=int, default=1200)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR)
    parser.add_argument("--until-ms", type=int, default=None)
    parser.add_argument("--only", default="", help="sous-ensemble de variantes (essais de durée)")
    parser.add_argument("--final", default=None, help="ouvre le test final (une seule fois)")
    args = parser.parse_args()
    if args.final:
        run_final(args)
    else:
        run_study(args)


if __name__ == "__main__":
    main()
