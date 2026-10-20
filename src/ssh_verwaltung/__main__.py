from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--connect":
        from .connect_cli import run

        return run(sys.argv[2])
    if len(sys.argv) >= 2 and sys.argv[1] == "--askpass":
        from .askpass import main as askpass_main

        return askpass_main(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from . import storage
    from .gui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("SSH-Verwaltung")
    path = storage.config_path()
    window = MainWindow(storage.load(path), path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
