from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.models.config import DEFAULT_SFTP_PORT, AppSettings, AuthMethod, ConnectionRequest
from app.services.sftp_service import SFTPService
from app.utils.file_dialogs import resolve_initial_file_directory


class LoginPage(QWidget):
    connect_requested = Signal(ConnectionRequest)

    def __init__(self) -> None:
        super().__init__()
        self._build_ui()
        self._connect_signals()

    def set_form_data(self, settings: AppSettings, password: str) -> None:
        self.host_edit.setText(settings.host)
        self.port_edit.setText("" if settings.port == DEFAULT_SFTP_PORT else str(settings.port))
        self.username_edit.setText(settings.username)
        self.password_radio.setChecked(settings.auth_method == AuthMethod.PASSWORD)
        self.key_radio.setChecked(settings.auth_method == AuthMethod.SSH_KEY)
        self.password_edit.setText(password)
        self.key_path_edit.setText(settings.key_path)
        self._update_auth_mode()

    def current_request(self) -> ConnectionRequest:
        port_text = self.port_edit.text().strip()
        return ConnectionRequest(
            host=self.host_edit.text().strip(),
            username=self.username_edit.text().strip(),
            auth_method=AuthMethod.PASSWORD if self.password_radio.isChecked() else AuthMethod.SSH_KEY,
            port=SFTPService.normalize_port(port_text or DEFAULT_SFTP_PORT),
            password=self.password_edit.text(),
            key_path=self.key_path_edit.text().strip(),
        )

    def show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Ошибка подключения", message)

    def set_busy(self, busy: bool) -> None:
        self.connect_button.setEnabled(not busy)

    def _build_ui(self) -> None:
        title = QLabel("Подключение к SFTP-серверу")
        title.setObjectName("pageTitle")

        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("Например, 192.168.1.10")
        form_layout.addRow("IP-адрес сервера", self.host_edit)

        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText(str(DEFAULT_SFTP_PORT))
        self.port_edit.setValidator(QIntValidator(1, 65535, self))
        form_layout.addRow("Порт SFTP", self.port_edit)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("Имя пользователя")
        form_layout.addRow("Пользователь", self.username_edit)

        auth_group = QGroupBox("Авторизация")
        auth_layout = QVBoxLayout(auth_group)
        self.password_radio = QRadioButton("По паролю")
        self.key_radio = QRadioButton("По SSH-ключу")
        self.password_radio.setChecked(True)
        auth_layout.addWidget(self.password_radio)
        auth_layout.addWidget(self.key_radio)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.key_path_edit = QLineEdit()
        self.key_path_edit.setPlaceholderText("Если пусто, будут проверены ключи из ~/.ssh")
        self.key_browse_button = QPushButton("...")
        self.key_browse_button.setFixedWidth(36)
        key_layout = QHBoxLayout()
        key_layout.addWidget(self.key_path_edit)
        key_layout.addWidget(self.key_browse_button)
        key_container = QWidget()
        key_container.setLayout(key_layout)

        form_layout.addRow(auth_group)
        form_layout.addRow("Пароль", self.password_edit)
        form_layout.addRow("SSH-ключ", key_container)

        self.connect_button = QPushButton("Подключиться")
        self.connect_button.setMinimumHeight(40)

        card = QWidget()
        card.setObjectName("loginCard")
        card.setMinimumWidth(260)
        card.setMaximumWidth(520)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 28, 28, 28)
        card_layout.setSpacing(18)
        card_layout.addWidget(title)
        card_layout.addLayout(form_layout)
        card_layout.addWidget(self.connect_button)

        row_layout = QHBoxLayout()
        row_layout.setContentsMargins(20, 0, 20, 0)
        row_layout.addStretch()
        row_layout.addWidget(card)
        row_layout.addStretch()

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addStretch()
        root_layout.addLayout(row_layout)
        root_layout.addStretch()

    def _connect_signals(self) -> None:
        self.password_radio.toggled.connect(self._update_auth_mode)
        self.key_radio.toggled.connect(self._update_auth_mode)
        self.key_browse_button.clicked.connect(self._choose_key_file)
        self.connect_button.clicked.connect(self._submit)

    def _update_auth_mode(self) -> None:
        is_password = self.password_radio.isChecked()
        self.password_edit.setEnabled(is_password)
        self.key_path_edit.setEnabled(not is_password)
        self.key_browse_button.setEnabled(not is_password)

    def _choose_key_file(self) -> None:
        initial_dir = resolve_initial_file_directory(self.key_path_edit.text().strip(), Path.home() / ".ssh")
        selected, _ = QFileDialog.getOpenFileName(self, "Выберите SSH-ключ", initial_dir)
        if selected:
            self.key_path_edit.setText(selected)

    def _submit(self) -> None:
        try:
            request = self.current_request()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        if not request.host:
            self.show_error("Укажите IP-адрес или DNS-имя сервера.")
            return
        if not request.username:
            self.show_error("Укажите имя пользователя.")
            return
        if request.auth_method == AuthMethod.PASSWORD and not request.password:
            self.show_error("Введите пароль.")
            return
        self.connect_requested.emit(request)
