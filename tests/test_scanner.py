import json

import numpy as np

import app.scanner as scanner_module
from app.scanner import STANCE_KEY, Scanner, ScanSettings
from app.signals import SignalPublisher
from tests.helpers import (
    BUY_SERIES,
    FIRST_BUY,
    SELL_SERIES,
    FakeMarket,
    FakeState,
    FakeStream,
    walk,
)

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


# ── Mémoïsation des analyses ─────────────────────────────────────────────────────────────────


def count_indicator_computations(monkeypatch) -> list[int]:
    calls = [0]
    real = scanner_module.compute_indicators

    def counting(frame):
        calls[0] += 1
        return real(frame)

    monkeypatch.setattr(scanner_module, "compute_indicators", counting)
    return calls


def test_une_bougie_deja_analysee_n_est_jamais_recalculee(monkeypatch) -> None:
    calls = count_indicator_computations(monkeypatch)
    scanner, market, _, _ = build({"BTC": UP, "ETH": DOWN})

    scanner.run_once()
    first = calls[0]
    assert first == 2 * 3  # 2 symboles × 3 bougies de confirmation

    scanner.run_once()  # le moteur tourne chaque minute : rien n'a changé, rien à recalculer
    assert calls[0] == first

    market.series["BTC"] = UP_NEXT_CANDLE  # une nouvelle bougie pour BTC seulement
    scanner.run_once()
    assert calls[0] == first + 1  # seule la nouvelle bougie est calculée


def test_une_serie_qui_change_sous_le_meme_horodatage_ne_sert_jamais_une_reponse_perimee(
    monkeypatch,
) -> None:
    # Même longueur, donc mêmes horodatages ; contenu opposé. Un cache indexé par l'horodatage seul
    # répondrait « BUY » à une série baissière.
    up, down = walk(0.002, 150, seed=7), walk(-0.002, 150, seed=7)
    calls = count_indicator_computations(monkeypatch)
    scanner, market, state, stream = build({"AAA": up}, emit_on_first_scan=True)
    scanner.run_once()
    before = calls[0]

    market.series["AAA"] = down
    scanner.run_once()

    assert calls[0] == before + 3  # tout est recalculé : le contenu a changé
    assert state.hashes[STANCE_KEY]["AAA"] != "BUY"


def test_la_memoire_est_bornee_par_symbole() -> None:
    scanner, market, _, _ = build({"BTC": walk(0.001, 400, seed=3)})
    for n in range(100, 400):
        market.series["BTC"] = walk(0.001, 400, seed=3)[:n]
        scanner.run_once()
    assert len(scanner._cache["BTC"]) <= scanner_module.CACHE_PER_SYMBOL


# ── Délai de carence ─────────────────────────────────────────────────────────────────────────

HOUR_MS = 3_600_000
T = 1_793_100_000_000


def test_delai_de_carence_retient_un_signal_trop_proche_puis_le_publie() -> None:
    scanner, market, state, stream = build(
        {"AAA": FLAT}, cooldown_seconds=24 * 3600, emit_on_first_scan=False
    )
    market.clock_ms = T
    scanner.run_once()  # baseline HOLD

    market.series["AAA"] = UP
    market.clock_ms = T + HOUR_MS
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY"]

    market.series["AAA"] = DOWN  # la vente arrive 2 h plus tard : trop tôt
    market.clock_ms = T + 3 * HOUR_MS
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY"]
    assert state.hashes[STANCE_KEY]["AAA"] == "BUY"  # le changement n'est pas « consommé »

    market.clock_ms = T + 25 * HOUR_MS  # 24 h après le premier signal : la vente part
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY", "SELL"]


def test_sans_delai_de_carence_la_vente_part_tout_de_suite() -> None:
    scanner, market, _, stream = build({"AAA": FLAT})
    market.clock_ms = T
    scanner.run_once()
    market.series["AAA"] = UP
    market.clock_ms = T + HOUR_MS
    scanner.run_once()
    market.series["AAA"] = DOWN
    market.clock_ms = T + 3 * HOUR_MS
    scanner.run_once()
    assert [s["action"] for s in published(stream)] == ["BUY", "SELL"]


