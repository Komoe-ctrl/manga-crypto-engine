"""Le message publié respecte le contrat de la SPEC (section 7), tel que l'API le valide."""

import json
import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.signals import MAX_MESSAGE_BYTES, Signal, SignalPublisher, decimal_string, signal_id
from tests.helpers import FakeStream

TS = datetime(2026, 10, 26, 8, 0, 0, tzinfo=UTC)


def make(**over: object) -> Signal:
    base = {
        "id": "BTC-1h-1793000000000-BUY", "ts": TS, "symbol": "BTC", "action": "BUY", "score": 0.82,
        "price": "81700.5", "reason": "EMA20 > EMA50", "indicators": {"rsi": 41.2},
    }  # fmt: skip
    base.update(over)
    return Signal(**base)  # type: ignore[arg-type]


def test_json_conforme_au_contrat() -> None:
    data = json.loads(make().to_json())
    assert data == {
        "v": 1, "id": "BTC-1h-1793000000000-BUY", "ts": "2026-10-26T08:00:00Z", "symbol": "BTC",
        "action": "BUY", "score": 0.82, "price": "81700.5", "reason": "EMA20 > EMA50",
        "indicators": {"rsi": 41.2},
    }  # fmt: skip


def test_ts_en_secondes_et_en_z_meme_avec_des_microsecondes_ou_un_autre_fuseau() -> None:
    paris = timezone(timedelta(hours=0))  # offset nul : accepté
    data = json.loads(make(ts=datetime(2026, 10, 26, 8, 0, 0, 123456, tzinfo=paris)).to_json())
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["ts"])


def test_les_champs_absents_sont_omis() -> None:
    data = json.loads(make(price=None, reason=None, indicators=None).to_json())
    assert set(data) == {"v", "id", "ts", "symbol", "action", "score"}


@pytest.mark.parametrize(
    "over",
    [
        {"id": ""}, {"id": "x" * 65}, {"id": "a b"}, {"id": "é"},
        {"symbol": "btc"}, {"symbol": "B"}, {"symbol": "ABCDEFGHIJKLM"}, {"symbol": "BTC/USDT"},
        {"action": "WAIT"}, {"score": -0.01}, {"score": 1.01},
        {"price": "0"}, {"price": "-1"}, {"price": "1e-7"}, {"price": "abc"},
        {"reason": "x" * 501},
        {"ts": datetime(2026, 10, 26, 8, 0, 0)},
        {"ts": datetime(2026, 10, 26, 8, 0, 0, tzinfo=timezone(timedelta(hours=2)))},
        {"indicators": {"k": "x" * 3000}},
    ],
)  # fmt: skip
def test_un_signal_que_l_api_refuserait_n_est_jamais_construit(over: dict) -> None:
    with pytest.raises(ValidationError):
        make(**over)


def test_nan_refuse_dans_le_json() -> None:
    with pytest.raises((ValueError, ValidationError)):
        make(indicators={"rsi": float("nan")}).to_json()


def test_message_de_8_ko_au_plus() -> None:
    assert len(make().to_json().encode()) < MAX_MESSAGE_BYTES


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (81700.5, "81700.5"),
        (0.0000001, "0.0000001"),
        (1e-8, "0.00000001"),
        (3.0, "3.0"),
        (123456789.12, "123456789.12"),
    ],
)
def test_prix_jamais_en_notation_scientifique(value: float, expected: str) -> None:
    assert decimal_string(value) == expected
    make(price=decimal_string(value))  # accepté par le contrat


def test_identifiant_deterministe_par_bougie_et_par_action() -> None:
    a = signal_id("BTC", "1h", 1793000000000, "BUY")
    assert a == signal_id("BTC", "1h", 1793000000000, "BUY")
    assert a != signal_id("BTC", "1h", 1793003600000, "BUY")
    assert a != signal_id("BTC", "1h", 1793000000000, "SELL")
    assert len(signal_id("A" * 12, "15m", 1793000000000, "SELL")) <= 64


def test_publication_xadd_avec_data_et_maxlen_approximatif() -> None:
    stream = FakeStream()
    entry_id = SignalPublisher(stream, "signals", 10_000).publish(make())

    assert entry_id == "1-0"
    name, fields, options = stream.entries[0]
    assert name == "signals"
    assert list(fields) == ["data"]  # un seul champ, comme l'exige le contrat
    assert json.loads(fields["data"])["symbol"] == "BTC"
    assert options == {"maxlen": 10_000, "approximate": True}


def test_echec_redis_remonte_a_l_appelant() -> None:
    with pytest.raises(ConnectionError):
        SignalPublisher(FakeStream(fail=True), "signals", 10_000).publish(make())
