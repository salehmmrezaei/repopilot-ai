from pathlib import Path

import pytest

from app.indexing.chunks import chunk_source
from app.indexing.errors import ImportFailure
from app.indexing.pipeline import index_source


def test_symbols_decorators_scopes_and_non_execution() -> None:
    text = Path("tests/fixtures/indexing/sample.py").read_text()
    result = index_source(text, "python")
    assert result.diagnostic is None
    assert [(s.qualified_name, s.kind) for s in result.symbols] == [
        ("FilePath", "import"),
        ("DEFAULT_LIMIT", "variable"),
        ("Catalog", "class"),
        ("Catalog.label", "variable"),
        ("Catalog.find", "method"),
        ("Catalog.find.normalize", "function"),
        ("refresh", "function"),
    ]
    method = result.symbols[4]
    assert method.start_line == 11 and method.end_line == 18
    assert method.signature == "def find(name: str) -> str"
    assert method.docstring == "Return a matching name."
    assert result.symbols[5].parent == 4
    assert any(
        c.content.startswith("    @staticmethod") and "return normalize(name)" in c.content
        for c in result.chunks
    )
    assert "".join(c.content for c in result.chunks) == text
    assert result == index_source(text, "python")


@pytest.mark.parametrize(
    "text",
    ["", "# comment only\n", "def broken(:\n", "a = '\x00'", "π = 1\r\n# 👩🏾‍💻\r\n", "x\ry\r"],
)
def test_exact_source_coverage(text: str) -> None:
    result = index_source(text, "python")
    assert "".join(c.content for c in result.chunks) == text
    previous = 0
    for c in result.chunks:
        assert c.start_offset == previous
        assert text[c.start_offset : c.end_offset] == c.content
        assert c.start_line >= 1 and c.end_line >= c.start_line
        previous = c.end_offset
    assert previous == len(text)


def test_syntax_error_is_visible_and_falls_back() -> None:
    result = index_source("def invalid(:\n    pass\n", "python")
    assert result.diagnostic and result.diagnostic.startswith("syntax_error")
    assert result.symbols == [] and result.chunks[0].kind == "fallback"


@pytest.mark.parametrize(
    "text", ["a" * 200, "🧠" * 50, "ab\r\n" * 40, "def f():\n" + "    pass\n" * 30]
)
def test_large_regions_and_long_unicode_lines_are_bounded(text: str) -> None:
    result = chunk_source(text, [], "text", max_bytes=17)
    assert "".join(c.content for c in result) == text
    assert all(0 < len(c.content.encode("utf-8")) <= 17 for c in result)
    assert all(c.content == text[c.start_offset : c.end_offset] for c in result)


def test_oversized_file_rejected_and_other_languages_remain_text() -> None:
    with pytest.raises(ImportFailure, match="size limit"):
        index_source("x" * (256 * 1024 + 1), "python")
    result = index_source("export function main() {}", "javascript")
    assert not result.symbols and result.chunks[0].kind == "text"


def test_duplicate_names_keep_distinct_ordinals() -> None:
    result = index_source("def f(): pass\ndef f(): pass\n", "python")
    assert len(result.symbols) == 2
    assert [c.symbol_ordinal for c in result.chunks] == [0, 1]


def test_ast_budget_fallback_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.indexing.parser.MAX_NODES", 3)
    result = index_source("a = 1\nb = 2\n", "python")
    assert result.diagnostic and result.diagnostic.startswith("node_limit")
    assert result.symbols == []
