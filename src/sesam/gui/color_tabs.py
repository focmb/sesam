"""Reiterleiste, die einzelne Reiter in einer Farbe hinterlegen kann (Farbwert als ``tabData``)."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QTabBar, QTabWidget


class ColorTabBar(QTabBar):
    """Zeichnet über den normalen Reiter eine leichte Tönung und einen Streifen am unteren Rand.

    Die Farbe steht als ``#rrggbb`` in ``tabData(index)`` und wandert mit dem Reiter, wenn er verschoben wird;
    ohne Wert (``None``) bleibt der Reiter unverändert."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        for index in range(self.count()):
            value = self.tabData(index)
            rect = self.tabRect(index)
            if not value or not rect.intersects(event.rect()):
                continue
            color = QColor(value)
            if not color.isValid():
                continue
            tint = QColor(color)
            tint.setAlpha(70 if index == self.currentIndex() else 38)
            painter.fillRect(rect, tint)
            painter.fillRect(rect.left(), rect.bottom() - 2, rect.width(), 3, color)
        painter.end()


class ColorTabWidget(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabBar(ColorTabBar())
