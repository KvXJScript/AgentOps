from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from agentops.api.dependencies import DocumentServiceDep
from agentops.api.schemas import ChunkOut, DocumentListOut, DocumentOut, ErrorEnvelope
from agentops.application.documents.validation import TITLE_MAX_CHARS
from agentops.domain.documents import FileTooLarge

router = APIRouter(prefix="/documents", tags=["documents"])

DUPLICATE_HEADER = "X-Duplicate"
READ_CHUNK_BYTES = 64 * 1024
# Room for multipart boundaries and the title field on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 64 * 1024

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorEnvelope}}
_UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "title": {"type": "string", "maxLength": TITLE_MAX_CHARS},
                    },
                }
            }
        },
    }
}


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentOut,
    openapi_extra=_UPLOAD_OPENAPI,
    responses={
        200: {"model": DocumentOut, "description": "Duplicate (X-Duplicate: true) or re-ingested failed document"},
        413: {"model": ErrorEnvelope},
        415: {"model": ErrorEnvelope},
        422: {"model": ErrorEnvelope},
    },
)
async def upload_document(request: Request, service: DocumentServiceDep) -> JSONResponse:
    limit = service.max_upload_bytes
    too_large = FileTooLarge(f"The file is larger than {_human_size(limit)}.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + MULTIPART_OVERHEAD_BYTES:
        raise too_large  # rejected before the body is read

    form = await request.form(max_files=1, max_fields=1)
    upload, title = form.get("file"), form.get("title")
    if not isinstance(upload, UploadFile):
        raise _invalid("file", "A file is required")
    if title is not None and not isinstance(title, str):
        raise _invalid("title", "Title must be text")
    title = title.strip() if title else None
    if title and len(title) > TITLE_MAX_CHARS:
        raise _invalid("title", f"Title must be at most {TITLE_MAX_CHARS} characters")

    content = bytearray()
    while piece := await upload.read(READ_CHUNK_BYTES):
        content.extend(piece)
        if len(content) > limit:
            raise too_large
    result = await service.upload(upload.filename or "", bytes(content), title=title or None)

    headers = {DUPLICATE_HEADER: "true"} if result.duplicate else None
    return JSONResponse(
        DocumentOut.of(result.document).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        headers=headers,
    )


@router.get("", response_model=DocumentListOut)
async def list_documents(service: DocumentServiceDep) -> DocumentListOut:
    return DocumentListOut(items=[DocumentOut.of(d) for d in await service.list_documents()])


@router.get("/{document_id}", response_model=DocumentOut, responses=_NOT_FOUND)
async def get_document(document_id: UUID, service: DocumentServiceDep) -> DocumentOut:
    return DocumentOut.of(await service.get_document(document_id))


@router.get("/{document_id}/chunks/{chunk_id}", response_model=ChunkOut, responses=_NOT_FOUND)
async def get_chunk(document_id: UUID, chunk_id: UUID, service: DocumentServiceDep) -> ChunkOut:
    return ChunkOut.of(await service.get_chunk(document_id, chunk_id))


@router.post(
    "/{document_id}/retry",
    response_model=DocumentOut,
    responses={404: {"model": ErrorEnvelope}, 409: {"model": ErrorEnvelope}},
)
async def retry_document(document_id: UUID, service: DocumentServiceDep) -> DocumentOut:
    return DocumentOut.of(await service.retry(document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
async def delete_document(document_id: UUID, service: DocumentServiceDep) -> Response:
    await service.delete(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _invalid(field: str, message: str) -> RequestValidationError:
    return RequestValidationError([{"loc": ("body", field), "msg": message, "type": "value_error"}])


def _human_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):g} MB"
    return f"{size / 1024:g} KB"
