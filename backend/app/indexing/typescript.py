"""Bounded syntax-only TypeScript/TSX extraction. No compiler, project config or execution."""

import re
import warnings
from bisect import bisect_right
from functools import lru_cache
from time import monotonic

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from app.indexing.parser import Parsed, Symbol

PARSER_VERSION = "ts-0.23.2-bindings-0.25.2-v1"
MAX_NODES = 40000
MAX_DEPTH = 200
MAX_SYMBOLS = 10000
PARSE_MICROS = 250000
WALK_SECONDS = 0.5
DECLARATIONS = {
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class": "class",
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "function_signature": "function",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "internal_module": "namespace",
    "module": "namespace",
    "method_definition": "method",
    "method_signature": "method",
    "abstract_method_signature": "method",
    "public_field_definition": "property",
    "property_signature": "property",
}
FUNCTIONS = {"arrow_function", "function_expression", "generator_function"}
NAME_TYPES = {
    "identifier",
    "type_identifier",
    "property_identifier",
    "private_property_identifier",
    "shorthand_property_identifier_pattern",
    "string",
    "number",
    "nested_identifier",
}
SCOPES = {"class", "function", "method", "interface", "type", "enum", "namespace"}


@lru_cache(maxsize=2)
def language(tsx: bool) -> Language:
    return Language(
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )


def parse_tree(source: bytes, tsx: bool) -> Node | None:
    # Pinned 0.25.2's native timeout works with direct byte input. Avoid its Python
    # progress callback, which crashes in this runtime. Revisit on a tested binding upgrade.
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", message="Use the progress_callback in parse", category=DeprecationWarning
        )
        parser = Parser(language(tsx), timeout_micros=PARSE_MICROS)
    try:
        tree = parser.parse(source)
    except ValueError:
        return None  # The binding reports an expired native timeout as ValueError.
    return tree.root_node if tree else None


def bindings(node: Node) -> list[Node]:
    """Binding names only, excluding object keys, defaults and type annotations."""
    result = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in {"identifier", "shorthand_property_identifier_pattern"}:
            result.append(current)
        elif current.type in {"pair_pattern", "assignment_pattern", "object_assignment_pattern"}:
            target = current.child_by_field_name(
                "value" if current.type == "pair_pattern" else "left"
            )
            if target:
                stack.append(target)
        elif current.type in {"object_pattern", "array_pattern", "rest_pattern"}:
            stack.extend(reversed(current.named_children))
    return result


def callable_value(node: Node | None) -> Node | None:
    for _ in range(20):
        if node is None or node.type in FUNCTIONS:
            return node
        if node.type not in {
            "as_expression",
            "satisfies_expression",
            "parenthesized_expression",
            "non_null_expression",
            "type_assertion",
        }:
            return None
        children = node.named_children
        node = (
            children[-1]
            if node.type == "type_assertion" and children
            else children[0]
            if children
            else None
        )
    return None


