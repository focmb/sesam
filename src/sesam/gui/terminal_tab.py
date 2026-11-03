"""Ein Reiter des integrierten Terminals mit einer oder mehreren Sitzungen als Kacheln (verschachtelte Splitter)."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from .terminal_view import TerminalView


class TerminalTab(QWidget):
    """Hält die Kacheln eines Reiters. Die Sitzungen selbst (Start, Ende, Signale) verwaltet das Hauptfenster."""

    changed = Signal()  # Kacheln hinzugekommen/entfernt, Fokus oder Broadcast gewechselt: Reitertitel neu setzen

    def __init__(self, first: TerminalView, parent=None):
        super().__init__(parent)
        self._root = self._splitter(Qt.Orientation.Horizontal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._root)
        self._known: set[TerminalView] = set()
        self._active: TerminalView | None = None
        self._zoomed: TerminalView | None = None
        self._broadcast = False
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
        """Alle Kacheln in Anordnungsreihenfolge (links nach rechts, oben nach unten)."""
        found: list[TerminalView] = []

        def walk(splitter: QSplitter):
            for i in range(splitter.count()):
                widget = splitter.widget(i)
                if isinstance(widget, TerminalView):
                    found.append(widget)
                elif isinstance(widget, QSplitter):
                    walk(widget)

        walk(self._root)
        return found

    def is_empty(self) -> bool:
        return not self.views()

    def active(self) -> TerminalView | None:
        views = self.views()
        if self._active in views:
            return self._active
        return views[0] if views else None

    def title(self) -> str:
        """Name der aktiven Kachel, bei mehreren mit der Anzahl der weiteren („Server1 (+2)“); „⚠“ bei Eingabe an alle."""
        active = self.active()
        if active is None:
            return ""
        extra = len(self.views()) - 1
        text = f"{active.title} (+{extra})" if extra else active.title
        return f"⚠ {text}" if self._broadcast else text

    def focus_active(self) -> None:
        active = self.active()
        if active is not None:
            active.setFocus()

    # --- Kacheln anmelden ---
    def _register(self, view: TerminalView) -> None:
        if view not in self._known:
            self._known.add(view)
            view.focused.connect(self._view_focused)

    def _unregister(self, view: TerminalView) -> None:
        if view in self._known:
            self._known.discard(view)
            try:
                view.focused.disconnect(self._view_focused)
            except (RuntimeError, TypeError):
                pass

    @Slot()
    def _view_focused(self) -> None:
        view = self.sender()
        if isinstance(view, TerminalView) and view is not self._active and view in self._known:
            self._active = view
            self.changed.emit()

    def _update_tiled(self) -> None:
        views = self.views()
        if len(views) < 2:
            self._broadcast = False  # allein gibt es nichts zu verteilen
        for view in views:
            view.set_tiled(len(views) > 1)
            view.set_broadcast(self._broadcast)
        self.changed.emit()

    # --- Kacheln ändern ---
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
        new.show()
        self._active = new
        self._update_tiled()

    @staticmethod
    def _equalize(splitter: QSplitter) -> None:
        splitter.setSizes([1000] * splitter.count())

    def detach(self, view: TerminalView) -> None:
        """Nimmt die Kachel aus dem Reiter, ohne sie zu löschen (z. B. um sie in einen anderen Reiter zu setzen)."""
        self.unzoom()
        parent = view.parentWidget()
        was_active = view is self._active
        self._unregister(view)
        view.setParent(None)
        view.set_tiled(False)
        view.set_broadcast(False)
        if isinstance(parent, QSplitter):
            self._collapse(parent)
        if was_active:
            remaining = self.views()
            self._active = remaining[-1] if remaining else None
            if self._active is not None:
                self._active.setFocus()
        self._update_tiled()

    def remove_view(self, view: TerminalView) -> None:
        """Nimmt die Kachel heraus, weil ihre Sitzung endet (löschen muss der Aufrufer)."""
        self.detach(view)
        view.hide()

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

    # --- Raster ---
    @staticmethod
    def grid_shape(count: int) -> tuple[int, int]:
        """(Spalten, Zeilen) für ``count`` Kacheln: möglichst quadratisch, Spalten zuerst (2 → 2×1, 3 → 2×2, 5 → 3×2)."""
        columns = max(1, math.ceil(math.sqrt(count)))
        return columns, max(1, math.ceil(count / columns))

    def arrange_grid(self, views: list[TerminalView]) -> None:
        """Ordnet die Kacheln als Raster an. Kacheln aus anderen Reitern müssen vorher dort mit :meth:`detach` gelöst sein."""
        self.unzoom()
        ordered = list(dict.fromkeys(views))
        ordered += [v for v in self.views() if v not in ordered]  # nichts darf verloren gehen
        for view in ordered:
            view.setParent(None)
        for i in reversed(range(self._root.count())):
            leftover = self._root.widget(i)
            leftover.setParent(None)
            leftover.deleteLater()
        columns, rows = self.grid_shape(len(ordered))
        self._root.setOrientation(Qt.Orientation.Vertical if rows > 1 else Qt.Orientation.Horizontal)
        for r in range(rows):
            chunk = ordered[r * columns:(r + 1) * columns]
            if rows == 1:
                for view in chunk:
                    self._root.addWidget(view)
            elif len(chunk) == 1:
                self._root.addWidget(chunk[0])
            else:
                row = self._splitter(Qt.Orientation.Horizontal)
                for view in chunk:
                    row.addWidget(view)
                self._root.addWidget(row)
                self._equalize(row)
        self._equalize(self._root)
        for view in ordered:
            self._register(view)
            view.show()
        if self._active not in ordered:
            self._active = ordered[0] if ordered else None
        self._update_tiled()

    # --- Eingabe an alle Kacheln ---
    def broadcast(self) -> bool:
        return self._broadcast

    def set_broadcast(self, enabled: bool) -> None:
        enabled = bool(enabled) and len(self.views()) > 1
        if enabled != self._broadcast:
            self._broadcast = enabled
            self._update_tiled()

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
