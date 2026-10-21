"""Dateibrowser (SFTP): lokale und entfernte Dateien nebeneinander, Übertragen per Drag & Drop oder Knopf."""
from __future__ import annotations

import json
import os
import posixpath
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSplitter, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import sftp
from ..models import Config, Connection
from . import style, theme

MIME = "application/x-sesam-files"
NAME_ROLE = Qt.ItemDataRole.UserRole
_ORPHANS: set = set()  # noch laufende Threads geschlossener Fenster: sie dürfen nicht vorzeitig zerstört werden


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}".replace(".", ",")
        value /= 1024
    return ""


def format_time(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M") if timestamp else ""
    except (OverflowError, OSError, ValueError):
        return ""


class Job(QThread):
    """Führt eine blockierende Funktion im Hintergrund aus. ``fn(job)`` darf ``job.progress`` und ``job.cancel_event`` nutzen."""

    result = Signal(object)
    failed = Signal(object)  # die Ausnahme
    progress = Signal(int, int, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self.cancel_event = threading.Event()

    def run(self):
        try:
            value = self._fn(self)
        except BaseException as exc:  # auch Abbruch und unerwartete Fehler dürfen den Thread nicht still beenden
            self.failed.emit(exc)
        else:
            self.result.emit(value)


class _Item(QTreeWidgetItem):
    """Zeile der Dateiliste: Ordner stehen beim Sortieren immer oben."""

    def __init__(self, name: str, is_dir: bool, size: int, mtime: float, is_link: bool = False):
        super().__init__([name, "" if is_dir else format_size(size), format_time(mtime)])
        self.name, self.is_dir, self.size, self.mtime = name, is_dir, size, mtime
        self.setData(0, NAME_ROLE, name)
        self.setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if is_link:
            self.setToolTip(0, "Verknüpfung")

    def __lt__(self, other):
        tree = self.treeWidget()
        ascending = tree.header().sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
        if self.is_dir != other.is_dir:
            return self.is_dir if ascending else not self.is_dir
        column = tree.sortColumn()
        if column == 1:
            return (self.size, self.name.casefold()) < (other.size, other.name.casefold())
        if column == 2:
            return (self.mtime, self.name.casefold()) < (other.mtime, other.name.casefold())
        return self.name.casefold() < other.name.casefold()


class FileList(QTreeWidget):
    """Dateiliste mit Drag & Drop. Sie verändert nichts selbst, sondern meldet nur, was abgelegt wurde."""

    dropped = Signal(str, str, list, str)  # Quellseite, Quellordner, Namen, Zielordner ("" = aktueller Ordner)
    dropped_urls = Signal(list, str)  # Pfade aus dem Dateimanager, Zielordner
    open_requested = Signal(str)  # Ordnername (Doppelklick)

    def __init__(self, side: str, accept_urls: bool):
        super().__init__()
        self.side = side
        self.directory = ""
        self._accept_urls = accept_urls
        self.setHeaderLabels(["Name", "Größe", "Geändert"])
        self.headerItem().setTextAlignment(1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)  # über den Zahlen
        self._icons = (QIcon(), QIcon())  # (Ordner, Datei); werden vom Panel passend zur Darstellung gesetzt
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        header = self.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(60)
        self.setColumnWidth(0, 260)
        self.setColumnWidth(1, 90)
        self.itemDoubleClicked.connect(lambda it, _c: self.open_requested.emit(it.name) if it.is_dir else None)

    def show_entries(self, directory: str, entries):
        self.directory = directory
        self.setSortingEnabled(False)
        self.clear()
        for e in entries:
            item = _Item(e["name"], e["is_dir"], e["size"], e["mtime"], e.get("is_link", False))
            item.setIcon(0, self._icons[0] if item.is_dir else self._icons[1])
            self.addTopLevelItem(item)
        self.setSortingEnabled(True)

    def set_icons(self, folder: QIcon, file: QIcon, size: int):
        self._icons = (folder, file)
        self.setIconSize(QSize(size, size))
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            item.setIcon(0, folder if item.is_dir else file)

    def selected(self) -> list[_Item]:
        return [i for i in self.selectedItems() if isinstance(i, _Item)]

    def names(self) -> list[str]:
        return [i.name for i in self.selected()]

    def entry_names(self) -> set[str]:
        return {self.topLevelItem(i).name for i in range(self.topLevelItemCount())}

    # --- Drag & Drop ---
    def mimeData(self, items):
        data = QMimeData()
        payload = {"side": self.side, "dir": self.directory, "names": [i.name for i in items if isinstance(i, _Item)]}
        data.setData(MIME, json.dumps(payload).encode("utf-8"))
        return data

    def _acceptable(self, mime: QMimeData) -> bool:
        if mime.hasFormat(MIME):
            try:
                return json.loads(bytes(mime.data(MIME)).decode("utf-8"))["side"] != self.side
            except (ValueError, KeyError):
                return False
        return self._accept_urls and mime.hasUrls()

    def dragEnterEvent(self, event):
        if self._acceptable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._acceptable(event.mimeData()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        mime = event.mimeData()
        if not self._acceptable(mime):
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        target = item.name if isinstance(item, _Item) and item.is_dir else ""
        if mime.hasFormat(MIME):
            payload = json.loads(bytes(mime.data(MIME)).decode("utf-8"))
            self.dropped.emit(payload["side"], payload["dir"], payload["names"], target)
        else:
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if paths:
                self.dropped_urls.emit(paths, target)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()


class FilePanel(QWidget):
    """Eine Seite des Browsers: Titel, Pfadzeile mit Knöpfen und Dateiliste."""

    navigate = Signal(str)  # gewünschter Ordner
    refresh_requested = Signal()
    go_home = Signal()
    new_folder = Signal()
    rename = Signal()
    delete = Signal()
    transfer = Signal()  # Auswahl auf die andere Seite übertragen

    def __init__(self, side: str, title: str, accept_urls: bool, sep: str, can_delete: bool, transfer_text: str):
        super().__init__()
        self.side, self.sep, self._can_delete, self._transfer_text = side, sep, can_delete, transfer_text
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.up_button = QToolButton()
        self.up_button.setToolTip("Eine Ebene höher")
        self.home_button = QToolButton()
        self.home_button.setToolTip("Persönlicher Ordner")
        self.refresh_button = QToolButton()
        self.refresh_button.setToolTip("Aktualisieren (F5)")
        self.path = QLineEdit()
        self.path.setPlaceholderText("Pfad")
        self.list = FileList(side, accept_urls)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.open_requested.connect(self._open_child)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for w in (self.up_button, self.home_button, self.refresh_button):
            bar.addWidget(w)
        bar.addWidget(self.path, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.title)
        lay.addLayout(bar)
        lay.addWidget(self.list, 1)

        self.up_button.clicked.connect(lambda: self.navigate.emit(self._parent_of(self.list.directory)))
        self.home_button.clicked.connect(self.go_home)
        self.refresh_button.clicked.connect(self.refresh_requested)
        self.path.returnPressed.connect(lambda: self.navigate.emit(self.path.text().strip()))
        keys = [("F5", self.refresh_requested.emit), ("F2", self.rename.emit), ("Ctrl+Shift+N", self.new_folder.emit)]
        if can_delete:
            keys.append(("Del", self.delete.emit))
        for key, slot in keys:
            shortcut = QShortcut(QKeySequence(key), self.list)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        focus_path = QShortcut(QKeySequence("Ctrl+L"), self)
        focus_path.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        focus_path.activated.connect(self.path.setFocus)
        self.refresh_icons()

    def refresh_icons(self):
        c = theme.colors()
        size = max(14, round(18 * theme.scale() / 100))
        for button, name in ((self.up_button, "arrow_up"), (self.home_button, "home"), (self.refresh_button, "sync")):
            button.setIcon(style.icon(name, c["text"], c["muted"]))
            button.setIconSize(QSize(size, size))
        self.list.set_icons(style.icon("folder", c["accent"], c["muted"]), style.icon("file", c["muted"], c["muted"]), size)

    def _parent_of(self, directory: str) -> str:
        if self.sep == "/":
            return posixpath.dirname(directory.rstrip("/")) or "/"
        parent = os.path.dirname(directory.rstrip("\\/"))
        return parent or directory

    def child_path(self, name: str) -> str:
        return posixpath.join(self.list.directory, name) if self.sep == "/" else os.path.join(self.list.directory, name)

    def _open_child(self, name: str):
        self.navigate.emit(self.child_path(name))

    def show_entries(self, directory: str, entries):
        self.path.setText(directory)
        self.list.show_entries(directory, entries)

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is not None and not item.isSelected():
            self.list.clearSelection()
            item.setSelected(True)
        has_selection = bool(self.list.selected())
        menu = QMenu(self)
        transfer = menu.addAction(self._transfer_text)
        transfer.setEnabled(has_selection)
        transfer.triggered.connect(lambda: self.transfer.emit())
        menu.addSeparator()
        menu.addAction("Neuer Ordner …").triggered.connect(lambda: self.new_folder.emit())
        rename = menu.addAction("Umbenennen …")
        rename.setEnabled(len(self.list.selected()) == 1)
        rename.triggered.connect(lambda: self.rename.emit())
        if self._can_delete:
            delete = menu.addAction("Löschen")
            delete.setEnabled(has_selection)
            delete.triggered.connect(lambda: self.delete.emit())
        menu.addSeparator()
        menu.addAction("Aktualisieren").triggered.connect(lambda: self.refresh_requested.emit())
        menu.exec(self.list.viewport().mapToGlobal(pos))


class TransferRow(QWidget):
    """Eine laufende oder beendete Übertragung mit Fortschrittsbalken."""

    def __init__(self, text: str, job: Job):
        super().__init__()
        self.job = job
        self.label = QLabel(text)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("Plane …")
        self.cancel = QPushButton("Abbrechen")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label, 2)
        lay.addWidget(self.bar, 3)
        lay.addWidget(self.cancel)
        self.cancel.clicked.connect(self._cancel)
        job.progress.connect(self.set_progress)
        self.finished_state = ""

    def _cancel(self):
        self.job.cancel_event.set()
        self.cancel.setEnabled(False)
        self.bar.setFormat("Abbrechen …")

    def set_progress(self, done: int, total: int, name: str):
        self.bar.setValue(int(1000 * done / total) if total else 1000)
        self.bar.setFormat(f"{format_size(done)} von {format_size(total)} – {name}")

    def finish(self, state: str, message: str):
        self.finished_state = state
        self.cancel.setVisible(False)
        if state == "ok":
            self.bar.setValue(1000)
        self.bar.setFormat(message)
        self.bar.setToolTip(message)


class SftpWindow(QMainWindow):
    closed = Signal(object)

    def __init__(self, cfg: Config, conn: Connection, parent=None):
        super().__init__(parent)
        self.cfg, self.conn = cfg, conn
        self.session: sftp.SftpSession | None = None
        self._jobs: set[Job] = set()
        self._closing = False
        self._password_asked = False
        name = conn.name or conn.host
        self.setWindowTitle(f"Dateien – {name}")
        self.resize(1100, 680)

        self.local = FilePanel("local", "Dieser Rechner", accept_urls=False, sep=os.sep, can_delete=False, transfer_text="Hochladen")
        self.remote = FilePanel("remote", f"Server: {name}", accept_urls=True, sep="/", can_delete=True, transfer_text="Herunterladen")
        self.remote.setEnabled(False)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.local)
        splitter.addWidget(self.remote)

        self.upload_button = QPushButton("Hochladen")
        self.download_button = QPushButton("Herunterladen")
        self.upload_button.setToolTip("Auswahl von links in den Ordner rechts hochladen")
        self.download_button.setToolTip("Auswahl von rechts in den Ordner links herunterladen")
        self.upload_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.clear_button = QPushButton("Liste leeren")
        self.clear_button.setToolTip("Beendete Übertragungen aus der Liste entfernen")
        buttons = QHBoxLayout()
        buttons.addWidget(self.upload_button)
        buttons.addWidget(self.download_button)
        buttons.addStretch(1)
        buttons.addWidget(self.clear_button)

        self.transfers = QWidget()
        self.transfers_layout = QVBoxLayout(self.transfers)
        self.transfers_layout.setContentsMargins(0, 0, 0, 0)
        self.transfers_layout.setSpacing(6)
        self.transfers_layout.addStretch(1)
        self.transfer_scroll = QScrollArea()
        self.transfer_scroll.setWidgetResizable(True)
        self.transfer_scroll.setWidget(self.transfers)
        self.transfer_scroll.setFixedHeight(96)
        self.transfer_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(10)
        lay.addWidget(splitter, 1)
        lay.addLayout(buttons)
        lay.addWidget(self.transfer_scroll)
        self.setCentralWidget(central)
        self.statusBar().setSizeGripEnabled(False)

        self.local.navigate.connect(self.local_go)
        self.local.go_home.connect(lambda: self.local_go(str(Path.home())))
        self.local.refresh_requested.connect(lambda: self.local_go(self.local.list.directory))
        self.local.new_folder.connect(self.local_new_folder)
        self.local.rename.connect(self.local_rename)
        self.local.transfer.connect(self.upload_selected)
        self.remote.navigate.connect(self.remote_go)
        self.remote.go_home.connect(lambda: self.remote_go("."))
        self.remote.refresh_requested.connect(lambda: self.remote_go(self.remote.list.directory))
        self.remote.new_folder.connect(self.remote_new_folder)
        self.remote.rename.connect(self.remote_rename)
        self.remote.delete.connect(self.remote_delete)
        self.remote.transfer.connect(self.download_selected)
        self.upload_button.clicked.connect(self.upload_selected)
        self.download_button.clicked.connect(self.download_selected)
        self.clear_button.clicked.connect(self.clear_finished)
        self.remote.list.dropped.connect(self._dropped_on_remote)
        self.remote.list.dropped_urls.connect(self._urls_on_remote)
        self.local.list.dropped.connect(self._dropped_on_local)
        theme.notifier().changed.connect(self._refresh_icons)
        self._icons_connected = True

        self.local_go(str(Path.home()))
        self._connect()

    # --- Hilfen ---
    def _start(self, fn, on_result=None, on_failed=None) -> Job:
        job = Job(fn, self)
        self._jobs.add(job)
        if on_result:
            job.result.connect(on_result)
        job.failed.connect(on_failed or self._show_error)
        job.finished.connect(lambda j=job: self._jobs.discard(j))
        job.finished.connect(job.deleteLater)
        job.start()
        return job

    def _show_error(self, exc):
        if self._closing:
            return
        self.statusBar().showMessage(str(exc) or exc.__class__.__name__, 10000)
        QMessageBox.warning(self, "Dateien", str(exc) or exc.__class__.__name__)

    def _refresh_icons(self):
        self.local.refresh_icons()
        self.remote.refresh_icons()

    def _update_buttons(self):
        connected = self.session is not None
        self.upload_button.setEnabled(connected)
        self.download_button.setEnabled(connected)

    # --- Verbindung ---
    def _connect(self, password: str | None = None):
        self.statusBar().showMessage("Verbinde …")
        self._start(
            lambda job: sftp.SftpSession.connect(self.cfg, self.conn, password=password),
            self._connected,
            self._connect_failed,
        )

    def _ask_password(self) -> str | None:
        text, ok = QInputDialog.getText(
            self, "Anmeldung", f"Passwort für {self.conn.user + '@' if self.conn.user else ''}{self.conn.host}:",
            QLineEdit.EchoMode.Password,
        )
        return text if ok else None

    def _connect_failed(self, exc):
        if self._closing:
            return
        if isinstance(exc, sftp.SftpAuthError) and not self._password_asked:
            self._password_asked = True  # nur einmal nachfragen; das Passwort gilt nur für dieses Fenster
            password = self._ask_password()
            if password:
                self._connect(password)
                return
        self.statusBar().showMessage("Nicht verbunden")
        QMessageBox.critical(self, "Verbindung fehlgeschlagen", str(exc) or exc.__class__.__name__)
        self.close()

    def _connected(self, session):
        if self._closing:
            session.close()
            return
        self.session = session
        self.remote.setEnabled(True)
        self._update_buttons()
        self.statusBar().showMessage(f"Verbunden mit {session.label}", 5000)
        self.remote_go(".")

    # --- Ordner anzeigen ---
    def local_go(self, path: str):
        path = os.path.expanduser(path or str(Path.home()))
        try:
            entries = []
            with os.scandir(path) as it:
                for e in it:
                    try:
                        st = e.stat()
                        is_dir = e.is_dir()
                    except OSError:
                        continue
                    entries.append({"name": e.name, "is_dir": is_dir, "size": st.st_size, "mtime": st.st_mtime, "is_link": e.is_symlink()})
        except OSError as exc:
            self.statusBar().showMessage(f"Ordner kann nicht gelesen werden: {exc}", 8000)
            self.local.path.setText(self.local.list.directory)
            return
        self.local.show_entries(os.path.abspath(path), entries)

    def remote_go(self, path: str):
        if self.session is None:
            return
        session = self.session

        def work(job):
            target = session.normalize(path or ".")
            return target, session.listdir(target)

        def done(value):
            target, entries = value
            self.remote.show_entries(
                target, [{"name": e.name, "is_dir": e.is_dir, "size": e.size, "mtime": e.mtime, "is_link": e.is_link} for e in entries]
            )

        def failed(exc):
            self.remote.path.setText(self.remote.list.directory)
            self.statusBar().showMessage(str(exc), 8000)

        self._start(work, done, failed)

    # --- Ordner anlegen und umbenennen ---
    def _ask_name(self, title: str, label: str, text: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, text)
        name = name.strip()
        if not ok or not name:
            return None
        if "/" in name or "\\" in name or name in (".", ".."):
            QMessageBox.warning(self, title, "Der Name darf keine Pfadtrenner enthalten.")
            return None
        return name

    def local_new_folder(self):
        name = self._ask_name("Neuer Ordner", "Name des neuen Ordners:")
        if name:
            try:
                os.mkdir(os.path.join(self.local.list.directory, name))
            except OSError as exc:
                QMessageBox.warning(self, "Neuer Ordner", f"Ordner konnte nicht angelegt werden: {exc}")
            self.local_go(self.local.list.directory)

    def local_rename(self):
        names = self.local.list.names()
        if len(names) != 1:
            return
        new = self._ask_name("Umbenennen", "Neuer Name:", names[0])
        if new and new != names[0]:
            try:
                os.rename(self.local.child_path(names[0]), self.local.child_path(new))
            except OSError as exc:
                QMessageBox.warning(self, "Umbenennen", f"Umbenennen fehlgeschlagen: {exc}")
            self.local_go(self.local.list.directory)

    def remote_new_folder(self):
        if self.session is None:
            return
        name = self._ask_name("Neuer Ordner", "Name des neuen Ordners auf dem Server:")
        if name:
            path = self.remote.child_path(name)
            session = self.session
            self._start(lambda job: session.mkdir(path), lambda _v: self.remote_go(self.remote.list.directory), self._refresh_after_error)

    def remote_rename(self):
        names = self.remote.list.names()
        if self.session is None or len(names) != 1:
            return
        new = self._ask_name("Umbenennen", "Neuer Name:", names[0])
        if new and new != names[0]:
            old_path, new_path, session = self.remote.child_path(names[0]), self.remote.child_path(new), self.session
            self._start(lambda job: session.rename(old_path, new_path), lambda _v: self.remote_go(self.remote.list.directory), self._refresh_after_error)

    def _confirm_delete(self, names: list[str]) -> bool:
        shown = "\n".join(names[:8]) + (f"\n… und {len(names) - 8} weitere" if len(names) > 8 else "")
        answer = QMessageBox.question(
            self, "Löschen",
            f"Auf dem Server endgültig löschen (Ordner samt Inhalt)?\n\n{shown}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def remote_delete(self):
        names = self.remote.list.names()
        if self.session is None or not names or not self._confirm_delete(names):
            return
        paths, session = [self.remote.child_path(n) for n in names], self.session

        def work(job):
            for p in paths:
                session.remove(p)

        self._start(work, lambda _v: self.remote_go(self.remote.list.directory), self._refresh_after_error)

    def _refresh_after_error(self, exc):
        self._show_error(exc)
        self.remote_go(self.remote.list.directory)

    # --- Übertragen ---
    def upload_selected(self):
        names = self.local.list.names()
        if names and self.remote.list.directory:
            self._upload([self.local.child_path(n) for n in names], self.remote.list.directory)

    def download_selected(self):
        names = self.remote.list.names()
        if names and self.local.list.directory:
            self._download([self.remote.child_path(n) for n in names], self.local.list.directory)

    def _dropped_on_remote(self, side: str, directory: str, names: list, target: str):
        if side == "local" and names:
            dest = self.remote.child_path(target) if target else self.remote.list.directory
            self._upload([os.path.join(directory, n) for n in names], dest)

    def _urls_on_remote(self, paths: list, target: str):
        dest = self.remote.child_path(target) if target else self.remote.list.directory
        self._upload([p for p in paths if os.path.exists(p)], dest)

    def _dropped_on_local(self, side: str, directory: str, names: list, target: str):
        if side == "remote" and names:
            dest = self.local.child_path(target) if target else self.local.list.directory
            self._download([posixpath.join(directory, n) for n in names], dest)

    def _ask_conflict(self, names: list[str]) -> str | None:
        """„overwrite“, „skip“ oder None (abbrechen)."""
        shown = "\n".join(names[:8]) + (f"\n… und {len(names) - 8} weitere" if len(names) > 8 else "")
        box = QMessageBox(QMessageBox.Icon.Question, "Vorhandene Dateien", f"Am Ziel gibt es schon:\n\n{shown}", parent=self)
        overwrite = box.addButton("Überschreiben", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("Überspringen", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        return "overwrite" if clicked is overwrite else "skip" if clicked is skip else None

    def _upload(self, paths: list[str], remote_dir: str):
        if self.session is None or not paths:
            return
        session = self.session
        # Erst den Zielordner lesen: so sind Namenskonflikte auch beim Ablegen auf einen Unterordner bekannt.
        self._start(
            lambda job: {e.name for e in session.listdir(remote_dir)},
            lambda existing: self._run_transfer("up", paths, remote_dir, existing),
            self._show_error,
        )

    def _download(self, paths: list[str], local_dir: str):
        if self.session is None or not paths:
            return
        try:
            existing = set(os.listdir(local_dir))
        except OSError as exc:
            QMessageBox.warning(self, "Herunterladen", f"Zielordner kann nicht gelesen werden: {exc}")
            return
        self._run_transfer("down", paths, local_dir, existing)

    def _run_transfer(self, direction: str, paths: list[str], target_dir: str, existing: set):
        if self._closing or self.session is None:
            return
        base = (lambda p: posixpath.basename(p.rstrip("/"))) if direction == "down" else (lambda p: os.path.basename(p.rstrip("/\\")))
        conflicts = [base(p) for p in paths if base(p) in existing]
        skip: set[str] = set()
        if conflicts:
            choice = self._ask_conflict(conflicts)
            if choice is None:
                return
            if choice == "skip":
                skip = set(conflicts)
                if len(skip) == len({base(p) for p in paths}):
                    self.statusBar().showMessage("Alles übersprungen", 5000)
                    return
        session = self.session

        def work(job):
            run = session.upload if direction == "up" else session.download
            return run(paths, target_dir, progress=job.progress.emit, cancel=job.cancel_event, skip=skip)

        arrow = "→" if direction == "up" else "←"
        label = (base(paths[0]) if len(paths) == 1 else f"{len(paths)} Elemente")
        job = Job(work, self)
        row = TransferRow(f"{arrow} {label}", job)
        self.transfers_layout.insertWidget(self.transfers_layout.count() - 1, row)
        self._jobs.add(job)

        def ok(count):
            row.finish("ok", f"Fertig ({count} Datei{'en' if count != 1 else ''})")
            self._refresh_target(direction, target_dir)

        def failed(exc):
            if isinstance(exc, sftp.TransferCancelled):
                row.finish("cancelled", "Abgebrochen")
            else:
                row.finish("error", f"Fehler: {exc}")
            self._refresh_target(direction, target_dir)

        job.result.connect(ok)
        job.failed.connect(failed)
        job.finished.connect(lambda j=job: self._jobs.discard(j))
        job.finished.connect(job.deleteLater)
        job.start()

    def _refresh_target(self, direction: str, target_dir: str):
        if self._closing:
            return
        panel = self.remote if direction == "up" else self.local
        current = panel.list.directory
        # Auch beim Ablegen auf einen Unterordner ändert sich nichts in der aktuellen Ansicht – Aktualisieren schadet nicht.
        if direction == "up":
            self.remote_go(current)
        else:
            self.local_go(current)

    def clear_finished(self):
        for i in reversed(range(self.transfers_layout.count())):
            w = self.transfers_layout.itemAt(i).widget()
            if isinstance(w, TransferRow) and w.finished_state:
                self.transfers_layout.removeWidget(w)
                w.deleteLater()

    def active_transfers(self) -> int:
        return sum(1 for i in range(self.transfers_layout.count())
                   if isinstance(self.transfers_layout.itemAt(i).widget(), TransferRow)
                   and not self.transfers_layout.itemAt(i).widget().finished_state)

    # --- Schließen ---
    def _confirm_close(self) -> bool:
        answer = QMessageBox.question(
            self, "Dateien",
            "Es laufen noch Übertragungen. Abbrechen und schließen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event):
        if not self._closing and self.active_transfers() and not self._confirm_close():
            event.ignore()
            return
        self._closing = True
        for job in list(self._jobs):
            job.cancel_event.set()
        for job in list(self._jobs):
            if not job.wait(5000):  # hängt noch (z. B. Verbindungsaufbau): im Hintergrund auslaufen lassen
                _ORPHANS.add(job)
                job.finished.connect(lambda j=job: _ORPHANS.discard(j))
                job.setParent(None)
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
        if self._icons_connected:
            self._icons_connected = False
            theme.notifier().changed.disconnect(self._refresh_icons)
        self.closed.emit(self)
        super().closeEvent(event)
