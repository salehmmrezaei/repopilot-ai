import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from test_repositories import sign_in

from app.core.errors import AppError
from app.models import GitHubAccount
from app.services.auth import AuthService
from app.services.github_oauth import GitHubOAuth


def test_oauth_exchange_pkce_identity_encryption_and_replay(auth_client):
    sign_in(auth_client)
    settings = auth_client.app.state.settings
    settings.github_oauth_enabled = True
    settings.github_client_id = "client"
    settings.github_client_secret = SecretStr("secret")
    settings.github_token_key = SecretStr(Fernet.generate_key().decode())
    session = auth_client.cookies.get("repopilot_session")
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "github.com":
            assert b"code_verifier=" in request.content
            return httpx.Response(
                200, json={"access_token": "private-token", "token_type": "bearer", "scope": "repo"}
            )
        assert request.headers["authorization"] == "Bearer private-token"
        return httpx.Response(
            200, json={"id": 123, "login": "octocat", "email": "untrusted@example.com"}
        )

    async def scenario():
        async with auth_client.app.state.test_factory() as db:
            auth = AuthService(db, 24, auth_client.app.state.dummy_password_hash)
            identity = await auth.authenticate(session)
            oauth = GitHubOAuth(db, settings)
            url, browser = await oauth.start(identity, True)
            state = parse_qs(urlsplit(url).query)["state"][0]
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                result = await oauth.finish(state, browser, "code", session, auth, client)
                assert result.user.id == identity.user.id
                account = await db.scalar(select(GitHubAccount))
                assert account.encrypted_token != "private-token"
                assert (
                    Fernet(settings.github_token_key.get_secret_value()).decrypt(
                        account.encrypted_token.encode()
                    )
                    == b"private-token"
                )
                assert account.private_access
                with pytest.raises(AppError, match="Authorization expired"):
                    await oauth.finish(state, browser, "code", session, auth, client)

    asyncio.run(scenario())
    assert len(calls) == 2
