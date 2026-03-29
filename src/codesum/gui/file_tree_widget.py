"""
file_tree_widget.py — Interactive file tree widget for CodeSum GUI.
version complète avec toutes les fonctionnalités du TUI curses.

Fonctionnalités :
  - Individual file selection (Space / left-click)
  - AI compression marking ★ (S key / right-click)
  - Toggle entire folder (F or Space on folder)
  - Select/Deselect all (A)
  - Expand/Collapse (E all, C collapse, double-click folder)
  - Navigate ←/→ between sibling folders (like TUI)
  - Persist collapse state via summary_utils
  - Async token count per file + folder totals
  - Built-in filter/search bar
  - Right-click context menu
  - selection_changed signal emitted on any change
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set

from PySide6.QtCore import Qt, Signal, QThreadPool, QTimer
from PySide6.QtGui import QColor, QFont, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QTreeWidget, QTreeWidgetItem, QHeaderView,
    QAbstractItemView, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QMenu, QFrame, QLabel, QToolButton,
)

from .. import summary_utils
from .workers import TokenCounter

             
COL_NAME   = 0
COL_TOKENS = 1
COL_STATE  = 2

                    
ROLE_ABS_PATH   = Qt.ItemDataRole.UserRole
ROLE_IS_FOLDER  = Qt.ItemDataRole.UserRole + 1
ROLE_REL_PATH   = Qt.ItemDataRole.UserRole + 2
ROLE_COMPRESSED = Qt.ItemDataRole.UserRole + 3

                       
C_SELECTED   = QColor("#e5c07b")
C_COMPRESSED = QColor("#c678dd")
C_FOLDER     = QColor("#61afef")
C_PARTIAL    = QColor("#e06c75")
C_DEFAULT    = QColor("#abb2bf")
C_DIM        = QColor("#4b5263")
C_FOLDER_SEL = QColor("#98c379")

_TREE_QSS = """
QTreeWidget {
    background-color: #282c34;
    alternate-background-color: #2c313a;
    color: #abb2bf;
    border: none;
    outline: none;
}
QTreeWidget::item { padding: 2px 4px; border-radius: 3px; }
QTreeWidget::item:selected { background-color: #3e4451; color: #e5c07b; }
QTreeWidget::item:hover:!selected { background-color: #2c313a; }
QTreeWidget::branch { background-color: #282c34; }
QHeaderView::section {
    background-color: #21252b; color: #9da5b4;
    padding: 4px 6px; border: none;
    border-bottom: 1px solid #181a1f;
    font-weight: bold; font-size: 12px;
}
QScrollBar:vertical { background: #21252b; width: 8px; border-radius: 4px; }
QScrollBar::handle:vertical { background: #4b5263; border-radius: 4px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background: #636d83; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
"""

_SEARCH_QSS = """
QWidget#SearchBar { background-color: #21252b; }
QLineEdit {
    background-color: #282c34; color: #abb2bf;
    border: 1px solid #3e4451; border-radius: 4px;
    padding: 4px 8px; font-size: 12px;
}
QLineEdit:focus { border-color: #61afef; color: #e5c07b; }
QLabel { color: #5c6370; font-size: 13px; }
QToolButton { background: transparent; color: #5c6370; border: none; font-size: 13px; padding: 2px 4px; }
QToolButton:hover { color: #abb2bf; }
"""

_MENU_QSS = """
QMenu { background-color: #21252b; color: #abb2bf; border: 1px solid #3e4451; border-radius: 4px; }
QMenu::item { padding: 6px 20px; }
QMenu::item:selected { background-color: #3e4451; color: #e5c07b; }
QMenu::separator { background-color: #3e4451; height: 1px; margin: 2px 0; }
"""


                                                                              
           
                                                                              

class SearchBar(QWidget):
    text_changed = Signal(str)
    cleared      = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SearchBar")
        self.setFixedHeight(36)
        self.setStyleSheet(_SEARCH_QSS)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(4)

        lbl = QLabel("🔍")
        row.addWidget(lbl)

        self._edit = QLineEdit()
        self._edit.setPlaceholderText("Filter files… (Ctrl+F)")
        self._edit.setClearButtonEnabled(True)
        self._edit.textChanged.connect(self._debounce)
        row.addWidget(self._edit)

        clr = QToolButton()
        clr.setText("✕")
        clr.setToolTip("Clear filter")
        clr.clicked.connect(self._edit.clear)
        row.addWidget(clr)

        self._timer = QTimer(singleShot=True, interval=180)
        self._timer.timeout.connect(lambda: self.text_changed.emit(self._edit.text()))

    def _debounce(self, text):
        self._timer.start()
        if not text:
            self.cleared.emit()

    def text(self):
        return self._edit.text()

    def set_focus(self):
        self._edit.setFocus()
        self._edit.selectAll()

    def clear(self):
        self._edit.clear()


                                                                              
                
                                                                              

class FileTreeWidget(QTreeWidget):
    """
    Interactive file selection tree.

    Signals:
        selection_changed(selected_files: list[str], compressed_files: list[str])
    """

    selection_changed = Signal(list, list)

    def __init__(self, base_dir=None, parent=None):
        super().__init__(parent)
        self._base_dir: Optional[Path] = Path(base_dir).resolve() if base_dir else None
        self._selected_paths:   Set[str] = set()
        self._compressed_paths: Set[str] = set()
        self._token_cache:      Dict[str, int] = {}
        self._all_file_items:   List[QTreeWidgetItem] = []
        self._all_folder_items: List[QTreeWidgetItem] = []
        self._collapsed_rels:   Set[str] = set()
        self._filter_text:      str = ""
        self._pool = QThreadPool.globalInstance()
        self._setup_ui()

                                                                                

    def _setup_ui(self):
        self.setColumnCount(3)
        self.setHeaderLabels(["File", "Tokens", "State"])
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setExpandsOnDoubleClick(False)
        self.setAnimated(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.itemDoubleClicked.connect(self._on_double_click)

        hdr = self.header()
        hdr.setSectionResizeMode(COL_NAME,   QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(COL_TOKENS, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(COL_STATE,  QHeaderView.ResizeMode.ResizeToContents)
        hdr.setMinimumSectionSize(55)

        f = QFont()
        f.setFamilies(["Menlo", "Consolas", "Courier New", "monospace"])
        f.setPointSize(12)
        self.setFont(f)
        self.setStyleSheet(_TREE_QSS)

                                                                               

    def load_tree(self, tree: dict, prev_sel: List[str], prev_comp: List[str],
                  base_dir=None):
        if base_dir:
            self._base_dir = Path(base_dir).resolve()

                                       
        if self._base_dir:
            prev_col = summary_utils.read_previous_collapsed_folders(self._base_dir)
            self._collapsed_rels = set(prev_col) if prev_col else set()

        self.clear()
        self._all_file_items.clear()
        self._all_folder_items.clear()
        self._selected_paths   = {str(Path(p).resolve()) for p in prev_sel  if p}
        self._compressed_paths = {str(Path(p).resolve()) for p in prev_comp if p}

        self._build_items(tree, self.invisibleRootItem(), "")
        self._restore_collapse_state()
        self._refresh_all_visuals()
        self._emit_selection()

        for it in self._all_file_items:
            p = it.data(COL_NAME, ROLE_ABS_PATH)
            if p and p not in self._token_cache:
                self._schedule_token_count(p)

    def _build_items(self, subtree: dict, parent: QTreeWidgetItem, prefix: str):
        files   = {k: v for k, v in subtree.items() if not isinstance(v, dict)}
        folders = {k: v for k, v in subtree.items() if isinstance(v, dict)}

        for name, abs_path in sorted(files.items()):
            it = QTreeWidgetItem(parent)
            it.setText(COL_NAME,   name)
            it.setText(COL_TOKENS, "…")
            it.setText(COL_STATE,  "")
            it.setData(COL_NAME, ROLE_ABS_PATH,  str(Path(abs_path).resolve()))
            it.setData(COL_NAME, ROLE_IS_FOLDER, False)
            it.setData(COL_NAME, ROLE_REL_PATH,  f"{prefix}{name}")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._all_file_items.append(it)

        for name, sub in sorted(folders.items()):
            rel = f"{prefix}{name}"
            fi = QTreeWidgetItem(parent)
            fi.setText(COL_NAME,  f"▸ {name}/")
            fi.setText(COL_TOKENS, "")
            fi.setText(COL_STATE,  "")
            fi.setData(COL_NAME, ROLE_IS_FOLDER, True)
            fi.setData(COL_NAME, ROLE_REL_PATH,  rel)
            fi.setForeground(COL_NAME, C_FOLDER)
            fi.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._all_folder_items.append(fi)
            self._build_items(sub, fi, f"{rel}/")

    def _restore_collapse_state(self):
        self.expandAll()
        for fi in self._all_folder_items:
            rel = fi.data(COL_NAME, ROLE_REL_PATH)
            if rel in self._collapsed_rels:
                self.collapseItem(fi)
                fi.setText(COL_NAME, fi.text(COL_NAME).replace("▾", "▸"))
            else:
                self.expandItem(fi)
                fi.setText(COL_NAME, fi.text(COL_NAME).replace("▸", "▾"))

    def _persist_collapse(self):
        if not self._base_dir:
            return
        collapsed = [fi.data(COL_NAME, ROLE_REL_PATH)
                     for fi in self._all_folder_items
                     if not fi.isExpanded() and fi.data(COL_NAME, ROLE_REL_PATH)]
        self._collapsed_rels = set(collapsed)
        try:
            summary_utils.write_previous_collapsed_folders(collapsed, self._base_dir)
        except Exception:
            pass

                                                                               

    def get_selected_files(self) -> List[str]:
        return sorted(self._selected_paths)

    def get_compressed_files(self) -> List[str]:
        return sorted(self._compressed_paths)

    def get_total_tokens(self) -> int:
        return sum(self._token_cache.get(p, 0)
                   for p in self._selected_paths
                   if p not in self._compressed_paths)

                                                                               

    def toggle_file(self, item: QTreeWidgetItem):
        p = item.data(COL_NAME, ROLE_ABS_PATH)
        if not p or item.data(COL_NAME, ROLE_IS_FOLDER):
            return
        if p in self._selected_paths:
            self._selected_paths.discard(p)
            self._compressed_paths.discard(p)
        else:
            self._selected_paths.add(p)
        self._refresh_item_visual(item)
        self._refresh_all_folder_visuals()
        self._emit_selection()

    def toggle_compression(self, item: QTreeWidgetItem):
        p = item.data(COL_NAME, ROLE_ABS_PATH)
        if not p or item.data(COL_NAME, ROLE_IS_FOLDER):
            return
        self._selected_paths.add(p)
        if p in self._compressed_paths:
            self._compressed_paths.discard(p)
        else:
            self._compressed_paths.add(p)
        self._refresh_item_visual(item)
        self._refresh_all_folder_visuals()
        self._emit_selection()

    def toggle_folder(self, folder_item: QTreeWidgetItem):
        if not folder_item.data(COL_NAME, ROLE_IS_FOLDER):
            return
        files = self._collect_file_items(folder_item)
        all_sel = all(it.data(COL_NAME, ROLE_ABS_PATH) in self._selected_paths
                      for it in files)
        for it in files:
            p = it.data(COL_NAME, ROLE_ABS_PATH)
            if p:
                if all_sel:
                    self._selected_paths.discard(p)
                    self._compressed_paths.discard(p)
                else:
                    self._selected_paths.add(p)
        self._refresh_all_visuals()
        self._emit_selection()

    def select_all(self):
        for it in self._all_file_items:
            p = it.data(COL_NAME, ROLE_ABS_PATH)
            if p:
                self._selected_paths.add(p)
        self._refresh_all_visuals()
        self._emit_selection()

    def deselect_all(self):
        self._selected_paths.clear()
        self._compressed_paths.clear()
        self._refresh_all_visuals()
        self._emit_selection()

    def toggle_select_all(self):
        if len(self._selected_paths) < len(self._all_file_items):
            self.select_all()
        else:
            self.deselect_all()

                                                                               

    def expand_all_recursive(self):
        self.expandAll()
        for fi in self._all_folder_items:
            fi.setText(COL_NAME, fi.text(COL_NAME).replace("▸", "▾"))
        self._persist_collapse()

    def collapse_children(self, item: QTreeWidgetItem):
        if not item.data(COL_NAME, ROLE_IS_FOLDER):
            return
        self.collapseItem(item)
        item.setText(COL_NAME, item.text(COL_NAME).replace("▾", "▸"))
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(COL_NAME, ROLE_IS_FOLDER):
                self.collapse_children(child)
        self._persist_collapse()

    def toggle_expand(self, item: QTreeWidgetItem):
        if not item.data(COL_NAME, ROLE_IS_FOLDER):
            return
        if item.isExpanded():
            self.collapseItem(item)
            item.setText(COL_NAME, item.text(COL_NAME).replace("▾", "▸"))
        else:
            self.expandItem(item)
            item.setText(COL_NAME, item.text(COL_NAME).replace("▸", "▾"))
        self._persist_collapse()

                                                                               

    def _visible_folders(self) -> List[QTreeWidgetItem]:
        return [fi for fi in self._all_folder_items if not fi.isHidden()]

    def jump_to_prev_folder(self):
        cur = self.currentItem()
        fols = self._visible_folders()
        if not fols:
            return
        if cur is None:
            self.setCurrentItem(fols[-1])
        else:
            try:
                idx = fols.index(cur)
                target = fols[max(0, idx - 1)]
            except ValueError:
                target = self._find_parent_folder(cur) or fols[0]
            self.setCurrentItem(target)
        self.scrollToItem(self.currentItem())

    def jump_to_next_folder(self):
        cur = self.currentItem()
        fols = self._visible_folders()
        if not fols:
            return
        if cur is None:
            self.setCurrentItem(fols[0])
        else:
            try:
                idx = fols.index(cur)
                target = fols[min(len(fols) - 1, idx + 1)]
            except ValueError:
                target = self._find_parent_folder(cur) or fols[-1]
            self.setCurrentItem(target)
        self.scrollToItem(self.currentItem())

    def _find_parent_folder(self, item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
        p = item.parent()
        while p:
            if p.data(COL_NAME, ROLE_IS_FOLDER):
                return p
            p = p.parent()
        return None

                                                                               

    def apply_filter(self, text: str):
        self._filter_text = text.lower().strip()
        if not self._filter_text:
            for it in self._all_file_items:
                it.setHidden(False)
            for fi in self._all_folder_items:
                fi.setHidden(False)
            self._restore_collapse_state()
            return

        matched_parents: Set[QTreeWidgetItem] = set()
        for it in self._all_file_items:
            name = it.text(COL_NAME).lower()
            rel  = (it.data(COL_NAME, ROLE_REL_PATH) or "").lower()
            match = self._filter_text in name or self._filter_text in rel
            it.setHidden(not match)
            if match:
                p = it.parent()
                while p:
                    matched_parents.add(p)
                    p = p.parent()

        for fi in self._all_folder_items:
            visible = fi in matched_parents
            fi.setHidden(not visible)
            if visible:
                self.expandItem(fi)

    def clear_filter(self):
        self.apply_filter("")

                                                                               

    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        cur = self.currentItem()

        if key == Qt.Key.Key_Space and cur:
            if cur.data(COL_NAME, ROLE_IS_FOLDER):
                self.toggle_folder(cur)
            else:
                self.toggle_file(cur)
            return

        if key == Qt.Key.Key_S and cur:
            if not cur.data(COL_NAME, ROLE_IS_FOLDER):
                self.toggle_compression(cur)
            return

        if key == Qt.Key.Key_F and cur:
            if cur.data(COL_NAME, ROLE_IS_FOLDER):
                self.toggle_folder(cur)
            else:
                pf = self._find_parent_folder(cur)
                if pf:
                    self.toggle_folder(pf)
            return

        if key == Qt.Key.Key_A:
            self.toggle_select_all()
            return

        if key == Qt.Key.Key_E:
            self.expand_all_recursive()
            return

        if key == Qt.Key.Key_C and cur:
            target = cur if cur.data(COL_NAME, ROLE_IS_FOLDER) else self._find_parent_folder(cur)
            if target:
                self.collapse_children(target)
            return

        if key == Qt.Key.Key_Left:
            self.jump_to_prev_folder()
            return

        if key == Qt.Key.Key_Right:
            self.jump_to_next_folder()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and cur:
            if cur.data(COL_NAME, ROLE_IS_FOLDER):
                self.toggle_expand(cur)
            else:
                self.toggle_file(cur)
            return

        super().keyPressEvent(event)

                                                                               

    def mousePressEvent(self, event: QMouseEvent):
        super().mousePressEvent(event)
        item = self.itemAt(event.pos())
        if not item:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            if not item.data(COL_NAME, ROLE_IS_FOLDER):
                self.toggle_file(item)

    def _on_double_click(self, item: QTreeWidgetItem, column: int):
        if item.data(COL_NAME, ROLE_IS_FOLDER):
            self.toggle_expand(item)

                                                                               

    def _show_context_menu(self, pos):
        item = self.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)
        is_folder = item.data(COL_NAME, ROLE_IS_FOLDER)

        if is_folder:
            menu.addAction("☑  Select entire folder",  lambda: self.toggle_folder(item))
            menu.addAction("▾  Expand / Collapse",             lambda: self.toggle_expand(item))
            menu.addSeparator()
            menu.addAction("⊟  Collapse subfolders",     lambda: self.collapse_children(item))
        else:
            p = item.data(COL_NAME, ROLE_ABS_PATH)
            is_sel  = p in self._selected_paths
            is_comp = p in self._compressed_paths
            lbl_sel  = "☒  Deselect"           if is_sel  else "☑  Select"
            lbl_comp = "✰  Remove compression ★"    if is_comp else "★  Mark for AI compression"
            menu.addAction(lbl_sel,  lambda: self.toggle_file(item))
            menu.addAction(lbl_comp, lambda: self.toggle_compression(item))
            menu.addSeparator()
            pf = self._find_parent_folder(item)
            if pf:
                pname = pf.text(COL_NAME).strip()
                menu.addAction(f"☑  Toggle folder '{pname}'", lambda: self.toggle_folder(pf))

        menu.addSeparator()
        menu.addAction("A  Select/Deselect all", self.toggle_select_all)
        menu.addAction("E  Expand all",                       self.expand_all_recursive)
        menu.exec(self.viewport().mapToGlobal(pos))

                                                                               

    def _schedule_token_count(self, abs_path: str):
        c = TokenCounter(abs_path)
        c.signals.result.connect(self._on_token_result)
        self._pool.start(c)

    def _on_token_result(self, abs_path: str, count: int):
        self._token_cache[abs_path] = max(count, 0)
        for it in self._all_file_items:
            if it.data(COL_NAME, ROLE_ABS_PATH) == abs_path:
                it.setText(COL_TOKENS, _fmt(count))
                it.setForeground(COL_TOKENS, C_DIM)
                break
        self._update_folder_token_sums()
        self._emit_selection()

    def _update_folder_token_sums(self):
        for fi in self._all_folder_items:
            files = self._collect_file_items(fi)
            total = sum(self._token_cache.get(
                it.data(COL_NAME, ROLE_ABS_PATH), 0) for it in files)
            fi.setText(COL_TOKENS, _fmt(total) if total else "")
            fi.setForeground(COL_TOKENS, C_DIM)

                                                                               

    def _refresh_all_visuals(self):
        for it in self._all_file_items:
            self._refresh_item_visual(it)
        self._refresh_all_folder_visuals()

    def _refresh_item_visual(self, item: QTreeWidgetItem):
        p = item.data(COL_NAME, ROLE_ABS_PATH)
        if not p:
            return
        is_comp = p in self._compressed_paths
        is_sel  = p in self._selected_paths
        if is_comp:
            item.setText(COL_STATE, "★")
            item.setForeground(COL_NAME,   C_COMPRESSED)
            item.setForeground(COL_STATE,  C_COMPRESSED)
            item.setForeground(COL_TOKENS, C_COMPRESSED)
        elif is_sel:
            item.setText(COL_STATE, "[X]")
            item.setForeground(COL_NAME,   C_SELECTED)
            item.setForeground(COL_STATE,  C_SELECTED)
            item.setForeground(COL_TOKENS, C_DIM)
        else:
            item.setText(COL_STATE, "")
            item.setForeground(COL_NAME,   C_DEFAULT)
            item.setForeground(COL_STATE,  C_DIM)
            item.setForeground(COL_TOKENS, C_DIM)

    def _refresh_all_folder_visuals(self):
        for fi in self._all_folder_items:
            self._refresh_folder_visual(fi)

    def _refresh_folder_visual(self, fi: QTreeWidgetItem):
        files = self._collect_file_items(fi)
        if not files:
            fi.setForeground(COL_NAME, C_FOLDER)
            fi.setText(COL_STATE, "")
            return
        n_sel = sum(1 for it in files
                    if it.data(COL_NAME, ROLE_ABS_PATH) in self._selected_paths)
        n_tot = len(files)
        if n_sel == 0:
            fi.setForeground(COL_NAME, C_FOLDER)
            fi.setText(COL_STATE, "")
        elif n_sel == n_tot:
            fi.setForeground(COL_NAME, C_FOLDER_SEL)
            fi.setText(COL_STATE, f"[{n_sel}]")
            fi.setForeground(COL_STATE, C_FOLDER_SEL)
        else:
            fi.setForeground(COL_NAME, C_PARTIAL)
            fi.setText(COL_STATE, f"[{n_sel}/{n_tot}]")
            fi.setForeground(COL_STATE, C_PARTIAL)

                                                                               

    def _collect_file_items(self, parent: QTreeWidgetItem) -> List[QTreeWidgetItem]:
        result = []
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(COL_NAME, ROLE_IS_FOLDER):
                result.extend(self._collect_file_items(child))
            else:
                result.append(child)
        return result

    def _emit_selection(self):
        self.selection_changed.emit(
            self.get_selected_files(),
            self.get_compressed_files(),
        )


                                                                              
                                                   
                                                                              

class FileTreePanel(QWidget):
    """
    Full container (search bar + tree + shortcut bar).
    This widget is inserted inside MainWindow's splitter.
    """

    selection_changed = Signal(list, list)

    def __init__(self, base_dir=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #282c34;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._search = SearchBar()
        layout.addWidget(self._search)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #181a1f; max-height: 1px;")
        layout.addWidget(sep)

        self.tree = FileTreeWidget(base_dir=base_dir)
        layout.addWidget(self.tree)

        hint = QLabel(
            "  Space·select  S·★compress  F·folder  A·all  "
            "E·expand  C·collapse  ←/→·folders  Right-click·menu"
        )
        hint.setFixedHeight(20)
        hint.setStyleSheet(
            "background-color: #21252b; color: #3e4451; font-size: 10px;"
            "border-top: 1px solid #181a1f;"
        )
        layout.addWidget(hint)

        self._search.text_changed.connect(self.tree.apply_filter)
        self._search.cleared.connect(self.tree.clear_filter)
        self.tree.selection_changed.connect(self.selection_changed)

                
    def load_tree(self, tree, prev_sel, prev_comp, base_dir=None):
        self.tree.load_tree(tree, prev_sel, prev_comp, base_dir)

    def get_selected_files(self):  return self.tree.get_selected_files()
    def get_compressed_files(self): return self.tree.get_compressed_files()
    def get_total_tokens(self):    return self.tree.get_total_tokens()

    @property
    def _all_file_items(self):
        return self.tree._all_file_items

    def focus_search(self):
        self._search.set_focus()


                                                                               

def _fmt(count: int) -> str:
    if count < 0:    return "(?)"
    if count == 0:   return "0"
    if count < 1000: return str(count)
    if count < 1_000_000: return f"{count/1000:.1f}k"
    return f"{count/1_000_000:.1f}M"
