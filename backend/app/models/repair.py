from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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


class RepairRun(Base):
    __tablename__ = "repair_runs"
    __table_args__ = (
        UniqueConstraint("root_agent_id", "request_key", name="uq_repair_request"),
        CheckConstraint("max_revisions BETWEEN 1 AND 2", name="ck_repair_budget"),
        CheckConstraint("revision BETWEEN 0 AND max_revisions", name="ck_repair_revision"),
        CheckConstraint(
            "status IN ('running','passed','exhausted','stopped','cancelled')",
            name="ck_repair_status",
        ),
        CheckConstraint("profile IN ('auto','python','javascript')", name="ck_repair_profile"),
        Index(
            "uq_repair_active",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    root_agent_id: Mapped[UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    request_key: Mapped[UUID]
    profile: Mapped[str] = mapped_column(String(20))
    max_revisions: Mapped[int]
    revision: Mapped[int] = mapped_column(default=0)
    current_agent_id: Mapped[UUID] = mapped_column(index=True)
    current_execution_id: Mapped[UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    reason: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
