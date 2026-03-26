from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.services.theme_manager import ThemeManager
from app.ui.main_window import MainWindow
from app.utils.constants import APP_NAME, ORGANIZATION_NAME


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORGANIZATION_NAME)
    app.setQuitOnLastWindowClosed(False)
    theme_manager = ThemeManager(app)
    theme_manager.listen_for_theme_changes()
    app.theme_manager = theme_manager  # type: ignore[attr-defined]

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
