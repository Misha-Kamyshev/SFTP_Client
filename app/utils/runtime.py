from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def launch_command_parts() -> list[str]:
    if is_frozen():
        return [str(Path(sys.executable).resolve())]

    argv0 = Path(sys.argv[0]).expanduser()
    if argv0.exists() and argv0.is_file() and argv0.suffix.lower() not in {".py", ".pyw"}:
        return [str(argv0.resolve())]

    root = Path(__file__).resolve().parents[2]
    return [str(Path(sys.executable).resolve()), str(root / "main.py")]


def launch_command_for_posix() -> str:
    return shlex.join(launch_command_parts())


def launch_command_for_windows() -> str:
    return subprocess.list2cmdline(launch_command_parts())
