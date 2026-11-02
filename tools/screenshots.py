"""Erzeugt die Screenshots für die README (docs/screenshots) mit frei erfundenen Beispieldaten.

Aufruf im Repository-Ordner (unter Linux ohne sichtbare Fenster, unter Windows blitzen sie kurz auf; die echte Konfiguration und der
Schlüsselbund bleiben unberührt). Bei Bildschirmskalierung über 100 % sind die Bilder entsprechend größer (schärfer):

    python tools/screenshots.py                  # alle Bilder, deutsch, nach docs/screenshots
    python tools/screenshots.py --lang en --out docs/screenshots-en
    python tools/screenshots.py --only hauptfenster snippets

Benötigt PySide6 und paramiko (für den Dateibrowser läuft ein kleiner SFTP-Server aus tests/sftp_server.py auf Loopback).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
if not sys.platform.startswith("win"):
    # Unter Windows kennt das Plugin „offscreen“ die Systemschriften nicht (nur Kästchen statt Text): dort wird die normale
    # Fensterdarstellung benutzt, die Fenster blitzen kurz auf dem Bildschirm auf. Über QT_QPA_PLATFORM lässt sich das ändern.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTreeWidgetItemIterator  # noqa: E402

from sesam import docker, i18n, passwords, proxmox, sftp, storage  # noqa: E402
from sesam.gui import style, theme  # noqa: E402
from sesam.models import Config, Connection, Snippet, Terminal  # noqa: E402

MODES = (("light", "hell"), ("dark", "dunkel"))
PARTS = ("hauptfenster", "verbindung", "dateibrowser", "snippets", "synckonflikt")


def demo_config() -> tuple[Config, dict[str, Connection]]:
    c: dict[str, Connection] = {}

    def add(key, name, host, **kw):
        c[key] = Connection(name=name, host=host, user=kw.pop("user", "admin"), **kw)

    stamp = time.time()  # frisch ermittelt: die Spalte „Betriebssystem“ zeigt die Werte, ohne dass eine Abfrage startet
    add("web1", "web-01", "web-01.example.com", group="Produktion/Webserver", color="green", tags=["prod", "nginx"], use_password=True,
        os_info="Debian GNU/Linux 12 (bookworm)", os_checked=stamp)
    add("web2", "web-02", "web-02.example.com", group="Produktion/Webserver", color="green", tags=["prod", "nginx"],
        os_info="Debian GNU/Linux 12 (bookworm)", os_checked=stamp)
    add("db", "db-01", "db-01.example.com", group="Produktion", color="red", tags=["prod", "postgres"], sudo_mode="login",
        os_info="Ubuntu 24.04.1 LTS", os_checked=stamp)
    add("docker", "docker-host", "docker.example.com", group="Produktion", color="blue", tags=["docker"], docker=True,
        docker_shell="/bin/bash", os_info="Ubuntu 24.04.1 LTS", os_checked=stamp)
    add("pve", "pve-01", "pve.example.com", group="Labor", color="purple", tags=["proxmox"], proxmox=True, user="root",
        os_info="Debian GNU/Linux 12 (bookworm)", os_checked=stamp)
    add("sw", "switch-keller", "10.0.0.2", group="Labor", color="orange", tags=["netz"], cisco=True,
        os_info="Cisco IOS 15.2(7)E14 (C3560CX)", os_checked=stamp)
    add("nas", "nas", "nas.example.com", group="Privat", color="teal", port=2222, user="christian",
        os_info="Alpine Linux v3.20", os_checked=stamp)
    add("pi", "raspberry", "192.168.1.20", group="Privat", user="pi", tags=["iot"],
        os_info="Debian GNU/Linux 12 (bookworm)", os_checked=stamp)
    cfg = Config(connections=list(c.values()))
    term = Terminal("Windows Terminal", "wt.exe new-tab --title {title} {ssh_args}", platform="windows")
    cfg.terminals = [term, Terminal("GNOME Terminal", "gnome-terminal --title {title} -- {ssh_args}", platform="linux")]
    cfg.default_terminal_id = term.id
    cfg.hidden_columns = ["terminal"]  # Platz für die Spalte „Betriebssystem“ (sonst standardmäßig ausgeblendet)
    cfg.column_widths = [250, 230, 150, 150, 260]  # Name, Ziel, Terminal, Tags, Betriebssystem: lange Werte ungekürzt
    cfg.groups = ["Produktion", "Produktion/Webserver", "Labor", "Privat"]
    cfg.group_colors = {"Produktion": "red", "Labor": "purple"}
    cfg.snippets = [
        Snippet("Speicherplatz prüfen", command="df -h && free -m", category="System"),
        Snippet("Dienste neu starten", command="systemctl restart nginx", category="System", sudo=True),
        Snippet("Journal der letzten Stunde", command="journalctl --since '1 hour ago' -p warning", category="System/Logs", sudo=True),
        Snippet("Container anzeigen", command="docker ps --format 'table {{.Names}}\t{{.Status}}'", category="Docker"),
        Snippet("Docker-Aufräumen", command="docker system prune -f", category="Docker", after="wait"),
        Snippet("Uptime", command="uptime", after="wait"),
    ]
    return cfg, c


def grab(widget, path: Path) -> None:
    widget.show()
    for _ in range(5):
        QApplication.processEvents()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not widget.grab().save(str(path)):
        raise SystemExit(f"Konnte {path} nicht schreiben")
    print("geschrieben:", path)


def main_window(cfg, conns, tmp: Path, out: Path, label: str) -> None:
    from sesam.gui.main_window import MainWindow

    w = MainWindow(cfg, tmp / "config.json")
    w.resize(1180, 680)
    w.show()
    QApplication.processEvents()
    boxes = [
        docker.Container("a1", "nginx", "nginx:1.27", "Up 3 days", "0.0.0.0:80->80/tcp", "running"),
        docker.Container("b2", "postgres", "postgres:16", "Up 3 days", "5432/tcp", "running"),
        docker.Container("c3", "redis", "redis:7", "Up 2 hours", "6379/tcp", "running"),
        docker.Container("d4", "backup-job", "restic/restic:latest", "Exited (0) 6 hours ago", "", "exited"),
    ]
    guests = [
        proxmox.Guest(100, "dns", "lxc", "running", "pve-01"),
        proxmox.Guest(101, "nextcloud", "qemu", "running", "pve-01"),
        proxmox.Guest(102, "testvm", "qemu", "stopped", "pve-01"),
    ]
    for key, data in (("docker", boxes), ("pve", guests)):  # Beispieldaten statt einer SSH-Abfrage
        w._containers[conns[key].id] = data
        w._docker_expanded.add(conns[key].id)
    w.refresh()
    item = w._find_item(conns["web1"].id)
    if item is not None:
        w.tree.setCurrentItem(item)
    w.resize(1180, 680)
    grab(w, out / f"hauptfenster-{label}.png")
    w.close()


def connection_dialog(cfg, conns, out: Path) -> None:
    from sesam.gui.connection_dialog import ConnectionDialog

    d = ConnectionDialog(cfg, conns["docker"])
    d.adjustSize()
    grab(d, out / "verbindung-bearbeiten.png")
    d.close()


def sync_conflict_dialog(cfg, conns, out: Path, label: str) -> None:
    from dataclasses import replace

    from sesam.gui.sync_conflict_dialog import SyncConflictDialog
    from sesam.syncing import Conflict, item_from_obj

    def conflict(key: str, **remote_changes) -> Conflict:
        local = conns[key]
        return Conflict(("c", local.id), item_from_obj("c", local, None), item_from_obj("c", replace(local, **remote_changes), None), local.id)

    d = SyncConflictDialog([conflict("nas", port=22, user="admin"), conflict("web1", host="web-01.example.org")])
    d.adjustSize()
    grab(d, out / f"sync-konflikt-{label}.png")
    d.close()


def snippets_dialog(cfg, conns, out: Path, label: str) -> None:
    from sesam.gui.snippets_dialog import SnippetsDialog

    d = SnippetsDialog(cfg, lambda: None, lambda s, c: None, [conns["web1"].id])
    it = QTreeWidgetItemIterator(d.tree)
    while it.value():  # alles aufklappen und ein Snippet mit sudo auswählen
        it.value().setExpanded(True)
        if it.value().text(0) == "Journal der letzten Stunde":
            d.tree.setCurrentItem(it.value())
        it += 1
    d.resize(760, 520)
    grab(d, out / f"snippets-{label}.png")
    d.close()


def sftp_window(cfg, conns, tmp: Path, out: Path, label: str) -> None:
    import paramiko

    from sesam.gui.sftp_window import SftpWindow
    from sftp_server import SftpTestServer

    remote, local = tmp / "remote", tmp / "Projekte"
    for folder in (remote / "backups", remote / "html", remote / "logs", local / "dokumente", local / "bilder"):
        folder.mkdir(parents=True, exist_ok=True)
    for base, files in ((remote, {"index.html": 4_200, "nginx.conf": 2_800, "deploy.sh": 640, "backup-2026-10-22.tar.gz": 48_300_000}),
                        (local, {"bericht.pdf": 1_250_000, "notizen.txt": 3_100, "server-liste.csv": 18_400, "update.sh": 900})):
        for name, size in files.items():
            with open(base / name, "wb") as f:
                f.truncate(size)  # dünn besetzte Datei: zeigt die Größe, belegt aber kaum Platz
    server = SftpTestServer(str(remote), host_key=paramiko.RSAKey.generate(2048))
    try:
        conn = Connection(name="web-01", host="127.0.0.1", port=server.port, user="tester", use_password=True)
        with mock.patch.object(passwords, "get_password", lambda _i: "geheim"), \
                mock.patch.object(sftp, "default_known_hosts", lambda: tmp / "known_hosts"):
            w = SftpWindow(cfg, conn)
            w.resize(1100, 640)
            w.local_go(str(local))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not (w.session is not None and w.remote.list.directory == "/"):
                QApplication.processEvents()
                time.sleep(0.01)
            if w.session is None:
                raise SystemExit("Der Dateibrowser konnte sich nicht mit dem Test-Server verbinden.")
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.01)
            grab(w, out / f"dateibrowser-{label}.png")
            w.close()
    finally:
        server.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "docs" / "screenshots"), help="Zielordner")
    ap.add_argument("--lang", default="de", choices=("de", "en"), help="Sprache der Oberfläche")
    ap.add_argument("--only", nargs="+", choices=PARTS, help="nur diese Bilder erzeugen")
    args = ap.parse_args()
    parts = set(args.only or PARTS)
    out = Path(args.out)

    app = QApplication(sys.argv)
    app.setApplicationName("Sesam")
    app.setWindowIcon(style.app_icon())
    i18n.set_language(args.lang)
    with tempfile.TemporaryDirectory() as tmpname, \
            mock.patch.object(passwords, "get_password", lambda _i: None), \
            mock.patch.object(passwords, "set_password", lambda _i, _p: None), \
            mock.patch.object(passwords, "delete_password", lambda _i: None):
        tmp = Path(tmpname)
        cfg, conns = demo_config()
        storage.save(cfg, tmp / "config.json")
        for mode, label in MODES:
            theme.apply(app, mode)
            theme.apply_scale(app, 100)
            if "hauptfenster" in parts:
                main_window(cfg, conns, tmp, out, label)
            if "verbindung" in parts and mode == "light":
                connection_dialog(cfg, conns, out)
            if "snippets" in parts:
                snippets_dialog(cfg, conns, out, label)
            if "synckonflikt" in parts:
                sync_conflict_dialog(cfg, conns, out, label)
            if "dateibrowser" in parts:
                sftp_window(cfg, conns, tmp, out, label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
