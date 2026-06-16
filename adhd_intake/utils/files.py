"""File helpers: hashing and safe moves."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the SHA-256 hex digest of a file (used for de-duplication / audit)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_move(src: Path, dest_dir: Path) -> Path:
    """Move ``src`` into ``dest_dir``, appending a counter if a name collides."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    counter = 1
    while target.exists():
        target = dest_dir / f"{src.stem}__{counter}{src.suffix}"
        counter += 1
    shutil.move(str(src), str(target))
    return target


def safe_copy(src: Path, dest_dir: Path) -> Path:
    """Copy ``src`` into ``dest_dir``, appending a counter if a name collides."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    counter = 1
    while target.exists():
        target = dest_dir / f"{src.stem}__{counter}{src.suffix}"
        counter += 1
    shutil.copy2(str(src), str(target))
    return target
