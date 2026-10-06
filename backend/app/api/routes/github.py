from typing import Annotated, cast

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    SESSION_COOKIE,
    get_db,
    get_service,
    get_settings,
    require_browser_request,
    require_csrf,
)
from app.api.routes.auth import set_session_cookie
from app.core.config import Settings
from app.core.rate_limits import Limit, RateLimiter
from app.models import GitHubAccount
from app.services.auth import AuthService, Identity
from app.services.github_oauth import GitHubOAuth

router = APIRouter(prefix="/auth/github", tags=["github"])
DB = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]
Auth = Annotated[AuthService, Depends(get_service)]
COOKIE = "repopilot_oauth"


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    private_access: bool = False


@router.get("/status")
async def status(request: Request, db: DB, settings: Config, auth: Auth) -> dict[str, object]:
    account = None
    if request.cookies.get(SESSION_COOKIE):
        identity = await auth.authenticate(request.cookies.get(SESSION_COOKIE))
        account = await db.get(GitHubAccount, identity.user.id)
    return {
        "enabled": settings.github_oauth_enabled,
        "connected": bool(account and account.encrypted_token),
        "login": account.login if account else None,
        "private_access": bool(account and account.private_access),
    }


@router.post("/start", dependencies=[Depends(require_browser_request)])
async def start(
    data: Start, request: Request, response: Response, db: DB, settings: Config, auth: Auth
) -> dict[str, str]:
    identity = None
    if request.cookies.get(SESSION_COOKIE):
        identity = await auth.authenticate(request.cookies.get(SESSION_COOKIE))
        require_csrf(request, identity, None)
    limiter = cast(RateLimiter, request.app.state.rate_limiter)
    await limiter.check(
        [
            Limit(
                "oauth:"
                + (
                    str(identity.user.id)
                    if identity
                    else request.client.host
                    if request.client
                    else "unknown"
                ),
                10,
                600,
            )
        ]
    )
    url, browser = await GitHubOAuth(db, settings).start(identity, data.private_access)
    response.set_cookie(
        COOKIE,
        browser,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return {"url": url}


@router.get("/callback")
async def callback(
    request: Request, db: DB, settings: Config, auth: Auth, state: str = "", code: str = ""
) -> Response:
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        identity = await GitHubOAuth(db, settings).finish(
            state,
            request.cookies.get(COOKIE, ""),
            code,
            request.cookies.get(SESSION_COOKIE),
            auth,
            client,
        )
    response = RedirectResponse(settings.frontend_origin, status_code=303)
    response.delete_cookie(
        COOKIE, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
    set_session_cookie(response, identity, settings)
    return response


@router.delete("/connection", status_code=204)
async def disconnect(identity: Annotated[Identity, Depends(require_csrf)], db: DB) -> Response:
    account = await db.get(GitHubAccount, identity.user.id)
    if account:
        account.encrypted_token = None
        account.private_access = False
        await db.commit()
    # Keep identity binding so a GitHub-only account can sign in again safely.
    return Response(status_code=204)
