import asyncio
import hmac
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_db
from app.core.errors import AppError
from app.models import (
    AgentRun,
    AnswerRun,
    ExecutionRun,
    ImportJob,
    PullReview,
    RepairRun,
    RepositoryIndex,
    SearchIndex,
)

router = APIRouter(tags=["operations"])


def authorized(request: Request) -> None:
    configured = request.app.state.settings.metrics_token
    supplied = request.headers.get("authorization", "")
    if (
        configured is None
        or len(configured.get_secret_value()) < 32
        or not hmac.compare_digest(
            supplied.encode(), ("Bearer " + configured.get_secret_value()).encode()
        )
    ):
        raise AppError("not_found", "Not found.", 404)


@router.get("/metrics", dependencies=[Depends(authorized)], include_in_schema=False)
async def metrics(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> Response:
    output = request.app.state.http_metrics.render()
    output += "# TYPE repopilot_jobs gauge\n# TYPE repopilot_oldest_pending_seconds gauge\n"
    async with asyncio.timeout(5):
        models: list[tuple[Any, str]] = [
            (ImportJob, "import"),
            (RepositoryIndex, "index"),
            (SearchIndex, "search"),
            (AnswerRun, "answer"),
            (AgentRun, "agent"),
            (ExecutionRun, "execution"),
            (RepairRun, "repair"),
            (PullReview, "review"),
        ]
        for model, kind in models:
            for status, count in (
                await db.execute(select(model.status, func.count()).group_by(model.status))
            ).all():
                output += f'repopilot_jobs{{kind="{kind}",status="{status}"}} {count}\n'
            oldest = await db.scalar(
                select(func.min(model.created_at)).where(model.status.in_(["queued", "running"]))
            )
            age = (
                max(0, (datetime.now(UTC) - oldest.replace(tzinfo=UTC)).total_seconds())
                if oldest
                else 0
            )
            output += f'repopilot_oldest_pending_seconds{{kind="{kind}"}} {age}\n'
    return Response(output, media_type="text/plain; version=0.0.4")
