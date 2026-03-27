from __future__ import annotations

from pathlib import Path


EXCLUDED_SSH_NAMES = {"known_hosts", "config", "authorized_keys"}


def find_ssh_keys(limit: int = 3) -> list[Path]:
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists() or not ssh_dir.is_dir():
        return []

    candidates = [
        path
        for path in sorted(ssh_dir.iterdir(), key=lambda item: item.name.lower())
        if path.is_file()
        and path.suffix != ".pub"
        and path.name not in EXCLUDED_SSH_NAMES
    ]
    return candidates[:limit]
