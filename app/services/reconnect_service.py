from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app.models.config import ConnectionRequest
from app.services.sftp_service import SFTPService


class _ConnectivityWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port

    @Slot()
    def run(self) -> None:
        ok, reason = SFTPService.check_network_connectivity(self._host, self._port)
        self.finished.emit(ok, reason or "")


class ReconnectService(QObject):
    status_changed = Signal(str)
    log_message = Signal(str)
    reconnect_requested = Signal(object, bool)

    def __init__(self, parent: QObject | None = None, retry_interval_ms: int = 30_000) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(retry_interval_ms)
        self._timer.timeout.connect(self._run_check)
        self._request: ConnectionRequest | None = None
        self._resume_sync = False
        self._check_thread: QThread | None = None
        self._check_worker: _ConnectivityWorker | None = None

    def is_active(self) -> bool:
        return self._timer.isActive() or self._check_thread is not None

    def set_resume_sync(self, resume_sync: bool) -> None:
        self._resume_sync = resume_sync

    def start_network_retry_loop(self, request: ConnectionRequest, resume_sync: bool, reason: str) -> None:
        self._request = request
        self._resume_sync = resume_sync
        self._update_waiting_state(reason)
        if not self._timer.isActive():
            self._timer.start()

    def stop_network_retry_loop(self) -> None:
        self._timer.stop()
        self._request = None
        self._resume_sync = False

    def handle_connection_lost(self, request: ConnectionRequest, resume_sync: bool, reason: str) -> None:
        self.start_network_retry_loop(request, resume_sync, reason)

    def try_reconnect(self) -> None:
        if self._request is None:
            return
        self.status_changed.emit("Переподключение")
        self.log_message.emit("Сеть восстановлена. Выполняется переподключение.")
        self._timer.stop()
        self.reconnect_requested.emit(self._request, self._resume_sync)

    def _run_check(self) -> None:
        if self._request is None or self._check_thread is not None:
            return

        self.log_message.emit("Повторная проверка сети и сервера.")
        self.status_changed.emit("Переподключение")
        self._check_thread = QThread(self)
        self._check_worker = _ConnectivityWorker(self._request.host, SFTPService.normalize_port(self._request.port))
        self._check_worker.moveToThread(self._check_thread)
        self._check_thread.started.connect(self._check_worker.run)
        self._check_worker.finished.connect(self._handle_check_result)
        self._check_worker.finished.connect(self._check_thread.quit)
        self._check_worker.finished.connect(self._check_worker.deleteLater)
        self._check_thread.finished.connect(self._check_thread.deleteLater)
        self._check_thread.finished.connect(self._clear_check_thread)
        self._check_thread.start()

    @Slot(bool, str)
    def _handle_check_result(self, ok: bool, reason: str) -> None:
        if ok:
            self.try_reconnect()
            return
        self._update_waiting_state(reason or "server_unreachable")

    @Slot()
    def _clear_check_thread(self) -> None:
        self._check_thread = None
        self._check_worker = None

    def _update_waiting_state(self, reason: str) -> None:
        if reason == "no_network":
            self.status_changed.emit("Ожидание сети")
            self.log_message.emit("Нет подключения к сети. Повторная проверка через 30 секунд.")
            return
        if reason == "dns_error":
            self.status_changed.emit("Сервер недоступен")
            self.log_message.emit("Не удалось разрешить адрес сервера. Повторная проверка через 30 секунд.")
            return
        if reason == "timeout":
            self.status_changed.emit("Сервер недоступен")
            self.log_message.emit("Сервер не отвечает. Повторная проверка через 30 секунд.")
            return
        if reason == "refused":
            self.status_changed.emit("Сервер недоступен")
            self.log_message.emit("Сервер отклонил соединение. Повторная проверка через 30 секунд.")
            return
        self.status_changed.emit("Сервер недоступен")
        self.log_message.emit("Сервер недоступен. Повторная проверка через 30 секунд.")
