from __future__ import annotations

import hashlib
import uuid
from typing import Any

from agentops.domain.documents import DocumentStatus
from tests.document_fakes import FlakyEmbedder, make_pdf
from tests.rag_harness import RagHarness, rag_harness

HANDBOOK = (
    b"# Handbook\n\n## Vacation\n\nEmployees get 26 vacation days per year. Unused days expire in March.\n\n"
    b"## Parking\n\nThe office garage has 40 parking spots for bikes and cars.\n"
)


async def _upload(h: RagHarness, filename: str, content: bytes, **data: str) -> Any:
    return await h.client.post("/documents", files={"file": (filename, content)}, data=data)


async def test_upload_markdown_is_ingested_and_ready() -> None:
    async with rag_harness() as h:
        response = await _upload(h, "handbook.md", HANDBOOK)

        assert response.status_code == 201
        assert "x-duplicate" not in response.headers
        body = response.json()
        assert body == {
            "id": body["id"],
            "title": "handbook",
            "filename": "handbook.md",
            "media_type": "text/markdown",
            "size_bytes": len(HANDBOOK),
            "checksum_sha256": hashlib.sha256(HANDBOOK).hexdigest(),
            "status": "ready",
            "error_code": None,
            "chunk_count": 2,
            "embedding_model": "fake-hash-bow",
            "created_at": body["created_at"],
            "updated_at": body["updated_at"],
        }
        assert (await h.client.get(f"/documents/{body['id']}")).json() == body
        assert [d["id"] for d in (await h.client.get("/documents")).json()["items"]] == [body["id"]]


async def test_custom_title_and_pdf_upload() -> None:
    async with rag_harness() as h:
        response = await _upload(h, "scan.pdf", make_pdf(["Quarterly revenue grew", "Costs fell"]), title=" Q3 ")

        body = response.json()
        assert response.status_code == 201
        assert (body["title"], body["media_type"], body["status"]) == ("Q3", "application/pdf", "ready")
        chunk = h.documents.chunks[uuid.UUID(body["id"])][0].chunk
        assert chunk.location.page == 1


async def test_duplicate_ready_upload_returns_existing_without_new_chunks() -> None:
    async with rag_harness() as h:
        first = (await _upload(h, "handbook.md", HANDBOOK)).json()
        chunk_ids = {c.chunk.id for c in h.documents.chunks[uuid.UUID(first["id"])]}

        again = await _upload(h, "renamed.md", HANDBOOK, title="Other title")

        assert again.status_code == 200
        assert again.headers["x-duplicate"] == "true"
        assert again.json()["id"] == first["id"]
        assert again.json()["title"] == "handbook"
        assert {c.chunk.id for c in h.documents.chunks[uuid.UUID(first["id"])]} == chunk_ids
        assert len(h.documents.documents) == 1


async def test_embedding_failure_keeps_source_and_retry_recovers() -> None:
    async with rag_harness(embedder=FlakyEmbedder(failures=1)) as h:
        response = await _upload(h, "handbook.md", HANDBOOK)

        failed = response.json()
        assert response.status_code == 201
        assert (failed["status"], failed["error_code"], failed["chunk_count"]) == ("failed", "embedding_failed", 0)
        assert h.documents.contents[uuid.UUID(failed["id"])] == HANDBOOK

        retried = await h.client.post(f"/documents/{failed['id']}/retry")

        assert retried.status_code == 200
        assert (retried.json()["status"], retried.json()["error_code"]) == ("ready", None)
        assert retried.json()["chunk_count"] == 2


async def test_reupload_of_failed_document_reruns_ingestion() -> None:
    async with rag_harness(embedder=FlakyEmbedder(failures=1)) as h:
        failed = (await _upload(h, "handbook.md", HANDBOOK)).json()

        again = await _upload(h, "handbook.md", HANDBOOK)

        assert again.status_code == 200
        assert "x-duplicate" not in again.headers
        assert (again.json()["id"], again.json()["status"]) == (failed["id"], "ready")


async def test_wrong_embedding_dimensions_fail_the_document() -> None:
    embedder = FlakyEmbedder()
    embedder.wrong_dimensions = True
    async with rag_harness(embedder=embedder) as h:
        body = (await _upload(h, "handbook.md", HANDBOOK)).json()

        assert (body["status"], body["error_code"]) == ("failed", "embedding_failed")


async def test_retry_is_only_allowed_from_failed() -> None:
    async with rag_harness() as h:
        ready = (await _upload(h, "handbook.md", HANDBOOK)).json()

        response = await h.client.post(f"/documents/{ready['id']}/retry")
        missing = await h.client.post(f"/documents/{uuid.uuid4()}/retry")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "document_not_ready"
        assert missing.status_code == 404


