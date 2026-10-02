"""Content identities and atomic JSON publication for reproducible runs."""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import tempfile
from typing import Any


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def algorithm_provenance() -> dict[str, Any]:
    """Include every package source and installed numerical/reporting version.

    Do not cache this value: an edited algorithm must invalidate the next run
    even when several batches are invoked in the same Python process.
    """
    root = Path(__file__).resolve().parent
    versions = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    for distribution in (
        "numpy", "pandas", "PyYAML", "scipy", "scikit-learn", "matplotlib",
    ):
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return {
        "source_sha256": {
            path.relative_to(root).as_posix(): file_sha256(path)
            for path in sorted(root.rglob("*.py"))
        },
        "software_versions": versions,
    }


def atomic_write_json(path: Path, payload: Any) -> None:
    """Readers see either the previous complete JSON or the new complete JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
