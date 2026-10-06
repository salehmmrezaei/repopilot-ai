"""Run the real PostgreSQL retrieval pipeline on a small, versioned development corpus."""

import argparse
import asyncio
import json
from hashlib import sha256
from importlib.metadata import version
from math import ceil
from pathlib import Path
from statistics import mean, median
from uuid import uuid4

import httpx
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.rate_limits import MemoryRateLimiter
from app.database.session import create_engine
from app.embeddings.factory import create_provider
from app.evaluation.fixtures import read_corpus, seed
from app.evaluation.metrics import recall_at_k, reciprocal_rank
from app.evaluation.recording import RecordingCandidates
from app.indexing.archive import LANGUAGES
from app.indexing.pipeline import PIPELINE_VERSION, index_source
from app.jobs.prepare_search import run_preparation
from app.models import SearchIndex, User
from app.repositories.search import SearchStore
from app.retrieval.postgres import PostgresCandidates
from app.retrieval.ranking import fuse
from app.retrieval.text import VERSION
from app.schemas.search import SearchRequest
from app.services.search import SearchService
from app.services.search_preparation import PreparationService

DATASET = Path(__file__).resolve().parents[2] / "evaluation/retrieval/v1"


class Case(BaseModel):
    id: str
    query: str
    category: str
    relevant: set[str]


class Dataset(BaseModel):
    version: str
    cases: list[Case]


async def benchmark(
    settings: Settings,
    mode: str = "keyword",
    allow_paid: bool = False,
    dataset_path: Path = DATASET,
    rerank: bool = False,
) -> dict[str, object]:
    if not settings.database_url.get_secret_value().split("?")[0].endswith("_test"):
        raise ValueError("Evaluation requires a dedicated database whose name ends in _test")
    if mode == "hybrid" and (not allow_paid or not settings.embeddings_enabled):
        raise ValueError("Hybrid evaluation requires enabled embeddings and --allow-paid")
    raw = (dataset_path / "cases.json").read_bytes()
    dataset = Dataset.model_validate_json(raw)
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid4()
    limiter = MemoryRateLimiter()
    try:
        repo_id, corpus_hash = await seed(factory, dataset_path / "corpus", user_id)
        async with httpx.AsyncClient(trust_env=False) as client:
            provider = create_provider(settings, client) if mode == "hybrid" else None
            async with factory() as db:
                job, _ = await PreparationService(db, limiter, settings).start(
                    user_id, repo_id, mode
                )
            await run_preparation(factory, job.id, provider)
            async with factory() as db:
                stored = await db.get(SearchIndex, job.id)
                if stored is None or stored.status != "completed":
                    raise RuntimeError(
                        "Fixture search preparation failed; inspect safe worker logs"
                    )
                preparation_tokens, reserved = stored.input_tokens, stored.reserved_tokens
                chunks = await SearchStore(db).chunks(job.source_index_id)
            labels = {c.id: f"{c.path}::{c.name or '(module)'}" for c in chunks}
            ties = {c.id: f"{c.path}:{c.start_offset:010}" for c in chunks}
            cases: list[dict[str, object]] = []
            scores: dict[str, list[dict[str, float]]] = {}
            latencies: list[float] = []
            query_tokens, query_cost = 0, 0.0
            for case in dataset.cases:
                if not case.relevant or not case.relevant <= set(labels.values()):
                    raise ValueError(f"Invalid relevance labels for {case.id}")
                async with factory() as db:
                    recorder = RecordingCandidates(PostgresCandidates(db))
                    result = await SearchService(db, recorder, limiter, settings, provider).search(
                        user_id,
                        repo_id,
                        SearchRequest(query=case.query, mode=mode, top_k=8, rerank=rerank),
                    )
                latencies.append(result.duration_ms)
                query_tokens += result.query_tokens
                query_cost += result.estimated_query_cost_usd
                channel_sets = {
                    "lexical": ["lexical"],
                    "symbol": ["symbol"],
                    "keyword": ["lexical", "symbol"],
                }
                if mode == "hybrid":
                    channel_sets.update(
                        {"vector": ["vector"], "hybrid": ["lexical", "symbol", "vector"]}
                    )
                rankings: dict[str, list[str]] = {}
                for name, channels in channel_sets.items():
                    ranked = fuse({c: recorder.channels[c] for c in channels}, 8, ties)
                    retrieved = [labels[item.chunk_id] for item in ranked]
                    rankings[name] = retrieved
                    values = {
                        f"recall@{k}": recall_at_k(retrieved, case.relevant, k)
                        for k in (1, 3, 5, 8)
                    }
                    values["mrr@8"] = reciprocal_rank(retrieved, case.relevant)
                    scores.setdefault(name, []).append(values)
                cases.append(
                    {
                        "id": case.id,
                        "category": case.category,
                        "query": case.query,
                        "relevant": sorted(case.relevant),
                        "rankings": rankings,
                        "latency_ms": result.duration_ms,
                        "query_tokens": result.query_tokens,
                        "estimated_query_cost_usd": result.estimated_query_cost_usd,
                    }
                )
            return {
                "dataset": dataset.version,
                "dataset_sha256": sha256(raw).hexdigest(),
                "corpus_sha256": corpus_hash,
                "pipeline_version": VERSION,
                "source_pipeline_version": PIPELINE_VERSION,
                "provider_profile": job.provider_profile,
                "mode": mode,
                "reranker": result.reranker,
                "tiktoken_version": version("tiktoken"),
                "semantic_api_called": mode == "hybrid",
                "case_count": len(cases),
                "latency_ms": {
                    "p50": median(latencies),
                    "p95": sorted(latencies)[ceil(0.95 * len(latencies)) - 1],
                },
                "query_tokens": query_tokens,
                "estimated_query_cost_usd": query_cost,
                "preparation_tokens": preparation_tokens,
                "reserved_preparation_tokens": reserved,
                "preparation_cost_upper_estimate_usd": reserved * job.price_per_million / 1000000,
                "metrics": {
                    name: {key: mean(row[key] for row in rows) for key in rows[0]}
                    for name, rows in scores.items()
                },
                "cases": cases,
            }
    finally:
        async with engine.begin() as connection:
            await connection.execute(delete(User).where(User.id == user_id))
        await engine.dispose()


