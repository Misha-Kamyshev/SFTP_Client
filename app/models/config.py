from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AuthMethod(StrEnum):
    PASSWORD = "password"
    SSH_KEY = "ssh_key"


DEFAULT_SFTP_PORT = 22


@dataclass(slots=True)
class ConnectionRequest:
    host: str
    username: str
    auth_method: AuthMethod
    port: int = DEFAULT_SFTP_PORT
    password: str = ""
    key_path: str = ""


@dataclass(slots=True)
class AppSettings:
    host: str = ""
    port: int = DEFAULT_SFTP_PORT
    username: str = ""
    auth_method: AuthMethod = AuthMethod.PASSWORD
    key_path: str = ""
    local_dir: str = ""
    remote_dir: str = ""
    sync_was_running: bool = False
    autostart_enabled: bool = True
