from __future__ import annotations

import posixpath
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.models.config import ConnectionRequest
from app.services.sftp_service import SFTPService


@dataclass(slots=True)
class FileSnapshot:
    size: int
    mtime_ns: int


class LocalEventHandler(FileSystemEventHandler):
    def __init__(self, event_queue: queue.Queue[str]) -> None:
        super().__init__()
        self._queue = event_queue

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._queue.put(event.dest_path)

    def _enqueue(self, event: FileSystemEvent) -> None:
        self._queue.put(event.src_path)


class SyncWorker(QObject):
    log_message = Signal(str)
    sync_state_changed = Signal(str)
    connection_state_changed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        connection_request: ConnectionRequest,
        local_dir: str,
        remote_dir: str,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        super().__init__()
        self._request = connection_request
        self._local_dir = Path(local_dir).expanduser().resolve()
        self._remote_dir = remote_dir
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._event_queue: queue.Queue[str] = queue.Queue()
        self._observer: Observer | None = None
        self._service = SFTPService()
        self._uploaded_index: dict[str, FileSnapshot] = {}
        self._directory_index: dict[str, FileSnapshot] = {}
        self._ignored_prefixes = (".goutputstream-",)
        self._ignored_suffixes = ("~", ".swp", ".swx", ".tmp", ".part", ".crdownload")

    @Slot()
    def run(self) -> None:
        self.sync_state_changed.emit("running")
        try:
            self._service.connect(self._request)
            self.connection_state_changed.emit("connected")
            self.log_message.emit("Подключение к SFTP установлено.")
            self._service.ensure_remote_dir(self._remote_dir)
            self._initial_scan()
            self._start_observer()
            self.log_message.emit("Отслеживание локальной директории запущено.")
            while not self._stop_event.is_set():
                try:
                    path = self._event_queue.get(timeout=self._poll_interval_seconds)
                except queue.Empty:
                    continue
                self._process_local_path(Path(path))
        except Exception as exc:  # noqa: BLE001
            self.connection_state_changed.emit("disconnected")
            self.log_message.emit(f"Ошибка фоновой синхронизации: {exc}")
        finally:
            self._cleanup()
            self.sync_state_changed.emit("stopped")
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()

    def _initial_scan(self) -> None:
        self.log_message.emit("Запуск первичной синхронизации.")
        for path in sorted(self._local_dir.rglob("*")):
            if self._stop_event.is_set():
                break
            if path.is_dir():
                self._sync_directory_if_changed(path)
            elif path.is_file():
                self._upload_if_changed(path)
        self.log_message.emit("Первичная синхронизация завершена.")

    def _start_observer(self) -> None:
        handler = LocalEventHandler(self._event_queue)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._local_dir), recursive=True)
        self._observer.start()

    def _process_local_path(self, path: Path) -> None:
        if self._stop_event.is_set():
            return
        candidate = path.expanduser().resolve()
        if self._should_ignore_path(candidate):
            return
        if not candidate.exists():
            return
        if candidate.is_dir():
            self._sync_directory_if_changed(candidate)
        elif candidate.is_file():
            self._upload_if_changed(candidate)

    def _upload_if_changed(self, local_path: Path) -> None:
        if self._should_ignore_path(local_path):
            return

        relative_path = local_path.relative_to(self._local_dir).as_posix()
        try:
            stat_info = local_path.stat()
        except FileNotFoundError:
            return
        snapshot = FileSnapshot(size=stat_info.st_size, mtime_ns=stat_info.st_mtime_ns)
        previous = self._uploaded_index.get(relative_path)
        if previous == snapshot:
            return

        remote_base = self._remote_dir if self._remote_dir == "/" else self._remote_dir.rstrip("/")
        remote_path = posixpath.join(remote_base, relative_path)
        try:
            if previous is None and self._service.remote_file_matches_local(local_path, remote_path):
                self._uploaded_index[relative_path] = snapshot
                return
        except FileNotFoundError:
            return

        try:
            self._service.upload_file(local_path, remote_path)
            self._uploaded_index[relative_path] = snapshot
            self.log_message.emit(f"Загружен файл: {relative_path}")
        except FileNotFoundError:
            return
        except Exception as first_exc:  # noqa: BLE001
            self.log_message.emit(f"Ошибка загрузки {relative_path}: {first_exc}. Повторное подключение.")
            self._service.reconnect()
            self.connection_state_changed.emit("connected")
            try:
                self._service.upload_file(local_path, remote_path)
            except FileNotFoundError:
                return
            self._uploaded_index[relative_path] = snapshot
            self.log_message.emit(f"Файл загружен после переподключения: {relative_path}")

    def _sync_directory_if_changed(self, local_path: Path) -> None:
        relative_path = local_path.relative_to(self._local_dir).as_posix()
        stat_info = local_path.stat()
        snapshot = FileSnapshot(size=stat_info.st_size, mtime_ns=stat_info.st_mtime_ns)
        previous = self._directory_index.get(relative_path)
        if previous == snapshot:
            return

        remote_base = self._remote_dir if self._remote_dir == "/" else self._remote_dir.rstrip("/")
        remote_path = posixpath.join(remote_base, relative_path) if relative_path else remote_base
        if previous is None and self._service.remote_directory_matches_local(local_path, remote_path):
            self._directory_index[relative_path] = snapshot
            return

        self._service.sync_directory(local_path, remote_path)
        self._directory_index[relative_path] = snapshot

    def _should_ignore_path(self, path: Path) -> bool:
        name = path.name
        if any(name.startswith(prefix) for prefix in self._ignored_prefixes):
            return True
        if any(name.endswith(suffix) for suffix in self._ignored_suffixes):
            return True
        return False

    def _cleanup(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._service.disconnect()
        self.connection_state_changed.emit("disconnected")
