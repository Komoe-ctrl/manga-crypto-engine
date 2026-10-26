from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.backtest import engine as engine_module
from app.backtest.engine import (
    BacktestConfig,
    SimMarket,
    align,
    buy_and_hold_return_pct,
    run_backtest,
)
from app.backtest.metrics import compute_metrics, max_drawdown_pct
from app.backtest.portfolio import MIN_NOTIONAL, Portfolio, Trade
from app.signals import Signal
from tests.helpers import walk

HOUR_MS = 3_600_000
T0 = 1_793_000_000_000


def ohlc(closes, t0: int = T0) -> pd.DataFrame:
    """Bougies : ouverture = clôture précédente, mèches de 0,1 %."""
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return pd.DataFrame(
        {
            "open_ms": t0 + np.arange(len(closes)) * HOUR_MS,
            "open": opens,
            "high": np.maximum(opens, closes) * 1.001,
            "low": np.minimum(opens, closes) * 0.999,
            "close": closes,
            "volume": 1.0,
        }
    )


def frictionless(**over) -> Portfolio:
    return Portfolio(cash=10_000.0, fee_rate=0, slippage_rate=0, **over)


# ── Portefeuille ───────────────────────────────────────────────────────────────────────────────


def test_achat_dimensionne_comme_l_api() -> None:
    pf = Portfolio(cash=10_000.0)  # frais 0,1 %, glissement 0,05 %, exposition max 5 %
    assert pf.buy("BTC", 0.8, 100.0, 5, {"BTC": 100.0})

    spend = 10_000 * 0.05 * 0.8  # 5 % du capital × score
    position = pf.positions["BTC"]
    assert position.quantity == pytest.approx(spend / (100.0 * 1.0005))
    assert pf.fees_paid == pytest.approx(spend * 0.001)
    assert pf.cash == pytest.approx(10_000 - spend - spend * 0.001)
    assert position.stop_loss == pytest.approx(position.avg_price * 0.97)
    assert position.take_profit == pytest.approx(position.avg_price * 1.06)


def test_achat_refuse_si_le_plafond_d_exposition_est_atteint() -> None:
    pf = frictionless()
    assert pf.buy("BTC", 1.0, 100.0, 0, {"BTC": 100.0})  # 500 USDT = 5 %
    assert not pf.buy("BTC", 1.0, 100.0, 1, {"BTC": 100.0})  # plus de place
    assert pf.positions["BTC"].quantity == pytest.approx(5.0)


def test_complement_d_achat_seulement_dans_la_place_restante() -> None:
    pf = frictionless()
    pf.buy("BTC", 0.5, 100.0, 0, {"BTC": 100.0})  # 250 sur 500 de place
    assert pf.buy("BTC", 1.0, 100.0, 1, {"BTC": 100.0})  # il reste 250 de place
    assert pf.positions["BTC"].quantity == pytest.approx(5.0)


def test_achat_trop_petit_refuse() -> None:
    pf = Portfolio(cash=100.0)  # 5 % de 100 = 5 USDT < minimum
    assert not pf.buy("BTC", 1.0, 100.0, 0, {"BTC": 100.0})
    assert MIN_NOTIONAL == 10.0


def test_achat_jamais_au_dela_du_cash() -> None:
    pf = frictionless(max_exposure_pct=100.0)
    pf.cash = 300.0
    assert pf.buy("BTC", 1.0, 100.0, 0, {"BTC": 100.0})
    assert pf.cash == pytest.approx(0.0, abs=1e-9)
    assert pf.positions["BTC"].quantity == pytest.approx(3.0)


def test_vente_totale_resultat_net_des_frais_et_du_glissement() -> None:
    pf = Portfolio(cash=10_000.0)
    pf.buy("BTC", 1.0, 100.0, 2, {"BTC": 100.0})
    assert pf.sell_all("BTC", 110.0, 9, "SELL")

    [trade] = pf.trades
    assert "BTC" not in pf.positions
    assert (trade.entry_index, trade.exit_index, trade.reason) == (2, 9, "SELL")
    # Le résultat du portefeuille et celui de l'opération concordent au centime.
    assert pf.cash - 10_000.0 == pytest.approx(trade.pnl)
    assert trade.pnl < trade.quantity * (110.0 - 100.0)  # frais et glissement mordent
    assert pf.fees_paid > 0


def test_aller_retour_sans_frais_vaut_la_difference_de_prix() -> None:
    pf = frictionless()
    pf.buy("BTC", 1.0, 100.0, 0, {"BTC": 100.0})
    pf.sell_all("BTC", 120.0, 3, "SELL")
    assert pf.trades[0].pnl == pytest.approx(5.0 * 20.0)


def test_vendre_sans_position_ne_fait_rien() -> None:
    pf = frictionless()
    assert not pf.sell_all("BTC", 100.0, 0, "SELL")
    assert pf.trades == []


