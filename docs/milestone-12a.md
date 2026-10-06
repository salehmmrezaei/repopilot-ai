# Milestone 12A — TypeScript and TSX source understanding

Implemented as the first Milestone 12 extension selected after Milestone 11.
Milestone 12 is a set of independent extensions, not a single completed release.
Incremental indexing is next (12B); OAuth/private repositories, reranking, PR review,
deployment and deeper observability remain separate work.

## What this release adds

- Static TypeScript and TSX syntax parsing alongside the existing Python AST parser.
- Symbols for named functions, directly assigned arrow/function expressions, classes,
  constructors/methods/accessors, class fields, interfaces, interface members, type
  aliases, enums, namespaces and imports with local aliases. Explicit anonymous default
  function/class exports use `default` as their stable display name.
- Exact source ranges, declaration signatures and adjacent JSDoc metadata. TSX function
  components remain ordinary function symbols; no React runtime or framework inference.
- Symbol-aware chunks for functions, methods, interfaces, type aliases, enums and
  namespaces. Original UTF-8 source, CRLF/CR/LF line endings and character offsets remain
  intact; byte offsets from the parser are not exposed as character offsets.
- `.ts`, `.tsx`, `.d.ts`, `.mts` and `.cts` source import support. TSX grammar is chosen
  only for `.tsx` filenames; other TypeScript extensions use the TypeScript grammar.
- Exact symbol search terms preserving qualified names, `$` identifiers and Unicode.
  Agent `find_symbol` also returns declaration-containing chunks for symbols without a
  dedicated chunk, such as imports and properties. `find_references` remains lexical
  substring candidate lookup, not compiler-resolved references.
- Source browser language indicators, escaped declaration/JSDoc details, neutral empty
  state text and an explicit **Rebuild index** prompt for an older parser generation.
- A ten-case TypeScript/TSX retrieval dataset, free structural validation and a real
  PostgreSQL benchmark command. CI includes the dataset check and PostgreSQL retrieval test.

## Upgrade from Milestone 11

Back up the database and working tree; let active jobs/repairs finish or cancel them.
Use the full archive in a new folder, preserving your `.env`, or apply the exact patch
from your Milestone 11 project root:

```bash
git apply --check UPGRADE_FROM_MILESTONE_11.patch
git apply UPGRADE_FROM_MILESTONE_11.patch
```

The patch targets the previously delivered Milestone 11 archive. Review conflicts if
you changed those files locally; do not overwrite your work or remove your database volume.
`docs/milestone-12a-files.txt` lists changed/added paths.

Two dependencies are added and locked: `tree-sitter==0.25.2` and
`tree-sitter-typescript==0.23.2`. Existing dependency versions are unchanged. Rebuild
backend, worker and dispatcher together so they all use the same parser and tool versions:

```bash
docker compose build
docker compose run --rm migrate
docker compose up -d
```

No database migration or environment setting is added. Migration head remains
`0015_repair_runs`. For host development:

```bash
cd backend
uv sync --frozen
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
```

Use **Rebuild index** for existing imported repositories, then **Prepare keyword search**
(or your existing hybrid preparation action) to create search documents for that index.
Hybrid rebuilding can incur embedding charges; keyword preparation does not call a model.
The parser upgrade does not automatically re-import, re-index or re-embed repositories.
Existing `.ts`/`.tsx` files can be re-indexed from their saved source snapshot. `.mts` and
`.cts` were excluded by the earlier importer, so those files require a new import;
refreshing an existing imported repository is not implemented in this extension.

The source pipeline is now:
`python312-v1:ts-0.23.2-bindings-0.25.2-v1:source-bytes-v2`.
Search pipeline is `hybrid-v2`; rebuild search even when retaining an older source index.
Previous completed source generations stay available until a replacement succeeds;
saved answers and proposals keep their original evidence. Agent tool configuration is
versioned to `read-only-v2`. Queued agents/repairs with the old configuration stop rather
than silently switch tools. Create a new proposal against the rebuilt source when needed.

## Parsing boundary and limitations

