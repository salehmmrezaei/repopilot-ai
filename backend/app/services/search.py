import asyncio
import logging
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.provider_usage import ProviderUsageError
from app.core.rate_limits import Limit, RateLimiter
from app.embeddings.provider import EmbeddingProvider, aggregate
from app.repositories.search import SearchStore
from app.retrieval.context import build_context
from app.retrieval.contracts import CandidateStore
from app.retrieval.ranking import fuse
from app.retrieval.text import VERSION, query_terms, symbol_terms
from app.schemas.search import SearchHit, SearchRequest, SearchResponse
from app.services.search_preparation import PreparationService
from app.services.usage_receipts import ReceiptWriter

logger = logging.getLogger("repopilot.retrieval")


class SearchService:
    def __init__(
        self,
        db: AsyncSession,
        candidates: CandidateStore,
        limiter: RateLimiter,
        settings: Settings,
        provider: EmbeddingProvider | None,
    ) -> None:
        self.db, self.candidates, self.limiter, self.settings, self.provider = (
            db,
            candidates,
            limiter,
            settings,
            provider,
        )
        self.receipts: ReceiptWriter | None = None
        self.store = SearchStore(db)
        self.preparation = PreparationService(db, limiter, settings)

    async def search(
        self, user_id: UUID, repository_id: UUID, request: SearchRequest
    ) -> SearchResponse:
        try:
            async with asyncio.timeout(25):
                return await self._search(user_id, repository_id, request)
        except TimeoutError:
            raise AppError(
                "retrieval_timeout", "Search timed out. Retry or use keyword search.", 503
            ) from None

    async def _search(
        self, user_id: UUID, repository_id: UUID, request: SearchRequest
    ) -> SearchResponse:
        started = perf_counter()
        source = await self.preparation.source(user_id, repository_id)
        index = await self.store.active(repository_id, source.id, request.mode)
        if index is None and request.mode == "keyword":
            index = await self.store.active(repository_id, source.id, "hybrid")
        if index is None:
            raise AppError(
                "search_required",
                "Prepare the selected search mode for this source index first.",
                409,
            )
        await self.limiter.check([Limit("retrieval:user:" + str(user_id), 30, 60)])
        source_id, commit_sha = source.id, source.commit_sha
        index_id, profile = index.id, index.provider_profile
        terms = query_terms(request.query)
        channels = {
            "lexical": await self.candidates.lexical(index_id, terms),
            "symbol": await self.candidates.symbols(index_id, symbol_terms(request.query)),
        }
        # Release the read transaction/connection before awaiting an external provider.
        await self.db.commit()
        usage, cost = 0, 0.0
        if request.mode == "hybrid":
            if not self.settings.embeddings_enabled or self.provider is None:
                raise AppError(
                    "embeddings_disabled",
                    "Semantic search is disabled. Choose keyword search.",
                    409,
                )
            if self.provider.profile != profile:
                raise AppError(
                    "embedding_profile_mismatch",
                    "Rebuild search with the configured embedding profile.",
                    409,
                )
            parts = self.provider.tokenize(request.query)
            logger.info(
                "query_embedding_requested",
                extra={
                    "job_id": str(index_id),
                    "input_tokens": sum(map(len, parts)),
                    "estimated_cost_usd": sum(map(len, parts))
                    * self.settings.embedding_price_per_million
                    / 1000000,
                },
            )
            receipt_id = (
                await self.receipts.begin(
                    "query_embedding",
                    self.provider.profile,
                    self.settings.embedding_price_per_million,
                )
                if self.receipts
                else None
            )
            try:
                response = await self.provider.embed(parts)
            except ProviderUsageError as exc:
                if self.receipts and receipt_id:
                    await self.receipts.finish(receipt_id, exc.input_tokens, exc.output_tokens)
                raise
            if self.receipts and receipt_id:
                await self.receipts.finish(receipt_id, response.input_tokens, 0)
            if len(response.vectors) != len(parts) or response.input_tokens != sum(map(len, parts)):
                raise AppError("embedding_invalid", "Query embedding did not match its input.", 502)
            vector = aggregate(response.vectors, list(map(len, parts)))
            channels["vector"] = await self.candidates.vector(index_id, vector)
            usage = response.input_tokens
            cost = usage * self.settings.embedding_price_per_million / 1000000
        candidate_ids = list(dict.fromkeys(key for ids in channels.values() for key in ids))
        chunks = {c.id: c for c in await self.store.chunks(source_id, candidate_ids)}
        ranked = fuse(
            channels,
            request.top_k,
            {c.id: f"{c.path}:{c.start_offset:010}" for c in chunks.values()},
        )
        results: list[SearchHit] = []
        for item in ranked:
            c = chunks.get(item.chunk_id)
            if c is None:
                raise AppError(
                    "search_inconsistent", "Search source changed. Refresh and retry.", 409
                )
            results.append(
                SearchHit(
                    chunk_id=c.id,
                    file_id=c.file_id,
                    path=c.path,
                    language=c.language,
                    symbol=c.name,
                    start_line=c.start_line,
                    end_line=c.end_line,
                    start_offset=c.start_offset,
                    end_offset=c.end_offset,
                    content=c.content,
                    score=item.score,
                    channel_ranks=item.channel_ranks,
                )
            )
        context, tokens = build_context(results, commit_sha, request.context_token_budget)
        duration = round((perf_counter() - started) * 1000, 2)
        logger.info(
            "retrieval_completed",
            extra={
                "job_id": str(index_id),
                "duration_ms": duration,
                "input_tokens": usage,
                "estimated_cost_usd": cost,
                "result_count": len(results),
            },
        )
        return SearchResponse(
            search_index_id=index_id,
            source_index_id=source_id,
            commit_sha=commit_sha,
            mode=request.mode,
            pipeline_version=VERSION,
            provider_profile=profile,
            results=results,
            context=context,
            context_tokens=tokens,
            context_omitted=len(results) - len(context),
            query_tokens=usage,
            estimated_query_cost_usd=cost,
            duration_ms=duration,
        )
