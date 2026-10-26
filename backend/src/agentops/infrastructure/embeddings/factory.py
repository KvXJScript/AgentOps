from __future__ import annotations

import logging

from agentops.config import Settings
from agentops.domain.documents import Embedder
from agentops.infrastructure.embeddings.fake import FakeEmbedder
from agentops.infrastructure.embeddings.fastembed import FastEmbedEmbedder

logger = logging.getLogger(__name__)


def build_embedder(settings: Settings, *, required_dimensions: int) -> Embedder:
    """Build the configured embedder. Refuses a model whose size doesn't match the vector column."""
    embedder: Embedder
    if settings.embedding_provider == "fake":
        logger.warning("embeddings.using_fake", extra={"dimensions": required_dimensions})
        embedder = FakeEmbedder(dimensions=required_dimensions)
    else:
        embedder = FastEmbedEmbedder(
            settings.embedding_model, cache_dir=settings.embedding_cache_dir, batch_size=settings.embedding_batch_size
        )
    if embedder.dimensions != required_dimensions:
        raise ValueError(
            f"EMBEDDING_MODEL {embedder.model!r} produces {embedder.dimensions}-dim vectors, "
            f"but the database stores {required_dimensions}-dim vectors; a new migration is required"
        )
    logger.info("embeddings.configured", extra={"embedding_model": embedder.model, "dimensions": embedder.dimensions})
    return embedder
