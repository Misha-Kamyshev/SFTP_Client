from __future__ import annotations

import os
import platform
from pathlib import Path

from app.utils.constants import APP_DISPLAY_NAME, APP_NAME
from app.utils.runtime import launch_command_for_posix, launch_command_for_windows


class AutostartService:
    """Creates per-user autostart entries for supported desktop platforms."""

    def __init__(self) -> None:
        self._platform = platform.system().lower()
        if self._platform == "windows":
            startup_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            self._autostart_dir = startup_dir / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            self._entry_file = self._autostart_dir / f"{APP_NAME}.cmd"
        elif self._platform == "linux":
            self._autostart_dir = Path.home() / ".config" / "autostart"
            self._entry_file = self._autostart_dir / f"{APP_NAME.lower()}.desktop"
        else:
            self._autostart_dir = Path()
            self._entry_file = None

    def is_supported(self) -> bool:
        return self._entry_file is not None

    def enable(self) -> None:
        if self._entry_file is None:
            return
        self._autostart_dir.mkdir(parents=True, exist_ok=True)
        self._entry_file.write_text(self._entry_contents(), encoding="utf-8")

    def disable(self) -> None:
        if self._entry_file is not None and self._entry_file.exists():
            self._entry_file.unlink()

    def is_enabled(self) -> bool:
        return self._entry_file is not None and self._entry_file.exists()

    def entry_file_path(self) -> Path | None:
        return self._entry_file

    def _entry_contents(self) -> str:
        if self._platform == "windows":
            return self._windows_startup_script()
        return self._desktop_entry()

    def _desktop_entry(self) -> str:
        exec_line = launch_command_for_posix()
        return (
            "[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={APP_DISPLAY_NAME}\n"
            f"Exec={exec_line}\n"
            "X-GNOME-Autostart-enabled=true\n"
            "Terminal=false\n"
            "Categories=Network;Utility;\n"
        )

    @staticmethod
    def _windows_startup_script() -> str:
        launch_command = launch_command_for_windows()
        return f'@echo off\nstart "" {launch_command}\n'
