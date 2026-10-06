import gzip
import hashlib
import io
import tarfile
import time
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.indexing.errors import ImportFailure


@dataclass(frozen=True)
class ArchiveLimits:
    expanded_bytes: int = 40 * 1024 * 1024
    stored_bytes: int = 10 * 1024 * 1024
    file_bytes: int = 256 * 1024
    members: int = 5000
    seconds: float = 15


@dataclass(frozen=True)
class ImportedFile:
    path: str
    language: str
    content: str
    size: int
    content_hash: str


@dataclass(frozen=True)
class ArchiveResult:
    files: list[ImportedFile]
    scanned: int
    skipped: int


class BoundedReader(io.BufferedIOBase):
    def __init__(self, stream: gzip.GzipFile, limit: int, deadline: float) -> None:
        self.stream, self.remaining, self.deadline = stream, limit, deadline

    def read(self, size: int | None = -1) -> bytes:
        if time.monotonic() > self.deadline:
            raise ImportFailure("archive_timeout", "Archive processing exceeded its time limit.")
        data = self.stream.read(
            min(size if size is not None and size >= 0 else self.remaining + 1, self.remaining + 1)
        )
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ImportFailure(
                "archive_too_large", "Expanded archive exceeds the configured size limit."
            )
        return data


IGNORED = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "dist",
    "build",
    "coverage",
    ".next",
    "__pycache__",
    "vendor",
    ".ssh",
    ".aws",
}
SECRET_NAMES = {".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519", "credentials"}
LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".md": "markdown",
    ".sql": "sql",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".txt": "text",
    ".ini": "text",
    ".cfg": "text",
}


_DEFAULT_LIMITS = ArchiveLimits()


def read_archive(blob: bytes, limits: ArchiveLimits = _DEFAULT_LIMITS) -> ArchiveResult:
    files: list[ImportedFile] = []
    scanned = skipped = stored = members = 0
    root: str | None = None
    seen: set[str] = set()
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
            bounded = BoundedReader(gz, limits.expanded_bytes, time.monotonic() + limits.seconds)
            with tarfile.open(fileobj=bounded, mode="r|") as archive:
                for member in archive:
                    members += 1
                    if members > limits.members:
                        raise ImportFailure(
                            "too_many_files", "Archive exceeds the configured entry count limit."
                        )
                    name = member.name.rstrip("/")
                    parts = name.split("/")
                    if (
                        not name
                        or len(name) > 600
                        or name.startswith("/")
                        or "\\" in name
                        or any(part in {"", ".", ".."} for part in parts)
                        or any(ord(char) < 32 or ord(char) == 127 for char in name)
                        or ":" in name
                    ):
                        raise ImportFailure("unsafe_path", "Archive contains an unsafe path.")
                    if root is None:
                        root = parts[0]
                    if parts[0] != root:
                        raise ImportFailure(
                            "unsafe_archive", "Archive has inconsistent root directories."
                        )
                    if member.isdir():
                        continue
                    if len(parts) < 2:
                        raise ImportFailure(
                            "unsafe_path", "Archive file has no repository-relative path."
                        )
                    path = "/".join(parts[1:])
                    if len(path) > 512 or path in seen:
                        raise ImportFailure(
                            "unsafe_path", "Archive contains a duplicate or overlong path."
                        )
                    seen.add(path)
                    scanned += 1
                    suffix = PurePosixPath(path).suffix.lower()
                    base = parts[-1].lower()
                    excluded = any(
                        part in IGNORED or part.lower().startswith(".env") for part in parts[1:]
                    )
                    allowed = suffix in LANGUAGES or base in {
                        "readme",
                        "license",
                        "dockerfile",
                        "makefile",
                    }
                    if (
                        not member.isfile()
                        or member.issparse()
                        or excluded
                        or base in SECRET_NAMES
                        or not allowed
                        or member.size > limits.file_bytes
                        or member.size < 0
                    ):
                        skipped += 1
                        continue
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ImportFailure("invalid_archive", "Archive file could not be read.")
                    content = stream.read(limits.file_bytes + 1)
                    if len(content) > limits.file_bytes or b"\x00" in content:
                        skipped += 1
                        continue
                    try:
                        decoded = content.decode("utf-8")
                    except UnicodeDecodeError:
                        skipped += 1
                        continue
                    if decoded.startswith("version https://git-lfs.github.com/spec/v1"):
                        skipped += 1
                        continue
                    stored += len(content)
                    if stored > limits.stored_bytes:
                        raise ImportFailure(
                            "content_too_large", "Accepted source files exceed the storage limit."
                        )
                    files.append(
                        ImportedFile(
                            path,
                            LANGUAGES.get(suffix, "text"),
                            decoded,
                            len(content),
                            hashlib.sha256(content).hexdigest(),
                        )
                    )
                # Account for gzip data after tar end markers as well.
                while bounded.read(64 * 1024):
                    pass
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise ImportFailure("invalid_archive", "GitHub archive is invalid or truncated.") from exc
    if not files:
        raise ImportFailure("no_supported_files", "No supported UTF-8 text files were found.")
    return ArchiveResult(files, scanned, skipped)
