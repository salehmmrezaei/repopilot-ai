"""Replaceable structured action provider; the model never executes tools itself."""

import json
from hashlib import sha256
from pathlib import Path

import httpx

from app.agents.contracts import Decision, ProposalDecision, StepResult
from app.core.config import Settings
from app.core.errors import AppError
from app.core.provider_usage import ProviderUsageError
from app.generation.openai_payload import ResponsePayload, Usage

INSTRUCTIONS = (Path(__file__).parent / "prompts/investigation_v1.txt").read_text()
PROMPT_HASH = sha256(INSTRUCTIONS.encode()).hexdigest()


PROPOSAL_INSTRUCTIONS = (Path(__file__).parent / "prompts/proposal_v1.txt").read_text()


def configuration(settings: Settings, mode: str = "investigate") -> str:
    value = [
        PROMPT_HASH,
        settings.answer_model,
        settings.answer_max_output_tokens,
        settings.answer_input_price_per_million,
        settings.answer_output_price_per_million,
        "read-only-v2",
        5,
        8000,
    ]
    if mode == "propose":
        value.extend(["proposal-v1", sha256(PROPOSAL_INSTRUCTIONS.encode()).hexdigest()])
    return sha256(json.dumps(value).encode()).hexdigest()


class OpenAIAgent:
    def __init__(
        self, client: httpx.AsyncClient, settings: Settings, mode: str = "investigate"
    ) -> None:
        self.client, self.settings = client, settings
        self.schema = ProposalDecision if mode == "propose" else Decision
        self.instructions = PROPOSAL_INSTRUCTIONS if mode == "propose" else INSTRUCTIONS

    async def decide(self, payload: str) -> StepResult:
        key = self.settings.openai_api_key
        if key is None:
            raise AppError("agent_disabled", "Configure the agent provider first.", 409)
        usage: Usage | None = None
        try:
            async with self.client.stream(
                "POST",
                "https://api.openai.com/v1/responses",
                headers={"Authorization": "Bearer " + key.get_secret_value()},
                json={
                    "model": self.settings.answer_model,
                    "instructions": self.instructions,
                    "input": [{"role": "user", "content": payload}],
                    "store": False,
                    "tools": [],
                    "max_output_tokens": self.settings.answer_max_output_tokens,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": "investigation_decision",
                            "strict": True,
                            "schema": self.schema.model_json_schema(),
                        }
                    },
                },
                timeout=25,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    raise AppError(
                        "agent_provider_unavailable",
                        "Agent provider rejected the request. No automatic retry was made.",
                        503,
                    )
                body = bytearray()
                async for block in response.aiter_bytes():
                    body.extend(block)
                    if len(body) > 256 * 1024:
                        raise ValueError("Provider response too large")
            raw = json.loads(body)
            if isinstance(raw, dict) and raw.get("model") == self.settings.answer_model:
                candidate = Usage.model_validate(raw.get("usage"))
                if (
                    candidate.total_tokens == candidate.input_tokens + candidate.output_tokens
                    and candidate.input_tokens <= 100000
                    and candidate.output_tokens <= self.settings.answer_max_output_tokens
                ):
                    usage = candidate
            data = ResponsePayload.model_validate(raw)
            if usage is None or data.status != "completed" or len(data.output) != 1:
                raise ValueError("Invalid model envelope")
            item = data.output[0]
            if (
                item.type != "message"
                or item.role != "assistant"
                or item.status != "completed"
                or len(item.content) != 1
                or item.content[0].type != "output_text"
            ):
                raise ValueError("Model refused or returned unsupported output")
            return StepResult(
                decision=self.schema.model_validate_json(item.content[0].text or ""),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
        except (httpx.HTTPError, TimeoutError):
            raise AppError(
                "agent_provider_unavailable", "Agent provider timed out. Usage may be unknown.", 503
            ) from None
        except (ValueError, TypeError):
            error = AppError(
                "agent_output_invalid",
                "Agent output failed validation. No automatic retry was made.",
                502,
            )
            if usage is not None:
                raise ProviderUsageError(error, usage.input_tokens, usage.output_tokens) from None
            raise error from None
