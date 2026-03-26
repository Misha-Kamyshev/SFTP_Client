from __future__ import annotations

import posixpath
import stat

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from app.services.sftp_service import SFTPService


class RemoteDirectoryDialog(QDialog):
    def __init__(self, sftp_service: SFTPService, initial_path: str = "/", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sftp_service = sftp_service
        self._current_path = initial_path or "/"
        self._selected_path = initial_path or "/"
        self._folder_icon: QIcon | None = None
        self.setWindowTitle("Выбор директории на сервере")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
        )
        self._build_ui()
        self._connect_signals()
        self.setFixedSize(520, 420)
        self._load_directory(self._current_path)

    def selected_path(self) -> str:
        return self._selected_path

    def _build_ui(self) -> None:
        self._folder_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self.path_label = QLabel()
        self.list_widget = QListWidget()
        self.up_button = QPushButton("Вверх")
        self.new_folder_button = QPushButton("Новая папка")
        self.select_button = QPushButton("Выбрать")
        self.cancel_button = QPushButton("Отмена")

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.up_button)
        buttons_layout.addWidget(self.new_folder_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.select_button)
        buttons_layout.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addWidget(self.list_widget)
        layout.addLayout(buttons_layout)

    def _connect_signals(self) -> None:
        self.up_button.clicked.connect(self._go_up)
        self.new_folder_button.clicked.connect(self._create_folder)
        self.select_button.clicked.connect(self._accept_selection)
        self.cancel_button.clicked.connect(self.reject)
        self.list_widget.itemDoubleClicked.connect(self._open_item)
        self.list_widget.itemClicked.connect(self._select_item)

    def _load_directory(self, path: str, highlight_path: str | None = None) -> None:
        try:
            normalized = self._sftp_service.normalize(path)
            items = self._sftp_service.list_directory(normalized)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть директорию:\n{exc}")
            return

        self._current_path = normalized
        self._selected_path = normalized
        self.path_label.setText(f"Текущий путь: {normalized}")
        self.list_widget.clear()
        for item in items:
            if stat.S_ISDIR(item.st_mode):
                item_path = posixpath.join(normalized.rstrip("/") or "/", item.filename)
                widget_item = QListWidgetItem(self._folder_icon, item.filename)
                widget_item.setData(Qt.ItemDataRole.UserRole, item_path)
                self.list_widget.addItem(widget_item)
                if highlight_path and item_path == highlight_path:
                    self.list_widget.setCurrentItem(widget_item)
                    self.list_widget.scrollToItem(widget_item)

    def _open_item(self, item: QListWidgetItem) -> None:
        next_path = str(item.data(Qt.ItemDataRole.UserRole))
        self._load_directory(next_path)

    def _select_item(self, item: QListWidgetItem) -> None:
        self._selected_path = str(item.data(Qt.ItemDataRole.UserRole))

    def _go_up(self) -> None:
        if self._current_path == "/":
            return
        parent = posixpath.dirname(self._current_path.rstrip("/")) or "/"
        self._load_directory(parent)

    def _create_folder(self) -> None:
        folder_name, accepted = QInputDialog.getText(self, "Новая папка", "Имя новой папки:")
        if not accepted:
            return

        normalized_name = folder_name.strip()
        if not self._is_valid_directory_name(normalized_name):
            QMessageBox.warning(
                self,
                "Некорректное имя",
                "Введите непустое имя папки. Нельзя использовать '.', '..' или символ '/'.",
            )
            return

        try:
            created_path = self._sftp_service.create_directory(self._current_path, normalized_name)
        except FileExistsError as exc:
            QMessageBox.warning(self, "Конфликт имени", str(exc))
            return
        except PermissionError as exc:
            QMessageBox.critical(self, "Недостаточно прав", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось создать директорию:\n{exc}")
            return

        self._load_directory(self._current_path, highlight_path=created_path)
        self._selected_path = created_path

    def _accept_selection(self) -> None:
        current_item = self.list_widget.currentItem()
        if current_item is not None:
            self._selected_path = str(current_item.data(Qt.ItemDataRole.UserRole))
        else:
            self._selected_path = self._current_path
        self.accept()

    @staticmethod
    def _is_valid_directory_name(value: str) -> bool:
        return bool(value and value not in {".", ".."} and "/" not in value)
