from __future__ import annotations

import sys

from .i18n import tr


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
        QMessageBox.critical(None, tr("Konfiguration nicht lesbar"), tr("Die Konfigurationsdatei kann nicht gelesen werden:\n{path}\n\n{exc}", path=path, exc=exc))
        return None
    try:
        backup = storage.quarantine(path)
    except OSError as exc:
        QMessageBox.critical(
            None, tr("Konfiguration beschädigt"),
            tr("Die Konfigurationsdatei ist beschädigt ({reason}) und konnte nicht gesichert werden:\n{exc}\n\nSesam wird beendet, damit nichts überschrieben wird.", reason=reason, exc=exc))
        return None
    QMessageBox.warning(
        None, tr("Konfiguration beschädigt"),
        tr("Die Konfigurationsdatei konnte nicht gelesen werden:\n{reason}\n\nSie wurde nicht gelöscht, sondern gesichert als:\n{backup}\n\nSesam startet jetzt mit einer leeren Konfiguration. Passwörter im Schlüsselbund sind nicht betroffen. Du kannst die gesicherte Datei reparieren und als config.json zurückkopieren. Nutzt du die Git-Synchronisation, richte sie wieder ein – die Verbindungen kommen dann aus dem Repository zurück.", reason=reason, backup=backup))
    return storage.load(path)


_crash_log = None  # offen halten: faulthandler schreibt beim Absturz direkt in diese Datei


def _enable_crash_log(directory) -> None:
    """Schreibt bei einem harten Absturz (z. B. Stack-Überlauf in Qt) die Python-Stacks nach ``crash.log`` im Konfigurationsordner.

    Das Fensterprogramm unter Windows hat keine Konsole, ohne diese Datei bliebe der Absturz spurlos."""
    global _crash_log
    import datetime
    import faulthandler

    try:
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "crash.log"
        if log.exists() and log.stat().st_size > 256 * 1024:  # nicht unbegrenzt wachsen lassen
            log.replace(directory / "crash.log.old")
        _crash_log = open(log, "a", encoding="utf-8", buffering=1)
        _crash_log.write(f"\n--- Start {datetime.datetime.now().isoformat(timespec='seconds')} ---\n")
        faulthandler.enable(_crash_log, all_threads=True)
    except OSError:
        pass  # Diagnose darf den Start nie verhindern


def main() -> int:
    from . import i18n

    i18n.load_saved_language()  # auch Meldungen vor dem Laden der Konfiguration und der Befehlszeilenmodus sprechen die gewählte Sprache
    if len(sys.argv) >= 3 and sys.argv[1] == "--connect":
        from .connect_cli import parse_overrides, run

        return run(sys.argv[2], parse_overrides(sys.argv[3:]))
    if len(sys.argv) >= 2 and sys.argv[1] == "--askpass":
        from .askpass import main as askpass_main

        return askpass_main(sys.argv[2:])

    from PySide6.QtWidgets import QApplication

    from . import migration, storage
    from .gui import safe, style, theme
    from .gui.main_window import MainWindow

    migration.run(log=lambda m: print(m, file=sys.stderr))
    app = QApplication(sys.argv)
    safe.install_plain_message_boxes()  # Meldungen mit fremdem Text nie als HTML anzeigen
    app.setApplicationName("Sesam")
    app.setApplicationDisplayName("Sesam")
    app.setDesktopFileName("sesam")  # ordnet das Fenster dem Starter sesam.desktop zu (Dash, Taskleiste)
    app.setWindowIcon(style.app_icon())
    theme.apply(app, "system")  # damit auch Meldungen vor dem Laden der Konfiguration passend aussehen
    path = storage.config_path()
    _enable_crash_log(path.parent)
    cfg = _load_config(path)
    if cfg is None:
        return 1
    i18n.set_language(cfg.language)
    theme.apply(app, cfg.theme)
    theme.apply_scale(app, cfg.font_scale)
    window = MainWindow(cfg, path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
