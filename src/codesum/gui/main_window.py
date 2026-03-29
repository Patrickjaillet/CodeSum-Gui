"""
main_window.py — CodeSum GUI main window (Phase 5).

Phase 5 features:
  - Palette Qt dark native appliquée dès le lancement (pas seulement QSS)
  - Icône d'application SVG inline (aucune dépendance externe)
  - About dialog enriched with version, author, shortcuts
  - Full global shortcuts: Ctrl+G (generate), Ctrl+R (search),
    Ctrl+O (open), Ctrl+M (configs), Ctrl+Q (quit), F1 (help)
  - Gestion propre du thème : détection dark/light du système
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QSize, QByteArray
from PySide6.QtGui import (
    QAction, QColor, QFont, QIcon, QKeySequence,
    QPalette, QPixmap, QPainter,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QProgressDialog,
    QSplitter, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from .. import config as app_config
from .. import file_utils
from .. import summary_utils
from .file_tree_widget import FileTreePanel, ROLE_ABS_PATH, ROLE_REL_PATH, COL_NAME
from .summary_panel import SummaryPanel
from .config_dialog import SettingsDialog, SelectionConfigDialog
from .workers import FileScanner, SummaryWorker

_APP_ICON_SVG = b"""
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#282c34"/>
  <rect x="8" y="14" width="30" height="4" rx="2" fill="#61afef"/>
  <rect x="8" y="22" width="22" height="4" rx="2" fill="#abb2bf"/>
  <rect x="8" y="30" width="26" height="4" rx="2" fill="#abb2bf"/>
  <rect x="8" y="38" width="18" height="4" rx="2" fill="#abb2bf"/>
  <circle cx="48" cy="44" r="12" fill="#98c379"/>
  <rect x="45" y="38" width="6" height="12" rx="3" fill="#282c34"/>
  <rect x="42" y="41" width="12" height="6" rx="3" fill="#282c34"/>
