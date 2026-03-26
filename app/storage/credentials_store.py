from __future__ import annotations

import logging
from base64 import b64decode, b64encode
from pathlib import Path

import keyring
from PySide6.QtCore import QSettings

from app.models.config import AuthMethod
from app.utils.constants import APP_NAME
from app.utils.paths import ensure_private_file

LOGGER = logging.getLogger(__name__)


class CredentialsStore:
    """Stores sensitive credentials in keyring with a Linux-friendly fallback."""

    PASSWORD_KEY = "password"
    KEY_PATH_KEY = "key_path"
    FALLBACK_GROUP = "credentials_fallback"

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._fallback_file = ensure_private_file(Path.home() / ".config" / APP_NAME / "credentials.ini")

    def save(self, host: str, username: str, auth_method: AuthMethod, password: str = "", key_path: str = "") -> None:
        service_name = self._service_name(host, username)
        try:
            if auth_method == AuthMethod.PASSWORD:
                keyring.set_password(service_name, self.PASSWORD_KEY, password)
                self._safe_delete(service_name, self.KEY_PATH_KEY)
            else:
                keyring.set_password(service_name, self.KEY_PATH_KEY, key_path)
                self._safe_delete(service_name, self.PASSWORD_KEY)
            self._clear_fallback()
            self._settings.setValue("credentials_backend", "keyring")
            return
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to use keyring, switching to fallback storage: %s", exc)

        self._save_fallback(host, username, auth_method, password, key_path)

    def load_password(self, host: str, username: str) -> str:
        service_name = self._service_name(host, username)
        try:
            value = keyring.get_password(service_name, self.PASSWORD_KEY)
            if value:
                return value
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to read password from keyring: %s", exc)
        return self._load_fallback_value(self.PASSWORD_KEY)

    def load_key_path(self, host: str, username: str) -> str:
        service_name = self._service_name(host, username)
        try:
            value = keyring.get_password(service_name, self.KEY_PATH_KEY)
            if value:
                return value
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to read SSH key path from keyring: %s", exc)
        return self._load_fallback_value(self.KEY_PATH_KEY)

    def delete(self, host: str, username: str) -> None:
        service_name = self._service_name(host, username)
        for key in (self.PASSWORD_KEY, self.KEY_PATH_KEY):
            self._safe_delete(service_name, key)
        self._clear_fallback()

    def clear_all(self) -> None:
        fallback_settings = QSettings(str(self._fallback_file), QSettings.IniFormat)
        host = str(fallback_settings.value("host", ""))
        username = str(fallback_settings.value("username", ""))
        if host and username:
            self.delete(host, username)
            return
        self._clear_fallback()

    def backend_name(self) -> str:
        return str(self._settings.value("credentials_backend", "keyring"))

    def _save_fallback(self, host: str, username: str, auth_method: AuthMethod, password: str, key_path: str) -> None:
        # Fallback keeps data out of JSON files and restricts file permissions to the current user.
        fallback_settings = QSettings(str(self._fallback_file), QSettings.IniFormat)
        fallback_settings.setValue("host", host)
        fallback_settings.setValue("username", username)
        fallback_settings.setValue("auth_method", auth_method.value)
        fallback_settings.setValue(self.PASSWORD_KEY, self._encode(password))
        fallback_settings.setValue(self.KEY_PATH_KEY, self._encode(key_path))
        fallback_settings.sync()
        self._settings.setValue("credentials_backend", "fallback-file")

    def _load_fallback_value(self, key: str) -> str:
        fallback_settings = QSettings(str(self._fallback_file), QSettings.IniFormat)
        return self._decode(str(fallback_settings.value(key, "")))

    def _clear_fallback(self) -> None:
        fallback_settings = QSettings(str(self._fallback_file), QSettings.IniFormat)
        fallback_settings.clear()
        fallback_settings.sync()

    @staticmethod
    def _safe_delete(service_name: str, key: str) -> None:
        try:
            keyring.delete_password(service_name, key)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _service_name(host: str, username: str) -> str:
        return f"{APP_NAME}:{host}:{username}"

    @staticmethod
    def _encode(value: str) -> str:
        return b64encode(value.encode("utf-8")).decode("ascii") if value else ""

    @staticmethod
    def _decode(value: str) -> str:
        return b64decode(value.encode("ascii")).decode("utf-8") if value else ""
