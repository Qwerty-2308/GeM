"""Keeps each analysed bid PDF, named by its SHA-256, so it can be re-read later.

Names are the content hash only — never a bid number or anything else from the portal —
so no PDF can choose where it is written.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SHA256 = re.compile(r"[0-9a-f]{64}")


class FileDocumentStore:
    def __init__(self, root: Path):
        self._root = root

    def _path(self, sha256: str) -> Path:
        if not _SHA256.fullmatch(sha256):
            raise ValueError(f"not a SHA-256 hex digest: {sha256!r}")
        return self._root / f"{sha256}.pdf"

    def save(self, sha256: str, pdf: bytes) -> None:
        path = self._path(sha256)
        if path.exists():
            return  # same name, same content
        self._root.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_bytes(pdf)
        os.replace(partial, path)  # never leaves a half-written PDF under the real name

    def load(self, sha256: str) -> bytes | None:
        path = self._path(sha256)
        return path.read_bytes() if path.exists() else None
