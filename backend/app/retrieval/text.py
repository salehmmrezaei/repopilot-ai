import re

VERSION = "hybrid-v2"
STOP = frozenset(
    (
        "a an the is are was where how what does do in of to for and or with "
        "implemented implementation"
    ).split()
)


def words(text: str) -> list[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    original = re.findall(r"[^\W_]+(?:_[^\W_]+)*", text.lower(), re.UNICODE)
    split = re.findall(r"[^\W_]+", expanded.lower(), re.UNICODE)
    return list(dict.fromkeys([*original, *split]))


def query_terms(query: str) -> list[str]:
    return [word for word in words(query) if word not in STOP][:12]


def lexical_text(text: str) -> str:
    # Retain repeated terms for PostgreSQL ranking, plus split identifier spelling.
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).replace("_", " ")
    return text + "\n" + expanded


def embedding_text(path: str, name: str | None, signature: str | None, content: str) -> str:
    # All values are untrusted source data, not instructions to an LLM.
    return (
        f"File: {path}\nSymbol: {name or '(module)'}\n"
        f"Signature: {signature or ''}\nCode:\n{content}"
    )


def symbol_terms(query: str) -> list[str]:
    """Exact identifier spellings for a bound SQL array, never a tsquery expression."""
    names = []
    for token in re.findall(r"#?[$\w]+(?:\.#?[$\w]+)*", query, re.UNICODE):
        if all(
            part.removeprefix("#").replace("$", "_").isidentifier() for part in token.split(".")
        ):
            if token.lower() not in STOP:
                names.append(token.lower())
    return list(dict.fromkeys([*names, *query_terms(query)]))[:24]
