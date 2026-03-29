#!/usr/bin/env python3
"""
main.py — Point d'entrée racine de CodeSum.

Permet de lancer l'application directement depuis la racine du projet :

    python main.py           # mode TUI curses (défaut)
    python main.py --gui     # mode interface graphique PySide6
    python main.py --configure
    python main.py --mcp-server

Ce fichier ajoute automatiquement `src/` au sys.path pour permettre
l'import du package `codesum` sans installation préalable (utile en
développement).
"""

import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR  = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if __name__ == "__main__":
    try:
        from codesum.app import main
    except ImportError as exc:
        print(
            f"Erreur : impossible d'importer le package codesum.\n"
            f"Assurez-vous d'être à la racine du projet et que le dossier src/ existe.\n"
            f"Détail : {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    main()
