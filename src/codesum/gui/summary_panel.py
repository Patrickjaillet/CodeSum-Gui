"""
summary_panel.py — Right panel: stats, progress and preview.
Phase 4: per-file QProgressBar, Markdown HTML rendering, detailed log, raw/render view.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import summary_utils

                                                                             
_PANEL_STYLE = """
QWidget#SummaryPanel { background-color: #21252b; }
QLabel { color: #abb2bf; }
QLabel#stat_value { color: #e5c07b; font-weight: bold; }
QLabel#stat_label { color: #5c6370; }
QPlainTextEdit, QTextEdit {
    background-color: #1e2127; color: #abb2bf;
    border: 1px solid #3e4451; border-radius: 4px;
    font-family: 'Menlo','Consolas','Courier New',monospace;
    font-size: 11px; padding: 6px;
}
QProgressBar {
    background-color: #1e2127; border: 1px solid #3e4451;
    border-radius: 4px; text-align: center;
    color: #abb2bf; font-size: 11px; min-height: 18px;
}
QProgressBar::chunk { background-color: #61afef; border-radius: 3px; }
QPushButton#gen_btn {
    background-color: #98c379; color: #282c34;
    border: none; border-radius: 5px;
    padding: 9px 18px; font-size: 14px; font-weight: bold;
}
QPushButton#gen_btn:hover { background-color: #b5e890; }
QPushButton#gen_btn:disabled { background-color: #3e4451; color: #5c6370; }
QPushButton#action_btn {
    background-color: #3e4451; color: #abb2bf;
    border: 1px solid #4b5263; border-radius: 4px;
    padding: 5px 12px; font-size: 12px;
}
QPushButton#action_btn:hover { background-color: #4b5263; color: #e5c07b; }
QPushButton#action_btn:disabled { color: #3e4451; border-color: #2c313a; }
QFrame[frameShape="4"] { color: #3e4451; }
"""

_HTML_STYLE = """
<style>
body { background:#1e2127; color:#abb2bf;
       font-family:'Menlo','Consolas','Courier New',monospace;
       font-size:12px; margin:8px; }
h1 { color:#e5c07b; border-bottom:1px solid #3e4451; padding-bottom:4px; }
h2 { color:#61afef; border-bottom:1px solid #3e4451; padding-bottom:4px; margin-top:16px; }
pre { background:#282c34; border:1px solid #3e4451; border-radius:4px;
      padding:10px; white-space:pre-wrap; word-break:break-all; }
code { color:#98c379; }
hr { border:none; border-top:1px solid #3e4451; margin:12px 0; }
p { margin:4px 0; }
</style>
"""


def _fmt_tokens(count: int) -> str:
    if count <= 0: return "0"
    if count < 1_000: return str(count)
    if count < 1_000_000: return f"{count/1_000:.1f}k"
    return f"{count/1_000_000:.1f}M"


def _esc(t: str) -> str:
    return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")


def _md_to_html(md: str) -> str:
    lines = md.splitlines()
    out = [_HTML_STYLE, "<body>"]
    in_code = False
    for line in lines:
        if line.startswith("```"):
            if not in_code:
                out.append("<pre><code>")
                in_code = True
            else:
                out.append("</code></pre>")
                in_code = False
            continue
        if in_code:
            out.append(_esc(line).replace(" ","&nbsp;") + "\n")
            continue
        if re.match(r"^-{3,}$", line.strip()):
            out.append("<hr>"); continue
        m = re.match(r"^(#{1,2}) (.+)", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_esc(m.group(2))}</h{lvl}>")
        elif line.strip() == "":
            out.append("<br>")
        else:
            out.append(f"<p>{_esc(line)}</p>")
    out.append("</body>")
    return "".join(out)


def _make_sep() -> QFrame:
    s = QFrame()
    s.setFrameShape(QFrame.Shape.HLine)
    s.setStyleSheet("color: #3e4451;")
    return s


class StatRow(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self._lbl = QLabel(label); self._lbl.setObjectName("stat_label"); self._lbl.setFixedWidth(165)
        self._val = QLabel("—");   self._val.setObjectName("stat_value")
        h.addWidget(self._lbl); h.addWidget(self._val); h.addStretch()

    def set_value(self, t: str): self._val.setText(t)


class SummaryPanel(QWidget):
    """CodeSum right panel — Phase 4."""

    generate_requested = Signal()

    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("SummaryPanel")
        self.setStyleSheet(_PANEL_STYLE)
        self.setMinimumWidth(300)
        self._base_dir = Path(base_dir).resolve()
        self._raw_content = ""

        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.setContentsMargins(12, 12, 12, 12)

               
        title = QLabel("📊 Project summary")
        title.setFont(QFont("sans-serif", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: #61afef;")
        v.addWidget(title)
        v.addWidget(_make_sep())

               
        self._stat_files      = StatRow("Selected files:")
        self._stat_compressed = StatRow("Compressed files ★:")
        self._stat_tokens     = StatRow("Selected tokens:")
        self._stat_dir        = StatRow("Directory:")
        for r in (self._stat_files, self._stat_compressed, self._stat_tokens, self._stat_dir):
            v.addWidget(r)
        self._stat_dir.set_value(str(self._base_dir))
        v.addWidget(_make_sep())

                        
        self._gen_btn = QPushButton("▶  Generate summary")
        self._gen_btn.setObjectName("gen_btn")
        self._gen_btn.setMinimumHeight(40)
        self._gen_btn.clicked.connect(self.generate_requested)
        v.addWidget(self._gen_btn)

                                       
        self._prog_label = QLabel("")
        self._prog_label.setStyleSheet("color: #5c6370; font-size: 11px;")
        self._prog_label.setWordWrap(True)
        self._prog_label.setVisible(False)
        v.addWidget(self._prog_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        v.addWidget(self._progress)

             
        log_hdr = QHBoxLayout()
        lbl = QLabel("Log:"); lbl.setStyleSheet("color:#5c6370;font-size:11px;")
        log_hdr.addWidget(lbl); log_hdr.addStretch()
        clr = QPushButton("Clear")
        clr.setStyleSheet("QPushButton{font-size:10px;padding:2px 6px;"
                          "background:#2c313a;color:#5c6370;border:1px solid #3e4451;border-radius:3px;}"
                          "QPushButton:hover{color:#abb2bf;}")
        log_hdr.addWidget(clr)
        v.addLayout(log_hdr)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(100)
        self._log.setPlaceholderText("Generation messages will appear here…")
        v.addWidget(self._log)
        clr.clicked.connect(self._log.clear)

        v.addWidget(_make_sep())

                                 
        prev_hdr = QHBoxLayout()
        prev_hdr.addWidget(QLabel("Preview:"))
        prev_hdr.addStretch()

        self._raw_btn = QPushButton("Raw")
        self._raw_btn.setObjectName("action_btn")
        self._raw_btn.setCheckable(True)
        self._raw_btn.setFixedWidth(52)
        self._raw_btn.toggled.connect(self._toggle_view)
        prev_hdr.addWidget(self._raw_btn)

        self._copy_btn = QPushButton("📋 Copier")
        self._copy_btn.setObjectName("action_btn")
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        prev_hdr.addWidget(self._copy_btn)
        v.addLayout(prev_hdr)

                           
        self._stack = QStackedWidget()
        self._html_view = QTextEdit()
        self._html_view.setReadOnly(True)
        self._html_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._html_view.setPlaceholderText(
            "The generated summary will appear here.\n\nSelect files then click 'Generate'."
        )
        self._raw_view = QPlainTextEdit()
        self._raw_view.setReadOnly(True)
        self._raw_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._stack.addWidget(self._html_view)
        self._stack.addWidget(self._raw_view)
        v.addWidget(self._stack)

                                                                             

    def update_stats(self, selected: list, compressed: list, tokens: int) -> None:
        self._stat_files.set_value(str(len(selected)))
        self._stat_compressed.set_value(str(len(compressed)))
        self._stat_tokens.set_value(_fmt_tokens(tokens))
        self._gen_btn.setEnabled(len(selected) > 0)

    def set_base_dir(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir).resolve()
        self._stat_dir.set_value(str(self._base_dir))

                                                                             

    def start_generation(self) -> None:
        self._log.clear()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._prog_label.setText("Initializing…")
        self._prog_label.setVisible(True)
        self._gen_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)

    def update_progress(self, message: str, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        else:
            self._progress.setRange(0, 0)
        display = message if len(message) <= 60 else "…" + message[-57:]
        self._prog_label.setText(display)

    def append_log(self, message: str) -> None:
        self._log.appendPlainText(message)
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())

    def finish_generation(self, token_count: int) -> None:
        self._progress.setVisible(False)
        self._prog_label.setVisible(False)
        self._gen_btn.setEnabled(True)
        self._stat_tokens.set_value(_fmt_tokens(token_count))

        summary_path = (
            summary_utils.get_summary_dir(self._base_dir)
            / summary_utils.CODE_SUMMARY_FILENAME
        )
        if summary_path.exists():
            try:
                content = summary_path.read_text(encoding="utf-8")
                self._set_preview(content)
                self._copy_btn.setEnabled(True)
            except Exception as exc:
                self._html_view.setPlainText(f"Read error: {exc}")
        else:
            self._html_view.setPlainText("Summary file not found.")

    def show_error(self, message: str) -> None:
        self._progress.setVisible(False)
        self._prog_label.setVisible(False)
        self._gen_btn.setEnabled(True)
        self.append_log(f"❌ Error: {message}")

                                                                             

    def _set_preview(self, content: str) -> None:
        self._raw_content = content
        self._html_view.setHtml(_md_to_html(content))
        self._raw_view.setPlainText(content)
        for view in (self._html_view, self._raw_view):
            view.moveCursor(QTextCursor.MoveOperation.Start)

    def _toggle_view(self, raw: bool) -> None:
        self._stack.setCurrentIndex(1 if raw else 0)
        self._raw_btn.setText("Rendered" if raw else "Raw")

                                                                             

    def _copy_to_clipboard(self) -> None:
        try:
            summary_utils.copy_summary_to_clipboard(self._base_dir)
            self.append_log("✓ Copied to clipboard!")
        except Exception as exc:
            self.append_log(f"⚠ Failed to copy: {exc}")
