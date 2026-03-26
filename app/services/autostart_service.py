from __future__ import annotations

import shlex
import sys
from pathlib import Path

from app.utils.constants import APP_DISPLAY_NAME, APP_NAME


class AutostartService:
    """Creates an XDG autostart .desktop entry for Linux sessions."""

    def __init__(self) -> None:
        self._autostart_dir = Path.home() / ".config" / "autostart"
        self._desktop_file = self._autostart_dir / f"{APP_NAME.lower()}.desktop"

    def enable(self) -> None:
        self._autostart_dir.mkdir(parents=True, exist_ok=True)
        self._desktop_file.write_text(self._desktop_entry(), encoding="utf-8")

    def disable(self) -> None:
        if self._desktop_file.exists():
            self._desktop_file.unlink()

    def is_enabled(self) -> bool:
        return self._desktop_file.exists()

    def desktop_file_path(self) -> Path:
        return self._desktop_file

    def _desktop_entry(self) -> str:
        exec_line = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve().parents[2] / 'main.py'))}"
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_DISPLAY_NAME}\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Terminal=false\n"
            "Categories=Network;Utility;\n"
        )