def check_dataset(path: Path) -> dict[str, object]:
    raw = (path / "cases.json").read_bytes()
    dataset = Dataset.model_validate_json(raw)
    if not dataset.cases or len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("Require nonempty cases with unique identifiers")
    files = read_corpus(path / "corpus")
    labels = set()
    symbol_count = chunk_count = 0
    for name, content in files:
        result = index_source(content, LANGUAGES[Path(name).suffix.lower()], name)
        if result.diagnostic:
            raise ValueError(f"Corpus parsing failed for {name}: {result.diagnostic}")
        symbol_count += len(result.symbols)
        chunk_count += len(result.chunks)
        for chunk in result.chunks:
            symbol = (
                result.symbols[chunk.symbol_ordinal].qualified_name
                if chunk.symbol_ordinal is not None
                else "(module)"
            )
            labels.add(f"{name}::{symbol}")
    for case in dataset.cases:
        if not case.relevant or not case.relevant <= labels:
            raise ValueError(f"Invalid relevance labels for {case.id}")
    return {
        "valid_cases": len(dataset.cases),
        "files": len(files),
        "symbols": symbol_count,
        "chunks": chunk_count,
        "dataset_sha256": sha256(raw).hexdigest(),
        "source_pipeline_version": PIPELINE_VERSION,
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--mode", choices=["keyword", "hybrid"], default="keyword")
    parser.add_argument("--rerank", action="store_true")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    check = check_dataset(args.dataset)
    if args.check:
        print(json.dumps(check))
        return
    if args.output is None:
        parser.error("--output is required for a database benchmark")
    report = asyncio.run(
        benchmark(Settings(), args.mode, args.allow_paid, args.dataset, args.rerank)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
