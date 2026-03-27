from __future__ import annotations

import errno
import hashlib
import logging
import posixpath
import socket
import stat
from collections.abc import Callable
from pathlib import Path

import paramiko
from paramiko.ssh_exception import PasswordRequiredException

from app.models.config import DEFAULT_SFTP_PORT, ConnectionRequest
from app.utils.ssh_keys import find_ssh_keys


LOGGER = logging.getLogger(__name__)


class NetworkConnectivityError(ConnectionError):
    """Raised when there is no usable network connectivity."""


class ServerUnavailableError(ConnectionError):
    """Raised when the target SSH/SFTP server is not reachable."""


class SSHKeyLoadError(ValueError):
    """Raised when the SSH private key cannot be loaded."""


class AuthorizationError(ConnectionError):
    """Raised when authentication fails."""


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

    def connect(self, request: ConnectionRequest, log_callback: Callable[[str], None] | None = None) -> ConnectionRequest:
        self.disconnect()

        port = self.normalize_port(request.port)
        network_ok, network_reason = self.check_network_connectivity(request.host, port)
        if not network_ok:
            if network_reason == "no_network":
                raise NetworkConnectivityError("Нет подключения к сети. Проверьте интернет-соединение.")
            raise ServerUnavailableError(self._network_reason_to_message(network_reason))

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        if request.auth_method.value == "password":
            self._connect_with_password(client, request, port)
            actual_request = request
        else:
            actual_request = self._connect_with_ssh_key(client, request, port, log_callback)

        self._last_request = actual_request
        self._client = client
        self._sftp = client.open_sftp()
        return actual_request

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

    def _connect_with_ssh_key(
        self,
        client: paramiko.SSHClient,
        request: ConnectionRequest,
        port: int,
        log_callback: Callable[[str], None] | None = None,
    ) -> ConnectionRequest:
        candidates = [Path(request.key_path).expanduser()] if request.key_path else find_ssh_keys(limit=3)
        if not candidates:
            raise SSHKeyLoadError("SSH-ключи не найдены в директории ~/.ssh")

        last_auth_error: AuthorizationError | None = None
        last_key_error: SSHKeyLoadError | None = None
        for key_path in candidates:
            if log_callback is not None:
                log_callback(f"Пробуем ключ: {key_path.name}")
            try:
                key = self._load_private_key(str(key_path))
                self._connect_client(
                    client,
                    hostname=request.host,
                    port=port,
                    username=request.username,
                    pkey=key,
                )
                return ConnectionRequest(
                    host=request.host,
                    username=request.username,
                    auth_method=request.auth_method,
                    port=request.port,
                    password=request.password,
                    key_path=str(key_path),
                )
            except SSHKeyLoadError as exc:
                last_key_error = exc
                LOGGER.warning("SSH key loading failed for %s: %s", key_path, exc)
            except AuthorizationError as exc:
                last_auth_error = exc
                LOGGER.warning(
                    "SSH key authentication failed for %s@%s:%s with key %s",
                    request.username,
                    request.host,
                    port,
                    key_path,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Unexpected SSH key connection failure for %s", key_path)
                raise

        if request.key_path and last_key_error is not None:
            raise last_key_error
        if last_auth_error is not None:
            raise last_auth_error
        if last_key_error is not None:
            raise last_key_error
        raise AuthorizationError("Ошибка аутентификации: проверьте логин и SSH-ключ.")

    def _connect_with_password(self, client: paramiko.SSHClient, request: ConnectionRequest, port: int) -> None:
        self._connect_client(
            client,
            hostname=request.host,
            port=port,
            username=request.username,
            password=request.password,
        )

    def _connect_client(self, client: paramiko.SSHClient, **kwargs: object) -> None:
        try:
            client.connect(
                banner_timeout=15,
                auth_timeout=15,
                timeout=15,
                look_for_keys=False,
                allow_agent=False,
                **kwargs,
            )
        except paramiko.AuthenticationException as exc:
            LOGGER.warning(
                "Authentication failed for %s@%s:%s",
                kwargs.get("username"),
                kwargs.get("hostname"),
                kwargs.get("port"),
            )
            raise AuthorizationError("Ошибка аутентификации: проверьте логин, пароль или SSH-ключ.") from exc
        except socket.timeout as exc:
            LOGGER.warning("Timed out connecting to %s:%s", kwargs.get("hostname"), kwargs.get("port"))
            raise ServerUnavailableError("Сервер недоступен по указанному адресу или порту.") from exc
        except ConnectionRefusedError as exc:
            LOGGER.warning("Connection refused for %s:%s", kwargs.get("hostname"), kwargs.get("port"))
            raise ServerUnavailableError("Сервер недоступен по указанному адресу или порту.") from exc
        except socket.gaierror as exc:
            LOGGER.warning("DNS resolution failed for %s: %s", kwargs.get("hostname"), exc)
            raise ServerUnavailableError("Сервер недоступен по указанному адресу или порту.") from exc
        except OSError as exc:
            reason = self._classify_network_os_error(exc)
            if reason == "no_network":
                LOGGER.warning(
                    "No network connectivity while connecting to %s:%s: %s",
                    kwargs.get("hostname"),
                    kwargs.get("port"),
                    exc,
                )
                raise NetworkConnectivityError("Нет подключения к сети. Проверьте интернет-соединение.") from exc
            LOGGER.warning("Server connection failed for %s:%s: %s", kwargs.get("hostname"), kwargs.get("port"), exc)
            raise ServerUnavailableError("Сервер недоступен по указанному адресу или порту.") from exc

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
        try:
            sftp.utime(remote_path, (int(local_stat.st_atime), int(local_stat.st_mtime)))
        except OSError:
            # Some SFTP servers do not allow updating directory timestamps.
            pass

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
                raise SSHKeyLoadError(
                    "Не удалось загрузить SSH-ключ. Ключ защищён passphrase, ввод passphrase в текущей версии не поддерживается."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise SSHKeyLoadError(
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
                raise SSHKeyLoadError(
                    "Не удалось загрузить SSH-ключ. Ключ защищён passphrase, ввод passphrase в текущей версии не поддерживается."
                ) from exc
            except Exception:  # noqa: BLE001
                continue
        raise SSHKeyLoadError(
            "Не удалось загрузить SSH-ключ. Проверьте формат файла или passphrase. Поддерживаются Ed25519, ECDSA и RSA."
        )

    @staticmethod
    def check_network_connectivity(host: str, port: int) -> tuple[bool, str | None]:
        try:
            if not SFTPService._has_general_network_connectivity():
                LOGGER.warning("General network connectivity check failed before connecting to %s:%s", host, port)
                return False, "no_network"
            socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            LOGGER.warning("DNS error for %s:%s: %s", host, port, exc)
            return False, "dns_error"
        except OSError as exc:
            reason = SFTPService._classify_network_os_error(exc)
            LOGGER.warning("Network precheck failed for %s:%s: %s", host, port, exc)
            return False, reason

        try:
            with socket.create_connection((host, port), timeout=5):
                return True, None
        except socket.timeout as exc:
            LOGGER.warning("Server timeout during precheck for %s:%s: %s", host, port, exc)
            return False, "timeout"
        except ConnectionRefusedError as exc:
            LOGGER.warning("Connection refused during precheck for %s:%s: %s", host, port, exc)
            return False, "refused"
        except socket.gaierror as exc:
            LOGGER.warning("DNS error during precheck for %s:%s: %s", host, port, exc)
            return False, "dns_error"
        except OSError as exc:
            reason = SFTPService._classify_network_os_error(exc)
            LOGGER.warning("Socket precheck failed for %s:%s: %s", host, port, exc)
            return False, reason

    @staticmethod
    def _has_general_network_connectivity() -> bool:
        probes = (("1.1.1.1", 53), ("8.8.8.8", 53))
        last_error: OSError | None = None
        for probe_host, probe_port in probes:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            try:
                sock.connect((probe_host, probe_port))
                return True
            except OSError as exc:
                last_error = exc
                if SFTPService._classify_network_os_error(exc) != "no_network":
                    return True
            finally:
                sock.close()
        if last_error is not None:
            LOGGER.warning("General network probes failed: %s", last_error)
        return False

    @staticmethod
    def _classify_network_os_error(exc: OSError) -> str:
        if exc.errno in {
            errno.ENETUNREACH,
            errno.ENETDOWN,
            errno.EHOSTUNREACH,
            errno.ENONET,
            errno.EADDRNOTAVAIL,
        }:
            return "no_network"
        return "server_unreachable"

    @staticmethod
    def _network_reason_to_message(reason: str | None) -> str:
        if reason == "no_network":
            return "Нет подключения к сети. Проверьте интернет-соединение."
        if reason == "dns_error":
            return "Сервер недоступен: не удалось разрешить адрес."
        if reason == "timeout":
            return "Сервер недоступен по указанному адресу или порту."
        if reason == "refused":
            return "Сервер недоступен по указанному адресу или порту."
        return "Сервер недоступен по указанному адресу или порту."

    @staticmethod
    def user_friendly_error(exc: Exception) -> str:
        if isinstance(exc, FileNotFoundError):
            return str(exc)
        if isinstance(exc, (SSHKeyLoadError, ValueError)):
            return str(exc)
        if isinstance(exc, NetworkConnectivityError):
            return str(exc)
        if isinstance(exc, ServerUnavailableError):
            return str(exc)
        if isinstance(exc, AuthorizationError):
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
    def error_category(exc: Exception) -> str:
        if isinstance(exc, NetworkConnectivityError):
            return "network"
        if isinstance(exc, ServerUnavailableError):
            return "server"
        if isinstance(exc, AuthorizationError):
            return "authorization"
        if isinstance(exc, SSHKeyLoadError):
            return "ssh_key"
        if isinstance(exc, FileNotFoundError):
            return "ssh_key"
        if isinstance(exc, ValueError):
            return "validation"
        return "other"

    @staticmethod
    def should_retry_connection_error(exc: Exception) -> bool:
        return SFTPService.error_category(exc) in {"network", "server"}

    @staticmethod
    def retry_reason(exc: Exception) -> str:
        if isinstance(exc, NetworkConnectivityError):
            return "no_network"
        if isinstance(exc, ServerUnavailableError):
            return "server_unreachable"
        return SFTPService.error_category(exc)

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
