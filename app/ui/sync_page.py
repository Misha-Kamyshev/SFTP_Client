from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.utils.file_dialogs import resolve_initial_path


class SyncPage(QWidget):
    choose_remote_requested = Signal()
    start_sync_requested = Signal()
    stop_sync_requested = Signal()
    disconnect_requested = Signal()
    autostart_toggled = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._log_entries: list[tuple[str, bool]] = []
        self._connection_active = False
        self._session_available = False
        self._sync_running = False
        self._sync_waiting = False
        self._build_ui()
        self._connect_signals()
        app = QApplication.instance()
        theme_manager = getattr(app, "theme_manager", None)
        if theme_manager is not None:
            theme_manager.theme_changed.connect(self.refresh_log_view)

    def set_local_path(self, value: str) -> None:
        self.local_dir_edit.setText(value)

    def set_remote_path(self, value: str) -> None:
        self.remote_dir_edit.setText(value)

    def append_log(self, message: str) -> None:
        self._log_entries.append((message, "ошибка" in message.lower()))
        self.refresh_log_view()

    def clear_logs(self) -> None:
        self._log_entries.clear()
        self.log_output.clear()

    def set_connection_status(self, text: str) -> None:
        self.connection_status_value.setText(text)

    def set_sync_status(self, text: str) -> None:
        self.sync_status_value.setText(text)

    def set_sync_controls(self, running: bool, waiting: bool = False) -> None:
        self._sync_running = running
        self._sync_waiting = waiting
        self._refresh_controls()

    def set_connection_active(self, active: bool) -> None:
        self._connection_active = active
        self._refresh_controls()

    def set_session_available(self, available: bool) -> None:
        self._session_available = available
        self._refresh_controls()

    def local_path(self) -> str:
        return self.local_dir_edit.text().strip()

    def remote_path(self) -> str:
        return self.remote_dir_edit.text().strip()

    def set_autostart_enabled(self, enabled: bool) -> None:
        previous = self.autostart_checkbox.blockSignals(True)
        self.autostart_checkbox.setChecked(enabled)
        self.autostart_checkbox.blockSignals(previous)

    def set_autostart_supported(self, supported: bool, description: str | None = None) -> None:
        self.autostart_checkbox.setEnabled(supported)
        self.autostart_checkbox.setToolTip(description or "")

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.setSpacing(16)

        status_group = QGroupBox("Состояние")
        status_layout = QGridLayout(status_group)
        self.connection_status_value = QLabel("Не подключено")
        self.sync_status_value = QLabel("Остановлено")
        status_layout.addWidget(QLabel("Подключение"), 0, 0)
        status_layout.addWidget(self.connection_status_value, 0, 1)
        status_layout.addWidget(QLabel("Синхронизация"), 1, 0)
        status_layout.addWidget(self.sync_status_value, 1, 1)

        self.local_dir_edit = QLineEdit()
        self.local_dir_edit.setReadOnly(True)
        self.local_dir_button = QPushButton("Выбрать...")
        local_layout = QHBoxLayout()
        local_layout.addWidget(self.local_dir_edit)
        local_layout.addWidget(self.local_dir_button)

        self.remote_dir_edit = QLineEdit()
        self.remote_dir_edit.setReadOnly(True)
        self.remote_dir_button = QPushButton("Выбрать...")
        remote_layout = QHBoxLayout()
        remote_layout.addWidget(self.remote_dir_edit)
        remote_layout.addWidget(self.remote_dir_button)

        path_group = QGroupBox("Пути синхронизации")
        path_layout = QGridLayout(path_group)
        path_layout.addWidget(QLabel("Локальная директория"), 0, 0)
        path_layout.addLayout(local_layout, 0, 1)
        path_layout.addWidget(QLabel("Удалённая директория"), 1, 0)
        path_layout.addLayout(remote_layout, 1, 1)

        self.autostart_checkbox = QCheckBox("Автозапуск после входа в систему")

        buttons_layout = QHBoxLayout()
        self.start_button = QPushButton("Запуск синхронизации")
        self.stop_button = QPushButton("Остановка синхронизации")
        self.disconnect_button = QPushButton("Отключиться от сервера")
        self.stop_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)
        buttons_layout.addWidget(self.start_button)
        buttons_layout.addWidget(self.stop_button)
        buttons_layout.addWidget(self.disconnect_button)

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Журнал событий будет отображаться здесь.")

        root_layout.addWidget(status_group)
        root_layout.addWidget(path_group)
        root_layout.addWidget(self.autostart_checkbox)
        root_layout.addLayout(buttons_layout)
        root_layout.addWidget(self.log_output, stretch=1)

    def _connect_signals(self) -> None:
        self.local_dir_button.clicked.connect(self._choose_local_dir)
        self.remote_dir_button.clicked.connect(self.choose_remote_requested)
        self.start_button.clicked.connect(self.start_sync_requested)
        self.stop_button.clicked.connect(self.stop_sync_requested)
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        self.autostart_checkbox.toggled.connect(self.autostart_toggled)

    def _choose_local_dir(self) -> None:
        initial_dir = resolve_initial_path(self.local_dir_edit.text().strip())
        selected = QFileDialog.getExistingDirectory(self, "Выберите локальную директорию", initial_dir)
        if selected:
            self.local_dir_edit.setText(selected)

    def refresh_log_view(self) -> None:
        colors = QApplication.instance().property("theme_colors") or {}
        text_color = colors.get("text", "#1f2937")
        error_color = colors.get("error", "#b42318")
        rendered_lines = []
        for message, is_error in self._log_entries:
            color = error_color if is_error else text_color
            rendered_lines.append(f'<div style="color: {color};">{html.escape(message)}</div>')
        self.log_output.setHtml("".join(rendered_lines))
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.log_output.setTextCursor(cursor)

    def _refresh_controls(self) -> None:
        self.remote_dir_button.setEnabled(self._connection_active)
        self.disconnect_button.setEnabled(self._session_available)
        self.start_button.setEnabled(self._connection_active and not self._sync_running and not self._sync_waiting)
        self.stop_button.setEnabled((self._connection_active and self._sync_running) or self._sync_waiting)
