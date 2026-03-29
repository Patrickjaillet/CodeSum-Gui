"""
config_dialog.py — CodeSum GUI configuration dialogs (Phase 2).

  - SettingsDialog        : OpenAI API key + LLM model
  - SelectionConfigDialog : CRUD configs nommées via summary_utils
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QComboBox, QFrame,
)

from .. import config as app_config
from .. import summary_utils

PRESET_MODELS = [
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo",
]

_DLG_QSS = """
QDialog { background-color: #282c34; color: #abb2bf; }
QLabel  { color: #abb2bf; }
QLineEdit, QComboBox {
    background-color: #21252b; color: #e5c07b;
    border: 1px solid #3e4451; border-radius: 4px;
    padding: 6px 8px; font-size: 13px;
}
QLineEdit:focus, QComboBox:focus { border-color: #61afef; }
QPushButton {
    background-color: #3e4451; color: #abb2bf;
    border: 1px solid #4b5263; border-radius: 4px;
    padding: 6px 14px; font-size: 13px;
}
QPushButton:hover { background-color: #4b5263; color: #e5c07b; }
QPushButton:pressed { background-color: #528bff; color: #fff; }
QListWidget {
    background-color: #21252b; color: #abb2bf;
    border: 1px solid #3e4451; border-radius: 4px;
}
QListWidget::item { padding: 5px 8px; }
QListWidget::item:selected { background-color: #3e4451; color: #e5c07b; }
QListWidget::item:hover:!selected { background-color: #2c313a; }
QFrame[frameShape="4"] { color: #3e4451; }
"""

def _sep():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setStyleSheet("color: #3e4451;")
    return f


                                                                              
                
                                                                              

class SettingsDialog(QDialog):
    """OpenAI API key + LLM model settings."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙  CodeSum Settings")
        self.setMinimumWidth(520)
        self.setStyleSheet(_DLG_QSS)

        api_key, llm_model = app_config.load_config()

        v = QVBoxLayout(self)
        v.setSpacing(14)
        v.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Settings")
        title.setFont(QFont("sans-serif", 16, QFont.Weight.Bold))
        title.setStyleSheet("color: #e5c07b;")
        v.addWidget(title)
        v.addWidget(_sep())

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

                 
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("sk-…  (empty = disable AI)")
        if api_key:
            self._key.setText(api_key)

        key_row = QHBoxLayout()
        key_row.addWidget(self._key)
        eye = QPushButton("👁")
        eye.setFixedWidth(36)
        eye.setCheckable(True)
        eye.toggled.connect(lambda v: self._key.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password))
        key_row.addWidget(eye)
        form.addRow("OpenAI API Key:", key_row)

                
        self._model = QComboBox()
        self._model.setEditable(True)
        for m in PRESET_MODELS:
            self._model.addItem(m)
        if llm_model in PRESET_MODELS:
            self._model.setCurrentText(llm_model)
        else:
            self._model.insertItem(0, llm_model)
            self._model.setCurrentIndex(0)
        form.addRow("LLM Model:", self._model)

        v.addLayout(form)

        info = QLabel(f"📄 Config: {app_config.CONFIG_FILE}")
        info.setStyleSheet("color: #5c6370; font-size: 11px;")
        info.setWordWrap(True)
        v.addWidget(info)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _save(self):
        key   = self._key.text().strip() or None
        model = self._model.currentText().strip() or app_config.DEFAULT_LLM_MODEL
        if app_config.save_config(key, model):
            self.accept()
        else:
            QMessageBox.warning(self, "Error", "Unable to save configuration.")


                                                                              
                       
                                                                              

class SelectionConfigDialog(QDialog):
    """
    CRUD management of named selection configurations.
    Utilise les fonctions de summary_utils (read/write/save/load/delete/rename).
    """

    config_loaded = Signal(list, list)

    def __init__(self, base_dir: Path, cur_sel: List[str], cur_comp: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("📂  Selection configurations")
        self.setMinimumSize(500, 400)
        self.setStyleSheet(_DLG_QSS)

        self._base_dir   = Path(base_dir).resolve()
        self._cur_sel    = list(cur_sel)
        self._cur_comp   = list(cur_comp)

        v = QVBoxLayout(self)
        v.setSpacing(12)
        v.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Selection configurations")
        title.setFont(QFont("sans-serif", 15, QFont.Weight.Bold))
        title.setStyleSheet("color: #e5c07b;")
        v.addWidget(title)

        info = QLabel(
            f"Current selection : {len(cur_sel)} files  ·  "
            f"{len(cur_comp)} compressed files ★"
        )
        info.setStyleSheet("color: #5c6370; font-size: 12px;")
        v.addWidget(info)
        v.addWidget(_sep())

        self._list = QListWidget()
        v.addWidget(self._list)
        self._populate()

                          
        row = QHBoxLayout()
        row.setSpacing(8)
        for label, slot in [
            ("💾  Save", self._save),
            ("📥  Load",     self._load),
            ("✏  Rename",    self._rename),
            ("🗑  Delete",   self._delete),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        v.addLayout(row)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        v.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignRight)

                                                                               

    def _populate(self):
        self._list.clear()
        configs = summary_utils.read_selection_configs(self._base_dir)
        for name in sorted(configs):
            entry   = configs[name]
            n_files = len(entry.get("selected_files", []))
            n_comp  = len(entry.get("compressed_files", []))
            item    = QListWidgetItem(f"{name}   ({n_files} files, {n_comp} ★)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)

    def _current_name(self) -> str | None:
        it = self._list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

                                                                               

    def _save(self):
        name, ok = QInputDialog.getText(self, "Save", "Configuration name:")
        if not ok or not (name := name.strip()):
            return
        configs = summary_utils.read_selection_configs(self._base_dir)
        if name in configs:
            r = QMessageBox.question(
                self, "Confirm", f"'{name}' already exists. Overwrite?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if r != QMessageBox.StandardButton.Yes:
                return
        summary_utils.save_selection_config(
            name, self._cur_sel, self._cur_comp, self._base_dir
        )
        self._populate()

    def _load(self):
        name = self._current_name()
        if not name:
            QMessageBox.information(self, "Info", "Please select a configuration.")
            return
        result = summary_utils.load_selection_config(name, self._base_dir)
        if result:
            sel, comp = result
            self.config_loaded.emit(sel, comp)
            self.accept()
        else:
            QMessageBox.warning(self, "Error", f"Configuration '{name}' not found.")

    def _rename(self):
        name = self._current_name()
        if not name:
            QMessageBox.information(self, "Info", "Please select a configuration.")
            return
        new_name, ok = QInputDialog.getText(self, "Rename", "New name:", text=name)
        if not ok or not (new_name := new_name.strip()) or new_name == name:
            return
        if not summary_utils.rename_selection_config(name, new_name, self._base_dir):
            QMessageBox.warning(self, "Error",
                                f"Unable to rename (maybe '{new_name}' already exists).")
        self._populate()

    def _delete(self):
        name = self._current_name()
        if not name:
            QMessageBox.information(self, "Info", "Please select a configuration.")
            return
        r = QMessageBox.question(
            self, "Confirm", f"Delete configuration '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            summary_utils.delete_selection_config(name, self._base_dir)
            self._populate()
