"""Contrat des signaux (moteur → API) et publication dans le Redis Stream.

Source de vérité : `../crypto-api/docs/SPEC.md` section 7. Le message est validé **avant**
publication avec les mêmes règles que l'API : un signal que l'API refuserait n'est jamais envoyé
(il finirait dans `signals:dead`).
"""

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_BYTES = 8 * 1024
MAX_INDICATORS_BYTES = 2 * 1024
_ID = re.compile(r"^[A-Za-z0-9\-_.:]{1,64}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,12}$")


class Signal(BaseModel):
    model_config = ConfigDict(frozen=True)

    v: Literal[1] = 1
    id: str
    ts: datetime
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    score: float = Field(ge=0, le=1)
    price: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    indicators: dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID.match(value):
            raise ValueError("id : 1 à 64 caractères parmi A-Z a-z 0-9 - _ . :")
        return value

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, value: str) -> str:
        if not _SYMBOL.match(value):
            raise ValueError("symbol : 2 à 12 caractères A-Z 0-9 en majuscules")
        return value

    @field_validator("ts")
    @classmethod
    def _check_ts(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(None):
            raise ValueError("ts doit être en UTC")
        return value

    @field_validator("price")
    @classmethod
    def _check_price(cls, value: str | None) -> str | None:
        # Le format d'abord : Decimal("abc") lèverait InvalidOperation, que pydantic ne convertit
        # pas en erreur.
        if value is not None and not (re.fullmatch(r"\d+(\.\d+)?", value) and Decimal(value) > 0):
            raise ValueError("price : chaîne décimale positive, sans notation scientifique")
        return value

    @field_validator("indicators")
    @classmethod
    def _check_indicators(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        # allow_nan=False : un NaN deviendrait silencieusement `null` dans le message.
        if (
            value is not None
            and len(json.dumps(value, allow_nan=False).encode()) > MAX_INDICATORS_BYTES
        ):
            raise ValueError("indicators : 2 Ko au plus, sans NaN ni infini")
        return value

    def to_json(self) -> str:
        """JSON du champ `data` : `ts` en `…Z` (secondes), champs absents omis."""
        payload = self.model_dump(mode="json", exclude_none=True)
        payload["ts"] = self.ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        if len(text.encode()) > MAX_MESSAGE_BYTES:
            raise ValueError("message : 8 Ko au plus")
        return text


def decimal_string(value: float) -> str:
    """Prix en chaîne décimale positive, jamais en notation scientifique (« 1e-07 » serait
    refusé)."""
    return format(Decimal(repr(value)), "f")


def signal_id(symbol: str, timeframe: str, candle_open_ms: int, action: str) -> str:
    """Identifiant déterministe : une même bougie et une même action donnent toujours le même `id`.
    Un redémarrage ou une double exécution ne peut donc jamais produire deux ordres (déduplication
    de l'API)."""
    return f"{symbol}-{timeframe}-{candle_open_ms}-{action}"


class StreamClient(Protocol):
    def xadd(
        self,
        name: str,
        fields: dict[str, str],
        id: str = "*",
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> Any: ...


class SignalPublisher:
    def __init__(self, redis: StreamClient, stream: str, maxlen: int) -> None:
        self._redis = redis
        self._stream = stream
        self._maxlen = maxlen

    def publish(self, signal: Signal) -> str:
        """Publie le signal et renvoie l'identifiant de l'entrée du stream.
        Équivalent de `XADD signals MAXLEN ~ 10000 * data '<json>'`."""
        entry_id = self._redis.xadd(
            self._stream,
            {"data": signal.to_json()},
            maxlen=self._maxlen,
            approximate=True,
        )
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)


def utc_now() -> datetime:
    return datetime.now(UTC)
