import pandas as pd

from app.backtest.history import HOUR_MS, load_history

T0 = 1_793_000_000_000 // HOUR_MS * HOUR_MS


class FakeExchange:
    """Binance simulé : des bougies horaires, 1000 par appel au plus."""

    def __init__(self, count: int = 2500) -> None:
        self.data = [[T0 + i * HOUR_MS, 1.0, 2.0, 0.5, 1.5, 10.0] for i in range(count)]
        self.calls = 0

    def parse_timeframe(self, timeframe: str) -> int:
        assert timeframe == "1h"
        return 3600

    def fetch_ohlcv(self, symbol: str, timeframe: str, since: int, limit: int):
        self.calls += 1
        assert symbol == "BTC/USDT" and limit <= 1000
        return [row for row in self.data if row[0] >= since][:limit]


def test_telechargement_par_pages_sans_trou_ni_doublon(tmp_path) -> None:
    exchange = FakeExchange()
    until = T0 + 2400 * HOUR_MS

    frame = load_history(exchange, "BTC", "1h", T0, until, tmp_path)

    assert exchange.calls == 3  # 1000 + 1000 + 400
    assert len(frame) == 2400 and frame["open_ms"].is_unique
    assert list(frame["open_ms"]) == [T0 + i * HOUR_MS for i in range(2400)]


def test_la_bougie_en_formation_n_est_jamais_dans_l_historique(tmp_path) -> None:
    exchange = FakeExchange(count=500)
    # `until` tombe au milieu de la bougie 399 : elle n'est pas fermée.
    frame = load_history(exchange, "BTC", "1h", T0, T0 + 399 * HOUR_MS + 1800, tmp_path)
    assert int(frame["open_ms"].iloc[-1]) == T0 + 398 * HOUR_MS


def test_le_cache_evite_de_retelecharger(tmp_path) -> None:
    exchange = FakeExchange()
    until = T0 + 1200 * HOUR_MS
    first = load_history(exchange, "BTC", "1h", T0, until, tmp_path)
    calls = exchange.calls

    second = load_history(exchange, "BTC", "1h", T0, until, tmp_path)

    assert exchange.calls == calls  # servi par le cache
    pd.testing.assert_frame_equal(first, second, check_dtype=False)


def test_un_cache_partiel_est_ignore_plutot_que_de_produire_un_historique_troue(tmp_path) -> None:
    exchange = FakeExchange()
    load_history(exchange, "BTC", "1h", T0, T0 + 500 * HOUR_MS, tmp_path)
    calls = exchange.calls

    frame = load_history(exchange, "BTC", "1h", T0, T0 + 2000 * HOUR_MS, tmp_path)

    assert exchange.calls > calls and len(frame) == 2000


def test_la_plateforme_qui_ne_fait_plus_avancer_le_curseur_ne_fait_pas_boucler(tmp_path) -> None:
    class Stuck(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            self.calls += 1
            return [self.data[0]]  # toujours la même bougie

    exchange = Stuck()
    frame = load_history(exchange, "BTC", "1h", T0, T0 + 100 * HOUR_MS, tmp_path)
    assert exchange.calls <= 2 and len(frame) == 1
