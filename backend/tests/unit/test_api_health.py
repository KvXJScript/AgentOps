from __future__ import annotations

import pytest

from tests.conftest import Harness


async def test_health(harness: Harness) -> None:
    response = await harness.client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_ready_when_everything_is_up(harness: Harness) -> None:
    response = await harness.client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "llm": "configured"},
        "llm": {"provider": "scripted", "model": "scripted-1"},
    }


async def test_ready_reports_database_error(harness: Harness) -> None:
    harness.database.ok = False

    response = await harness.client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["database"] == "error"


@pytest.mark.parametrize("chat_model", [None])
async def test_ready_reports_llm_not_configured_without_llm_object(harness: Harness) -> None:
    response = await harness.client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "checks": {"database": "ok", "llm": "not_configured"}}