async def test_get_chunk_and_delete_cascades() -> None:
    async with rag_harness() as h:
        doc = (await _upload(h, "handbook.md", HANDBOOK)).json()
        chunk = h.documents.chunks[uuid.UUID(doc["id"])][1].chunk

        got = await h.client.get(f"/documents/{doc['id']}/chunks/{chunk.id}")
        wrong_doc = await h.client.get(f"/documents/{uuid.uuid4()}/chunks/{chunk.id}")

        assert got.status_code == 200
        assert got.json() == {
            "id": str(chunk.id),
            "document_id": doc["id"],
            "ordinal": 1,
            "text": chunk.text,
            "location": {
                "char_start": chunk.location.char_start,
                "char_end": chunk.location.char_end,
                "page": None,
                "heading_path": ["Handbook", "Parking"],
            },
        }
        assert wrong_doc.status_code == 404

        assert (await h.client.delete(f"/documents/{doc['id']}")).status_code == 204
        assert (await h.client.get(f"/documents/{doc['id']}")).status_code == 404
        assert (await h.client.get(f"/documents/{doc['id']}/chunks/{chunk.id}")).status_code == 404
        assert (await h.client.delete(f"/documents/{doc['id']}")).status_code == 404
        assert h.documents.chunks == {}
        assert h.documents.contents == {}


async def test_validation_errors_store_nothing() -> None:
    cases = [
        ("photo.png", b"\x89PNG\r\n\x1a\n", 415, "unsupported_file_type"),
        ("fake.pdf", b"hello", 415, "unsupported_file_type"),
        ("latin1.txt", "café".encode("latin-1"), 415, "unsupported_file_type"),
        ("empty.md", b"", 422, "empty_document"),
        ("blank.md", b"\n\n   \n", 422, "empty_document"),
        ("broken.pdf", b"%PDF-1.4 garbage", 422, "corrupt_document"),
        ("blank.pdf", make_pdf(["   "]), 422, "empty_document"),
    ]
    async with rag_harness() as h:
        for filename, content, status, code in cases:
            response = await _upload(h, filename, content)

            assert (response.status_code, response.json()["error"]["code"]) == (status, code), filename
            assert response.json()["error"]["message"]
        assert h.documents.documents == {}


async def test_size_limit_is_enforced_while_reading() -> None:
    async with rag_harness(max_upload_bytes=2048) as h:
        at_limit = await _upload(h, "ok.txt", b"a " * 1024)
        # Sent chunked (no Content-Length), so the limit is enforced while reading the stream.
        over = await h.client.post(
            "/documents",
            content=_multipart_stream(b"b" * 3000),
            headers={"Content-Type": "multipart/form-data; boundary=XyZ"},
        )

        assert at_limit.status_code == 201
        assert over.status_code == 413
        assert over.json()["error"] == {
            "code": "file_too_large",
            "message": "The file is larger than 2 KB.",
            "request_id": over.headers["x-request-id"],
        }
        assert len(h.documents.documents) == 1


async def test_declared_content_length_over_limit_is_rejected_upfront() -> None:
    async with rag_harness(max_upload_bytes=2048) as h:
        response = await _upload(h, "big.txt", b"c" * 200_000)

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"


async def test_missing_file_and_bad_title_are_validation_errors() -> None:
    async with rag_harness() as h:
        no_file = await h.client.post("/documents", data={"title": "x"}, files={"other": ("a.md", b"x")})
        long_title = await _upload(h, "a.md", b"hello", title="t" * 201)

        assert no_file.status_code == 422
        assert no_file.json()["error"]["code"] == "validation_error"
        assert long_title.status_code == 422
        assert long_title.json()["error"]["details"][0]["loc"] == ["body", "title"]


async def test_document_too_long_after_extraction() -> None:
    async with rag_harness(max_upload_bytes=1_000_000) as h:
        response = await _upload(h, "long.txt", b"word " * 5_000)  # 25k chars > 20k cap in the harness

        assert response.status_code == 413
        assert "characters" in response.json()["error"]["message"]


async def test_status_never_ready_without_chunks() -> None:
    async with rag_harness() as h:
        doc = (await _upload(h, "a.md", b"# Only a heading")).json()

        assert doc["status"] == DocumentStatus.READY.value
        assert doc["chunk_count"] == 1


def _multipart_stream(payload: bytes) -> Any:
    async def body() -> Any:
        yield b'--XyZ\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n'
        yield b"Content-Type: text/plain\r\n\r\n"
        for i in range(0, len(payload), 500):
            yield payload[i : i + 500]
        yield b"\r\n--XyZ--\r\n"

    return body()
