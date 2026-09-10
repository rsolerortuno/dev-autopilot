"""Bounded lexical retrieval over a clean, tracked repository snapshot.

Retrieved text is untrusted data, never an instruction or tool authorization.
Indexes are invalidated by any tracked or untracked worktree change.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

_EXTENSIONS = {".py", ".md", ".rst", ".toml", ".yaml", ".yml", ".json", ".txt"}
_SECRET_NAME = re.compile(r"(^|[._-])(secret|credentials?|token|password|private[-_]?key)([._-]|$)", re.I)
_SECRET_TEXT = re.compile(r"-----BEGIN .*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})")


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise ValueError("repository inspection failed")
    return result.stdout


def _snapshot(root: Path) -> str:
    if _git(root, "status", "--porcelain", "--untracked-files=all").strip():
        raise ValueError("retrieval requires a clean repository; commit changes before indexing or querying")
    return _git(root, "rev-parse", "HEAD").decode().strip()


def _terms(text: str) -> set[str]:
    return set(_tokens(text))


def _tokens(text: str) -> list[str]:
    separated = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    return re.findall(r"[a-z][a-z0-9]{1,}|[0-9]+", separated.lower())


def _batch_sizes(root: Path, object_ids: list[str]) -> dict[str, int]:
    if not object_ids:
        return {}
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch-check"],
        input=("".join(f"{object_id}\n" for object_id in object_ids)).encode("ascii"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("repository inspection failed")
    sizes: dict[str, int] = {}
    for object_id, line in zip(object_ids, result.stdout.splitlines(), strict=True):
        header = line.decode("ascii", errors="strict").split()
        if len(header) != 3 or header[0] != object_id or header[1] != "blob":
            raise ValueError("repository inspection failed")
        sizes[object_id] = int(header[2])
    return sizes


def _batch_blobs(root: Path, object_ids: list[str], sizes: dict[str, int]) -> dict[str, bytes]:
    if not object_ids:
        return {}
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input=("".join(f"{object_id}\n" for object_id in object_ids)).encode("ascii"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("repository inspection failed")
    blobs: dict[str, bytes] = {}
    offset = 0
    for object_id in object_ids:
        end = result.stdout.find(b"\n", offset)
        if end < 0:
            raise ValueError("repository inspection failed")
        header = result.stdout[offset:end].decode("ascii", errors="strict").split()
        if len(header) != 3 or header[0] != object_id or header[1] != "blob":
            raise ValueError("repository inspection failed")
        size = int(header[2])
        if size != sizes[object_id]:
            raise ValueError("repository changed during indexing")
        start = end + 1
        stop = start + size
        if stop >= len(result.stdout) or result.stdout[stop : stop + 1] != b"\n":
            raise ValueError("repository inspection failed")
        blobs[object_id] = result.stdout[start:stop]
        offset = stop + 1
    return blobs


@dataclass(frozen=True)
class Passage:
    path: str
    start_line: int
    end_line: int
    content: str
    sha256: str


@dataclass(frozen=True)
class RetrievalIndex:
    commit: str
    passages: tuple[Passage, ...]

    @classmethod
    def build(cls, repository: Path, *, max_file_bytes: int = 256_000, max_total_bytes: int = 8_000_000) -> RetrievalIndex:
        if max_file_bytes <= 0 or max_total_bytes <= 0:
            raise ValueError("retrieval limits must be positive")
        if repository.is_symlink():
            raise ValueError("repository root must not be a symlink")
        root = repository.resolve(strict=True)
        commit = _snapshot(root)
        passages: list[Passage] = []
        total = 0
        entries: list[tuple[str, str, bytes]] = []
        for raw in sorted(_git(root, "ls-tree", "-r", "-z", commit).split(b"\0")):
            if not raw:
                continue
            metadata, encoded_path = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.split()
            if mode not in {b"100644", b"100755"} or kind != b"blob":
                continue
            relative = encoded_path.decode("utf-8", errors="strict")
            path = root / relative
            if path.suffix.lower() not in _EXTENSIONS:
                continue
            if any(part.startswith(".") or _SECRET_NAME.search(part) for part in Path(relative).parts):
                continue
            blob = object_id.decode("ascii")
            entries.append((blob, relative, encoded_path))
        object_ids = list(dict.fromkeys(blob for blob, _, _ in entries))
        sizes = _batch_sizes(root, object_ids)
        selected: list[str] = []
        selected_set: set[str] = set()
        for blob, _, _ in entries:
            size = sizes[blob]
            if size > max_file_bytes:
                continue
            if total + size > max_total_bytes:
                raise ValueError("repository exceeds retrieval byte budget")
            total += size
            if blob not in selected_set:
                selected.append(blob)
                selected_set.add(blob)
        blobs = _batch_blobs(root, selected, sizes) if selected else {}
        for blob, relative, _ in entries:
            if blob not in blobs:
                continue
            data = blobs[blob]
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if _SECRET_TEXT.search(content) or "\0" in content:
                continue
            lines = content.splitlines()
            for offset in range(0, len(lines), 40):
                chunk = "\n".join(lines[offset : offset + 40])
                passages.append(
                    Passage(relative, offset + 1, min(offset + 40, len(lines)), chunk, hashlib.sha256(chunk.encode()).hexdigest())
                )
        if _snapshot(root) != commit:
            raise ValueError("repository changed during indexing")
        return cls(commit, tuple(passages))

    def search(self, repository: Path, query: str, *, top_k: int = 5, max_context_bytes: int = 16_000) -> dict[str, object]:
        if not 1 <= top_k <= 20 or max_context_bytes <= 0 or len(query) > 4096:
            raise ValueError("invalid retrieval query limits")
        root = repository.resolve(strict=True)
        if _snapshot(root) != self.commit:
            raise ValueError("stale retrieval index; rebuild for the current commit")
        terms = _terms(query)
        counts = [Counter(_tokens(p.content)) for p in self.passages]
        path_terms = [_terms(p.path) for p in self.passages]
        document_frequency = Counter(
            term for tokens, path in zip(counts, path_terms, strict=True) for term in tokens.keys() | path
        )
        average_length = sum(sum(tokens.values()) for tokens in counts) / max(1, len(counts)) or 1.0

        def relevance_score(position: int) -> float:
            tokens = counts[position]
            normalization = 1.2 * (0.25 + 0.75 * sum(tokens.values()) / average_length)
            value = 0.0
            for term in terms:
                frequency = tokens[term]
                inverse_frequency = math.log(
                    1 + (len(counts) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
                )
                value += inverse_frequency * frequency * 2.2 / (frequency + normalization)
                if term in path_terms[position]:
                    value += 0.75 * inverse_frequency
            return value

        ranked = sorted(
            ((relevance_score(position), p) for position, p in enumerate(self.passages)),
            key=lambda item: (-item[0], item[1].path, item[1].start_line),
        )
        matches: list[dict[str, object]] = []
        seen_paths: set[str] = set()
        for score, passage in ranked:
            if not score or len(matches) >= top_k:
                break
            if passage.path in seen_paths:
                continue
            candidate: dict[str, object] = {**asdict(passage), "score": score}
            proposed = {"trust": "untrusted_repository_data", "commit": self.commit, "matches": [*matches, candidate]}
            if len(json.dumps(proposed, ensure_ascii=False).encode()) > max_context_bytes:
                continue
            matches.append(candidate)
            seen_paths.add(passage.path)
        result: dict[str, object] = {"trust": "untrusted_repository_data", "commit": self.commit, "matches": matches}
        if len(json.dumps(result, ensure_ascii=False).encode()) > max_context_bytes:
            raise ValueError("context budget too small for retrieval envelope")
        if _snapshot(root) != self.commit:
            raise ValueError("repository changed during retrieval")
        return result
