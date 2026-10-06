from pathlib import Path

import pytest

from app.indexing.pipeline import index_source
from app.retrieval.text import query_terms, symbol_terms
from app.schemas.search import SearchRequest


def test_typescript_symbols_scopes_signatures_and_docs():
    text = Path("tests/fixtures/indexing/sample.ts").read_text()
    result = index_source(text, "typescript", "sample.ts")
    assert result.diagnostic is None
    assert [(s.qualified_name, s.kind) for s in result.symbols] == [
        ("Account", "import"),
        ("helpers", "import"),
        ("Options", "interface"),
        ("Options.limit", "property"),
        ("Options.onResult", "method"),
        ("Identifier", "type"),
        ("Status", "enum"),
        ("API", "namespace"),
        ("API.fetchUser", "function"),
        ("Catalog", "class"),
        ("Catalog.#value", "property"),
        ("Catalog.constructor", "method"),
        ("Catalog.current", "method"),
        ("Catalog.current", "method"),
        ("Catalog.find", "method"),
        ("Catalog.find.normalize", "function"),
        ("Catalog.save", "method"),
        ("$lookup", "function"),
        ("renamed", "variable"),
        ("other", "variable"),
        ("rest", "variable"),
    ]
    assert result.symbols[2].docstring == "/** Request options for the catalog. */"
    assert result.symbols[9].start_line == 20
    assert "@sealed" in result.symbols[9].signature
    assert result.symbols[15].parent == 14
    assert all(s.start_line <= s.end_line <= len(text.splitlines()) for s in result.symbols)
    assert "".join(c.content for c in result.chunks) == text
    assert result == index_source(text, "typescript", "sample.ts")
    for name in ("Identifier", "Status", "API.fetchUser", "Catalog.find", "$lookup"):
        assert any(
            c.symbol_ordinal is not None and result.symbols[c.symbol_ordinal].qualified_name == name
            for c in result.chunks
        ), name


def test_tsx_components_and_default_export():
    text = Path("tests/fixtures/indexing/sample.tsx").read_text()
    result = index_source(text, "typescript", "Card.tsx")
    assert result.diagnostic is None
    assert {"Card", "Container", "default", "CardProps"}.issubset({s.name for s in result.symbols})
    assert "".join(c.content for c in result.chunks) == text
    assert any(c.kind == "symbol" and "<article>" in c.content for c in result.chunks)
    assert index_source(text, "typescript", "Card.ts").diagnostic.startswith("syntax_error")
    generic = "export const identity = <T>(value: T): T => value;"
    assert index_source(generic, "typescript", "identity.ts").diagnostic is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "// comments only\n",
        'export const π = "👩🏾‍💻";\r\n',
        "export function f() {}\rexport function g() {}\r",
        "const bad: = ;",
        "\x00",
        "interface Missing { field: string",
    ],
)
def test_exact_unicode_and_line_coverage(text):
    result = index_source(text, "typescript", "test.ts")
    assert "".join(c.content for c in result.chunks) == text
    for chunk in result.chunks:
        assert text[chunk.start_offset : chunk.end_offset] == chunk.content
        assert len(chunk.content.encode()) <= 8192
    if result.diagnostic:
        assert not result.symbols
        assert all(c.kind == "fallback" for c in result.chunks)
    else:
        assert all(s.end_line <= max(1, len(text.splitlines())) for s in result.symbols)


def test_declarations_overloads_ambient_import_aliases_and_anonymous_scopes():
    text = """import Main, { type Foo, thing as local } from 'lib';
import Required = require('module');
export declare function overload(x: string): string;
export declare function overload(x: number): number;
export default class { method() {} }
declare module 'lib' { export interface Type { id: string } }
const named = function inner() { function child() {} };
const object = { hidden() {} };
list.map(() => { function callbackLocal() {} });
"""
    result = index_source(text, "typescript")
    assert result.diagnostic is None
    names = [s.qualified_name for s in result.symbols]
    assert names[:4] == ["Main", "Foo", "local", "Required"]
    assert names.count("overload") == 2
    assert "default.method" in names and "named.child" in names
    assert "inner" not in names and "callbackLocal" not in names and "hidden" not in names


@pytest.mark.parametrize(
    "limit,value", [("MAX_NODES", 2), ("MAX_DEPTH", 1), ("MAX_SYMBOLS", 1), ("WALK_SECONDS", 0)]
)
def test_budgets_fail_closed_to_text(monkeypatch, limit, value):
    monkeypatch.setattr("app.indexing.typescript." + limit, value)
    result = index_source("export class Thing { method() { return 1; } }", "typescript")
    assert result.diagnostic and not result.symbols
    assert all(c.kind == "fallback" for c in result.chunks)


def test_native_timeout_fallback(monkeypatch):
    monkeypatch.setattr("app.indexing.typescript.parse_tree", lambda *_: None)
    result = index_source("export const value = 1;", "typescript")
    assert result.diagnostic.startswith("parser_timeout") and not result.symbols


def test_identifier_terms_preserve_typescript_spelling_without_tsquery_injection():
    assert "$lookup" in symbol_terms("$lookup")
    assert "catalog.#value" in symbol_terms("Catalog.#value")
    assert "api.fetchuser" in symbol_terms("API.fetchUser")
    assert SearchRequest(query="$").query == "$"
    assert all(
        "$" not in term and ":" not in term and "|" not in term
        for term in query_terms("$lookup | ':'")
    )
    assert "π" in symbol_terms("π")


def test_typescript_retrieval_labels_resolve_without_provider_calls():
    from app.evaluation.retrieval import DATASET, check_dataset

    assert check_dataset(DATASET)["valid_cases"] == 14
    report = check_dataset(DATASET.parent / "typescript-v1")
    assert report["valid_cases"] == 10 and report["provider_calls"] == 0


def test_typescript_import_extensions():
    import tarfile

    from test_archive import archive

    from app.indexing.archive import read_archive

    result = read_archive(
        archive(
            [
                ("root/" + name, b"export const value = 1;", tarfile.REGTYPE)
                for name in ["a.ts", "b.tsx", "c.mts", "d.cts", "e.d.ts", "node_modules/skip.ts"]
            ]
        )
    )
    assert len(result.files) == 5
    assert all(f.language == "typescript" for f in result.files)


def test_inline_types_and_computed_methods_do_not_invent_outer_members():
    result = index_source(
        """export class Example {
  [factory()]() { function hidden() {} }
  config: { nested: string };
  run(value: { inner: string }) { return value; }
}
export interface Options { shape: { detail: string }; }
""",
        "typescript",
    )
    assert result.diagnostic is None
    names = {s.qualified_name for s in result.symbols}
    assert names == {"Example", "Example.config", "Example.run", "Options", "Options.shape"}