def bar(open_, high, low):
    return {"BTC": {"open": open_, "high": high, "low": low}}


def held() -> Portfolio:
    pf = frictionless()
    pf.buy("BTC", 1.0, 100.0, 0, {"BTC": 100.0})  # stop-loss 97, take-profit 106
    return pf


def test_stop_loss_touche_dans_la_bougie_sort_au_niveau_du_stop() -> None:
    pf = held()
    pf.check_exits(1, bar(100.0, 101.0, 96.0))
    assert (pf.trades[0].reason, pf.trades[0].exit_price) == ("STOP_LOSS", pytest.approx(97.0))


def test_ouverture_sous_le_stop_sort_a_l_ouverture_pas_au_niveau() -> None:
    pf = held()
    pf.check_exits(1, bar(90.0, 91.0, 88.0))  # trou à la baisse pendant la nuit
    assert pf.trades[0].exit_price == pytest.approx(90.0)


def test_take_profit_touche_sort_au_niveau() -> None:
    pf = held()
    pf.check_exits(1, bar(100.0, 107.0, 99.0))
    assert (pf.trades[0].reason, pf.trades[0].exit_price) == ("TAKE_PROFIT", pytest.approx(106.0))


def test_ouverture_au_dessus_du_take_profit_sort_a_l_ouverture() -> None:
    pf = held()
    pf.check_exits(1, bar(110.0, 111.0, 109.0))
    assert pf.trades[0].exit_price == pytest.approx(110.0)


def test_stop_et_take_profit_dans_la_meme_bougie_le_stop_l_emporte() -> None:
    pf = held()
    pf.check_exits(
        1, bar(100.0, 107.0, 96.0)
    )  # on ne sait pas lequel est venu en premier : prudence
    assert pf.trades[0].reason == "STOP_LOSS"


def test_aucune_sortie_dans_la_fourchette() -> None:
    pf = held()
    pf.check_exits(1, bar(100.0, 105.0, 98.0))
    assert pf.trades == [] and "BTC" in pf.positions


# ── Mesures ────────────────────────────────────────────────────────────────────────────────────


def test_perte_maximale() -> None:
    assert max_drawdown_pct([100, 120, 90, 110, 80, 130]) == pytest.approx((120 - 80) / 120 * 100)
    assert max_drawdown_pct([100, 101, 102]) == 0


def trade(pnl: float, reason: str = "SELL") -> Trade:
    return Trade("BTC", 0, 1, 100.0, 100.0, 1.0, pnl, reason)


def test_metriques_des_operations() -> None:
    m = compute_metrics(
        [100.0, 110.0, 105.0],
        [0.2, 0.4],
        [trade(30), trade(10), trade(-20, "STOP_LOSS"), trade(-20, "STOP_LOSS")],
        fees_paid=3.0,
    )
    assert m.return_pct == pytest.approx(5.0)
    assert (m.trades, m.win_rate_pct) == (4, 50.0)
    assert m.profit_factor == pytest.approx(40 / 40)
    assert (m.avg_win, m.avg_loss) == (20.0, -20.0)
    assert m.avg_exposure_pct == pytest.approx(30.0)
    assert m.by_reason == {"SELL": 2, "STOP_LOSS": 2}


def test_metriques_sans_operation_ni_perte() -> None:
    m = compute_metrics([100.0, 100.0], [0.0, 0.0], [], 0.0)
    assert (m.trades, m.win_rate_pct, m.profit_factor, m.sharpe) == (0, 0.0, None, 0.0)
    assert compute_metrics([100.0, 101.0], [0.1], [trade(5)], 0.0).profit_factor is None


# ── Simulation ─────────────────────────────────────────────────────────────────────────────────


def test_align_garde_les_bougies_communes_a_tous_les_symboles() -> None:
    a, b = ohlc(np.arange(1, 11)), ohlc(np.arange(1, 11))
    b = b.drop(index=[3]).reset_index(drop=True)  # trou chez B
    aligned = align({"AAA": a, "BBB": b})
    assert len(aligned["AAA"]) == len(aligned["BBB"]) == 9
    assert list(aligned["AAA"]["open_ms"]) == list(aligned["BBB"]["open_ms"])
    assert list(aligned["AAA"].index) == list(range(9))


def test_le_marche_simule_ne_montre_que_les_bougies_deja_fermees() -> None:
    market = SimMarket({"BTC": ohlc(np.arange(1, 301, dtype=float))})
    market.index = 99
    candles = market.candles("BTC", "1h", 50)
    assert len(candles.frame) == 50
    assert candles.frame["close"].iloc[-1] == 100.0  # la clôture de la bougie 99, pas une de plus
    assert candles.last_open_ms == T0 + 99 * HOUR_MS
    assert market.last_prices(["BTC"]) == {"BTC": 100.0}
    market.index = 10  # début d'historique : moins de bougies que demandé
    assert len(market.candles("BTC", "1h", 50).frame) == 11


