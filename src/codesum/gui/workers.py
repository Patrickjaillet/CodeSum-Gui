"""
workers.py — QThread workers for CodeSum long operations — Phase 4.

Contient :
  - FileScanner  : scanne le système de files en arrière-plan
  - TokenCounter: counts tokens of a file in the background (QRunnable)
  - SummaryWorker: génère le résumé de code en arrière-plan
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QThread, Signal, QRunnable, QObject, Slot

                                                               
from .. import file_utils
from .. import openai_utils
from .. import summary_utils


                                                                             
             
                                                                             

class FileScanner(QThread):
    """
    Scanne le répertoire de projet et construit le tree dict (même structure
    que dans tui.py) sans bloquer le thread Qt principal.

    Signals:
        finished(dict)  — tree dict émis à la fin du scan
        error(str)      — message d'erreur en cas de problème
    """

    finished = Signal(dict)
    error = Signal(str)

    def __init__(
        self,
        base_dir: Path,
        gitignore_specs,
        ignore_list: List[str],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.base_dir = Path(base_dir).resolve()
        self.gitignore_specs = gitignore_specs
        self.ignore_list = ignore_list

    @Slot()
    def run(self) -> None:
        try:
            tree = file_utils.build_tree_with_folders(
                self.base_dir,
                self.gitignore_specs,
                self.ignore_list,
            )
            self.finished.emit(tree)
        except Exception as exc:
            self.error.emit(str(exc))


                                                                             
                                                
                                                                             

class TokenCounterSignals(QObject):
    """Signals pour TokenCounter (QRunnable ne peut pas définir de signals directement)."""

    result = Signal(str, int)                                 


class TokenCounter(QRunnable):
    """
    Counts tokens for a single file via openai_utils.count_tokens().
    Destiné à être soumis à un QThreadPool pour ne pas bloquer l'UI.

    Le résultat est émis via TokenCounterSignals.result(file_path, count).
    count is -1 on error or unreadable file.
    """

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path
        self.signals = TokenCounterSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        count = -1
        try:
            path = Path(self.file_path)
            if path.exists() and path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace")
                count = openai_utils.count_tokens(content)
        except Exception:
            count = -1
        self.signals.result.emit(self.file_path, count)


                                                                             
               
                                                                             

class SummaryWorker(QThread):
    """
    Generates code summary file by file with detailed progress.

    Signals:
        progress(str, int, int) — (message, files_traités, total_files)
        log(str)                — ligne de log à afficher dans le panneau
        finished(int)           — nombre de tokens du résumé généré
        error(str)              — message d'erreur fatale
    """

    progress = Signal(str, int, int)                           
    log      = Signal(str)                           
    finished = Signal(int)                                
    error    = Signal(str)

    def __init__(
        self,
        selected_files: List[str],
        base_dir: Path,
        compressed_files: List[str],
        openai_client,                                 
        llm_model: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.selected_files    = list(selected_files)
        self.base_dir          = Path(base_dir).resolve()
        self.compressed_files  = list(compressed_files)
        self.openai_client     = openai_client
        self.llm_model         = llm_model

    @Slot()
    def run(self) -> None:
        try:
            total = len(self.selected_files)
            compressed_set = set(self.compressed_files)
            summary_dir = summary_utils.get_summary_dir(self.base_dir)
            summary_path = summary_dir / summary_utils.CODE_SUMMARY_FILENAME

            self.progress.emit("⚙ Préparation…", 0, total)
            self.log.emit(f"📁 Projet : {self.base_dir}")
            self.log.emit(f"📄 {total} files · {len(self.compressed_files)} ★ compressed")

                                                                                 
            self._generate_with_progress(total, compressed_set, summary_path)

                             
            token_count = 0
            if summary_path.exists():
                self.progress.emit("🔢 Comptage des tokens…", total, total)
                try:
                    content = summary_path.read_text(encoding="utf-8")
                    token_count = openai_utils.count_tokens(content)
                    self.log.emit(f"🔢 Total : {token_count:,} tokens")
                except Exception as exc:
                    self.log.emit(f"⚠ Error comptage tokens : {exc}")

                                  
            self.progress.emit("📋 Copie dans le presse-papiers…", total, total)
            try:
                summary_utils.copy_summary_to_clipboard(self.base_dir)
                self.log.emit("✓ Résumé copié dans le presse-papiers !")
            except Exception as exc:
                self.log.emit(f"⚠ Failed to copy: {exc}")

            self.finished.emit(token_count)

        except Exception as exc:
            self.error.emit(str(exc))

    def _generate_with_progress(
        self, total: int, compressed_set: set, summary_path: Path
    ) -> None:
        """
        Replays create_code_summary() with per-file signal emission.
        On n'appelle PAS create_code_summary() directement pour avoir la granularité.
        """
        import json
        from .. import file_utils

        summary_dir  = summary_utils.get_summary_dir(self.base_dir)
        project_root = self.base_dir

        if not summary_dir.exists():
            self.log.emit(f"⚠ Répertoire .summary_files introuvable, création…")
            summary_utils.create_hidden_directory(self.base_dir)

        try:
            gitignore_specs = file_utils.parse_gitignore(project_root)
            tree_output = file_utils.get_tree_output(
                project_root, gitignore_specs, file_utils.DEFAULT_IGNORE_LIST
            )
        except Exception as exc:
            tree_output = f"(erreur : {exc})"

        with open(summary_path, "w", encoding="utf-8") as out:
            out.write(f"Project Root: {project_root}\n")
            out.write(f"Project Structure:\n```\n{tree_output}\n```\n\n---\n")

            for idx, file_path_str in enumerate(self.selected_files):
                file_path = Path(file_path_str)
                try:
                    rel = file_path.relative_to(project_root).as_posix()
                except ValueError:
                    rel = file_path.name

                label = f"[{idx+1}/{total}] {rel}"
                if file_path_str in compressed_set:
                    label += "  ★"
                self.progress.emit(label, idx + 1, total)
                self.log.emit(f"{'★' if file_path_str in compressed_set else '•'} {rel}")

                lang = file_path.suffix.lstrip(".") if file_path.suffix else ""

                try:
                    if file_path_str in compressed_set and self.openai_client and self.llm_model:
                        out.write(f"## File: {rel} [AI Compressed]\n\n")
                        content = file_path.read_text(encoding="utf-8", errors="replace")
                        compressed = openai_utils.compress_single_file(
                            self.openai_client, self.llm_model, rel, content
                        )
                        if compressed:
                            out.write(f"{compressed}\n\n---\n")
                        else:
                            out.write(f"```{lang}\n{content}\n```\n---\n")
                    else:
                        out.write(f"## File: {rel}\n\n```{lang}\n")
                        out.write(file_path.read_text(encoding="utf-8", errors="replace"))
                        out.write("\n```\n---\n")

                except FileNotFoundError:
                    out.write(f"## File: {rel}\n\nError: file not found.\n\n---\n")
                    self.log.emit(f"  ⚠ introuvable : {rel}")
                except Exception as exc:
                    out.write(f"## File: {rel}\n\nError : {exc}\n\n---\n")
                    self.log.emit(f"  ⚠ {rel} : {exc}")
