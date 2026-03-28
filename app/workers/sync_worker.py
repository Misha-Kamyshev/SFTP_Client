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


@dataclass(slots=True)
class SyncEvent:
    kind: str
    path: str


class LocalEventHandler(FileSystemEventHandler):
    def __init__(self, event_queue: queue.Queue[SyncEvent]) -> None:
        super().__init__()
        self._queue = event_queue

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue("upsert", event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue("upsert", event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._enqueue("delete", event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._enqueue("delete", event.src_path)
        self._enqueue("upsert", event.dest_path)

    def _enqueue(self, kind: str, path: str) -> None:
        self._queue.put(SyncEvent(kind=kind, path=path))


class SyncWorker(QObject):
    log_message = Signal(str)
    sync_state_changed = Signal(str)
    connection_state_changed = Signal(str)
    connection_issue = Signal(str, str)
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
        self._event_queue: queue.Queue[SyncEvent] = queue.Queue()
        self._observer: Observer | None = None
        self._service = SFTPService()
        self._uploaded_index: dict[str, FileSnapshot] = {}
        self._directory_index: dict[str, FileSnapshot] = {}
        self._ignored_prefixes = (".goutputstream-",)
        self._ignored_suffixes = ("~", ".swp", ".swx", ".tmp", ".part", ".crdownload")

    @Slot()
    def run(self) -> None:
        final_sync_state = "stopped"
        final_connection_state = "disconnected"
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
                    event = self._event_queue.get(timeout=self._poll_interval_seconds)
                except queue.Empty:
                    continue
                self._process_local_event(event)
        except Exception as exc:  # noqa: BLE001
            message = self._service.user_friendly_error(exc)
            category = self._service.error_category(exc)
            self.connection_issue.emit(category, message)
            if self._service.should_retry_connection_error(exc):
                final_sync_state = "waiting"
                final_connection_state = "waiting"
                self.log_message.emit(f"Соединение потеряно: {message}")
            else:
                self.log_message.emit(f"Ошибка фоновой синхронизации: {message}")
        finally:
            self._cleanup(final_connection_state)
            self.sync_state_changed.emit(final_sync_state)
            self.finished.emit()

    def stop(self) -> None:
        self._stop_event.set()

    def _initial_scan(self) -> None:
        self.log_message.emit("Запуск первичной синхронизации.")
        self._download_missing_remote_entries()
        for path in sorted(self._local_dir.rglob("*")):
            if self._stop_event.is_set():
                break
            try:
                if path.is_dir():
                    self._sync_directory_if_changed(path)
                elif path.is_file():
                    self._upload_if_changed(path)
            except Exception as exc:  # noqa: BLE001
                self.log_message.emit(f"Ошибка синхронизации пути {path}: {exc}")
        self.log_message.emit("Первичная синхронизация завершена.")

    def _download_missing_remote_entries(self) -> None:
        self.log_message.emit("Проверка отсутствующих локальных файлов на сервере.")
        remote_directories, remote_files = self._service.walk_remote_tree(self._remote_dir)

        for relative_dir in remote_directories:
            if self._stop_event.is_set():
                return
            local_dir = self._local_dir / Path(relative_dir)
            if local_dir.exists():
                continue
            local_dir.mkdir(parents=True, exist_ok=True)
            self.log_message.emit(f"Создана локальная папка из сервера: {relative_dir}")

        remote_base = self._remote_dir if self._remote_dir == "/" else self._remote_dir.rstrip("/")
        for relative_file in remote_files:
            if self._stop_event.is_set():
                return
            local_file = self._local_dir / Path(relative_file)
            if local_file.exists():
                continue
            remote_path = posixpath.join(remote_base, relative_file)
            self._service.download_file(remote_path, local_file)
            self.log_message.emit(f"Скачан отсутствующий локально файл: {relative_file}")

    def _start_observer(self) -> None:
        handler = LocalEventHandler(self._event_queue)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._local_dir), recursive=True)
        self._observer.start()

    def _process_local_event(self, event: SyncEvent) -> None:
        if self._stop_event.is_set():
            return
        candidate = Path(event.path).expanduser().resolve()
        if self._should_ignore_path(candidate):
            return
        if event.kind == "delete":
            self._delete_remote_path(candidate)
            return
        if not candidate.exists():
            return
        try:
            if candidate.is_dir():
                self._sync_directory_if_changed(candidate)
            elif candidate.is_file():
                self._upload_if_changed(candidate)
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(f"Ошибка синхронизации пути {candidate}: {exc}")

    def _delete_remote_path(self, local_path: Path) -> None:
        try:
            relative_path = local_path.relative_to(self._local_dir).as_posix()
        except ValueError:
            return

        self._uploaded_index.pop(relative_path, None)
        self._directory_index.pop(relative_path, None)
        for key in list(self._uploaded_index):
            if key.startswith(f"{relative_path}/"):
                self._uploaded_index.pop(key, None)
        for key in list(self._directory_index):
            if key.startswith(f"{relative_path}/"):
                self._directory_index.pop(key, None)

        remote_base = self._remote_dir if self._remote_dir == "/" else self._remote_dir.rstrip("/")
        remote_path = posixpath.join(remote_base, relative_path) if relative_path else remote_base
        try:
            self._service.delete_remote_path(remote_path)
            self.log_message.emit(f"Удалён путь на сервере: {relative_path or '.'}")
        except Exception as exc:  # noqa: BLE001
            self.log_message.emit(
                f"Ошибка удаления {relative_path or '.'}: {self._service.user_friendly_error(exc)}. Повторное подключение."
            )
            self._service.reconnect()
            self.connection_state_changed.emit("connected")
            self._service.delete_remote_path(remote_path)
            self.log_message.emit(f"Путь удалён на сервере после переподключения: {relative_path or '.'}")

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
            self.log_message.emit(
                f"Ошибка загрузки {relative_path}: {self._service.user_friendly_error(first_exc)}. Повторное подключение."
            )
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
        self.log_message.emit(f"Синхронизирована директория: {relative_path or '.'}")

    def _should_ignore_path(self, path: Path) -> bool:
        name = path.name
        if any(name.startswith(prefix) for prefix in self._ignored_prefixes):
            return True
        if any(name.endswith(suffix) for suffix in self._ignored_suffixes):
            return True
        return False

    def _cleanup(self, connection_state: str) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._service.disconnect()
        self.connection_state_changed.emit(connection_state)
