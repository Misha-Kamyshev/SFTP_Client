from __future__ import annotations

import os
import subprocess
import sys
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class ThemeManager(QObject):
    """Detects the Linux system theme and applies an application-wide Qt theme."""

    theme_changed = Signal(str)

    COLORS: dict[ThemeMode, dict[str, str]] = {
        ThemeMode.LIGHT: {
            "window": "#f5f7fb",
            "surface": "#ffffff",
            "surface_alt": "#eef2f8",
            "border": "#cfd8e6",
            "border_strong": "#aab7cc",
            "text": "#1f2937",
            "text_muted": "#5b6576",
            "placeholder": "#7f8aa0",
            "button": "#1f5eff",
            "button_hover": "#1a4fd8",
            "button_pressed": "#163fa9",
            "button_text": "#ffffff",
            "button_disabled": "#9fb1d0",
            "accent": "#1f5eff",
            "accent_text": "#ffffff",
            "selection": "#d9e6ff",
            "selection_text": "#10203a",
            "error": "#b42318",
            "success": "#0b6b44",
            "menu_bg": "#ffffff",
        },
        ThemeMode.DARK: {
            "window": "#20252d",
            "surface": "#2b2f36",
            "surface_alt": "#343a45",
            "border": "#464f5d",
            "border_strong": "#667284",
            "text": "#eef2f7",
            "text_muted": "#c0cad8",
            "placeholder": "#8d97a8",
            "button": "#4f8cff",
            "button_hover": "#6a9dff",
            "button_pressed": "#3f72d4",
            "button_text": "#f8fbff",
            "button_disabled": "#566273",
            "accent": "#4f8cff",
            "accent_text": "#08111d",
            "selection": "#37527d",
            "selection_text": "#f3f7ff",
            "error": "#ff8f86",
            "success": "#68d8a3",
            "menu_bg": "#2b2f36",
        },
    }

    def __init__(self, app: QApplication, poll_interval_ms: int = 3000) -> None:
        super().__init__(app)
        self._app = app
        self._timer = QTimer(self)
        self._timer.setInterval(poll_interval_ms)
        self._timer.timeout.connect(self._check_for_theme_change)
        self._current_theme = ThemeMode.LIGHT

    def detect_system_theme(self) -> ThemeMode:
        if sys.platform != "linux":
            return ThemeMode.LIGHT

        gtk_theme = os.environ.get("GTK_THEME", "").strip().lower()
        if "dark" in gtk_theme:
            return ThemeMode.DARK
        if "light" in gtk_theme:
            return ThemeMode.LIGHT

        color_scheme = self._read_gsettings("org.gnome.desktop.interface", "color-scheme")
        if "dark" in color_scheme:
            return ThemeMode.DARK
        if "light" in color_scheme:
            return ThemeMode.LIGHT

        gtk_theme_name = self._read_gsettings("org.gnome.desktop.interface", "gtk-theme")
        if "dark" in gtk_theme_name:
            return ThemeMode.DARK

        return ThemeMode.LIGHT

    def apply_theme(self, app: QApplication | None = None, theme: ThemeMode | None = None) -> ThemeMode:
        target_app = app or self._app
        selected_theme = theme or self.detect_system_theme()
        colors = self.COLORS[selected_theme]

        palette = self._build_palette(colors)
        target_app.setPalette(palette)
        target_app.setStyleSheet(self._build_stylesheet(colors))
        target_app.setProperty("theme_mode", selected_theme.value)
        target_app.setProperty("theme_colors", colors)

        self._current_theme = selected_theme
        self.theme_changed.emit(selected_theme.value)
        return selected_theme

    def listen_for_theme_changes(self) -> None:
        self.apply_theme()
        self._timer.start()

    def _check_for_theme_change(self) -> None:
        detected = self.detect_system_theme()
        if detected != self._current_theme:
            self.apply_theme(theme=detected)

    @staticmethod
    def _read_gsettings(schema: str, key: str) -> str:
        try:
            result = subprocess.run(
                ["gsettings", "get", schema, key],
                check=True,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip().strip("'").lower()

    @staticmethod
    def _build_palette(colors: dict[str, str]) -> QPalette:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(colors["window"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(colors["surface"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["surface_alt"]))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["surface"]))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(colors["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(colors["button"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["button_text"]))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(colors["accent_text"]))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors["accent_text"]))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors["placeholder"]))
        palette.setColor(QPalette.ColorRole.Link, QColor(colors["accent"]))
        palette.setColor(QPalette.ColorRole.LinkVisited, QColor(colors["button_hover"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(colors["text_muted"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(colors["text_muted"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(colors["text_muted"]))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.PlaceholderText, QColor(colors["placeholder"]))
        return palette

    @staticmethod
    def _build_stylesheet(colors: dict[str, str]) -> str:
        return f"""
        QWidget {{
            font-family: "Noto Sans", "DejaVu Sans", sans-serif;
            font-size: 13px;
            color: {colors["text"]};
        }}
        QMainWindow, QDialog, QWidget#centralWidget {{
            background: {colors["window"]};
        }}
        QLabel#pageTitle {{
            font-size: 20px;
            font-weight: 700;
            color: {colors["text"]};
        }}
        QWidget#loginCard {{
            background: {colors["surface"]};
            border: 1px solid {colors["border"]};
            border-radius: 14px;
        }}
        QGroupBox {{
            font-weight: 600;
            background: {colors["surface"]};
            border: 1px solid {colors["border"]};
            border-radius: 10px;
            margin-top: 12px;
            padding: 14px 12px 12px 12px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 12px;
            padding: 0 4px;
            color: {colors["text"]};
        }}
        QLineEdit, QTextEdit, QListWidget, QListView, QTreeView {{
            background: {colors["surface"]};
            color: {colors["text"]};
            border: 1px solid {colors["border"]};
            border-radius: 8px;
            padding: 6px 8px;
            selection-background-color: {colors["selection"]};
            selection-color: {colors["selection_text"]};
        }}
        QLineEdit[readOnly="true"] {{
            background: {colors["surface_alt"]};
            color: {colors["text"]};
        }}
        QLineEdit:focus, QTextEdit:focus, QListWidget:focus, QListView:focus, QTreeView:focus {{
            border: 1px solid {colors["accent"]};
        }}
        QLineEdit::placeholder {{
            color: {colors["placeholder"]};
        }}
        QPushButton {{
            background: {colors["button"]};
            color: {colors["button_text"]};
            border: none;
            border-radius: 8px;
            padding: 8px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {colors["button_hover"]};
        }}
        QPushButton:pressed {{
            background: {colors["button_pressed"]};
        }}
        QPushButton:disabled {{
            background: {colors["button_disabled"]};
            color: {colors["text_muted"]};
        }}
        QCheckBox, QRadioButton, QLabel {{
            color: {colors["text"]};
        }}
        QMenu {{
            background: {colors["menu_bg"]};
            color: {colors["text"]};
            border: 1px solid {colors["border"]};
        }}
        QMenu::item:selected {{
            background: {colors["selection"]};
            color: {colors["selection_text"]};
        }}
        QMessageBox, QFileDialog {{
            background: {colors["window"]};
            color: {colors["text"]};
        }}
        """
