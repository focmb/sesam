from __future__ import annotations

import sys


def _load_config(path):
    """Lädt die Konfiguration. Ist die Datei beschädigt, wird sie gesichert und Sesam startet leer.

    Liefert None, wenn nicht gestartet werden soll (Datei nicht lesbar oder nicht sicherbar) – dann bleibt alles unverändert.
    """
    from PySide6.QtWidgets import QMessageBox

    from . import storage

    try:
        return storage.load(path)
    except storage.ConfigError as exc:
        reason = exc.reason
    except OSError as exc:
        QMessageBox.critical(None, "Konfiguration nicht lesbar", f"Die Konfigurationsdatei kann nicht gelesen werden:\n{path}\n\n{exc}")
        return None
    try:
        backup = storage.quarantine(path)
    except OSError as exc:
        QMessageBox.critical(
            None, "Konfiguration beschädigt",
            f"Die Konfigurationsdatei ist beschädigt ({reason}) und konnte nicht gesichert werden:\n{exc}\n\n"
            "Sesam wird beendet, damit nichts überschrieben wird.")
        return None
    QMessageBox.warning(
        None, "Konfiguration beschädigt",
        f"Die Konfigurationsdatei konnte nicht gelesen werden:\n{reason}\n\n"
        f"Sie wurde nicht gelöscht, sondern gesichert als:\n{backup}\n\n"
        "Sesam startet jetzt mit einer leeren Konfiguration. Passwörter im Schlüsselbund sind nicht betroffen. "
        "Du kannst die gesicherte Datei reparieren und als config.json zurückkopieren. "
        "Nutzt du die Git-Synchronisation, richte sie wieder ein – die Verbindungen kommen dann aus dem Repository zurück.")
    return storage.load(path)


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--connect":
        from .connect_cli import parse_overrides, run

        return run(sys.argv[2], parse_overrides(sys.argv[3:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--askpass":
        from .askpass import main as askpass_main

        return askpass_main(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from . import migration, storage
    from .gui import style, theme
    from .gui.main_window import MainWindow

    migration.run(log=lambda m: print(m, file=sys.stderr))
    app = QApplication(sys.argv)
    app.setApplicationName("Sesam")
    app.setApplicationDisplayName("Sesam")
    app.setDesktopFileName("sesam")  # ordnet das Fenster dem Starter sesam.desktop zu (Dash, Taskleiste)
    app.setWindowIcon(style.app_icon())
    theme.apply(app, "system")  # damit auch Meldungen vor dem Laden der Konfiguration passend aussehen
    path = storage.config_path()
    cfg = _load_config(path)
    if cfg is None:
        return 1
    theme.apply(app, cfg.theme)
    window = MainWindow(cfg, path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
