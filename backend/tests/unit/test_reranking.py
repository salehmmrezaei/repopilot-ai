from uuid import uuid4

from app.retrieval.contracts import SourceChunk
from app.retrieval.ranking import Ranked
from app.retrieval.reranking import rerank


def test_exact_symbol_reranking_and_deterministic_bounds():
    first, second, file = uuid4(), uuid4(), uuid4()
    chunks = {
        first: SourceChunk(first, file, "misc.py", "python", None, None, "unrelated", 1, 1, 0, 9),
        second: SourceChunk(
            second,
            file,
            "auth.py",
            "python",
            "validate_token",
            None,
            "def validate_token(): pass",
            1,
            1,
            0,
            25,
        ),
    }
    ranked = [Ranked(first, 0.03, {"lexical": 1}), Ranked(second, 0.02, {"symbol": 1})]
    result = rerank("validate_token", ranked, chunks, 1)
    assert len(result) == 1 and result[0].chunk_id == second
    assert result[0].channel_ranks == {"symbol": 1}
    assert ranked[0].chunk_id == first
