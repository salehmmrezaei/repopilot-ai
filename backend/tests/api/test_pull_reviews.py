import asyncio
import io
import json
import tarfile
from uuid import UUID, uuid4

from test_indexes import imported

from app.generation.contracts import AnswerDraft, Claim, GenerationResult
from app.services.reviews import review_pull


class PullGitHub:
    async def _get(self, url, limit):
        if "/pulls/" in url:
            return json.dumps(
                {
                    "number": 1,
                    "changed_files": 1,
                    "base": {"sha": "a" * 40},
                    "head": {"sha": "b" * 40},
                }
            ).encode()
        return json.dumps(
            {
                "merge_base_commit": {"sha": "c" * 40},
                "files": [{"filename": "math.py", "status": "modified"}],
            }
        ).encode()

    async def download(self, source):
        value = (
            b"def divide(a, b):\n    return a / b\n"
            if source.sha == "b" * 40
            else b"def divide(a, b):\n    return a / b if b else 0\n"
        )
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            info = tarfile.TarInfo("root/math.py")
            info.size = len(value)
            archive.addfile(info, io.BytesIO(value))
        return stream.getvalue()


class Reviewer:
    model = "test-model"
    calls = 0
    citation = "E2"

    async def generate(self, instructions, payload):
        self.calls += 1
        assert "untrusted data" in instructions
        assert "divide" in payload
        return GenerationResult(
            model=self.model,
            draft=AnswerDraft(
                status="answered",
                claims=[
                    Claim(
                        text="math.py:2 now raises for zero. Restore the guard.",
                        citation_ids=[self.citation],
                    )
                ],
                limitation="Tests were not run.",
            ),
            refused=False,
            input_tokens=100,
            output_tokens=30,
        )


def test_review_pinned_merge_base_evidence_idempotency_and_usage(auth_client):
    _, repo, _ = imported(auth_client)
    settings = auth_client.app.state.settings
    settings.answers_enabled = True
    key = uuid4()
    provider = Reviewer()

    async def run():
        async with auth_client.app.state.test_factory() as db:
            from app.models import Repository

            user_id = (await db.get(Repository, UUID(repo["id"]))).user_id
            result = await review_pull(
                db,
                user_id,
                UUID(repo["id"]),
                1,
                key,
                settings,
                auth_client.app.state.rate_limiter,
                PullGitHub(),
                provider,
            )
            assert result.status == "completed", result.error_code
            assert result.base_sha == "c" * 40 and result.head_sha == "b" * 40
            assert len(result.result["evidence"]) == 2
            assert result.input_tokens == 100 and result.estimated_cost_usd > 0
            again = await review_pull(
                db,
                user_id,
                UUID(repo["id"]),
                1,
                key,
                settings,
                auth_client.app.state.rate_limiter,
                PullGitHub(),
                provider,
            )
            assert again.id == result.id

    asyncio.run(run())
    assert provider.calls == 1
    rows = auth_client.get(f"/repositories/{repo['id']}/reviews").json()
    assert len(rows) == 1
    assert rows[0]["result"]["generation"]["draft"]["claims"]


def test_review_rejects_invalid_citation_but_preserves_usage(auth_client):
    _, repo, _ = imported(auth_client)
    settings = auth_client.app.state.settings
    settings.answers_enabled = True
    provider = Reviewer()
    provider.citation = "INVENTED"

    async def run():
        async with auth_client.app.state.test_factory() as db:
            from app.models import Repository

            user_id = (await db.get(Repository, UUID(repo["id"]))).user_id
            result = await review_pull(
                db,
                user_id,
                UUID(repo["id"]),
                1,
                uuid4(),
                settings,
                auth_client.app.state.rate_limiter,
                PullGitHub(),
                provider,
            )
            assert result.status == "failed"
            assert result.error_code == "answer_citation_invalid"
            assert result.result is None and result.input_tokens == 100

    asyncio.run(run())
