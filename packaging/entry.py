"""Einstiegspunkt für PyInstaller (das Paket nutzt relative Importe und kann nicht direkt als Skript starten)."""
import sys

from sesam.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
