from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .. import passwords, syncing
from ..models import Config
from ..syncing import Conflict, SyncEngine, SyncError, SyncRetry
from ..i18n import tr


class _Job(QThread):
    """Führt eine blockierende Funktion (Netzwerk) im Hintergrund aus."""

    done = Signal(object, object)  # Ergebnis, Fehler

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as exc:  # noqa: BLE001 - wird in der Oberfläche gemeldet
            self.done.emit(None, exc)


class SyncController(QObject):
    """Steuert den Abgleich: Holen, Committen und Hochladen im Hintergrund, Zusammenführen und Übernehmen im GUI-Thread (keine Wettlaufsituation mit Eingaben)."""

    status = Signal(str, bool)  # Text, ist Fehler
    cfg_changed = Signal()  # der Abgleich hat die Konfiguration verändert
    finished = Signal(object, object, bool)  # Bericht, Fehler, manuell ausgelöst

    DEBOUNCE_MS = 4000
    MAX_RETRIES = 3

    def __init__(
        self,
        cfg: Config,
        save_cfg: Callable[[], None],
        parent=None,
        engine_factory: Callable[[], SyncEngine] | None = None,
        resolver: Callable[[list[Conflict]], dict[str, str] | None] | None = None,
    ):
        super().__init__(parent)
        self.cfg = cfg
        self.save_cfg = save_cfg
        self.engine_factory = engine_factory or self._default_engine
        self.resolver = resolver  # fragt bei Konflikten, welche Version gelten soll (None = lokal behalten)
        self._decisions: dict[str, str] = {}  # Antworten dieses Abgleichs, damit ein Wiederholungsversuch nicht erneut fragt
        self.busy = False
        self.pending = False
        self._manual = False
        self._retries = 0
        self._applying = False
        self._engine: SyncEngine | None = None
        self._jobs: set[_Job] = set()
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(self.DEBOUNCE_MS)
        self.debounce.timeout.connect(lambda: self.sync())

    # ---- Konfiguration ----
    def enabled(self) -> bool:
        s = self.cfg.sync
        return bool(s.enabled and s.url.strip())

    def _default_engine(self) -> SyncEngine:
        s = self.cfg.sync
        phrase = None
        if s.passwords:
            try:
                phrase = passwords.get_password(syncing.PASSPHRASE_KEY)
            except passwords.PasswordError as exc:
                raise SyncError(str(exc)) from exc
        return SyncEngine(s, syncing.work_dir(), phrase, credentials=syncing.load_credentials(s))

    # ---- Auslöser ----
    def schedule(self) -> None:
        """Nach lokalen Änderungen: verzögert abgleichen (nur im Automatikmodus)."""
        if self.enabled() and self.cfg.sync.auto and not self._applying:
            self.debounce.start()

    def sync(self, manual: bool = False) -> None:
        if not self.enabled():
            return
        self.debounce.stop()
        if self.busy:
            self.pending = True
            self._manual = self._manual or manual
            return
        self.busy = True
        self._manual = manual
        self._retries = 0
        self._decisions = {}
        self._start()

    # ---- Ablauf ----
    def _run_job(self, fn, slot) -> None:
        job = _Job(fn, self)
        self._jobs.add(job)
        job.done.connect(slot)
        job.finished.connect(lambda j=job: (self._jobs.discard(j), j.deleteLater()))
        job.start()

    def _start(self) -> None:
        try:
            self._engine = self.engine_factory()
        except Exception as exc:  # noqa: BLE001
            self._finish(None, exc)
            return
        self.status.emit(tr("Synchronisiere …"), False)
        self._run_job(self._engine.fetch, self._after_fetch)

    def _after_fetch(self, remote, exc) -> None:
        if exc:
            self._finish(None, exc)
            return
        self._applying = True
        try:
            report = self._engine.prepare(self.cfg, remote, self._resolve if self.resolver else None)
        except Exception as err:  # noqa: BLE001
            self._finish(None, err)
            return
        finally:
            self._applying = False
        if report.local_changed:
            self.cfg_changed.emit()
        engine = self._engine

        def commit_and_push():  # Repository schreiben, committen (git) und hochladen: langsam, daher im Hintergrund
            engine.commit()
            return engine.push()

        self._run_job(commit_and_push, lambda pushed, err: self._after_push(report, pushed, err))

    def _resolve(self, conflicts: list[Conflict]):
        open_ = [c for c in conflicts if c.token not in self._decisions]
        if open_:
            answer = self.resolver(open_)
            if answer is None:
                return None
            self._decisions.update(answer)
        return dict(self._decisions)

    def _after_push(self, report, pushed, exc) -> None:
        if isinstance(exc, SyncRetry) and self._retries < self.MAX_RETRIES:
            self._retries += 1
            self._start()
            return
        if exc:
            self._finish(None, exc)
            return
        report.pushed = bool(pushed)
        self.cfg.sync.last_sync = syncing.stamp()
        try:
            self.save_cfg()
        except OSError:
            pass
        self._finish(report, None)

    def _finish(self, report, exc) -> None:
        manual = self._manual
        self.busy = False
        self._manual = False
        if exc:
            text = str(exc) or exc.__class__.__name__
            self.status.emit(tr("Sync-Fehler: {text}", text=text), True)
        else:
            self.status.emit(tr("Synchronisiert {last_sync} – {v}", last_sync=self.cfg.sync.last_sync, v=report.summary()), False)
        self.finished.emit(report, exc, manual)
        if self.pending:
            self.pending = False
            QTimer.singleShot(0, lambda: self.sync())
