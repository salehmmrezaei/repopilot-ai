"""Bounded, deterministic second-stage reranking; no provider calls or model downloads."""

from uuid import UUID

from app.retrieval.contracts import SourceChunk
from app.retrieval.ranking import Ranked
from app.retrieval.text import query_terms, symbol_terms

VERSION = "local-relevance-v1"


def rerank(
    query: str, ranked: list[Ranked], chunks: dict[UUID, SourceChunk], limit: int
) -> list[Ranked]:
    words = set(query_terms(query))
    identifiers = {t.lower() for t in symbol_terms(query)}
    rescored: list[Ranked] = []
    for item in ranked[:90]:
        chunk = chunks.get(item.chunk_id)
        if chunk is None:
            continue
        metadata = set(query_terms(" ".join([chunk.path, chunk.name or "", chunk.signature or ""])))
        body = set(query_terms(chunk.content[:8192]))
        coverage = len(words & body) / max(1, len(words))
        specific = len(words & metadata) / max(1, len(words))
        exact = float(bool(chunk.name and chunk.name.lower() in identifiers))
        score = item.score + 0.04 * coverage + 0.06 * specific + 0.10 * exact
        rescored.append(Ranked(item.chunk_id, score, item.channel_ranks))
    return sorted(
        rescored, key=lambda r: (-r.score, chunks[r.chunk_id].path, chunks[r.chunk_id].start_offset)
    )[:limit]
