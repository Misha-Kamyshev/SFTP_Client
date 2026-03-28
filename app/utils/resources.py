from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parents[2]


def app_icon_path() -> Path:
    return project_root() / "assets" / "icons" / "sftp-sync-client.png"


def app_icon() -> QIcon:
    return QIcon(str(app_icon_path()))
