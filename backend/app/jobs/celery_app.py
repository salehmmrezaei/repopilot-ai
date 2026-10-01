import asyncio
import logging
from uuid import UUID

import httpx
from celery import Celery
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.core.logging import configure_logging
from app.database.session import create_engine
from app.integrations.github.client import GitHubClient
from app.jobs.import_repository import run_import

settings = Settings()
celery_app = Celery("repopilot", broker=settings.redis_url.get_secret_value())
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=150,
    task_time_limit=170,
    broker_transport_options={
        "visibility_timeout": 360,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
    },
    broker_connection_timeout=2,
    broker_connection_retry_on_startup=True,
    task_publish_retry=False,
    worker_hijack_root_logger=False,
)


async def execute(job_id: UUID) -> None:
    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            await run_import(
                async_sessionmaker(engine, expire_on_commit=False),
                GitHubClient(client, settings.import_download_bytes),
                job_id,
                settings.import_timeout_seconds,
            )
    finally:
        await engine.dispose()


@celery_app.task(name="repopilot.import_repository")  # type: ignore[untyped-decorator]
def import_repository(job_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute(UUID(job_id)))
    except Exception as exc:
        # Leave durable queued/leased state for dispatcher recovery, without logging payloads.
        logging.getLogger("repopilot.worker").error(
            "worker_task_failed", extra={"error_type": type(exc).__name__}
        )


async def execute_index(job_id: UUID) -> None:
    from app.jobs.index_repository import run_index

    engine = create_engine(settings)
    try:
        await run_index(
            async_sessionmaker(engine, expire_on_commit=False),
            job_id,
            settings.index_timeout_seconds,
        )
    finally:
        await engine.dispose()


@celery_app.task(name="repopilot.index_repository")  # type: ignore[untyped-decorator]
def index_repository(job_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute_index(UUID(job_id)))
    except Exception as exc:
        logging.getLogger("repopilot.worker").error(
            "index_task_failed", extra={"error_type": type(exc).__name__}
        )


async def execute_preparation(job_id: UUID) -> None:
    from app.embeddings.factory import create_provider
    from app.jobs.prepare_search import run_preparation

    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            await run_preparation(
                async_sessionmaker(engine, expire_on_commit=False),
                job_id,
                create_provider(settings, client),
                settings.index_timeout_seconds,
            )
    finally:
        await engine.dispose()


@celery_app.task(name="repopilot.prepare_search")  # type: ignore[untyped-decorator]
def prepare_search(job_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute_preparation(UUID(job_id)))
    except Exception as exc:
        logging.getLogger("repopilot.worker").error(
            "search_task_failed", extra={"error_type": type(exc).__name__}
        )


async def execute_answer(run_id: UUID) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.rate_limits import RedisRateLimiter
    from app.embeddings.factory import create_provider
    from app.generation.factory import create_answer_provider
    from app.jobs.answer_run import run_answer
    from app.retrieval.postgres import PostgresCandidates
    from app.services.answers import AnswerService
    from app.services.search import SearchService

    engine = create_engine(settings)
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            limiter = RedisRateLimiter(redis)

            def build(db: AsyncSession) -> AnswerService:
                search = SearchService(
                    db, PostgresCandidates(db), limiter, settings, create_provider(settings, client)
                )
                return AnswerService(
                    db, search, limiter, settings, create_answer_provider(settings, client)
                )

            await run_answer(
                async_sessionmaker(engine, expire_on_commit=False), run_id, settings, build
            )
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="repopilot.answer_run")  # type: ignore[untyped-decorator]
def answer_run(run_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute_answer(UUID(run_id)))
    except Exception as exc:
        logging.getLogger("repopilot.worker").error(
            "answer_task_failed", extra={"job_id": run_id, "error_type": type(exc).__name__}
        )


async def execute_agent(run_id: UUID) -> None:
    from redis.asyncio import Redis
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.agents.provider import OpenAIAgent
    from app.agents.tools import RepositoryTools
    from app.core.rate_limits import RedisRateLimiter
    from app.jobs.agent_run import run_agent
    from app.models import AgentRun
    from app.retrieval.postgres import PostgresCandidates
    from app.services.search import SearchService

    engine = create_engine(settings)
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_timeout=2,
        socket_connect_timeout=2,
        decode_responses=True,
    )
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            limiter = RedisRateLimiter(redis)

            def build(
                db: AsyncSession, user_id: UUID, repository_id: UUID, index_id: UUID
            ) -> RepositoryTools:
                return RepositoryTools(
                    db,
                    SearchService(db, PostgresCandidates(db), limiter, settings, None),
                    user_id,
                    repository_id,
                    index_id,
                )

            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                run = await db.get(AgentRun, run_id)
                if run is None:
                    return
                mode = run.mode
            await run_agent(
                factory,
                run_id,
                settings,
                OpenAIAgent(client, settings, mode),
                build,
            )
    finally:
        await redis.aclose()
        await engine.dispose()


@celery_app.task(name="repopilot.agent_run")  # type: ignore[untyped-decorator]
def agent_run(run_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute_agent(UUID(run_id)))
    except Exception as exc:
        logging.getLogger("repopilot.worker").error(
            "agent_task_failed", extra={"job_id": run_id, "error_type": type(exc).__name__}
        )


async def execute_sandbox(run_id: UUID) -> None:
    from app.execution.client import SandboxClient
    from app.jobs.execution_run import run_execution

    engine = create_engine(settings)
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
            await run_execution(
                async_sessionmaker(engine, expire_on_commit=False),
                run_id,
                settings,
                GitHubClient(client, 10 * 1024 * 1024),
                SandboxClient(client, settings),
            )
    finally:
        await engine.dispose()


@celery_app.task(name="repopilot.execution_run", soft_time_limit=290, time_limit=310)  # type: ignore[untyped-decorator]
def execution_run(run_id: str) -> None:
    configure_logging()
    try:
        asyncio.run(execute_sandbox(UUID(run_id)))
    except Exception as exc:
        logging.getLogger("repopilot.worker").error(
            "execution_failed", extra={"job_id": run_id, "error_type": type(exc).__name__}
        )
