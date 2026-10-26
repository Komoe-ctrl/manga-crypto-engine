import numpy as np
import pytest

from app.indicators import (
    MIN_CANDLES,
    InsufficientDataError,
    compute_indicators,
    trend_is_up,
)
from tests.helpers import downtrend, frame, uptrend


def test_hausse_reguliere_donne_ema20_au_dessus_de_ema50() -> None:
    ind = compute_indicators(frame(uptrend()))
    assert ind.ema20 > ind.ema50
    assert ind.price == pytest.approx(uptrend()[-1])
    assert 0 <= ind.rsi <= 100
    assert ind.volatility_pct > 0


def test_baisse_reguliere_donne_ema20_sous_ema50() -> None:
    ind = compute_indicators(frame(downtrend()))
    assert ind.ema20 < ind.ema50


def test_rsi_extremes() -> None:
    assert compute_indicators(frame(100 * 1.02 ** np.arange(80))).rsi > 90
    assert compute_indicators(frame(100 * 0.98 ** np.arange(80))).rsi < 10


def test_prix_plat_ne_plante_pas_et_place_le_prix_au_milieu_des_bandes() -> None:
    ind = compute_indicators(frame(np.full(80, 50.0)))
    assert ind.bb_pct == 0.5
    assert ind.volatility_pct == 0
    assert ind.recent_cross is None


def test_croisement_haussier_recent_detecte() -> None:
    closes = np.concatenate([np.linspace(200, 100, 70), np.linspace(100, 130, 4)])
    # On remonte juste après une longue baisse : l'EMA 20 repasse au-dessus de l'EMA 50 (ou s'en
    # approche).
    series = np.concatenate([np.linspace(200, 100, 70), np.linspace(100, 200, 30)])
    crosses = [compute_indicators(frame(series[:n])).recent_cross for n in range(MIN_CANDLES, 101)]
    assert "UP" in crosses
    assert closes.size  # garde le scénario court lisible


def test_croisement_baissier_recent_detecte() -> None:
    series = np.concatenate([np.linspace(100, 200, 70), np.linspace(200, 100, 30)])
    crosses = [compute_indicators(frame(series[:n])).recent_cross for n in range(MIN_CANDLES, 101)]
    assert "DOWN" in crosses


def test_pas_assez_de_bougies() -> None:
    with pytest.raises(InsufficientDataError):
        compute_indicators(frame(uptrend(MIN_CANDLES - 1)))


@pytest.mark.parametrize("bad", [np.nan, 0.0, -1.0])
def test_prix_invalide_refuse(bad: float) -> None:
    closes = uptrend(100)
    closes[-5] = bad
    with pytest.raises(InsufficientDataError):
        compute_indicators(frame(closes))


def test_as_dict_est_serialisable_et_arrondi() -> None:
    import json

    data = compute_indicators(frame(uptrend())).as_dict()
    json.dumps(data, allow_nan=False)
    assert set(data) == {
        "price", "ema20", "ema50", "rsi", "macd_hist", "bb_pct", "volatility_pct", "recent_cross",
        "atr_pct",
    }  # fmt: skip


def test_atr_mesure_l_amplitude_d_une_bougie_en_pourcentage_du_prix() -> None:
    # Mèches de 0,1 % de chaque côté du prix : l'amplitude d'une bougie plate vaut ~0,2 %.
    flat = compute_indicators(frame(np.full(80, 100.0)))
    assert flat.atr_pct == pytest.approx(0.2, abs=0.01)
    # Des bougies plus agitées ont un ATR plus grand.
    wild = compute_indicators(frame(100 * (1 + 0.03 * np.sin(np.arange(80)))))
    assert wild.atr_pct > 3 * flat.atr_pct


def test_atr_proportionnel_au_prix_donc_comparable_entre_actifs() -> None:
    cheap = compute_indicators(frame(uptrend() / 1_000_000))
    dear = compute_indicators(frame(uptrend() * 1_000))
    assert cheap.atr_pct == pytest.approx(dear.atr_pct, rel=1e-6)


def test_regime_haussier_ema50_au_dessus_de_ema200() -> None:
    assert trend_is_up(frame(np.linspace(100, 300, 400))) is True
    assert trend_is_up(frame(np.linspace(300, 100, 400))) is False


def test_regime_demande_plus_de_200_bougies() -> None:
    with pytest.raises(InsufficientDataError):
        trend_is_up(frame(np.linspace(100, 300, 200)))
    assert trend_is_up(frame(np.linspace(100, 300, 201))) is True


@pytest.mark.parametrize("bad", [np.nan, 0.0, -5.0])
def test_regime_refuse_des_prix_invalides(bad: float) -> None:
    closes = np.linspace(100, 300, 400)
    closes[-10] = bad
    with pytest.raises(InsufficientDataError):
        trend_is_up(frame(closes))
