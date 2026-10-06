"""Single-use, browser/session-bound GitHub OAuth; never link accounts by email."""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.auth.tokens import new_token, token_hash
from app.core.config import Settings
from app.core.errors import AppError
from app.integrations.github.credentials import cipher, decrypt
from app.models import GitHubAccount, OAuthState, User
from app.services.auth import AuthService, Identity


async def bounded_json(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: object
) -> object:
    # Fixed endpoint callers only; no response bodies or access tokens in errors/logs.
    try:
        async with client.stream(method, url, **kwargs) as response:  # type: ignore[arg-type]
            if response.status_code != 200:
                raise ValueError("status")
            data = bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data) > 65536:
                    raise ValueError("size")
            return json.loads(data)
    except (httpx.HTTPError, ValueError):
        raise AppError(
            "github_oauth_failed", "GitHub authorization failed. Start again.", 502
        ) from None


class GitHubOAuth:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db, self.settings = db, settings

    async def start(self, identity: Identity | None, private: bool) -> tuple[str, str]:
        encryption = cipher(self.settings)
        state, browser, verifier = new_token(), new_token(), new_token()
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        await self.db.execute(delete(OAuthState).where(OAuthState.expires_at < datetime.now(UTC)))
        self.db.add(
            OAuthState(
                state_hash=token_hash(state),
                browser_hash=token_hash(browser),
                session_hash=token_hash(identity.token) if identity else None,
                user_id=identity.user.id if identity else None,
                encrypted_verifier=encryption.encrypt(verifier.encode()).decode(),
                private_access=private,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
        await self.db.commit()
        return "https://github.com/login/oauth/authorize?" + urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": self.settings.github_callback_url,
                "state": state,
                "scope": "repo" if private else "read:user",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        ), browser

    async def finish(
        self,
        state: str,
        browser: str,
        code: str,
        session: str | None,
        auth: AuthService,
        client: httpx.AsyncClient,
    ) -> Identity:
        cipher(self.settings)
        if not 1 <= len(state) <= 200 or not 1 <= len(browser) <= 200 or not 1 <= len(code) <= 512:
            raise AppError("oauth_state_invalid", "Authorization expired. Start again.", 400)
        row = await self.db.scalar(
            delete(OAuthState)
            .where(
                OAuthState.state_hash == token_hash(state),
                OAuthState.browser_hash == token_hash(browser),
                OAuthState.expires_at > datetime.now(UTC),
            )
            .returning(OAuthState)
        )
        if row is None:
            raise AppError("oauth_state_invalid", "Authorization expired. Start again.", 400)
        await self.db.commit()  # Consume before network I/O; replay never exchanges a code twice.
        if row.user_id is not None:
            current = await auth.authenticate(session)
            if current.user.id != row.user_id or token_hash(current.token) != row.session_hash:
                raise AppError("oauth_session_changed", "Session changed. Start again.", 403)
        elif session is not None:
            raise AppError("oauth_session_changed", "Session changed. Start again.", 403)
        secret = self.settings.github_client_secret
        assert secret is not None
        token_data = await bounded_json(
            client,
            "POST",
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self.settings.github_client_id,
                "client_secret": secret.get_secret_value(),
                "code": code,
                "redirect_uri": self.settings.github_callback_url,
                "code_verifier": decrypt(self.settings, row.encrypted_verifier),
            },
        )
        if not isinstance(token_data, dict) or not isinstance(token_data.get("access_token"), str):
            raise AppError("github_oauth_failed", "GitHub denied authorization.", 400)
        token = token_data["access_token"]
        if not 1 <= len(token) <= 4096 or token_data.get("token_type", "").lower() != "bearer":
            raise AppError("github_oauth_failed", "Invalid GitHub authorization.", 502)
        scopes = str(token_data.get("scope", "")).replace(",", " ").split()
        if row.private_access and "repo" not in scopes:
            raise AppError(
                "github_scope_missing", "Private repository permission was not granted.", 403
            )
        profile = await bounded_json(
            client,
            "GET",
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        if (
            not isinstance(profile, dict)
            or type(profile.get("id")) is not int
            or profile["id"] <= 0
            or not isinstance(profile.get("login"), str)
        ):
            raise AppError("github_oauth_failed", "Invalid GitHub identity.", 502)
        account = await self.db.scalar(
            select(GitHubAccount).where(GitHubAccount.github_id == profile["id"])
        )
        if account and row.user_id is not None and account.user_id != row.user_id:
            raise AppError(
                "github_already_linked", "GitHub identity belongs to another account.", 409
            )
        user = (
            await self.db.get(User, account.user_id if account else row.user_id)
            if account or row.user_id
            else None
        )
        if user is None:
            user = User(
                email=f"github-{profile['id']}@oauth.repopilot.invalid",
                name=profile["login"][:100],
                password_hash=await hash_password(new_token()),
            )
            self.db.add(user)
            await self.db.flush()
        if account is None:
            if await self.db.get(GitHubAccount, user.id):
                raise AppError(
                    "github_already_linked",
                    "This account already has a different GitHub identity.",
                    409,
                )
            account = GitHubAccount(
                user_id=user.id, github_id=profile["id"], login=profile["login"][:100]
            )
            self.db.add(account)
        account.encrypted_token = cipher(self.settings).encrypt(token.encode()).decode()
        account.private_access = "repo" in scopes
        account.login = profile["login"][:100]
        identity = await auth._new_session(user, session)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise AppError(
                "github_account_conflict", "Account changed. Start authorization again.", 409
            ) from None
        return identity
