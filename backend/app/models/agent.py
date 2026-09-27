from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("conversation_id", "request_key", name="uq_agent_request"),
        Index(
            "uq_agent_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
            sqlite_where=text("status IN ('queued','running')"),
        ),
        Index("ix_agent_dispatch", "status", "created_at"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_agent_status",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    request_key: Mapped[UUID]
    question: Mapped[str] = mapped_column(Text)
    source_index_id: Mapped[UUID]
    commit_sha: Mapped[str] = mapped_column(String(40))
    config_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), server_default="queued")
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(300))
    lease_token: Mapped[UUID | None]
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentEvent(Base):
    __tablename__ = "agent_events"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    summary: Mapped[str] = mapped_column(String(1000))
    tool: Mapped[str | None] = mapped_column(String(30))
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("sequence >= 1 AND sequence <= 32", name="ck_agent_event_sequence"),
    )


class AgentCall(Base):
    __tablename__ = "agent_calls"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True
    )
    step: Mapped[int] = mapped_column(primary_key=True)
    input_rate: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    output_rate: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("step >= 1 AND step <= 5", name="ck_agent_step"),
        CheckConstraint("input_rate >= 0 AND output_rate >= 0", name="ck_agent_rates"),
        CheckConstraint(
            "(input_tokens IS NULL AND output_tokens IS NULL AND cost_usd IS NULL) OR "
            "(input_tokens IS NOT NULL AND output_tokens IS NOT NULL AND cost_usd IS NOT NULL "
            "AND input_tokens >= 0 AND output_tokens >= 0 AND cost_usd >= 0)",
            name="ck_agent_usage",
        ),
    )