This is syntax extraction, not the TypeScript compiler or a language server. It does not
resolve module imports, `tsconfig` paths, inherited members, overload selection, generic
types or runtime behavior. It does not load `tsconfig`, install npm packages, run build
scripts, evaluate decorators or execute repository code. JavaScript/JSX and other imported
languages continue to use text chunks in this release.

The pinned grammar determines syntax coverage. Any syntax error or unsupported syntax
causes the entire file to use text chunks with a visible diagnostic; partial recovered
symbols are not presented as reliable declarations. Type aliases and enums are indexed
by declaration name; enum member values and inferred types are not evaluated. Computed
member names, arbitrary anonymous callbacks, HOC-wrapped components such as `memo(...)`,
object-literal methods and inferred class-expression bindings are not assigned invented
semantic names. Escaped identifier spellings are retained as source spelling. Simple
same-line declarations can share a chunk because chunk boundaries remain line-oriented.

Limits: 256 KiB/file, a 250 ms native parse timeout, 40,000 tree nodes, depth 200,
10,000 symbols/file (also 10,000 symbols/chunks across the repository), and a 500 ms
traversal/extraction deadline. Timeout/budget failures produce exact text chunks and a
visible diagnostic. Existing worker timeouts, leases and container limits still apply.
The C parser is trusted application dependency code; these bounds do not replace worker
process isolation or dependency maintenance.

The pinned binding's byte-input/native-timeout API is used. Its newer Python progress
callback crashed in this execution environment during development, so it is not used.
The native timeout API is deprecated upstream; its narrow deprecation warning is suppressed
at construction only. Upgrading the binding/grammar requires regression and stress tests,
a new pipeline version, and a source/search rebuild. Do not remove the timeout to silence
an upgrade warning. Relevant primary references:

- https://github.com/tree-sitter/tree-sitter-typescript
- https://github.com/tree-sitter/py-tree-sitter/releases
- https://pypi.org/project/tree-sitter-typescript/0.23.2/

## Evaluation

From `backend`, validate parsing and labels without a database, model key or execution:

```bash
uv run python -m app.evaluation.retrieval --check
uv run python -m app.evaluation.retrieval --check --dataset evaluation/retrieval/typescript-v1
```

The original Python dataset remains 14 cases. The TypeScript fixture contains four files,
21 symbols, 19 chunks and ten labeled queries covering methods, `$` identifiers, TSX
components, interfaces, aliases, enums, namespaces and behavior questions. These are
synthetic development fixtures, not a held-out quality claim.

On a migrated dedicated PostgreSQL database whose name ends in `_test`:

```bash
uv run python -m app.evaluation.retrieval --dataset evaluation/retrieval/typescript-v1 --output evaluation/retrieval/reports/typescript-keyword.json
RUN_DB_TESTS=1 uv run pytest tests/integration/test_typescript_retrieval.py -q
```

The benchmark uses actual index/search preparation workers, PostgreSQL lexical/symbol
candidates, ranking and context assembly. It records source/search pipeline versions,
dataset/corpus hashes and per-case Recall@K/MRR, latency and provider usage. Only a real
benchmark run establishes those scores; `--check` validates labels and source coverage.
The existing optional hybrid mode still requires configured embeddings and `--allow-paid`.
Do not compare new parser/search results to older reports without labeling version changes.

## Acceptance

Verify a mixed Python/TypeScript repository through import → index → source browser →
keyword preparation → search. Inspect a `.ts` generic arrow function and a `.tsx` component;
check exact source lines, signatures, JSDoc and escaped source text. Search `Card`, a
qualified namespace function, an interface and a `$` identifier. Verify malformed syntax
shows a diagnostic while its text stays searchable. Confirm another user cannot read the
repository/index or agent source. Rebuild an older index and verify historical results
remain pinned; try a failed rebuild and confirm the old completed generation survives.

Run standard frontend checks/tests/build, the migrated PostgreSQL/Redis CI suite and
browser acceptance before deployment. Real database races, rootless sandbox execution
and paid model evaluation remain separate gates from static TypeScript support.
The existing JavaScript sandbox profile is unchanged: TypeScript project tests require
an operator-curated offline image with the project's trusted test dependencies already
available. Parsing support does not promise arbitrary TypeScript test execution.
See `validation.md` for actual build-environment results and remaining gates.
