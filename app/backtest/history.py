"""Historique de bougies Binance PUBLIQUES, avec cache sur disque (aucune clé API)."""

from pathlib import Path

import ccxt
import pandas as pd

COLUMNS = ["open_ms", "open", "high", "low", "close", "volume"]
PAGE = 1000  # bougies par appel (maximum de Binance)
CACHE_DIR = Path("data/history")
HOUR_MS = 3_600_000


def load_history(
    exchange: ccxt.Exchange,
    base: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    cache_dir: Path = CACHE_DIR,
) -> pd.DataFrame:
    """Bougies **fermées** de `base`/USDT entre `since_ms` et `until_ms`, de la plus ancienne à la
    plus récente.

    Le cache couvre toute la période demandée ou est ignoré : un cache partiel ferait un historique
    troué sans que personne ne le voie.
    """
    period_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    path = cache_dir / f"{base}-{timeframe}.csv"
    if path.exists():
        cached = pd.read_csv(path)
        if _covers(cached, since_ms, until_ms, period_ms):
            return _slice(cached, since_ms, until_ms)

    frame = _download(exchange, base, timeframe, since_ms, until_ms, period_ms)
    cache_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return frame


def _download(
    exchange: ccxt.Exchange,
    base: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    period_ms: int,
) -> pd.DataFrame:
    rows: list[list[float]] = []
    cursor = since_ms
    while cursor < until_ms:
        page = exchange.fetch_ohlcv(f"{base}/USDT", timeframe, since=cursor, limit=PAGE)
        if not page:
            break
        rows.extend(page)
        next_cursor = int(page[-1][0]) + period_ms
        if next_cursor <= cursor:
            # La plateforme ne fait plus avancer le curseur : on s'arrête plutôt que de boucler.
            break
        cursor = next_cursor
    frame = pd.DataFrame(rows, columns=COLUMNS).drop_duplicates("open_ms").sort_values("open_ms")
    # Une bougie encore en formation ne fait pas partie de l'historique.
    frame = frame[frame["open_ms"] + period_ms <= until_ms]
    return _slice(frame, since_ms, until_ms)


def _slice(frame: pd.DataFrame, since_ms: int, until_ms: int) -> pd.DataFrame:
    mask = (frame["open_ms"] >= since_ms) & (frame["open_ms"] < until_ms)
    return frame[mask].reset_index(drop=True)


def _covers(frame: pd.DataFrame, since_ms: int, until_ms: int, period_ms: int) -> bool:
    if frame.empty:
        return False
    # Tolérance d'une bougie en début (le symbole peut être listé après `since`, ce que l'appelant
    # détecte) et de deux bougies en fin (la dernière bougie fermée change à chaque heure).
    return (
        int(frame["open_ms"].iloc[0]) <= since_ms + period_ms
        and int(frame["open_ms"].iloc[-1]) >= until_ms - 2 * period_ms
    )