class ScriptedScanner:
    """Remplace le scanner : émet les signaux d'un scénario à des bougies précises (via la vraie
    publication)."""

    scenario: dict[int, tuple[str, str, float]] = {}

    def __init__(self, market, state, publisher, settings) -> None:
        self.market, self.publisher = market, publisher

    def run_once(self) -> None:
        step = self.market.index
        if step in self.scenario:
            action, symbol, score = self.scenario[step]
            self.publisher.publish(
                Signal(
                    id=f"{symbol}-1h-{step}-{action}",
                    ts=datetime.now(UTC),
                    symbol=symbol,
                    action=action,
                    score=score,
                )
            )


def test_un_ordre_decide_a_la_cloture_part_a_l_ouverture_de_la_bougie_suivante(monkeypatch) -> None:
    closes = np.linspace(100, 200, 300)
    frame = ohlc(closes)
    monkeypatch.setattr(engine_module, "Scanner", ScriptedScanner)
    ScriptedScanner.scenario = {100: ("BUY", "BTC", 1.0), 110: ("SELL", "BTC", 1.0)}
    config = BacktestConfig(slippage_rate=0.0005)

    result = run_backtest({"BTC": frame}, 70, 250, config)

    [trade] = result.trades
    assert (trade.entry_index, trade.exit_index, trade.reason) == (101, 111, "SELL")
    # Entrée : ouverture de la bougie 101 (et pas la clôture de la 100) dégradée par le glissement.
    assert trade.entry_price == pytest.approx(frame["open"][101] * 1.0005)
    assert trade.exit_price == pytest.approx(frame["open"][111] * 0.9995)
    assert result.signals == 2
    # Aucune position restante : l'équité finale égale le cash, frais compris.
    assert result.equity[-1] == pytest.approx(config.capital + trade.pnl)
    assert result.metrics.fees_paid > 0 and result.metrics.trades == 1


def test_le_dernier_signal_n_est_jamais_execute_faute_de_bougie_suivante(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "Scanner", ScriptedScanner)
    ScriptedScanner.scenario = {249: ("BUY", "BTC", 1.0)}  # dernière bougie simulée : 249
    result = run_backtest({"BTC": ohlc(np.linspace(100, 200, 300))}, 70, 250)
    assert result.trades == [] and result.open_positions == []
    assert result.signals == 0  # le scanner ne tourne même pas sur la dernière bougie


def test_rien_n_est_negocie_pendant_la_periode_de_chauffe(monkeypatch) -> None:
    monkeypatch.setattr(engine_module, "Scanner", ScriptedScanner)
    ScriptedScanner.scenario = {50: ("BUY", "BTC", 1.0)}  # avant `start` : jamais appelé
    result = run_backtest({"BTC": ohlc(np.linspace(100, 200, 300))}, 100, 250)
    assert result.trades == [] and len(result.equity) == 150


def test_pas_de_regard_sur_le_futur() -> None:
    """Si l'avenir change, rien de ce qui s'est passé avant ne doit changer."""
    frames = {
        s: ohlc(walk(0.0008, 420, seed=seed)) for s, seed in (("AAA", 1), ("BBB", 2), ("CCC", 3))
    }
    k = 330  # bougie à partir de laquelle on réécrit l'avenir
    altered = {}
    for symbol, frame in frames.items():
        changed = frame.copy()
        for col in ("open", "high", "low", "close"):
            changed.loc[k + 1 :, col] = changed.loc[k + 1 :, col] * 0.2  # krach après k
        altered[symbol] = changed

    normal = run_backtest(frames, 100, 420)
    other = run_backtest(altered, 100, 420)

    assert normal.metrics.trades > 0  # le test ne vaut que si la stratégie a réellement négocié
    steps = k - 100 + 1
    assert normal.equity[:steps] == pytest.approx(other.equity[:steps])
    before = lambda r: [t for t in r.trades if t.exit_index <= k]  # noqa: E731
    assert before(normal) == before(other)


def test_la_simulation_est_deterministe() -> None:
    frames = {s: ohlc(walk(0.0008, 350, seed=seed)) for s, seed in (("AAA", 4), ("BBB", 5))}
    first, second = run_backtest(frames, 100, 350), run_backtest(frames, 100, 350)
    assert first.equity == second.equity and first.trades == second.trades


def test_achat_conservation_de_reference_frais_compris() -> None:
    bars = {"X": {"open": np.array([100.0, 100.0]), "close": np.array([100.0, 150.0])}}
    config = BacktestConfig(fee_rate=0.001, slippage_rate=0.0005)
    expected = (150 * 0.9995 * 0.999) / (100 * 1.0005 * 1.001) - 1
    assert buy_and_hold_return_pct(bars, 0, 2, config) == pytest.approx(expected * 100)
