from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "request_key", name="uq_execution_request"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_execution_status",
        ),
        CheckConstraint("profile IN ('auto','python','javascript')", name="ck_execution_profile"),
        Index(
            "uq_execution_active",
            "agent_run_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
            sqlite_where=text("status IN ('queued','running')"),
        ),
        Index("ix_execution_dispatch", "status", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    request_key: Mapped[UUID]
    profile: Mapped[str] = mapped_column(String(20))
    patch_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), server_default="queued")
    stage: Mapped[str] = mapped_column(String(40), server_default="queued")
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(String(300))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
