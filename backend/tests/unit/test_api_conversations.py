from __future__ import annotations

import re
import uuid

import pytest

from tests.conftest import Harness


async def test_create_conversation_with_default_title(harness: Harness) -> None:
    response = await harness.client.post("/conversations")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "title", "created_at", "updated_at"}
    assert body["title"] == "New conversation"
    uuid.UUID(body["id"])
    assert body["created_at"].endswith("Z")


async def test_create_conversation_with_title_is_trimmed(harness: Harness) -> None:
    response = await harness.client.post("/conversations", json={"title": "  Trip plan  "})

    assert response.status_code == 201
    assert response.json()["title"] == "Trip plan"


@pytest.mark.parametrize("payload", [{"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"title": 5}, {"x": 1}])
async def test_create_conversation_rejects_invalid_title(harness: Harness, payload: dict[str, object]) -> None:
    response = await harness.client.post("/conversations", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == response.headers["x-request-id"]
    assert error["details"]
    assert all("input" not in d for d in error["details"])


async def test_list_conversations_newest_updated_first(harness: Harness) -> None:
    first = (await harness.client.post("/conversations", json={"title": "first"})).json()
    await harness.client.post("/conversations", json={"title": "second"})
    # A message bumps `updated_at` of the first conversation.
    await harness.client.post(f"/conversations/{first['id']}/messages", json={"content": "hi"})

    response = await harness.client.get("/conversations")

    assert response.status_code == 200
    assert [c["title"] for c in response.json()["items"]] == ["first", "second"]


async def test_get_conversation_returns_messages_oldest_first(harness: Harness) -> None:
    conv = (await harness.client.post("/conversations")).json()
    await harness.client.post(f"/conversations/{conv['id']}/messages", json={"content": "hello"})

    response = await harness.client.get(f"/conversations/{conv['id']}")

    assert response.status_code == 200
    body = response.json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert set(body["messages"][0]) == {
        "id",
        "conversation_id",
        "role",
        "content",
        "status",
        "error_code",
        "model",
        "created_at",
    }


async def test_unknown_conversation_is_404_envelope(harness: Harness) -> None:
    missing = uuid.uuid4()
    for method, path in [
        ("GET", f"/conversations/{missing}"),
        ("DELETE", f"/conversations/{missing}"),
        ("POST", f"/conversations/{missing}/messages"),
    ]:
        kwargs = {"json": {"content": "hi"}} if method == "POST" else {}
        response = await harness.client.request(method, path, **kwargs)  # type: ignore[arg-type]
        assert response.status_code == 404, path
        assert response.json() == {
            "error": {
                "code": "not_found",
                "message": "Conversation not found",
                "request_id": response.headers["x-request-id"],
            }
        }


async def test_unknown_route_is_404_envelope(harness: Harness) -> None:
    response = await harness.client.get("/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_malformed_id_is_validation_error(harness: Harness) -> None:
    response = await harness.client.get("/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_delete_conversation(harness: Harness) -> None:
    conv = (await harness.client.post("/conversations")).json()

    assert (await harness.client.delete(f"/conversations/{conv['id']}")).status_code == 204
    assert (await harness.client.get(f"/conversations/{conv['id']}")).status_code == 404


async def test_request_id_is_generated(harness: Harness) -> None:
    response = await harness.client.get("/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_request_id_is_echoed(harness: Harness) -> None:
    response = await harness.client.get("/health", headers={"X-Request-ID": "client-abc.123"})

    assert response.headers["x-request-id"] == "client-abc.123"


async def test_unsafe_request_id_is_replaced(harness: Harness) -> None:
    response = await harness.client.get("/health", headers={"X-Request-ID": "bad id\twith spaces"})

    assert response.headers["x-request-id"] != "bad id\twith spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


async def test_unexpected_error_is_internal_error_envelope(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(limit: int) -> list[object]:
        raise RuntimeError("secret detail that must not leak")

    monkeypatch.setattr(harness.repo, "list_recent", boom)

    response = await harness.client.get("/conversations", headers={"X-Request-ID": "req-1"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-1"
    assert response.json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred.", "request_id": "req-1"}
    }


async def test_cors_preflight_allows_configured_origin(harness: Harness) -> None:
    response = await harness.client.options(
        "/conversations",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


async def test_cors_rejects_unknown_origin(harness: Harness) -> None:
    response = await harness.client.get("/health", headers={"Origin": "http://evil.example"})

    assert "access-control-allow-origin" not in response.headers
