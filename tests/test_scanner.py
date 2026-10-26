import json

import numpy as np

from app.scanner import STANCE_KEY, Scanner, ScanSettings
from app.signals import SignalPublisher
from tests.helpers import BUY_SERIES, FIRST_BUY, SELL_SERIES, FakeMarket, FakeState, FakeStream

# Séries où la stratégie décide réellement BUY / SELL (voir tests/helpers.py).
UP = BUY_SERIES[:-1]
UP_NEXT_CANDLE = BUY_SERIES  # la bougie suivante donne encore BUY
DOWN = SELL_SERIES[:-1]
FLAT = np.full(120, 100.0)


def build(series, prices=None, **settings):
    market = FakeMarket(series, prices)
    state, stream = FakeState(), FakeStream()
    scanner = Scanner(
        market, state, SignalPublisher(stream, "signals", 10_000), ScanSettings(**settings)
    )
    return scanner, market, state, stream


def published(stream: FakeStream) -> list[dict]:
    return [json.loads(fields["data"]) for _, fields, _ in stream.entries]


def test_premier_passage_enregistre_l_etat_sans_rien_publier() -> None:
    scanner, _, state, stream = build({"BTC": UP, "ETH": DOWN})

    report = scanner.run_once()

    assert stream.entries == []
    assert sorted(report.baseline) == ["BTC", "ETH"]
    assert set(state.hashes[STANCE_KEY]) == {"BTC", "ETH"}


def test_premier_passage_peut_publier_si_demande() -> None:
    scanner, _, _, stream = build({"BTC": UP}, emit_on_first_scan=True)

    report = scanner.run_once()

    [signal] = published(stream)
    assert signal["symbol"] == "BTC" and signal["action"] == "BUY"
    assert signal["score"] >= 0.6
    assert report.published == ["BTC:BUY"]
    assert "EMA20 > EMA50" in signal["reason"]
    assert signal["indicators"]["ema20"] > signal["indicators"]["ema50"]


def test_publie_quand_la_position_recommandee_change_puis_se_tait() -> None:
    scanner, market, state, stream = build({"BTC": FLAT})
    scanner.run_once()  # baseline : HOLD
    assert stream.entries == []

    market.series["BTC"] = UP
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY"]

    scanner.run_once()  # même position : rien de plus, même sur une nouvelle bougie
    market.series["BTC"] = UP_NEXT_CANDLE
    scanner.run_once()
    assert len(stream.entries) == 1

    market.series["BTC"] = DOWN
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY", "SELL"]
    assert state.hashes[STANCE_KEY]["BTC"] == "SELL"


def test_un_redemarrage_ne_republie_rien() -> None:
    market = FakeMarket({"BTC": FLAT})
    state, stream = FakeState(), FakeStream()

    def new_scanner():
        return Scanner(market, state, SignalPublisher(stream, "signals", 10_000), ScanSettings())

    new_scanner().run_once()
    market.series["BTC"] = UP
    new_scanner().run_once()  # BUY
    new_scanner().run_once()  # « redémarrage » : l'état est dans Redis
    assert len(stream.entries) == 1


def test_signal_faible_est_traite_comme_hold() -> None:
    scanner, _, state, stream = build({"BTC": UP}, emit_on_first_scan=True, emit_min_score=0.99)

    scanner.run_once()

    assert stream.entries == []
    assert state.hashes[STANCE_KEY]["BTC"] == "HOLD"


def test_hold_publie_seulement_si_demande() -> None:
    scanner, market, _, stream = build({"BTC": UP}, publish_hold=True)
    scanner.run_once()  # baseline BUY, rien
    market.series["BTC"] = FLAT
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["HOLD"]

    scanner2, market2, state2, stream2 = build({"BTC": UP})
    scanner2.run_once()
    market2.series["BTC"] = FLAT
    scanner2.run_once()
    assert stream2.entries == [] and state2.hashes[STANCE_KEY]["BTC"] == "HOLD"


def test_prix_du_signal_est_le_prix_du_moment_sinon_la_derniere_cloture() -> None:
    scanner, _, _, stream = build({"BTC": UP, "ETH": UP}, {"BTC": 81700.5}, emit_on_first_scan=True)
    scanner.run_once()
    prices = {s["symbol"]: s["price"] for s in published(stream)}
    assert prices["BTC"] == "81700.5"
    assert (
        float(prices["ETH"]) == round(float(UP[-1]), 8) or abs(float(prices["ETH"]) - UP[-1]) < 1e-6
    )


def test_identifiant_lie_a_la_bougie_donc_stable_si_on_republie() -> None:
    first = build({"BTC": UP}, emit_on_first_scan=True)
    second = build({"BTC": UP}, emit_on_first_scan=True)
    first[0].run_once()
    second[0].run_once()
    assert published(first[3])[0]["id"] == published(second[3])[0]["id"]


def test_un_symbole_en_echec_n_arrete_pas_les_autres() -> None:
    scanner, _, _, stream = build(
        {"BAD": ConnectionError("timeout"), "SHORT": np.full(10, 1.0), "BTC": UP},
        emit_on_first_scan=True,
    )

    report = scanner.run_once()

    assert [s["symbol"] for s in published(stream)] == ["BTC"]
    assert report.errors["BAD"].startswith("ConnectionError")
    assert report.errors["SHORT"].startswith("insufficient_data")


def test_echec_de_publication_n_enregistre_pas_l_etat_et_sera_retente() -> None:
    market = FakeMarket({"BTC": FLAT})
    state, stream = FakeState(), FakeStream()
    scanner = Scanner(market, state, SignalPublisher(stream, "signals", 10_000), ScanSettings())
    scanner.run_once()
    market.series["BTC"] = UP

    stream.fail = True
    report = scanner.run_once()
    assert "BTC" in report.errors and state.hashes[STANCE_KEY]["BTC"] == "HOLD"

    stream.fail = False
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY"]


def test_univers_configure_ou_classe() -> None:
    scanner, market, _, _ = build({"BTC": UP, "ETH": UP}, symbols=["ETH"], universe_size=5)
    report = scanner.run_once()
    assert market.universe_calls == [(["ETH"], 5)]
    assert report.symbols == ["ETH"]


def test_une_seule_bougie_ne_suffit_pas_il_faut_la_confirmation() -> None:
    # La toute première bougie BUY est précédée d'une autre décision : pas encore confirmée sur 3
    # bougies.
    scanner, _, state, stream = build({"BTC": FIRST_BUY}, emit_on_first_scan=True)
    scanner.run_once()
    assert stream.entries == []

    scanner1, _, _, stream1 = build({"BTC": FIRST_BUY}, emit_on_first_scan=True, confirm_candles=1)
    scanner1.run_once()
    assert [s["action"] for s in published(stream1)] == ["BUY"]


def test_changement_non_confirme_conserve_la_position_precedente() -> None:
    scanner, market, state, stream = build({"BTC": FLAT})
    scanner.run_once()  # baseline HOLD

    market.series["BTC"] = FIRST_BUY  # une bougie BUY isolée
    scanner.run_once()

    assert stream.entries == []
    assert state.hashes[STANCE_KEY]["BTC"] == "HOLD"

    market.series["BTC"] = UP  # confirmé sur 3 bougies
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY"]
