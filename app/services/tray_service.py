from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from app.utils.resources import app_icon


class TrayService(QSystemTrayIcon):
    @staticmethod
    def is_available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def __init__(
        self,
        parent: QWidget,
        on_show: Callable[[], None],
        on_start_sync: Callable[[], None],
        on_stop_sync: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        super().__init__(app_icon(), parent)
        self._on_show = on_show
        self._on_start_sync = on_start_sync
        self._on_stop_sync = on_stop_sync
        self._on_exit = on_exit
        self._menu = QMenu(parent)
        self._build_menu()
        self.setContextMenu(self._menu)
        self.activated.connect(self._handle_activation)

    def _build_menu(self) -> None:
        open_action = QAction("Открыть", self)
        open_action.triggered.connect(self._on_show)
        self._menu.addAction(open_action)

        start_action = QAction("Запустить синхронизацию", self)
        start_action.triggered.connect(self._on_start_sync)
        self._menu.addAction(start_action)

        stop_action = QAction("Остановить синхронизацию", self)
        stop_action.triggered.connect(self._on_stop_sync)
        self._menu.addAction(stop_action)

        self._menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.triggered.connect(self._on_exit)
        self._menu.addAction(exit_action)

    def _handle_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        }:
            self._on_show()

    def show_app_message(self, title: str, message: str, timeout_ms: int = 10000) -> None:
        self.showMessage(title, message, app_icon(), timeout_ms)
