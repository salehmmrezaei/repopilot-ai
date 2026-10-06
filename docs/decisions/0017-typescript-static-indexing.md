# ADR 0017: bounded TypeScript/TSX syntax extraction

Status: accepted for Milestone 12A.

Keep indexing inside the existing Python worker and parse source with pinned Tree-sitter
TypeScript and TSX grammars. Do not start Node, invoke a repository compiler or load
project configuration. Filenames select the TSX grammar; `.ts` generics must not be
misinterpreted as JSX. Python continues to use its existing AST parser.

Extract declarations with lexical parent ordinals, exact original source ranges and
bounded signature/JSDoc metadata. Unsupported syntax or resource exhaustion falls back
to complete text chunks with a diagnostic. No compiler-level references or inferred
component/type information are claimed. Computed/anonymous constructs without a stable
supported name are omitted rather than attached to a guessed scope.

Version both source and search pipelines. Rebuild explicitly; preserve previous completed
generations and saved results. New exact symbol terms travel as bound SQL array values,
separate from normalized full-text query terms. The agent's symbol tool covers declaration
starts as well as dedicated symbol chunks. This changes its configuration fingerprint.

The pinned binding's progress-callback path crashed during development. Use its working
native timeout with byte input, retain file/tree/traversal limits, and document the API's
deprecation. A future binding upgrade must prove budget/fallback behavior before changing
the parser version. Existing worker/container isolation remains required for native code.

A separate TypeScript retrieval fixture validates extraction and label provenance offline;
quality scores still require the real PostgreSQL benchmark. Incremental reuse, private
repository access and TypeScript compiler/type-check execution are outside this extension.
