from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--connect":
        from .connect_cli import parse_overrides, run

        return run(sys.argv[2], parse_overrides(sys.argv[3:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--askpass":
        from .askpass import main as askpass_main

        return askpass_main(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from . import migration, storage
    from .gui import theme
    from .gui.main_window import MainWindow

    migration.run(log=lambda m: print(m, file=sys.stderr))
    app = QApplication(sys.argv)
    app.setApplicationName("Sesam")
    app.setApplicationDisplayName("Sesam")
    path = storage.config_path()
    cfg = storage.load(path)
    theme.apply(app, cfg.theme)
    window = MainWindow(cfg, path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
