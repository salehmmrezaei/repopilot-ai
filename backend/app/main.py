import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint

from app.api.routes.agent_events import router as agent_event_router
from app.api.routes.agents import router as agent_router
from app.api.routes.answer_runs import router as run_router
from app.api.routes.answers import router as answer_router
from app.api.routes.auth import router as auth_router
from app.api.routes.conversations import router as conversation_router
from app.api.routes.health import router
from app.api.routes.indexes import router as index_router
from app.api.routes.repositories import router as repository_router
from app.api.routes.run_events import router as run_event_router
from app.api.routes.search import router as search_router
from app.auth.passwords import hash_password
from app.auth.throttle import AuthThrottle
from app.auth.tokens import new_token
from app.core.config import Settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.rate_limits import RedisRateLimiter
from app.database.session import DatabaseProbe, create_engine
from app.embeddings.factory import create_provider
from app.generation.factory import create_answer_provider

logger = logging.getLogger("repopilot.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(config)
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        app.state.dummy_password_hash = await hash_password(new_token())
        redis = Redis.from_url(
            config.redis_url.get_secret_value(),
            socket_timeout=2,
            socket_connect_timeout=2,
            decode_responses=True,
        )
        app.state.rate_limiter = RedisRateLimiter(redis)
        app.state.auth_throttle = AuthThrottle(app.state.rate_limiter)
        embedding_client = httpx.AsyncClient(trust_env=False, follow_redirects=False)
        app.state.embedding_provider = create_provider(config, embedding_client)
        app.state.answer_provider = create_answer_provider(config, embedding_client)
        app.state.readiness_probe = DatabaseProbe(engine, config.database_timeout_seconds)
        try:
            yield
        finally:
            await embedding_client.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="RepoPilot AI", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    install_error_handlers(app)
    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(repository_router)
    app.include_router(index_router)
    app.include_router(search_router)
    app.include_router(answer_router)
    app.include_router(run_router)
    app.include_router(run_event_router)
    app.include_router(conversation_router)
    app.include_router(agent_router)
    app.include_router(agent_event_router)

    @app.middleware("http")
    async def request_logging(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = perf_counter()
        request_id = str(uuid4())
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "error_type": type(exc).__name__,
                },
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "An unexpected error occurred.",
                        "request_id": request_id,
                    }
                },
            )
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        route = request.scope.get("route")
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "route": getattr(route, "path", "unmatched"),
                "status_code": response.status_code,
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            },
        )
        return response

    return app