</svg>
"""


def _make_app_icon() -> QIcon:
    """Creates a QIcon from inline SVG, no external files."""
    renderer = QSvgRenderer(QByteArray(_APP_ICON_SVG))
    pixmap = QPixmap(256, 256)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def apply_dark_palette(app: QApplication) -> None:
    """
    Applique une palette Qt dark complète à l'application.
    Complète le QSS : les widgets natifs (QMessageBox, QFileDialog…)
    héritent aussi du thème sombre.
    """
    palette = QPalette()

                                                    
    bg        = QColor("#282c34")
    bg_alt    = QColor("#21252b")
    bg_input  = QColor("#1e2127")
    fg        = QColor("#abb2bf")
    fg_bright = QColor("#e5c07b")
    accent    = QColor("#61afef")
    selection = QColor("#3e4451")
    disabled  = QColor("#5c6370")
    border    = QColor("#3e4451")

    palette.setColor(QPalette.ColorRole.Window,          bg)
    palette.setColor(QPalette.ColorRole.WindowText,      fg)
    palette.setColor(QPalette.ColorRole.Base,            bg_input)
    palette.setColor(QPalette.ColorRole.AlternateBase,   bg_alt)
    palette.setColor(QPalette.ColorRole.Text,            fg)
    palette.setColor(QPalette.ColorRole.BrightText,      fg_bright)
    palette.setColor(QPalette.ColorRole.Button,          selection)
    palette.setColor(QPalette.ColorRole.ButtonText,      fg)
    palette.setColor(QPalette.ColorRole.Highlight,       accent)
    palette.setColor(QPalette.ColorRole.HighlightedText, bg)
    palette.setColor(QPalette.ColorRole.Link,            accent)
    palette.setColor(QPalette.ColorRole.LinkVisited,     QColor("#c678dd"))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     bg_alt)
    palette.setColor(QPalette.ColorRole.ToolTipText,     fg)
    palette.setColor(QPalette.ColorRole.PlaceholderText, disabled)

                      
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    palette.setColor(QPalette.ColorGroup.Disabled,
                     QPalette.ColorRole.Base, bg_alt)

    app.setPalette(palette)

_WINDOW_QSS = """
QMainWindow { background-color: #282c34; }
QToolBar {
    background-color: #21252b;
    border-bottom: 1px solid #181a1f;
    padding: 4px; spacing: 6px;
}
QToolBar QToolButton {
    color: #abb2bf; background-color: transparent;
    border: 1px solid transparent; border-radius: 4px;
    padding: 5px 10px; font-size: 13px;
}
QToolBar QToolButton:hover {
    background-color: #3e4451; border-color: #4b5263;
}
QStatusBar {
    background-color: #21252b; color: #5c6370;
    font-size: 12px; border-top: 1px solid #181a1f;
}
QSplitter::handle { background-color: #3e4451; width: 2px; }
QMenuBar { background-color: #21252b; color: #abb2bf; }
QMenuBar::item:selected { background-color: #3e4451; }
QMenu {
    background-color: #21252b; color: #abb2bf;
    border: 1px solid #3e4451; border-radius: 4px;
}
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background-color: #3e4451; color: #e5c07b; }
QMenu::separator { background-color: #3e4451; height: 1px; margin: 2px 0; }
"""


class MainWindow(QMainWindow):
    """CodeSum GUI main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CodeSum")
        self.setMinimumSize(1050, 680)
        self.setStyleSheet(_WINDOW_QSS)

                             
        self.setWindowIcon(_make_app_icon())

                                                                  
        app = QApplication.instance()
        if app:
            apply_dark_palette(app)

                                                                              
        self._base_dir:         Path          = Path(".").resolve()
        self._api_key:          Optional[str] = None
        self._llm_model:        str           = app_config.DEFAULT_LLM_MODEL
        self._selected_files:   List[str]     = []
        self._compressed_files: List[str]     = []
        self._scanner:          Optional[FileScanner]  = None
        self._worker:           Optional[SummaryWorker] = None
        self._scan_progress:    Optional[QProgressDialog] = None
        self._gen_progress:     Optional[QProgressDialog] = None

        self._api_key, self._llm_model = app_config.load_config()

                                                                               
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

                                                                               
        self._start_scan(self._base_dir)

                                                                               
                          
                                                                               

    def _build_menu(self):
        mb = self.menuBar()

                 
        fm = mb.addMenu("File")
        a = QAction("📁  Open folder…", self, shortcut=QKeySequence("Ctrl+O"))
        a.triggered.connect(self._open_folder)
        fm.addAction(a)
        fm.addSeparator()
        q = QAction("Quit", self, shortcut=QKeySequence("Ctrl+Q"))
        q.triggered.connect(self.close)
        fm.addAction(q)

                        
        cm = mb.addMenu("Configurations")
        mg = QAction("📂  Manage configurations…", self, shortcut=QKeySequence("Ctrl+M"))
        mg.triggered.connect(self._open_config_manager)
        cm.addAction(mg)

                    
        sm = mb.addMenu("Settings")
        sa = QAction("⚙  Settings…", self, shortcut=QKeySequence("Ctrl+,"))
        sa.triggered.connect(self._open_settings)
        sm.addAction(sa)

              
        hm = mb.addMenu("Help")
        hm.addAction(QAction("Keyboard shortcuts", self,
                              shortcut=QKeySequence("F1"),
                              triggered=self._show_shortcuts))
        hm.addSeparator()
        hm.addAction(QAction("About CodeSum", self,
                              triggered=self._show_about))

    def _build_toolbar(self):
        tb = QToolBar("Principal", movable=False)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        def _act(label, tip, slot, shortcut=None):
            a = QAction(label, self)
            a.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            tb.addAction(a)

        _act("📁  Open",      "Open folder",           self._open_folder,       "Ctrl+O")
        tb.addSeparator()
        _act("▶  Generate",      "Generate summary",           self._generate_shortcut, "Ctrl+G")
        tb.addSeparator()
        _act("📂  Configs",     "Selection configurations", self._open_config_manager, "Ctrl+M")
        tb.addSeparator()
        _act("⚙  Settings",  "API key & LLM model",        self._open_settings,     "Ctrl+,")
        tb.addSeparator()
        _act("🔍  Search",  "Filter files",        self._focus_search,      "Ctrl+F")
        tb.addSeparator()

        self._dir_label = QLabel()
        self._dir_label.setStyleSheet("color: #5c6370; padding: 0 8px; font-size: 11px;")
        tb.addWidget(self._dir_label)
        self._refresh_dir_label()

    def _build_central(self):
        container = QWidget()
        self.setCentralWidget(container)
        root = QHBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(3)

                                                                               
        left = QWidget()
        lv   = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)

        hdr = QLabel("  📂  File selection")
        hdr.setFixedHeight(28)
        hdr.setStyleSheet(
            "background-color: #21252b; color: #9da5b4; font-size: 12px; "
            "font-weight: bold; border-bottom: 1px solid #181a1f;"
        )
        lv.addWidget(hdr)

        self._tree_panel = FileTreePanel(base_dir=self._base_dir)
        self._tree_panel.selection_changed.connect(self._on_selection_changed)
        lv.addWidget(self._tree_panel)

                                                                               
        self._summary_panel = SummaryPanel(self._base_dir)
        self._summary_panel.generate_requested.connect(self._start_generation)

        splitter.addWidget(left)
        splitter.addWidget(self._summary_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([660, 390])

        root.addWidget(splitter)

    def _build_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._status_lbl = QLabel("Initializing…")
        self._statusbar.addWidget(self._status_lbl)

                                                                               
                        
                                                                               

    def _start_scan(self, base_dir: Path):
        self._base_dir = base_dir.resolve()
        self._refresh_dir_label()
        self._summary_panel.set_base_dir(self._base_dir)
        summary_utils.create_hidden_directory(self._base_dir)

        prev_sel, prev_comp = summary_utils.read_previous_selection(self._base_dir)

        gitignore = file_utils.parse_gitignore(self._base_dir)
        ignore_list = list(file_utils.DEFAULT_IGNORE_LIST)
        custom = (summary_utils.get_summary_dir(self._base_dir)
                  / summary_utils.CUSTOM_IGNORE_FILENAME)
        if custom.exists():
            try:
                lines = custom.read_text(encoding="utf-8").splitlines()
                ignore_list.extend(
                    l.strip() for l in lines if l.strip() and not l.strip().startswith("#")
                )
            except Exception:
                pass

                                 
        self._scan_progress = QProgressDialog(
            f"Scanning '{self._base_dir.name}'…", None, 0, 0, self
        )
        self._scan_progress.setWindowTitle("Project analysis")
        self._scan_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._scan_progress.setMinimumDuration(400)
        self._scan_progress.setCancelButton(None)
        self._scan_progress.setStyleSheet("""
            QProgressDialog { background-color: #282c34; color: #abb2bf; }
            QLabel { color: #abb2bf; }
            QProgressBar {
                background-color: #1e2127; border: 1px solid #3e4451; border-radius: 4px;
            }
            QProgressBar::chunk { background-color: #61afef; }
        """)

        self._scanner = FileScanner(self._base_dir, gitignore, ignore_list)
        self._scanner.finished.connect(
            lambda tree: self._on_scan_done(tree, prev_sel, prev_comp)
        )
        self._scanner.error.connect(self._on_scan_error)
        self._scanner.start()
        self._status_lbl.setText(f"Scan de {self._base_dir} …")

    def _on_scan_done(self, tree: dict, prev_sel: List[str], prev_comp: List[str]):
        if self._scan_progress:
            self._scan_progress.close()
            self._scan_progress = None

        self._tree_panel.load_tree(tree, prev_sel, prev_comp, self._base_dir)

        n = len(self._tree_panel._all_file_items)
        self._status_lbl.setText(
            f"{n} file{'s' if n != 1 else ''} indexed  ·  {self._base_dir}"
        )

    def _on_scan_error(self, msg: str):
        if self._scan_progress:
            self._scan_progress.close()
            self._scan_progress = None
        QMessageBox.critical(self, "Scan error", f"Impossible de scanner :\n{msg}")
        self._status_lbl.setText("Scan error.")

                                                                               
                          
                                                                               

    def _start_generation(self):
        if not self._selected_files:
            QMessageBox.information(self, "No files",
                                    "Please select at least one file.")
            return

        openai_client = None
        compressed = list(self._compressed_files)

        if compressed:
            if not self._api_key:
                reply = QMessageBox.question(
                    self, "API key required",
                    "Des files ★ nécessitent une clé OpenAI.\n"
                    "Configurer maintenant ?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._open_settings()
                    self._api_key, self._llm_model = app_config.load_config()
                if not self._api_key:
                    compressed = []

            if self._api_key and compressed:
                try:
                    from openai import OpenAI
                    openai_client = OpenAI(api_key=self._api_key)
                except Exception as exc:
                    QMessageBox.warning(self, "OpenAI error",
                                        f"Impossible d'initialiser OpenAI :\n{exc}\n\n"
                                        "Compression will be ignored.")
                    compressed = []

        summary_utils.write_previous_selection(
            self._selected_files, self._base_dir, self._compressed_files
        )
        self._summary_panel.start_generation()

        total = len(self._selected_files)

                                                     
        self._gen_progress = QProgressDialog(
            "Generating summary…", "Cancel", 0, total, self
        )
        self._gen_progress.setWindowTitle("CodeSum — Generation")
        self._gen_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._gen_progress.setMinimumDuration(0)
        self._gen_progress.setStyleSheet("""
            QProgressDialog { background-color: #282c34; color: #abb2bf; }
            QLabel { color: #abb2bf; }
            QPushButton {
                background-color: #3e4451; color: #abb2bf;
                border: 1px solid #4b5263; border-radius: 4px; padding: 4px 12px;
            }
            QPushButton:hover { background-color: #4b5263; }
            QProgressBar {
                background-color: #1e2127; border: 1px solid #3e4451; border-radius: 4px;
            }
            QProgressBar::chunk { background-color: #61afef; }
        """)
        self._gen_progress.setValue(0)
        self._gen_progress.show()

        self._worker = SummaryWorker(
            self._selected_files, self._base_dir, compressed,
            openai_client, self._llm_model,
        )
                                                   
        self._worker.progress.connect(self._on_gen_progress)
        self._worker.log.connect(self._summary_panel.append_log)
        self._worker.finished.connect(self._on_gen_done)
        self._worker.error.connect(self._on_gen_error)
        self._worker.start()
        self._status_lbl.setText("Generating…")

    def _on_gen_progress(self, message: str, done: int, total: int):
        self._summary_panel.update_progress(message, done, total)
        if hasattr(self, "_gen_progress") and self._gen_progress:
            self._gen_progress.setValue(done)
                                                  
            label = message if len(message) <= 70 else "…" + message[-67:]
            self._gen_progress.setLabelText(label)

    def _on_gen_done(self, token_count: int):
        if hasattr(self, "_gen_progress") and self._gen_progress:
            self._gen_progress.close()
            self._gen_progress = None
        self._summary_panel.finish_generation(token_count)
        self._status_lbl.setText(
            f"✓ Summary generated  ·  {token_count:,} tokens  ·  copied to clipboard"
        )

    def _on_gen_error(self, msg: str):
        if hasattr(self, "_gen_progress") and self._gen_progress:
            self._gen_progress.close()
            self._gen_progress = None
        self._summary_panel.show_error(msg)
        self._status_lbl.setText(f"❌ Error: {msg}")

                                                                               
               
                                                                               

    def _on_selection_changed(self, sel: List[str], comp: List[str]):
        self._selected_files   = sel
        self._compressed_files = comp
        tok = self._tree_panel.get_total_tokens()
        self._summary_panel.update_stats(sel, comp, tok)
        n = len(sel)
        self._status_lbl.setText(
            f"{n} file{'s' if n != 1 else ''} selected"
            f"  ·  {self._base_dir}"
        )

                                                                               
             
                                                                               

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose project directory", str(self._base_dir)
        )
        if folder:
            self._start_scan(Path(folder))

    def _open_settings(self):
        if SettingsDialog(self).exec():
            self._api_key, self._llm_model = app_config.load_config()

    def _open_config_manager(self):
        dlg = SelectionConfigDialog(
            self._base_dir, self._selected_files, self._compressed_files, self
        )
        dlg.config_loaded.connect(self._apply_config)
        dlg.exec()

    def _apply_config(self, sel: List[str], comp: List[str]):
        """Applique une configuration chargée sans re-scanner."""
        tree = self._rebuild_tree_from_items()
        self._tree_panel.load_tree(tree, sel, comp, self._base_dir)

    def _rebuild_tree_from_items(self) -> dict:
        tree: dict = {}
        for it in self._tree_panel._all_file_items:
            abs_path = it.data(COL_NAME, ROLE_ABS_PATH)
            rel_path = it.data(COL_NAME, ROLE_REL_PATH)
            if not abs_path or not rel_path:
                continue
            parts = rel_path.split("/")
            cur = tree
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = abs_path
        return tree

    def _focus_search(self):
        self._tree_panel.focus_search()

    def _show_shortcuts(self):
        QMessageBox.information(self, "Keyboard shortcuts — CodeSum",
            "<b style='font-size:14px'>Keyboard shortcuts</b><br><br>"
            "<b>Application</b><br>"
            "<table cellspacing='4'>"
            "<tr><td><code>Ctrl+O</code></td><td>Open folder</td></tr>"
            "<tr><td><code>Ctrl+G</code></td><td>Generate summary</td></tr>"
            "<tr><td><code>Ctrl+M</code></td><td>Manage selection configs</td></tr>"
            "<tr><td><code>Ctrl+,</code></td><td>Open settings (API key, model)</td></tr>"
            "<tr><td><code>Ctrl+F</code></td><td>Search / filter</td></tr>"
            "<tr><td><code>Ctrl+Q</code></td><td>Quit</td></tr>"
            "<tr><td><code>F1</code></td><td>This help</td></tr>"
            "</table><br>"
            "<b>Tree navigation</b><br>"
            "<table cellspacing='4'>"
            "<tr><td><code>↑ / ↓</code></td><td>Move cursor</td></tr>"
            "<tr><td><code>← / →</code></td><td>Previous/Next folder</td></tr>"
            "<tr><td><code>Enter</code></td><td>Expand / collapse folder</td></tr>"
            "<tr><td><code>Double-click</code></td><td>Expand / collapse folder</td></tr>"
            "</table><br>"
            "<b>File selection</b><br>"
            "<table cellspacing='4'>"
            "<tr><td><code>Space</code></td><td>Select/Deselect</td></tr>"
            "<tr><td><code>S</code></td><td>Mark for AI compression ★</td></tr>"
            "<tr><td><code>F</code></td><td>Toggle current folder</td></tr>"
            "<tr><td><code>A</code></td><td>Select/Deselect all</td></tr>"
            "<tr><td><code>E</code></td><td>Expand all</td></tr>"
            "<tr><td><code>C</code></td><td>Collapse subfolders</td></tr>"
            "<tr><td><code>Right-click</code></td><td>Context menu</td></tr>"
            "</table>"
        )

    def _show_about(self):
        icon = _make_app_icon()
        box = QMessageBox(self)
        box.setWindowTitle("About CodeSum")
        box.setWindowIcon(icon)
        box.setIconPixmap(icon.pixmap(64, 64))
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            "<b style='font-size:16px'>CodeSum</b> "
            "<span style='color:#5c6370'>v0.3.1</span><br><br>"
            "Professional code summarization platform powered by AI.<br>"
            "Modern PySide6 interface with rich navigation and analytics.<br><br>"
            "<b>Features:</b><br>"
            "• Interactive file tree with token and selection insights<br>"
            "• Selective AI compression per file (★)<br>"
            "• Named selection profiles (CRUD support)<br>"
            "• Generation with per-file progress<br>"
            "• Markdown preview (HTML / raw)<br>"
            "• Native Qt dark theme, performance mode, and keyboard-first controls<br><br>"
            "<span style='color:#5c6370;font-size:11px'>"
            f"Config: {app_config.CONFIG_FILE}</span>"
        )
        box.exec()

                                                                               
                        
                                                                               

    def _generate_shortcut(self):
        """Proxy for Ctrl+G shortcut → triggers summary generation."""
        self._start_generation()

    def keyPressEvent(self, event):
        mod  = event.modifiers()
        key  = event.key()
        ctrl = Qt.KeyboardModifier.ControlModifier

        if mod == ctrl:
            if key == Qt.Key.Key_F:
                self._focus_search(); return
            if key == Qt.Key.Key_G:
                self._generate_shortcut(); return
        if key == Qt.Key.Key_F1:
            self._show_shortcuts(); return
        super().keyPressEvent(event)

                                                                               
               
                                                                               

    def closeEvent(self, event):
        if self._selected_files:
            try:
                summary_utils.write_previous_selection(
                    self._selected_files, self._base_dir, self._compressed_files
                )
            except Exception:
                pass
        for w in (self._scanner, self._worker):
            if w and w.isRunning():
                w.quit()
                w.wait(1000)
        event.accept()

                                                                               
             
                                                                               

    def _refresh_dir_label(self):
        if hasattr(self, "_dir_label"):
            txt = str(self._base_dir)
            if len(txt) > 65:
                txt = "…" + txt[-62:]
            self._dir_label.setText(f"📂  {txt}")
