from dataclasses import dataclass

from app.indexing.chunks import CHUNKER_VERSION, Chunk, chunk_source
from app.indexing.errors import ImportFailure
from app.indexing.parser import PARSER_VERSION, Parsed, Symbol, parse_python
from app.indexing.typescript import PARSER_VERSION as TS_PARSER_VERSION
from app.indexing.typescript import parse_typescript

PIPELINE_VERSION = PARSER_VERSION + ":" + TS_PARSER_VERSION + ":" + CHUNKER_VERSION


@dataclass(frozen=True)
class IndexedSource:
    symbols: list[Symbol]
    chunks: list[Chunk]
    diagnostic: str | None


def index_source(content: str, language: str, path: str = "") -> IndexedSource:
    if len(content.encode("utf-8")) > 256 * 1024:
        raise ImportFailure("index_file_limit", "Source file exceeds the indexing size limit.")
    if language == "python":
        parsed = parse_python(content)
    elif language == "typescript":
        parsed = parse_typescript(content, tsx=path.lower().endswith(".tsx"))
    else:
        parsed = Parsed([])
    mode = (
        "fallback"
        if parsed.diagnostic
        else "module"
        if language in {"python", "typescript"}
        else "text"
    )
    return IndexedSource(
        parsed.symbols, chunk_source(content, parsed.symbols, mode), parsed.diagnostic
    )
