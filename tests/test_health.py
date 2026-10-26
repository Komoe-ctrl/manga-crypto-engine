import asyncio
import contextlib

from fastapi.testclient import TestClient

from app.main import ScanLoop, app
from app.scanner import CycleReport
from app.signals import utc_now


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_status_sans_scanner() -> None:
    # Sous les tests, SCANNER_ENABLED=false (tests/conftest.py) : aucune connexion réseau ni Redis.
    with TestClient(app) as client:
        assert client.get("/status").json() == {"scanner": "disabled"}


class FlakyScanner:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self) -> CycleReport:
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("réseau coupé")
        return CycleReport(started_at=utc_now(), symbols=["BTC"])


def test_la_boucle_survit_a_un_cycle_en_echec_et_continue() -> None:
    async def scenario() -> ScanLoop:
        scanner = FlakyScanner()
        loop = ScanLoop(scanner, interval_seconds=0)  # type: ignore[arg-type]
        task = asyncio.create_task(loop.run())
        for _ in range(200):
            if loop.last_report is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return loop

    loop = asyncio.run(scenario())
    assert loop.last_report is not None and loop.last_report.symbols == ["BTC"]
    assert loop.last_error is None  # remis à zéro par le cycle réussi
