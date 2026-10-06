from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class GitHubAccount(Base):
    __tablename__ = "github_accounts"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(100))
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    private_access: Mapped[bool] = mapped_column(default=False)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    session_hash: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    encrypted_verifier: Mapped[str] = mapped_column(Text)
    private_access: Mapped[bool]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
