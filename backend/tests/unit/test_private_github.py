import asyncio

import httpx
import pytest

from app.indexing.errors import ImportFailure
from app.integrations.github.client import GitHubClient, RepositorySource


class Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"archive"


def test_signed_archive_redirect_does_not_forward_token():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.host == "api.github.com":
            assert request.headers["authorization"] == "Bearer secret"
            return httpx.Response(
                302,
                headers={
                    "location": "https://codeload.github.com/o/r/tar.gz/"
                    + "a" * 40
                    + "?signed=opaque"
                },
            )
        assert "authorization" not in request.headers
        return httpx.Response(200, stream=Body())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            value = await GitHubClient(client, 100, "secret").download(
                RepositorySource(1, "o", "r", "main", "a" * 40)
            )
            assert value == b"archive"

    asyncio.run(run())
    assert len(requests) == 2


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/archive",
        "http://codeload.github.com/a",
        "https://codeload.github.com@evil.example/a",
    ],
)
def test_private_archive_rejects_untrusted_redirects(location):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(302, headers={"location": location})
            )
        ) as client:
            with pytest.raises(ImportFailure):
                await GitHubClient(client, 100, "secret").download(
                    RepositorySource(1, "o", "r", "", "a" * 40)
                )

    asyncio.run(run())
