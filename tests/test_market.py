import pytest

from app.market import CcxtMarketData, is_tradable_base


@pytest.mark.parametrize("base", ["BTC", "ETH", "SOL", "1INCH", "PEPE"])
def test_actifs_negociables(base: str) -> None:
    assert is_tradable_base(base)


@pytest.mark.parametrize(
    "base",
    [
        "USDT",
        "USDC",
        "FDUSD",
        "DAI",
        "EUR",
        "BTCUP",
        "ETHDOWN",
        "BNBBULL",
        "XRPBEAR",
        "btc",
        "A",
        "WAYTOOLONGSYMBOL",
    ],
)
def test_actifs_exclus(base: str) -> None:
    assert not is_tradable_base(base)


class FakeExchange:
    markets = {
        "BTC/USDT": {"base": "BTC", "quote": "USDT", "spot": True, "active": True},
        "ETH/USDT": {"base": "ETH", "quote": "USDT", "spot": True, "active": True},
        "SOL/USDT": {"base": "SOL", "quote": "USDT", "spot": True, "active": True},
        "USDC/USDT": {"base": "USDC", "quote": "USDT", "spot": True, "active": True},
        "BTCUP/USDT": {"base": "BTCUP", "quote": "USDT", "spot": True, "active": True},
        "DEAD/USDT": {"base": "DEAD", "quote": "USDT", "spot": True, "active": False},
        "BTC/EUR": {"base": "BTC", "quote": "EUR", "spot": True, "active": True},
        "BTC/USDT:USDT": {"base": "BTC", "quote": "USDT", "spot": False, "active": True},
    }
    tickers = {
        "BTC/USDT": {"quoteVolume": 900.0, "last": 80000.0},
        "ETH/USDT": {"quoteVolume": 500.0, "last": 3000.0},
        "SOL/USDT": {"quoteVolume": 700.0, "last": 150.0},
        "USDC/USDT": {"quoteVolume": 9999.0, "last": 1.0},
        "BTCUP/USDT": {"quoteVolume": 9999.0, "last": 5.0},
        "DEAD/USDT": {"quoteVolume": 9999.0, "last": 1.0},
        "BTC/EUR": {"quoteVolume": 9999.0, "last": 70000.0},
        "BTC/USDT:USDT": {"quoteVolume": 9999.0, "last": 80001.0},
    }
    requested: list | None = None

    def load_markets(self):
        return self.markets

    def fetch_tickers(self, symbols=None):
        self.requested = symbols
        return {k: v for k, v in self.tickers.items() if symbols is None or k in symbols}


def market() -> CcxtMarketData:
    data = CcxtMarketData("binance")
    data._exchange = FakeExchange()  # type: ignore[assignment]
    return data


def test_classement_par_volume_hors_stablecoins_derives_et_marches_inactifs() -> None:
    assert market().universe([], 10) == ["BTC", "SOL", "ETH"]


def test_le_classement_est_borne() -> None:
    assert market().universe([], 2) == ["BTC", "SOL"]


def test_liste_configuree_filtre_les_actifs_non_negociables_et_les_doublons() -> None:
    data = market()
    assert data.universe(["ETH", "USDC", "ETH", "BTCUP", "BTC"], 20) == ["ETH", "BTC"]
    assert data._exchange.requested == ["ETH/USDT", "BTC/USDT"]  # type: ignore[attr-defined]


def test_prix_issus_du_dernier_appel_unique() -> None:
    data = market()
    bases = data.universe([], 3)
    assert data.last_prices(bases + ["INCONNU"]) == {"BTC": 80000.0, "SOL": 150.0, "ETH": 3000.0}
