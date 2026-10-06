from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PullReview(Base):
    __tablename__ = "pull_reviews"
    __table_args__ = (UniqueConstraint("repository_id", "request_key", name="uq_review_request"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"))
    request_key: Mapped[UUID]
    pull_number: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="running")
    base_sha: Mapped[str | None] = mapped_column(String(40))
    head_sha: Mapped[str | None] = mapped_column(String(40))
    result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None]
    output_tokens: Mapped[int | None]
    estimated_cost_usd: Mapped[float | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