def parse_typescript(content: str, *, tsx: bool = False) -> Parsed:
    source = content.encode("utf-8")
    if len(source) > 256 * 1024:
        return Parsed([], "file_limit: TypeScript exceeds 256 KiB; using text chunks")
    tree = parse_tree(source, tsx)
    if tree is None:
        return Parsed([], "parser_timeout: TypeScript parse exceeded its budget; using text chunks")
    if tree.has_error:
        return Parsed(
            [],
            "syntax_error: cannot parse with the pinned TypeScript/TSX grammar; using text chunks",
        )
    # Native node points count LF only; use the same CRLF/CR/LF convention as chunks.
    starts = [0, *(m.end() for m in re.finditer(rb"\r\n|\r|\n", source))]
    symbols: list[Symbol] = []
    deadline = monotonic() + WALK_SECONDS
    # Validate the complete tree budget before helpers inspect subtrees.
    cursor = tree.walk()
    visited = depth = 0
    while True:
        visited += 1
        if visited > MAX_NODES or depth > MAX_DEPTH or monotonic() > deadline:
            return Parsed(
                [],
                "parser_budget: TypeScript tree exceeds node/depth/time budget; using text chunks",
            )
        if cursor.goto_first_child():
            depth += 1
            continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                break
            depth -= 1
        else:
            continue
        break

    def text(node: Node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8")

    def add(
        node: Node, name: str, kind: str, parent: int | None, function: Node | None = None
    ) -> int:
        wrapper = node
        while wrapper.parent and wrapper.parent.type in {
            "export_statement",
            "ambient_declaration",
            "lexical_declaration",
            "variable_declaration",
        }:
            wrapper = wrapper.parent
        start = wrapper.start_byte
        # Do not give every variable in a multi-declarator statement the whole statement.
        if node.type == "variable_declarator" and node.parent and node.parent.named_child_count > 1:
            start = node.start_byte
        end = node.end_byte
        prefix = symbols[parent].qualified_name + "." if parent is not None else ""
        if len(name) > 512 or len(prefix + name) > 2048 or len(symbols) >= MAX_SYMBOLS:
            raise OverflowError
        body = (function or node).child_by_field_name("body")
        signature_end = body.start_byte if body else node.end_byte
        if node.type == "type_alias_declaration":
            signature_end = node.end_byte
        signature = source[start:signature_end].decode("utf-8").strip()
        if len(signature.encode()) > 8192:
            signature = ""
        previous = wrapper.prev_named_sibling
        docstring = None
        if previous and previous.type == "comment":
            candidate = text(previous)
            if (
                candidate.startswith("/**")
                and len(candidate.encode()) <= 8192
                and not source[previous.end_byte : wrapper.start_byte].strip()
            ):
                docstring = candidate
        symbols.append(
            Symbol(
                name,
                prefix + name,
                kind,
                parent,
                bisect_right(starts, start),
                bisect_right(starts, max(start, end - 1)),
                signature or None,
                docstring,
            )
        )
        return len(symbols) - 1

    stack: list[tuple[Node, int | None]] = [(tree, None)]
    try:
        while stack:
            if monotonic() > deadline:
                return Parsed(
                    [],
                    "parser_budget: TypeScript extraction exceeded time budget; using text chunks",
                )
            node, parent = stack.pop()
            scope = parent
            if node.type == "import_statement":
                pending = list(reversed(node.named_children))
                while pending:
                    part = pending.pop()
                    if part.type == "import_specifier":
                        name = part.child_by_field_name("alias") or part.child_by_field_name("name")
                        if name:
                            add(node, text(name), "import", parent)
                    elif part.type == "identifier":
                        add(node, text(part), "import", parent)
                    elif part.type in {
                        "import_clause",
                        "named_imports",
                        "namespace_import",
                        "import_require_clause",
                    }:
                        pending.extend(reversed(part.named_children))
                continue
            if node.type == "variable_declarator":
                name_node = node.child_by_field_name("name")
                value = callable_value(node.child_by_field_name("value"))
                if name_node:
                    for name in bindings(name_node):
                        if (
                            value
                            or parent is None
                            or symbols[parent].kind in {"namespace", "class"}
                        ):
                            scope = add(
                                node, text(name), "function" if value else "variable", parent, value
                            )
                if value:
                    stack.extend((child, scope) for child in reversed(value.named_children))
                # Non-callable initializers may contain callbacks/objects: don't invent scopes.
                continue
            if (
                node.type == "property_signature"
                and node.parent
                and node.parent.type == "object_type"
                and (
                    node.parent.parent is None
                    or node.parent.parent.type != "type_alias_declaration"
                )
            ):
                # Inline object types do not declare members on the enclosing class/function.
                continue
            if node.type in {"property_signature", "method_signature"} and (
                parent is None or symbols[parent].kind not in {"interface", "type"}
            ):
                continue
            if node.type in {
                "method_definition",
                "abstract_method_signature",
                "public_field_definition",
            } and (node.parent is None or node.parent.type != "class_body"):
                continue
            if node.type in DECLARATIONS:
                name_node = node.child_by_field_name("name")
                symbol_name = (
                    text(name_node) if name_node and name_node.type in NAME_TYPES else None
                )
                if (
                    symbol_name is None
                    and node.parent
                    and node.parent.type == "export_statement"
                    and b"default" in source[node.parent.start_byte : node.start_byte].split()
                ):
                    symbol_name = "default"
                if symbol_name:
                    kind = DECLARATIONS[node.type]
                    value = callable_value(node.child_by_field_name("value"))
                    if kind == "property" and value:
                        kind = "method"
                    ordinal = add(node, symbol_name, kind, parent, value)
                    if kind in SCOPES:
                        scope = ordinal
                    if value:
                        stack.extend((child, scope) for child in reversed(value.named_children))
                        continue
                else:
                    # Unsupported computed names must not leak children into an outer scope.
                    continue
            elif node.type in FUNCTIONS:
                # Only explicit default exports get a synthetic stable name.
                if node.parent and node.parent.type == "export_statement":
                    scope = add(node, "default", "function", parent, node)
                else:
                    continue
            stack.extend((child, scope) for child in reversed(node.named_children))
    except OverflowError:
        return Parsed(
            [], "symbol_limit: TypeScript symbol name/count exceeds limit; using text chunks"
        )
    return Parsed(symbols)
