from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QThread, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from app.models.config import AppSettings, AuthMethod, ConnectionRequest, RuntimeState
from app.services.autostart_service import AutostartService
from app.services.reconnect_service import ReconnectService
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
        self._reconnect_service = ReconnectService(self)
        self._sftp_service = SFTPService()
        self._sync_thread: QThread | None = None
        self._sync_worker: SyncWorker | None = None
        self._tray: TrayService | None = None
        self._sync_running = False
        self._force_exit = False
        self._shutdown_in_progress = False
        self._last_connection_request: ConnectionRequest | None = None
        self._runtime_state = RuntimeState()

        self._build_ui()
        self._load_initial_state()
        self._setup_tray()
        self._reconnect_service.status_changed.connect(self._on_retry_status_changed)
        self._reconnect_service.log_message.connect(self._sync_page.append_log)
        self._reconnect_service.reconnect_requested.connect(self._try_reconnect)
        QTimer.singleShot(0, self.restore_runtime_state)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._force_exit or self._tray is None:
            event.accept()
            return
        self._set_window_hidden_to_tray(True)
        self.hide()
        self._tray.showMessage("SFTP Sync Client", "Приложение свернуто в системный трей.")
        event.ignore()

    def changeEvent(self, event) -> None:  # type: ignore[override]
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self._set_window_hidden_to_tray(True)
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
        if not TrayService.is_available():
            return
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
        saved_local_path, saved_remote_path = self._settings_service.load_sync_paths()
        self._runtime_state = self._settings_service.load_runtime_state()
        has_saved_session = bool(settings.host and settings.username)
        autostart_supported = self._autostart_service.is_supported()
        self._sync_page.set_autostart_supported(
            autostart_supported,
            None if autostart_supported else "Автозапуск пока поддерживается только на Linux и Windows.",
        )
        password = ""
        if settings.host and settings.username and settings.auth_method == AuthMethod.PASSWORD:
            password = self._settings_service.load_password(settings.host, settings.username)
        elif settings.host and settings.username and settings.auth_method == AuthMethod.SSH_KEY:
            settings.key_path = self._settings_service.load_key_path(settings.host, settings.username) or settings.key_path

        self._login_page.set_form_data(settings, password)
        self._sync_page.set_local_path(saved_local_path)
        self._sync_page.set_remote_path(saved_remote_path)
        self._sync_page.set_session_available(has_saved_session)
        self._sync_page.set_autostart_enabled(autostart_supported and (settings.autostart_enabled or self._autostart_service.is_enabled()))
        self._sync_page.set_connection_status("Ожидание подключения" if has_saved_session else "Не подключено")
        self._sync_page.set_sync_status("Остановлено")
        self._sync_page.set_connection_active(False)
        self._sync_page.set_sync_controls(False)
        if has_saved_session:
            self._stack.setCurrentWidget(self._sync_page_container)
        if saved_local_path and not Path(saved_local_path).exists():
            self._sync_page.append_log(f"Сохранённая локальная папка недоступна: {saved_local_path}")

    def should_start_hidden_to_tray(self) -> bool:
        return self._runtime_state.window_hidden_to_tray and TrayService.is_available()

    def restore_runtime_state(self) -> None:
        self._runtime_state = self._settings_service.load_runtime_state()
        self._attempt_restore_session(self._runtime_state)
        self._restore_window_state(self._runtime_state)

    def _attempt_restore_session(self, runtime_state: RuntimeState) -> None:
        settings = self._settings_service.load()
        if not settings.host or not settings.username:
            return

        request = self._build_restored_request(settings)
        if request is None:
            self._sync_page.set_connection_status("Ошибка подключения")
            self._sync_page.append_log("Автовосстановление пропущено: недоступны сохранённые учётные данные.")
            return

        self._last_connection_request = request
        self._handle_connect_request(request, auto_restore=True, runtime_state=runtime_state)

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

    def _handle_connect_request(
        self,
        request: ConnectionRequest,
        auto_restore: bool = False,
        runtime_state: RuntimeState | None = None,
    ) -> None:
        self._login_page.set_busy(True)
        try:
            actual_request = self._sftp_service.connect(request, log_callback=self._sync_page.append_log)
        except Exception as exc:  # noqa: BLE001
            self._login_page.set_busy(False)
            message = self._sftp_service.user_friendly_error(exc)
            self._sync_page.append_log(message)
            if auto_restore:
                self._show_saved_session()
                self._last_connection_request = request
                if self._sftp_service.should_retry_connection_error(exc):
                    self._sync_page.append_log("Сохранённая сессия активна. Ожидание восстановления подключения.")
                    self._handle_connection_lost(
                        request=request,
                        reason=self._sftp_service.retry_reason(exc),
                        message=message,
                        resume_sync=(runtime_state.sync_running if runtime_state is not None else self._runtime_state.sync_running),
                    )
                else:
                    self._sync_page.set_connection_status("Ошибка подключения")
                return
            self._login_page.show_error(message)
            return

        self._login_page.set_busy(False)
        self._reconnect_service.stop_network_retry_loop()
        self._last_connection_request = actual_request
        self._persist_connection(actual_request)
        self._sync_page.set_session_available(True)
        self._stack.setCurrentWidget(self._sync_page_container)
        self._sync_page.set_connection_status("Подключено")
        self._sync_page.set_connection_active(True)
        self._sync_page.set_sync_controls(False)
        if actual_request.auth_method == AuthMethod.SSH_KEY and actual_request.key_path:
            self._sync_page.append_log(f"Использован SSH-ключ: {Path(actual_request.key_path).name}")
            self._login_page.key_path_edit.setText(actual_request.key_path)
        self._sync_page.append_log(f"Подключение к {actual_request.host}:{actual_request.port} успешно.")
        self._apply_default_autostart()

        settings = self._settings_service.load()
        effective_runtime_state = runtime_state or self._settings_service.load_runtime_state()
        if auto_restore and effective_runtime_state.sync_running and settings.local_dir and settings.remote_dir:
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
        if settings.autostart_enabled and self._autostart_service.is_supported():
            self._autostart_service.enable()

    def _set_autostart_enabled(self, enabled: bool) -> None:
        settings = self._settings_service.load()
        settings.autostart_enabled = enabled
        self._settings_service.save(settings)
        if not self._autostart_service.is_supported():
            self._sync_page.append_log("Автозапуск недоступен на текущей платформе.")
            return
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
        self._reconnect_service.stop_network_retry_loop()
        if not self._sftp_service.is_connected and self._last_connection_request is None:
            self._sync_page.set_connection_status("Не подключено")
            self._sync_page.set_sync_status("Остановлено")
            self._sync_page.set_connection_active(False)
            self._sync_page.set_session_available(False)
            self._sync_page.set_sync_controls(False)
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
        self._settings_service.clear_connection_settings(current_host, current_username)
        self._runtime_state = self._settings_service.load_runtime_state()
        self._login_page.set_form_data(AppSettings(), "")
        self._sync_page.clear_logs()
        saved_local_path, saved_remote_path = self._settings_service.load_sync_paths()
        self._sync_page.set_local_path(saved_local_path)
        self._sync_page.set_remote_path(saved_remote_path)
        self._sync_page.set_connection_status("Не подключено")
        self._sync_page.set_sync_status("Остановлено")
        self._sync_page.set_sync_controls(False)
        self._sync_page.set_connection_active(False)
        self._sync_page.set_session_available(False)
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

        self._save_runtime_state(sync_running=True)
        self._save_sync_paths(sync_was_running=True)
        self._sync_thread = QThread(self)
        self._sync_worker = SyncWorker(self._last_connection_request, local_dir, remote_dir)
        self._sync_worker.moveToThread(self._sync_thread)
        self._sync_thread.started.connect(self._sync_worker.run)
        self._sync_worker.log_message.connect(self._sync_page.append_log)
        self._sync_worker.sync_state_changed.connect(self._on_sync_state_changed)
        self._sync_worker.connection_state_changed.connect(self._on_worker_connection_state_changed)
        self._sync_worker.connection_issue.connect(self._on_worker_connection_issue)
        self._sync_worker.finished.connect(self._sync_thread.quit)
        self._sync_worker.finished.connect(self._sync_worker.deleteLater)
        self._sync_thread.finished.connect(self._sync_thread.deleteLater)
        self._sync_thread.finished.connect(self._on_thread_finished)
        self._sync_thread.start()

    def stop_sync(self) -> None:
        if self._reconnect_service.is_active():
            self._reconnect_service.set_resume_sync(False)
        self._stop_sync(wait=False)

    def _stop_sync(self, wait: bool, persist_runtime_state: bool = True) -> None:
        if self._sync_worker is not None:
            self._sync_worker.stop()
        if wait and self._sync_thread is not None:
            self._sync_thread.quit()
            self._sync_thread.wait(5000)
        if persist_runtime_state:
            self._sync_running = False
            self._save_runtime_state(sync_running=False)
            self._sync_page.set_sync_status("Остановлено")
            self._sync_page.set_sync_controls(False)
        self._save_sync_paths(sync_was_running=False)

    def _on_sync_state_changed(self, state: str) -> None:
        if state == "running":
            self._sync_running = True
            self._sync_page.set_sync_status("Запущено")
            self._sync_page.set_sync_controls(True)
            self._save_runtime_state(sync_running=True)
            self._save_sync_paths(sync_was_running=True)
            return

        if state == "waiting":
            self._sync_running = False
            self._sync_page.set_sync_status("Ожидание сети")
            self._sync_page.set_sync_controls(False, waiting=True)
            return

        self._sync_running = False
        self._sync_page.set_sync_status("Остановлено")
        self._sync_page.set_sync_controls(False)
        self._save_runtime_state(sync_running=False)
        self._save_sync_paths(sync_was_running=False)

    def _on_worker_connection_state_changed(self, state: str) -> None:
        if state == "connected":
            self._sync_page.set_connection_status("Подключено")
            self._sync_page.set_connection_active(True)
            return

        if state == "waiting":
            self._sync_page.set_connection_active(False)
            return

        if state == "disconnected":
            if self._last_connection_request is not None:
                self._sync_page.set_connection_status("Соединение потеряно")
                self._sync_page.set_connection_active(False)
            else:
                self._sync_page.set_connection_status("Отключено")
                self._sync_page.set_connection_active(False)

    def _on_worker_connection_issue(self, category: str, message: str) -> None:
        if self._last_connection_request is None:
            return
        if category in {"network", "server"}:
            self._handle_connection_lost(
                request=self._last_connection_request,
                reason="no_network" if category == "network" else "server_unreachable",
                message=message,
                resume_sync=self._runtime_state.sync_running,
            )
            return
        self._sync_page.append_log(message)
        self._sync_page.set_connection_status("Ошибка подключения")
        self._sync_page.set_connection_active(False)

    def _on_thread_finished(self) -> None:
        self._sync_thread = None
        self._sync_worker = None
        self._sync_running = False

    def _save_sync_paths(self, sync_was_running: bool | None = None) -> None:
        settings = self._settings_service.load()
        local_path = self._sync_page.local_path()
        remote_path = self._sync_page.remote_path()
        settings.local_dir = local_path
        settings.remote_dir = remote_path
        if sync_was_running is not None:
            settings.sync_was_running = sync_was_running
        self._settings_service.save_sync_paths(local_path, remote_path)
        self._settings_service.save(settings)

    def _save_runtime_state(
        self,
        sync_running: bool | None = None,
        window_hidden_to_tray: bool | None = None,
    ) -> None:
        if sync_running is not None:
            self._runtime_state.sync_running = sync_running
        if window_hidden_to_tray is not None:
            self._runtime_state.window_hidden_to_tray = window_hidden_to_tray
        self._settings_service.save_runtime_state(
            sync_running=self._runtime_state.sync_running,
            window_hidden_to_tray=self._runtime_state.window_hidden_to_tray,
        )

    def _set_window_hidden_to_tray(self, hidden: bool) -> None:
        if self._tray is None and hidden:
            return
        self._save_runtime_state(window_hidden_to_tray=hidden)

    def _restore_window_state(self, runtime_state: RuntimeState) -> None:
        if runtime_state.window_hidden_to_tray and self._tray is not None:
            self.hide()
            return
        if not self.isVisible():
            self.show()

    def _show_saved_session(self) -> None:
        self._sync_page.set_session_available(True)
        self._sync_page.set_connection_active(False)
        self._stack.setCurrentWidget(self._sync_page_container)

    def _handle_connection_lost(
        self,
        request: ConnectionRequest,
        reason: str,
        message: str,
        resume_sync: bool,
    ) -> None:
        self._show_saved_session()
        self._sync_page.set_connection_status("Нет сети" if reason == "no_network" else "Сервер недоступен")
        self._sync_page.set_connection_active(False)
        if resume_sync:
            self._sync_page.set_sync_status("Ожидание сети")
            self._sync_page.set_sync_controls(False, waiting=True)
        else:
            self._sync_page.set_sync_status("Остановлено")
            self._sync_page.set_sync_controls(False)
        self._last_connection_request = request
        self._reconnect_service.handle_connection_lost(request, resume_sync=resume_sync, reason=reason)
        self._sync_page.append_log(f"Выполняется ожидание восстановления подключения: {message}")

    def _on_retry_status_changed(self, status: str) -> None:
        self._show_saved_session()
        self._sync_page.set_connection_status(status)
        self._sync_page.set_connection_active(False)
        if self._runtime_state.sync_running:
            self._sync_page.set_sync_status("Ожидание сети")
            self._sync_page.set_sync_controls(False, waiting=True)

    def _try_reconnect(self, request: object, resume_sync: bool) -> None:
        if not isinstance(request, ConnectionRequest):
            return
        self._handle_connect_request(
            request,
            auto_restore=True,
            runtime_state=RuntimeState(
                sync_running=resume_sync,
                window_hidden_to_tray=self._runtime_state.window_hidden_to_tray,
            ),
        )

    def _show_from_tray(self) -> None:
        self._set_window_hidden_to_tray(False)
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
        self._reconnect_service.stop_network_retry_loop()
        self._save_runtime_state(
            sync_running=self._runtime_state.sync_running,
            window_hidden_to_tray=self.isHidden() and self._tray is not None,
        )
        self._stop_sync(wait=True, persist_runtime_state=False)
        self._sftp_service.disconnect()

        if hasattr(self, "_tray") and self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()

        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()
        sys.exit(0)