def test_le_delai_de_carence_est_par_symbole() -> None:
    scanner, market, _, stream = build({"AAA": FLAT, "BBB": FLAT}, cooldown_seconds=24 * 3600)
    market.clock_ms = T
    scanner.run_once()
    market.series["AAA"] = UP
    market.clock_ms = T + HOUR_MS
    scanner.run_once()
    market.series["BBB"] = UP  # AAA a signalé il y a 1 h : BBB n'est pas concerné
    market.clock_ms = T + 2 * HOUR_MS
    scanner.run_once()
    assert sorted(s["symbol"] for s in published(stream)) == ["AAA", "BBB"]


def test_le_delai_de_carence_survit_a_un_redemarrage() -> None:
    market = FakeMarket({"AAA": FLAT})
    state, stream = FakeState(), FakeStream()
    settings = ScanSettings(cooldown_seconds=24 * 3600)

    def new_scanner():
        return Scanner(market, state, SignalPublisher(stream, "signals", 10_000), settings)

    market.clock_ms = T
    new_scanner().run_once()
    market.series["AAA"] = UP
    market.clock_ms = T + HOUR_MS
    new_scanner().run_once()
    market.series["AAA"] = DOWN
    market.clock_ms = T + 2 * HOUR_MS
    new_scanner().run_once()  # nouveau processus : l'état de carence est dans Redis
    assert [s["action"] for s in published(stream)] == ["BUY"]


# ── Filtre de régime ─────────────────────────────────────────────────────────────────────────

BULL_MARKET = np.linspace(100, 300, 400)
BEAR_MARKET = np.linspace(300, 100, 400)


def test_filtre_de_regime_bloque_les_achats_quand_btc_est_baissier() -> None:
    scanner, _, state, stream = build(
        {"BTC": BEAR_MARKET, "AAA": UP}, regime_filter=True, emit_on_first_scan=True
    )
    scanner.run_once()
    assert [s for s in published(stream) if s["action"] == "BUY"] == []
    assert state.hashes[STANCE_KEY]["AAA"] == "HOLD"  # le BUY est traité comme « ne rien faire »


def test_filtre_de_regime_laisse_passer_les_achats_quand_btc_est_haussier() -> None:
    scanner, _, _, stream = build(
        {"BTC": BULL_MARKET, "AAA": UP}, regime_filter=True, emit_on_first_scan=True
    )
    scanner.run_once()
    assert [s["symbol"] for s in published(stream) if s["action"] == "BUY"] == ["AAA"]


def test_filtre_de_regime_ne_bloque_jamais_les_ventes() -> None:
    scanner, _, _, stream = build(
        {"BTC": BEAR_MARKET, "AAA": DOWN}, regime_filter=True, emit_on_first_scan=True
    )
    scanner.run_once()
    assert [s["symbol"] for s in published(stream) if s["action"] == "SELL"] == ["AAA"]


def test_sans_filtre_de_regime_btc_baissier_ne_change_rien() -> None:
    scanner, _, _, stream = build({"BTC": BEAR_MARKET, "AAA": UP}, emit_on_first_scan=True)
    scanner.run_once()
    assert [s["symbol"] for s in published(stream) if s["action"] == "BUY"] == ["AAA"]


def test_filtre_de_regime_en_cas_de_doute_on_n_achete_pas() -> None:
    # Pas assez de bougies pour juger le régime : échec « fermé », et l'erreur est visible.
    scanner, _, _, stream = build(
        {"BTC": np.linspace(100, 300, 150), "AAA": UP}, regime_filter=True, emit_on_first_scan=True
    )
    report = scanner.run_once()
    assert "regime" in report.errors
    assert [s for s in published(stream) if s["action"] == "BUY"] == []


def test_un_achat_bloque_par_le_regime_part_quand_le_regime_redevient_haussier() -> None:
    scanner, market, _, stream = build({"BTC": BEAR_MARKET, "AAA": UP}, regime_filter=True)
    scanner.run_once()  # baseline : AAA est « HOLD » (achat bloqué)
    assert published(stream) == []

    market.series["BTC"] = BULL_MARKET  # le marché d'ensemble se retourne
    scanner.run_once()
    assert [s["symbol"] for s in published(stream) if s["action"] == "BUY"] == ["AAA"]
