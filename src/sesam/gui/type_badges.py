"""Spalte „Typ“ der Hauptansicht: kleine Abzeichen (SSH, VNC, RDP) statt Text."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme

KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20  # Liste der Arten einer Verbindung, z. B. ["SSH", "VNC"]
PAD_X, GAP, MARGIN = 7, 5, 6


def badge_color(kind: str, c: dict[str, str]) -> QColor:
    return QColor({"VNC": c["accent"], "RDP": c["badge_rdp"]}.get(kind, c["muted"]))


class TypeBadgeDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        kinds = index.data(KIND_ROLE)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""  # Hintergrund und Auswahl zeichnet der Stil, die Abzeichen kommen darüber
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        if not kinds:
            return
        c = theme.colors()
        font = QFont(opt.font)
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1))
        font.setWeight(QFont.Weight.DemiBold)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        height = min(opt.rect.height() - 6, metrics.height() + 2)
        top = opt.rect.top() + (opt.rect.height() - height) / 2
        x = opt.rect.left() + MARGIN
        for kind in kinds:
            width = metrics.horizontalAdvance(kind) + 2 * PAD_X
            if x + width > opt.rect.right():
                break
            color = badge_color(kind, c)
            box = QRectF(x, top, width, height)
            fill = QColor(color)
            fill.setAlpha(36)
            edge = QColor(color)
            edge.setAlpha(120)
            painter.setBrush(fill)
            painter.setPen(edge)
            painter.drawRoundedRect(box, height / 2, height / 2)
            painter.setPen(color)
            painter.drawText(box, int(Qt.AlignmentFlag.AlignCenter), kind)
            x += width + GAP
        painter.restore()
