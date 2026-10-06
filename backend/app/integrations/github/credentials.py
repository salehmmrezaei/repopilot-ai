from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.models import GitHubAccount, Repository


def cipher(settings: Settings) -> Fernet:
    if not settings.github_oauth_enabled or settings.github_token_key is None:
        raise AppError("github_disabled", "GitHub sign-in is not configured.", 409)
    return Fernet(settings.github_token_key.get_secret_value().encode())


def decrypt(settings: Settings, value: str) -> str:
    try:
        return cipher(settings).decrypt(value.encode()).decode()
    except InvalidToken:
        raise AppError("github_reconnect", "Reconnect GitHub to restore access.", 409) from None


async def repository_token(db: AsyncSession, settings: Settings, repository_id: UUID) -> str | None:
    if not settings.github_oauth_enabled:
        return None
    account = await db.scalar(
        select(GitHubAccount)
        .join(
            Repository,
            Repository.user_id == GitHubAccount.user_id,
        )
        .where(Repository.id == repository_id)
    )
    return (
        decrypt(settings, account.encrypted_token) if account and account.encrypted_token else None
    )
