"""Non-overlapping, exact source slices with AST-aware boundaries."""

import re
from bisect import bisect_right
from dataclasses import dataclass
from hashlib import sha256

from app.indexing.parser import Symbol

CHUNKER_VERSION = "source-bytes-v2"
MAX_CHUNK_BYTES = 8192


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    symbol_ordinal: int | None
    kind: str
    content: str
    content_hash: str


def chunk_source(
    content: str, symbols: list[Symbol], mode: str, max_bytes: int = MAX_CHUNK_BYTES
) -> list[Chunk]:
    if max_bytes < 4:
        raise ValueError("Chunk limit must accommodate one UTF-8 character")
    # Character offsets (not AST's UTF-8 byte columns). CRLF is preserved exactly.
    starts = [0, *(m.end() for m in re.finditer(r"\r\n|\r|\n", content))]
    regions: list[tuple[int, int, int]] = []
    definition_parents = {
        s.parent
        for s in symbols
        if s.kind in {"class", "method", "function", "interface", "namespace"}
    }
    for i, s in enumerate(symbols):
        if s.kind not in {"function", "method", "class", "interface", "type", "enum", "namespace"}:
            continue
        ancestors: list[Symbol] = []
        parent = s.parent
        while parent is not None:
            ancestors.append(symbols[parent])
            parent = symbols[parent].parent
        if any(a.kind in {"function", "method"} for a in ancestors):
            continue
        # Classes with nested definitions are represented by header/gap + child chunks.
        if s.kind in {"class", "interface", "namespace"} and i in definition_parents:
            continue
        regions.append(
            (
                starts[s.start_line - 1],
                starts[s.end_line] if s.end_line < len(starts) else len(content),
                i,
            )
        )
    spans: list[tuple[int, int, int | None]] = []
    cursor = 0
    for begin, end, region_symbol in sorted(regions):
        if begin < cursor:
            continue
        if begin > cursor:
            spans.append((cursor, begin, None))
        spans.append((begin, end, region_symbol))
        cursor = end
    if cursor < len(content):
        spans.append((cursor, len(content), None))
    chunks: list[Chunk] = []
    for begin, end, symbol in spans:
        while begin < end:
            stop, size, last_break = begin, 0, begin
            while stop < end:
                width = len(content[stop].encode("utf-8"))
                if size + width > max_bytes:
                    break
                size += width
                stop += 1
                if content[stop - 1] == "\n" or (
                    content[stop - 1] == "\r" and (stop == end or content[stop] != "\n")
                ):
                    last_break = stop
            if stop < end and last_break > begin:
                stop = last_break
            value = content[begin:stop]
            chunks.append(
                Chunk(
                    len(chunks),
                    begin,
                    stop,
                    bisect_right(starts, begin),
                    bisect_right(starts, stop - 1),
                    symbol,
                    "symbol" if symbol is not None else mode,
                    value,
                    sha256(value.encode("utf-8")).hexdigest(),
                )
            )
            begin = stop
    return chunks
