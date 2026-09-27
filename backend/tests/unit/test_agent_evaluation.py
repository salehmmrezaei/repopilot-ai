import asyncio

import pytest
from pydantic import TypeAdapter

from app.agents.contracts import ToolInput
from app.evaluation.agents import DEFAULT, PROPOSAL_DEFAULT, Case, FixtureTools


@pytest.mark.parametrize("dataset", [DEFAULT, PROPOSAL_DEFAULT])
def test_versioned_agent_cases_are_executable_without_provider_calls(dataset):
    cases = TypeAdapter(list[Case]).validate_json(dataset.read_bytes())
    assert len(cases) == 4 and len({case.id for case in cases}) == 4

    async def exercise():
        for case in cases:
            tool = FixtureTools(case)
            for path, content in case.files.items():
                result = await tool.execute(
                    ToolInput(tool="read_file", path=path, start_line=1, query=None)
                )
                assert result[0].content == content and result[0].path == path

    asyncio.run(exercise())
