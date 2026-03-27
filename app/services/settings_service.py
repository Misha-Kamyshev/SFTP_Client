from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSettings

from app.models.config import DEFAULT_SFTP_PORT, AppSettings, AuthMethod, RuntimeState
from app.storage.credentials_store import CredentialsStore


class SettingsService:
    """Wraps QSettings and credential persistence."""

    def __init__(self) -> None:
        self._settings = QSettings()
        self._credentials_store = CredentialsStore(self._settings)

    def load(self) -> AppSettings:
        return AppSettings(
            host=str(self._settings.value("connection/host", "")),
            port=self._settings.value("connection/port", DEFAULT_SFTP_PORT, int),
            username=str(self._settings.value("connection/username", "")),
            auth_method=AuthMethod(str(self._settings.value("connection/auth_method", AuthMethod.PASSWORD.value))),
            key_path=str(self._settings.value("connection/key_path", "")),
            local_dir=str(self._settings.value("sync/local_dir", "")),
            remote_dir=str(self._settings.value("sync/remote_dir", "")),
            sync_was_running=self._settings.value("sync/was_running", False, bool),
            autostart_enabled=self._settings.value("ui/autostart_enabled", True, bool),
        )

    def save(self, data: AppSettings) -> None:
        self._settings.setValue("connection/host", data.host)
        self._settings.setValue("connection/port", data.port)
        self._settings.setValue("connection/username", data.username)
        self._settings.setValue("connection/auth_method", data.auth_method.value)
        self._settings.setValue("connection/key_path", data.key_path)
        self._settings.setValue("sync/local_dir", data.local_dir)
        self._settings.setValue("sync/remote_dir", data.remote_dir)
        self._settings.setValue("sync/was_running", data.sync_was_running)
        self._settings.setValue("ui/autostart_enabled", data.autostart_enabled)
        self._settings.sync()

    def save_sync_paths(self, local_path: str, remote_path: str) -> None:
        self._settings.setValue("sync/local_dir", local_path)
        self._settings.setValue("sync/remote_dir", remote_path)
        self._settings.sync()

    def load_sync_paths(self) -> tuple[str, str]:
        return (
            str(self._settings.value("sync/local_dir", "")),
            str(self._settings.value("sync/remote_dir", "")),
        )

    def save_runtime_state(self, sync_running: bool, window_hidden_to_tray: bool) -> None:
        self._settings.setValue("runtime/sync_running", sync_running)
        self._settings.setValue("runtime/window_hidden_to_tray", window_hidden_to_tray)
        self._settings.setValue("sync/was_running", sync_running)
        self._settings.sync()

    def load_runtime_state(self) -> RuntimeState:
        sync_running = self._settings.value(
            "runtime/sync_running",
            self._settings.value("sync/was_running", False, bool),
            bool,
        )
        return RuntimeState(
            sync_running=sync_running,
            window_hidden_to_tray=self._settings.value("runtime/window_hidden_to_tray", False, bool),
        )

    def clear_connection_settings(self, host: str = "", username: str = "") -> None:
        if host and username:
            self._credentials_store.delete(host, username)
        else:
            self._credentials_store.clear_all()

        current = self.load()
        preserved = replace(
            current,
            host="",
            port=DEFAULT_SFTP_PORT,
            username="",
            auth_method=AuthMethod.PASSWORD,
            key_path="",
            sync_was_running=False,
        )
        self.save(preserved)
        self.save_runtime_state(sync_running=False, window_hidden_to_tray=False)

    def save_credentials(
        self,
        host: str,
        username: str,
        auth_method: AuthMethod,
        password: str = "",
        key_path: str = "",
    ) -> None:
        self._credentials_store.save(host, username, auth_method, password, key_path)

    def load_password(self, host: str, username: str) -> str:
        return self._credentials_store.load_password(host, username)

    def load_key_path(self, host: str, username: str) -> str:
        return self._credentials_store.load_key_path(host, username)

    def credentials_backend_name(self) -> str:
        return self._credentials_store.backend_name()

    def clear_all(self, host: str = "", username: str = "") -> None:
        if host and username:
            self._credentials_store.delete(host, username)
        else:
            self._credentials_store.clear_all()
        self._settings.clear()
        self._settings.sync()
