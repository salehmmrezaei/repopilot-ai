from uuid import UUID

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.execution.contracts import SandboxRequest, SandboxResult


class SandboxClient:
    def __init__(self, client: httpx.AsyncClient, settings: Settings):
        self.client, self.settings = client, settings

    async def request(
        self, method: str, run_id: UUID, body: SandboxRequest | None = None
    ) -> SandboxResult:
        token = self.settings.sandbox_token
        if token is None:
            raise AppError("sandbox_disabled", "Sandbox is not configured.", 409)
        async with self.client.stream(
            method,
            self.settings.sandbox_url.rstrip("/") + "/runs/" + str(run_id),
            headers={"Authorization": "Bearer " + token.get_secret_value()},
            json=body.model_dump(mode="json") if body else None,
            timeout=20,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise AppError(
                    "sandbox_unavailable",
                    "Sandbox request failed. Inspect run status before retrying.",
                    503,
                )
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 256 * 1024:
                    raise AppError("sandbox_invalid", "Sandbox response exceeded its limit.", 502)
        return SandboxResult.model_validate_json(data)
