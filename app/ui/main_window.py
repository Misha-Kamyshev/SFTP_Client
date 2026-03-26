from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QThread, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from app.models.config import AppSettings, AuthMethod, ConnectionRequest
from app.services.autostart_service import AutostartService
from app.services.settings_service import SettingsService
from app.services.sftp_service import SFTPService
from app.services.tray_service import TrayService
from app.ui.login_page import LoginPage
from app.ui.remote_directory_dialog import RemoteDirectoryDialog
from app.ui.sync_page import SyncPage
from app.workers.sync_worker import SyncWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SFTP Sync Client")
        self.resize(800, 600)
        self.setMinimumSize(800, 600)

        self._settings_service = SettingsService()
        self._autostart_service = AutostartService()
        self._sftp_service = SFTPService()
        self._sync_thread: QThread | None = None
        self._sync_worker: SyncWorker | None = None
        self._sync_running = False
        self._force_exit = False
        self._shutdown_in_progress = False
        self._last_connection_request: ConnectionRequest | None = None

        self._build_ui()
        self._load_initial_state()
        self._setup_tray()
        QTimer.singleShot(0, self._attempt_restore_session)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_exit:
            event.accept()
            return
        self.hide()
        self._tray.showMessage("SFTP Sync Client", "Приложение свернуто в системный трей.")
        event.ignore()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def _build_ui(self) -> None:
        self._stack = QStackedWidget()
        self._login_page = LoginPage()
        self._sync_page = SyncPage()
        self._sync_page.setFixedSize(self._sync_page.sizeHint())
        self._sync_page_container = QWidget()
        sync_outer_layout = QVBoxLayout(self._sync_page_container)
        sync_outer_layout.setContentsMargins(0, 0, 0, 0)
        sync_outer_layout.addStretch()
        sync_row_layout = QHBoxLayout()
        sync_row_layout.setContentsMargins(0, 0, 0, 0)
        sync_row_layout.addStretch()
        sync_row_layout.addWidget(self._sync_page)
        sync_row_layout.addStretch()
        sync_outer_layout.addLayout(sync_row_layout)
        sync_outer_layout.addStretch()
        self._stack.addWidget(self._login_page)
        self._stack.addWidget(self._sync_page_container)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self.setCentralWidget(container)

        self._login_page.connect_requested.connect(self._handle_connect_request)
        self._sync_page.choose_remote_requested.connect(self._open_remote_directory_dialog)
        self._sync_page.start_sync_requested.connect(self.start_sync)
        self._sync_page.stop_sync_requested.connect(self.stop_sync)
        self._sync_page.disconnect_requested.connect(self.disconnect_from_server)
        self._sync_page.autostart_toggled.connect(self._set_autostart_enabled)
        self._sync_page.local_dir_edit.textChanged.connect(lambda _text: self._save_sync_paths())
        self._sync_page.remote_dir_edit.textChanged.connect(lambda _text: self._save_sync_paths())

    def _setup_tray(self) -> None:
        self._tray = TrayService(
            parent=self,
            on_show=self._show_from_tray,
            on_start_sync=self.start_sync,
            on_stop_sync=self.stop_sync,
            on_exit=self._exit_application,
        )
        self._tray.setToolTip("SFTP Sync Client")
        self._tray.show()

    def _load_initial_state(self) -> None:
        settings = self._settings_service.load()
        password = ""
        if settings.host and settings.username and settings.auth_method == AuthMethod.PASSWORD:
            password = self._settings_service.load_password(settings.host, settings.username)
        elif settings.host and settings.username and settings.auth_method == AuthMethod.SSH_KEY:
            settings.key_path = self._settings_service.load_key_path(settings.host, settings.username) or settings.key_path

        self._login_page.set_form_data(settings, password)
        self._sync_page.set_local_path(settings.local_dir)
        self._sync_page.set_remote_path(settings.remote_dir)
        self._sync_page.set_autostart_enabled(settings.autostart_enabled or self._autostart_service.is_enabled())
        self._sync_page.set_connection_status("Не подключено")
        self._sync_page.set_sync_status("Остановлено")
        self._sync_page.set_connection_active(False)

    def _attempt_restore_session(self) -> None:
        settings = self._settings_service.load()
        if not settings.host or not settings.username:
            return

        request = self._build_restored_request(settings)
        if request is None:
            self._sync_page.append_log("Автовосстановление пропущено: недоступны сохранённые учётные данные.")
            return

        self._handle_connect_request(request, auto_restore=True)

    def _build_restored_request(self, settings: AppSettings) -> ConnectionRequest | None:
        password = ""
        key_path = settings.key_path
        if settings.auth_method == AuthMethod.PASSWORD:
            password = self._settings_service.load_password(settings.host, settings.username)
            if not password:
                return None
        else:
            key_path = self._settings_service.load_key_path(settings.host, settings.username) or key_path
            if not key_path:
                return None
        return ConnectionRequest(
            host=settings.host,
            username=settings.username,
            auth_method=settings.auth_method,
            port=settings.port,
            password=password,
            key_path=key_path,
        )

    def _handle_connect_request(self, request: ConnectionRequest, auto_restore: bool = False) -> None:
        self._login_page.set_busy(True)
        try:
            self._sftp_service.connect(request)
        except Exception as exc:  # noqa: BLE001
            self._login_page.set_busy(False)
            message = self._sftp_service.user_friendly_error(exc)
            self._sync_page.append_log(message)
            if auto_restore:
                self._sync_page.set_connection_status("Ошибка подключения")
                return
            self._login_page.show_error(message)
            return

        self._login_page.set_busy(False)
        self._last_connection_request = request
        self._persist_connection(request)
        self._stack.setCurrentWidget(self._sync_page_container)
        self._sync_page.set_connection_status("Подключено")
        self._sync_page.set_connection_active(True)
        self._sync_page.append_log(f"Подключение к {request.host}:{request.port} успешно.")
        self._apply_default_autostart()

        settings = self._settings_service.load()
        if auto_restore and settings.sync_was_running and settings.local_dir and settings.remote_dir:
            QTimer.singleShot(250, self.start_sync)

    def _persist_connection(self, request: ConnectionRequest) -> None:
        settings = self._settings_service.load()
        settings.host = request.host
        settings.port = request.port
        settings.username = request.username
        settings.auth_method = request.auth_method
        settings.key_path = request.key_path
        self._settings_service.save(settings)
        self._settings_service.save_credentials(
            host=request.host,
            username=request.username,
            auth_method=request.auth_method,
            password=request.password,
            key_path=request.key_path,
        )

    def _apply_default_autostart(self) -> None:
        settings = self._settings_service.load()
        if settings.autostart_enabled:
            self._autostart_service.enable()

    def _set_autostart_enabled(self, enabled: bool) -> None:
        settings = self._settings_service.load()
        settings.autostart_enabled = enabled
        self._settings_service.save(settings)
        if enabled:
            self._autostart_service.enable()
            self._sync_page.append_log("Автозапуск включен.")
        else:
            self._autostart_service.disable()
            self._sync_page.append_log("Автозапуск отключен.")

    def _open_remote_directory_dialog(self) -> None:
        if not self._sftp_service.is_connected:
            QMessageBox.warning(self, "Нет подключения", "Сначала подключитесь к SFTP-серверу.")
            return
        dialog = RemoteDirectoryDialog(self._sftp_service, self._sync_page.remote_path() or "/", parent=self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        if dialog.exec():
            self._sync_page.set_remote_path(dialog.selected_path())
            self._save_sync_paths()

    def disconnect_from_server(self) -> None:
        if not self._sftp_service.is_connected and self._last_connection_request is None:
            self._sync_page.set_connection_status("Не подключено")
            self._sync_page.set_sync_status("Остановлено")
            self._sync_page.set_connection_active(False)
            self._sync_page.append_log("Соединение уже закрыто.")
            return

        current_host = self._last_connection_request.host if self._last_connection_request else ""
        current_username = self._last_connection_request.username if self._last_connection_request else ""
        try:
            self._stop_sync(wait=True)
            self._sftp_service.disconnect()
        except Exception as exc:  # noqa: BLE001
            message = self._sftp_service.user_friendly_error(exc)
            self._sync_page.append_log(message)
            QMessageBox.warning(self, "Ошибка отключения", message)
            return

        self._last_connection_request = None
        self._settings_service.clear_all(current_host, current_username)
        self._login_page.set_form_data(AppSettings(), "")
        self._sync_page.set_local_path("")
        self._sync_page.set_remote_path("")
        self._sync_page.clear_logs()
        self._sync_page.set_connection_status("Не подключено")
        self._sync_page.set_sync_status("Остановлено")
        self._sync_page.set_sync_controls(False)
        self._sync_page.set_connection_active(False)
        self._sync_page.append_log("Соединение с сервером закрыто пользователем.")
        self._stack.setCurrentWidget(self._login_page)

    def start_sync(self) -> None:
        if self._sync_running:
            return
        if self._last_connection_request is None:
            QMessageBox.warning(self, "Нет подключения", "Сначала выполните подключение к серверу.")
            return

        local_dir = self._sync_page.local_path()
        remote_dir = self._sync_page.remote_path()
        if not local_dir:
            QMessageBox.warning(self, "Локальная директория", "Выберите локальную директорию.")
            return
        if not Path(local_dir).exists():
            QMessageBox.warning(self, "Локальная директория", "Указанная локальная директория не существует.")
            return
        if not remote_dir:
            QMessageBox.warning(self, "Удалённая директория", "Выберите директорию на сервере.")
            return

        self._save_sync_paths(sync_was_running=True)
        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker(self._last_connection_request, local_dir, remote_dir)
        self._sync_worker.moveToThread(self._sync_thread)
        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.log_message.connect(self._sync_page.append_log)
        self._sync_worker.sync_state_changed.connect(self._on_sync_state_changed)
        self._sync_worker.connection_state_changed.connect(self._on_worker_connection_state_changed)
        self._sync_worker.finished.connect(self._sync_thread.quit)
        self._sync_worker.finished.connect(self._sync_worker.deleteLater)
        self._sync_thread.finished.connect(self._sync_thread.deleteLater)
        self._sync_thread.finished.connect(self._on_thread_finished)
        self._sync_thread.start()

    def stop_sync(self) -> None:
        self._stop_sync(wait=False)

    def _stop_sync(self, wait: bool) -> None:
        if self._sync_worker is not None:
            self._sync_worker.stop()
        if wait and self._sync_thread is not None:
            self._sync_thread.quit()
            self._sync_thread.wait(5000)
        self._save_sync_paths(sync_was_running=False)

    def _on_sync_state_changed(self, state: str) -> None:
        running = state == "running"
        self._sync_running = running
        self._sync_page.set_sync_status("Запущено" if running else "Остановлено")
        self._sync_page.set_sync_controls(running)
        self._save_sync_paths(sync_was_running=running)

    def _on_worker_connection_state_changed(self, state: str) -> None:
        if state == "connected":
            self._sync_page.set_connection_status("Подключено")
            self._sync_page.set_connection_active(True)
            return

        if state == "disconnected":
            if self._last_connection_request is not None:
                self._sync_page.set_connection_status("Подключено")
                self._sync_page.set_connection_active(True)
            else:
                self._sync_page.set_connection_status("Отключено")
                self._sync_page.set_connection_active(False)

    def _on_thread_finished(self) -> None:
        self._sync_thread = None
        self._sync_worker = None
        self._sync_running = False

    def _save_sync_paths(self, sync_was_running: bool | None = None) -> None:
        settings = self._settings_service.load()
        settings.local_dir = self._sync_page.local_path()
        settings.remote_dir = self._sync_page.remote_path()
        if sync_was_running is not None:
            settings.sync_was_running = sync_was_running
        self._settings_service.save(settings)

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _exit_application(self) -> None:
        self.shutdown()

    def shutdown(self) -> None:
        if self._shutdown_in_progress:
            return

        self._shutdown_in_progress = True
        self._force_exit = True
        self._stop_sync(wait=True)
        self._sftp_service.disconnect()

        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()

        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        sys.exit(0)
