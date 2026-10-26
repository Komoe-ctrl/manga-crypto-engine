import pytest

from app.indicators import compute_indicators
from app.strategy import HIGH_VOLATILITY_FACTOR, decide
from tests.helpers import downtrend, frame, indicators, uptrend

BULL = {"ema20": 102.0, "ema50": 100.0, "macd_hist": 0.5, "macd_hist_prev": 0.3, "rsi": 55.0}


def test_marche_neutre_donne_hold() -> None:
    decision = decide(indicators())
    assert decision.action == "HOLD"
    assert decision.score == 0


def test_tendance_haussiere_confirmee_donne_buy_avec_un_bon_score() -> None:
    decision = decide(indicators(**BULL, bb_pct=0.6))
    assert decision.action == "BUY"
    # tendance 0.35 + MACD 0.10 + MACD en hausse 0.10 + RSI 0.15 + Bollinger 0.10 + calme 0.05
    assert decision.score == pytest.approx(0.85)
    assert "EMA20 > EMA50" in decision.reason


def test_croisement_recent_ajoute_de_la_confiance() -> None:
    plain = decide(indicators(**BULL))
    crossed = decide(indicators(**BULL, recent_cross="UP"))
    assert crossed.score == pytest.approx(plain.score + 0.15)
    assert "croisement haussier récent" in crossed.reason


def test_surachat_interdit_l_achat() -> None:
    assert decide(indicators(**{**BULL, "rsi": 70.0})).action != "BUY"
    assert decide(indicators(**{**BULL, "rsi": 85.0})).action != "BUY"


def test_ecart_de_tendance_trop_faible_ne_donne_rien() -> None:
    decision = decide(indicators(ema20=100.1, ema50=100.0, macd_hist=1, macd_hist_prev=0, rsi=50))
    assert decision.action == "HOLD"


def test_forte_volatilite_reduit_le_score() -> None:
    calm = decide(indicators(**BULL, volatility_pct=1.0))
    wild = decide(indicators(**BULL, volatility_pct=6.0))
    assert wild.score < calm.score
    assert "volatilité élevée" in wild.reason
    # calme : le bonus de calme (0.05) disparaît et le total est multiplié par le facteur
    assert wild.score == pytest.approx(
        round((calm.score - 0.05) * HIGH_VOLATILITY_FACTOR, 3), abs=0.002
    )


def test_vente_est_le_miroir_exact_de_l_achat() -> None:
    bear = indicators(
        ema20=100.0, ema50=102.0, macd_hist=-0.5, macd_hist_prev=-0.3, rsi=45.0, bb_pct=0.4,
    )  # fmt: skip
    bull_equivalent = decide(indicators(**BULL, bb_pct=0.6))
    sell = decide(bear)
    assert sell.action == "SELL"
    assert sell.score == pytest.approx(bull_equivalent.score, abs=0.001)
    assert "Baissier" in sell.reason


def test_survente_interdit_de_vendre_le_creux() -> None:
    bear = {"ema20": 98.0, "ema50": 100.0, "macd_hist": -0.5, "macd_hist_prev": -0.3}
    assert decide(indicators(**bear, rsi=45.0)).action == "SELL"
    assert decide(indicators(**bear, rsi=30.0)).action != "SELL"
    assert decide(indicators(**bear, rsi=15.0)).action != "SELL"


def test_le_score_ne_depasse_jamais_1() -> None:
    best = decide(indicators(**{**BULL, "rsi": 45.0}, recent_cross="UP", bb_pct=0.2))
    assert 0 < best.score <= 1


def test_raison_bornee_a_500_caracteres_et_jamais_vide() -> None:
    for ind in (indicators(), indicators(**BULL), indicators(ema20=98, ema50=100, rsi=45)):
        assert 0 < len(decide(ind).reason) <= 500


def test_de_bout_en_bout_sur_des_series_realistes() -> None:
    up = decide(compute_indicators(frame(uptrend())))
    down = decide(compute_indicators(frame(downtrend())))
    assert up.action in ("BUY", "HOLD")
    assert down.action in ("SELL", "HOLD")
    assert not (up.action == "SELL" or down.action == "BUY")


def test_les_raisons_decrivent_le_marche_reel_pas_le_marche_inverse() -> None:
    # Défaut trouvé en vérifiant sur Binance : une vente se justifiait par « EMA20 > EMA50 ».
    sell = decide(
        indicators(
            ema20=98.0,
            ema50=100.0,
            macd_hist=-0.5,
            macd_hist_prev=-0.3,
            rsi=45.0,
            bb_pct=0.4,
            recent_cross="DOWN",
        )  # fmt: skip
    )
    assert sell.action == "SELL"
    for expected in (
        "EMA20 < EMA50", "croisement baissier récent", "MACD négatif", "MACD en baisse",
        "RSI sain (45)", "prix au-dessus de la bande basse",
    ):  # fmt: skip
        assert expected in sell.reason
    for forbidden in ("EMA20 > EMA50", "haussier", "MACD positif", "sous la bande haute"):
        assert forbidden not in sell.reason

    buy = decide(indicators(**BULL, recent_cross="UP", bb_pct=0.6))
    assert "EMA20 > EMA50" in buy.reason and "EMA20 < EMA50" not in buy.reason


def test_rsi_de_la_raison_est_le_vrai_rsi_pour_une_vente_aussi() -> None:
    sell = decide(indicators(ema20=98.0, ema50=100.0, rsi=62.0))
    assert sell.action == "SELL"
    assert "RSI haut, rebond (62)" in sell.reason  # le vrai RSI, pas le RSI inversé (38)
