from __future__ import annotations

import hashlib
import posixpath
import socket
import stat
from pathlib import Path

import paramiko
from paramiko.ssh_exception import PasswordRequiredException

from app.models.config import DEFAULT_SFTP_PORT, ConnectionRequest


class SFTPService:
    """Thin wrapper around Paramiko with reconnect support."""

    def __init__(self) -> None:
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._last_request: ConnectionRequest | None = None

    @property
    def is_connected(self) -> bool:
        transport = self._client.get_transport() if self._client is not None else None
        return bool(transport and transport.is_active() and self._sftp)

    def connect(self, request: ConnectionRequest) -> None:
        self.disconnect()
        self._last_request = request

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if request.auth_method.value == "password":
            client.connect(
                hostname=request.host,
                port=self.normalize_port(request.port),
                username=request.username,
                password=request.password,
                banner_timeout=15,
                auth_timeout=15,
                timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )
        else:
            key = self._load_private_key(request.key_path)
            client.connect(
                hostname=request.host,
                port=self.normalize_port(request.port),
                username=request.username,
                pkey=key,
                banner_timeout=15,
                auth_timeout=15,
                timeout=15,
                look_for_keys=False,
                allow_agent=False,
            )

        self._client = client
        self._sftp = client.open_sftp()

    def reconnect(self) -> None:
        if not self._last_request:
            raise RuntimeError("No saved connection parameters to reconnect.")
        self.connect(self._last_request)

    def disconnect(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._client is not None:
            self._client.close()
            self._client = None

    def list_directory(self, remote_path: str) -> list[paramiko.SFTPAttributes]:
        sftp = self._require_sftp()
        return sorted(sftp.listdir_attr(remote_path), key=lambda item: item.filename.lower())

    def normalize(self, remote_path: str) -> str:
        sftp = self._require_sftp()
        return sftp.normalize(remote_path)

    def is_dir(self, remote_path: str) -> bool:
        sftp = self._require_sftp()
        return stat.S_ISDIR(sftp.stat(remote_path).st_mode)

    def ensure_remote_dir(self, remote_path: str) -> None:
        sftp = self._require_sftp()
        normalized = posixpath.normpath(remote_path)
        parts = [part for part in normalized.split("/") if part]
        current = "/" if normalized.startswith("/") else "."
        for part in parts:
            current = posixpath.join(current, part)
            try:
                attrs = sftp.stat(current)
                if not stat.S_ISDIR(attrs.st_mode):
                    raise NotADirectoryError(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        sftp = self._require_sftp()
        parent = posixpath.dirname(remote_path) or "/"
        self.ensure_remote_dir(parent)
        sftp.put(str(local_path), remote_path)
        local_stat = local_path.stat()
        sftp.utime(remote_path, (int(local_stat.st_atime), int(local_stat.st_mtime)))

    def verify_remote_dir(self, remote_path: str) -> None:
        if not self.is_dir(remote_path):
            raise NotADirectoryError(remote_path)

    def remote_directory_matches_local(self, local_path: Path, remote_path: str) -> bool:
        sftp = self._require_sftp()
        try:
            remote_stat = sftp.stat(remote_path)
        except FileNotFoundError:
            return False

        if not stat.S_ISDIR(remote_stat.st_mode):
            return False

        local_stat = local_path.stat()
        if posixpath.basename(remote_path.rstrip("/")) != local_path.name:
            return False
        if remote_stat.st_size != local_stat.st_size:
            return False
        return int(remote_stat.st_mtime) == int(local_stat.st_mtime)

    def remote_file_matches_local(self, local_path: Path, remote_path: str) -> bool:
        sftp = self._require_sftp()
        try:
            remote_stat = sftp.stat(remote_path)
        except FileNotFoundError:
            return False

        local_stat = local_path.stat()
        if posixpath.basename(remote_path) != local_path.name:
            return False
        if remote_stat.st_size != local_stat.st_size:
            return False
        if int(remote_stat.st_mtime) == int(local_stat.st_mtime):
            return True

        local_hash = self._file_sha256(local_path)
        remote_hash = self._remote_file_sha256(remote_path)
        return local_hash == remote_hash

    def sync_directory(self, local_path: Path, remote_path: str) -> None:
        sftp = self._require_sftp()
        self.ensure_remote_dir(remote_path)
        local_stat = local_path.stat()
        sftp.utime(remote_path, (int(local_stat.st_atime), int(local_stat.st_mtime)))

    def create_directory(self, parent_path: str, directory_name: str) -> str:
        sftp = self._require_sftp()
        normalized_parent = self.normalize(parent_path)
        remote_path = posixpath.join(normalized_parent.rstrip("/") or "/", directory_name)
        try:
            sftp.mkdir(remote_path)
        except FileExistsError as exc:
            raise FileExistsError(f"Папка '{directory_name}' уже существует.") from exc
        except PermissionError as exc:
            raise PermissionError("Недостаточно прав для создания директории в текущем расположении.") from exc
        except OSError as exc:
            message = str(exc).lower()
            if "permission denied" in message:
                raise PermissionError("Недостаточно прав для создания директории в текущем расположении.") from exc
            if "failure" in message:
                raise OSError("Не удалось создать директорию на сервере.") from exc
            raise
        return remote_path

    def _require_sftp(self) -> paramiko.SFTPClient:
        if not self.is_connected or self._sftp is None:
            raise ConnectionError("SFTP connection is not active.")
        return self._sftp

    @staticmethod
    def _file_sha256(local_path: Path, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with local_path.open("rb") as source:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()

    def _remote_file_sha256(self, remote_path: str, chunk_size: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        sftp = self._require_sftp()
        with sftp.open(remote_path, "rb") as remote_file:
            while chunk := remote_file.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _load_private_key(key_path: str) -> paramiko.PKey:
        expanded = Path(key_path).expanduser()
        if not expanded.exists():
            raise FileNotFoundError(f"SSH key not found: {expanded}")
        if hasattr(paramiko.PKey, "from_path"):
            try:
                return paramiko.PKey.from_path(str(expanded))
            except PasswordRequiredException as exc:
                raise ValueError(
                    "Не удалось загрузить SSH-ключ. Ключ защищён passphrase, ввод passphrase в текущей версии не поддерживается."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise ValueError(
                    "Не удалось загрузить SSH-ключ. Проверьте формат файла. Поддерживаются Ed25519, ECDSA и RSA."
                ) from exc

        loaders: list[type[paramiko.PKey]] = [
            paramiko.Ed25519Key,
            paramiko.ECDSAKey,
            paramiko.RSAKey,
        ]
        for loader in loaders:
            try:
                return loader.from_private_key_file(str(expanded))
            except PasswordRequiredException as exc:
                raise ValueError(
                    "Не удалось загрузить SSH-ключ. Ключ защищён passphrase, ввод passphrase в текущей версии не поддерживается."
                ) from exc
            except Exception:  # noqa: BLE001
                continue
        raise ValueError(
            "Не удалось загрузить SSH-ключ. Проверьте формат файла или passphrase. Поддерживаются Ed25519, ECDSA и RSA."
        )

    @staticmethod
    def user_friendly_error(exc: Exception) -> str:
        if isinstance(exc, FileNotFoundError):
            return str(exc)
        if isinstance(exc, ValueError):
            return str(exc)
        if isinstance(exc, socket.gaierror):
            return "Не удалось разрешить адрес сервера."
        if isinstance(exc, TimeoutError):
            return "Истекло время ожидания подключения к серверу."
        if isinstance(exc, paramiko.AuthenticationException):
            return "Ошибка аутентификации: проверьте логин и способ входа."
        if isinstance(exc, paramiko.SSHException):
            return f"SSH/SFTP ошибка: {exc}"
        if isinstance(exc, ConnectionError):
            return str(exc)
        return f"Ошибка подключения: {exc}"

    @staticmethod
    def normalize_port(port: int | str | None) -> int:
        if port in (None, ""):
            return DEFAULT_SFTP_PORT
        try:
            value = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Порт SFTP должен быть числом в диапазоне 1-65535.") from exc
        if not 1 <= value <= 65535:
            raise ValueError("Порт SFTP должен быть числом в диапазоне 1-65535.")
        return value
