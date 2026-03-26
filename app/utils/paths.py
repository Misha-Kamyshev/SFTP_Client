from __future__ import annotations

import os
from pathlib import Path


def ensure_private_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch(mode=0o600)
    else:
        os.chmod(path, 0o600)
    return path
