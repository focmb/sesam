"""Ein Reiter des integrierten Terminals mit einer oder mehreren Sitzungen als Kacheln (verschachtelte Splitter)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .terminal_view import TerminalView


class TerminalTab(QWidget):
    """Hält die Kacheln eines Reiters. Die Sitzungen selbst (Start, Ende, Signale) verwaltet das Hauptfenster."""

    changed = Signal()  # Kacheln hinzugekommen/entfernt oder Fokus gewechselt: Reitertitel neu setzen

    def __init__(self, first: TerminalView, parent=None):
        super().__init__(parent)
        self._root = self._splitter(Qt.Orientation.Horizontal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._root)
        self._active: TerminalView | None = None
        self._zoomed: TerminalView | None = None
        self._root.addWidget(first)
        self._register(first)
        self._active = first
        self._update_tiled()

    @staticmethod
    def _splitter(orientation: Qt.Orientation) -> QSplitter:
        splitter = QSplitter(orientation)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        return splitter

    # --- Abfragen ---
    def views(self) -> list[TerminalView]:
        return self._root.findChildren(TerminalView)

    def is_empty(self) -> bool:
        return not self.views()

    def active(self) -> TerminalView | None:
        views = self.views()
        if self._active in views:
            return self._active
        return views[0] if views else None

    def title(self) -> str:
        """Name der aktiven Kachel, bei mehreren mit der Anzahl der weiteren („Server1 (+2)“)."""
        active = self.active()
        if active is None:
            return ""
        extra = len(self.views()) - 1
        return f"{active.title} (+{extra})" if extra else active.title

    def focus_active(self) -> None:
        active = self.active()
        if active is not None:
            active.setFocus()

    # --- Kacheln ändern ---
    def _register(self, view: TerminalView) -> None:
        view.focused.connect(lambda v=view: self._set_active(v))

    def _set_active(self, view: TerminalView) -> None:
        if view is not self._active:
            self._active = view
            self.changed.emit()

    def _update_tiled(self) -> None:
        views = self.views()
        for view in views:
            view.set_tiled(len(views) > 1)
        self.changed.emit()

    def add_view(self, new: TerminalView, anchor: TerminalView | None, orientation: Qt.Orientation) -> None:
        """Fügt ``new`` rechts (``Horizontal``) oder unter (``Vertical``) ``anchor`` ein; ohne Anker an die aktive Kachel."""
        self.unzoom()
        anchor = anchor if anchor in self.views() else self.active()
        self._register(new)
        parent = anchor.parentWidget() if anchor is not None else None
        if not isinstance(parent, QSplitter):
            self._root.addWidget(new)
        elif parent.count() == 1:  # ein Splitter mit nur einer Kachel nimmt die gewünschte Richtung an
            parent.setOrientation(orientation)
            parent.addWidget(new)
            self._equalize(parent)
        elif parent.orientation() == orientation:
            parent.insertWidget(parent.indexOf(anchor) + 1, new)
            self._equalize(parent)
        else:
            sizes = parent.sizes()
            index = parent.indexOf(anchor)
            inner = self._splitter(orientation)
            parent.insertWidget(index, inner)
            inner.addWidget(anchor)  # setzt die Kachel um
            inner.addWidget(new)
            parent.setSizes(sizes)
            self._equalize(inner)
        self._active = new
        self._update_tiled()

    @staticmethod
    def _equalize(splitter: QSplitter) -> None:
        splitter.setSizes([1000] * splitter.count())

    def remove_view(self, view: TerminalView) -> None:
        """Nimmt die Kachel heraus (löschen muss der Aufrufer); leere Splitter und Splitter mit einer Kachel lösen sich auf."""
        self.unzoom()
        parent = view.parentWidget()
        was_active = view is self._active
        view.hide()
        view.setParent(None)
        if isinstance(parent, QSplitter):
            self._collapse(parent)
        if was_active:
            remaining = self.views()
            self._active = remaining[-1] if remaining else None
            if self._active is not None:
                self._active.setFocus()
        self._update_tiled()

    def _collapse(self, splitter: QSplitter) -> None:
        if splitter is self._root:
            return
        parent = splitter.parentWidget()
        if not isinstance(parent, QSplitter):
            return
        if splitter.count() == 0:
            splitter.setParent(None)
            splitter.deleteLater()
            self._collapse(parent)
        elif splitter.count() == 1:
            child = splitter.widget(0)
            sizes = parent.sizes()
            parent.insertWidget(parent.indexOf(splitter), child)
            parent.setSizes(sizes)
            splitter.setParent(None)
            splitter.deleteLater()

    # --- Fokus zwischen den Kacheln ---
    def neighbor(self, view: TerminalView, direction: str) -> TerminalView | None:
        """Nächste sichtbare Kachel in ``left``/``right``/``up``/``down`` (nach den Mittelpunkten auf dem Bildschirm)."""
        origin = view.mapToGlobal(view.rect().center())
        best, best_score = None, None
        for other in self.views():
            if other is view or not other.isVisible():
                continue
            point = other.mapToGlobal(other.rect().center())
            dx, dy = point.x() - origin.x(), point.y() - origin.y()
            along, across = {"left": (-dx, dy), "right": (dx, dy), "up": (-dy, dx), "down": (dy, dx)}[direction]
            if along <= 0:
                continue
            score = along + 2 * abs(across)
            if best_score is None or score < best_score:
                best, best_score = other, score
        return best

    def move_focus(self, view: TerminalView, direction: str) -> None:
        target = self.neighbor(view, direction)
        if target is not None:
            target.setFocus()

    # --- Eine Kachel vergrößern ---
    def zoomed(self) -> TerminalView | None:
        return self._zoomed

    def unzoom(self) -> None:
        if self._zoomed is None:
            return
        self._zoomed = None
        for splitter in [self._root, *self._root.findChildren(QSplitter)]:
            for i in range(splitter.count()):
                splitter.widget(i).show()

    def toggle_zoom(self, view: TerminalView) -> None:
        """Zeigt die Kachel allein im ganzen Reiter; ein zweiter Aufruf stellt die Anordnung wieder her."""
        if self._zoomed is not None:
            self.unzoom()
            view.setFocus()
            return
        if len(self.views()) < 2:
            return
        self._zoomed = view
        for splitter in [self._root, *self._root.findChildren(QSplitter)]:
            for i in range(splitter.count()):
                child = splitter.widget(i)
                child.setVisible(child is view or (isinstance(child, QSplitter) and child.isAncestorOf(view)))
        view.setFocus()
