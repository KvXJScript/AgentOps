from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from agentops.api.dependencies import ChatServiceDep, RagServiceDep
from agentops.api.schemas import ErrorEnvelope, MessageRetrievalOut, RetrievedChunkOut, SearchOut, SearchRequest

router = APIRouter(tags=["retrieval"])


@router.post("/retrieval/search", response_model=SearchOut, responses={503: {"model": ErrorEnvelope}})
async def search(body: SearchRequest, rag: RagServiceDep) -> SearchOut:
    document_ids = list(dict.fromkeys(body.document_ids)) if body.document_ids is not None else None
    chunks = await rag.search(body.query, top_k=body.top_k, document_ids=document_ids)
    return SearchOut(items=[RetrievedChunkOut.of(c) for c in chunks], embedding_model=rag.embedding_model)


@router.get(
    "/messages/{message_id}/retrieval", response_model=MessageRetrievalOut, responses={404: {"model": ErrorEnvelope}}
)
async def message_retrieval(message_id: UUID, service: ChatServiceDep) -> MessageRetrievalOut:
    return MessageRetrievalOut.of(await service.get_retrieval(message_id))
