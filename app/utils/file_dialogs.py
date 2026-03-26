from __future__ import annotations

from pathlib import Path


def resolve_initial_path(path_value: str | None, default_dir: Path | None = None) -> str:
    candidate = Path(path_value).expanduser() if path_value else None
    fallback = (default_dir or Path.home()).expanduser()

    if candidate is None:
        return str(fallback)

    if candidate.exists():
        return str(candidate if candidate.is_dir() else candidate.parent)

    for parent in candidate.parents:
        if parent.exists() and parent.is_dir():
            return str(parent)

    return str(fallback)


def resolve_initial_file_directory(path_value: str | None, default_dir: Path | None = None) -> str:
    candidate = Path(path_value).expanduser() if path_value else None
    fallback = (default_dir or Path.home()).expanduser()

    if candidate is None:
        return str(fallback)

    if candidate.exists() and candidate.is_file():
        return str(candidate.parent)

    return str(fallback)
