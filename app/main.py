"""Point d'entrée FastAPI du moteur d'analyse.

Le moteur ne fait que produire des signaux. Il n'a pas accès aux clés API
et ne passe aucun ordre : c'est le rôle exclusif de crypto-api.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI

from app.config import settings
from app.market import CcxtMarketData
from app.scanner import CycleReport, Scanner, ScanSettings
from app.signals import SignalPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("engine")


class ScanLoop:
    """Boucle d'analyse en tâche de fond : un cycle, une pause, et ainsi de suite, sans jamais
    s'arrêter sur une erreur."""

    def __init__(self, scanner: Scanner, interval_seconds: int) -> None:
        self._scanner = scanner
        self._interval = interval_seconds
        self.last_report: CycleReport | None = None
        self.last_error: str | None = None

    async def run(self) -> None:
        while True:
            try:
                # CCXT et redis-py sont synchrones : hors de la boucle d'événements, pour ne pas
                # bloquer `/health`.
                self.last_report = await asyncio.to_thread(self._scanner.run_once)
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = type(error).__name__
                logger.exception("Cycle d'analyse en échec")
            await asyncio.sleep(self._interval)


def build_scanner() -> Scanner:
    client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    return Scanner(
        market=CcxtMarketData(settings.market_exchange),
        state=client,
        publisher=SignalPublisher(client, settings.signals_stream, settings.stream_maxlen),
        settings=ScanSettings(
            universe_size=settings.universe_size,
            symbols=[s.strip().upper() for s in settings.symbols.split(",") if s.strip()],
            timeframe=settings.signal_timeframe,
            candles_limit=settings.candles_limit,
            emit_min_score=settings.emit_min_score,
            confirm_candles=settings.confirm_candles,
            emit_on_first_scan=settings.emit_on_first_scan,
            publish_hold=settings.publish_hold,
        ),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    task: asyncio.Task | None = None
    app.state.loop = None
    if settings.scanner_enabled:
        app.state.loop = ScanLoop(build_scanner(), settings.scan_interval_seconds)
        task = asyncio.create_task(app.state.loop.run())
    try:
        yield
    finally:
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="Veille — crypto-engine", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.env}


@app.get("/status")
def status() -> dict[str, object]:
    """État de la boucle d'analyse : dernier cycle et dernière erreur (sans aucune donnée
    sensible)."""
    loop: ScanLoop | None = getattr(app.state, "loop", None)
    if loop is None:
        return {"scanner": "disabled"}
    return {
        "scanner": "running",
        "last_cycle": loop.last_report.summary() if loop.last_report else None,
        "last_error": loop.last_error,
    }
