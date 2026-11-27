"""Eigene Farbschemas verwalten: anlegen, bearbeiten, duplizieren, löschen, importieren und exportieren.

Der Dialog arbeitet auf einer Arbeitskopie. Änderungen sind sofort in der ganzen Oberfläche zu sehen (Live-Vorschau);
„Abbrechen“ stellt den vorherigen Zustand wieder her, „Speichern“ übernimmt die Arbeitskopie in die Konfiguration.
"""
from __future__ import annotations

import copy
import html
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from .. import scheme_io, schemes
from ..i18n import tr
from ..models import ColorScheme
from . import theme

UI_LABELS = {"bg": "Hintergrund", "surface": "Flächen", "text": "Text", "accent": "Akzent", "ok": "Erfolg", "warn": "Warnung", "danger": "Fehler"}
TERMINAL_LABELS = {"bg": "Hintergrund", "fg": "Vordergrund", "cursor": "Cursor", "selection": "Auswahl"}
ANSI_NAMES = ("Schwarz", "Rot", "Grün", "Gelb", "Blau", "Magenta", "Cyan", "Weiß")
PREVIEW_DELAY_MS = 150
MIN_TEXT_CONTRAST = 4.0


class ColorField(QWidget):
    """Eine Farbe: Farbfläche (öffnet den Farbdialog) und Hex-Eingabe. ``changed`` kommt nur bei gültigem Wert."""

    def __init__(self, value: str, on_change, label: str = ""):
        super().__init__()
        self._on_change = on_change
        self._value = value
        self.swatch = QPushButton()
        self.swatch.setAutoDefault(False)
        self.swatch.setToolTip(tr("Farbe auswählen …"))
        self.swatch.clicked.connect(self._pick)
        self.edit = QLineEdit(value)
        self.edit.setMaxLength(7)
        self.edit.setMinimumWidth(72)
        self.edit.setPlaceholderText("#rrggbb")
        self.edit.setAccessibleName(label)
        self.swatch.setFixedSize(30, self.edit.sizeHint().height())
        self.swatch.setAccessibleName(label)
        self.edit.textEdited.connect(self._edited)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self.swatch)
        row.addWidget(self.edit, 1)
        self._paint()

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self.edit.setText(value)
        self.edit.setStyleSheet("")
        self._paint()

    def set_valid(self, valid: bool) -> None:
        self.edit.setProperty("invalid", not valid)
        self.edit.setStyleSheet("" if valid else "QLineEdit { border: 1px solid #d92d20; }")

    def _paint(self) -> None:
        self.swatch.setStyleSheet(f"QPushButton {{ background: {self._value}; border: 1px solid rgba(128,128,128,0.6); border-radius: 4px; }}")

    def _edited(self, text: str) -> None:
        text = text.strip()
        if text and not text.startswith("#"):
            text = "#" + text
            self.edit.setText(text)
        if schemes.is_hex(text):
            self._value = text.lower()
            self.set_valid(True)
            self._paint()
            self._on_change()
        else:
            self.set_valid(False)

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self._value), self, tr("Farbe auswählen"))
        if color.isValid():
            self.set_value(color.name().lower())
            self._on_change()


class SchemesDialog(QDialog):
    """``cfg``: Konfiguration (``color_schemes`` wird beim Speichern ersetzt), ``save``: speichert und stößt den Abgleich an,
    ``set_theme(mode)``: wählt ein Schema aus, ``selected``: ID des in den Einstellungen gewählten Schemas (oder ``""``)."""

    def __init__(self, cfg, save, set_theme, selected: str = "", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._save = save
        self._set_theme = set_theme
        self._initial_theme = cfg.theme
        self.saved_id = ""
        self.work: list[ColorScheme] = copy.deepcopy(cfg.color_schemes)
        self._loading = False
        self.setWindowTitle(tr("Farbschemas verwalten"))
        self.setMinimumSize(780, 560)

        self.listing = QListWidget()
        self.listing.setMinimumWidth(190)
        self.listing.currentItemChanged.connect(self._select)
        self.btn_new = self._button(tr("Neu"), self.new_scheme, tr("Neues Schema auf Grundlage von Sesam dunkel"))
        self.btn_copy = self._button(tr("Duplizieren"), self.duplicate, tr("Kopie des gewählten Schemas, auch eines mitgelieferten"))
        self.btn_delete = self._button(tr("Löschen"), self.delete, tr("Das gewählte eigene Schema löschen"))
        self.btn_import = self._button(tr("Importieren …"), self.import_file, tr("Schema aus einer JSON-Datei laden"))
        self.btn_export = self._button(tr("Exportieren …"), self.export_file, tr("Gewähltes Schema als JSON-Datei speichern"))
        left = QVBoxLayout()
        left.addWidget(self.listing, 1)
        grid = QGridLayout()
        for i, b in enumerate((self.btn_new, self.btn_copy, self.btn_delete, self.btn_import, self.btn_export)):
            grid.addWidget(b, i // 2, i % 2)
        left.addLayout(grid)

        self.name = QLineEdit()
        self.name.setMaxLength(schemes.NAME_MAX)
        self.name.textEdited.connect(self._name_edited)
        self.base = QComboBox()
        self.base.addItem(tr("Hell"), "light")
        self.base.addItem(tr("Dunkel"), "dark")
        self.base.setToolTip(tr("Legt fest, ob Titelleiste und native Elemente hell oder dunkel dargestellt werden."))
        self.base.currentIndexChanged.connect(self._base_changed)
        self.ui_fields = {k: ColorField("#000000", self._changed, tr(UI_LABELS[k])) for k in schemes.UI_KEYS}
        self.term_fields = {k: ColorField("#000000", self._changed, tr(TERMINAL_LABELS[k])) for k in schemes.TERMINAL_KEYS}
        self.ansi_fields = [ColorField("#000000", self._changed, self._ansi_label(i)) for i in range(schemes.ANSI_COUNT)]

        form = QFormLayout()
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Grundlage"), self.base)
        form.addRow(QLabel("<b>" + tr("Oberfläche") + "</b>"))
        for k in schemes.UI_KEYS:
            form.addRow(tr(UI_LABELS[k]), self.ui_fields[k])
        form.addRow(QLabel("<b>" + tr("Terminal") + "</b>"))
        for k in schemes.TERMINAL_KEYS:
            form.addRow(tr(TERMINAL_LABELS[k]), self.term_fields[k])
        ansi = QGridLayout()
        for i, field in enumerate(self.ansi_fields):
            ansi.addWidget(QLabel(self._ansi_label(i)), i % 8, 0 if i < 8 else 2)
            ansi.addWidget(field, i % 8, 1 if i < 8 else 3)
        form.addRow(QLabel("<b>" + tr("ANSI-Farben") + "</b>"))
        form.addRow(ansi)
        self.hints = QLabel()
        self.hints.setWordWrap(True)
        self.hints.setObjectName("hint")
        self.hints.setTextFormat(Qt.TextFormat.RichText)
        self.preview = QLabel()
        self.preview.setTextFormat(Qt.TextFormat.RichText)
        self.preview.setMargin(8)
        self.preview.setAccessibleName(tr("Vorschau"))
        self.use_after = QCheckBox(tr("Schema nach dem Speichern verwenden"))
        self.use_after.setChecked(selected not in ("", "system") and any(c.id == selected for c in self.work))

        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.addLayout(form)
        inner.addStretch(1)
        self.editor = QScrollArea()
        self.editor.setWidgetResizable(True)
        self.editor.setFrameShape(QScrollArea.Shape.NoFrame)
        self.editor.setWidget(holder)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("Speichern"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Abbrechen"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.buttons = buttons

        right = QVBoxLayout()  # Hinweise und Vorschau bleiben beim Scrollen der Felder sichtbar
        right.addWidget(self.editor, 1)
        right.addWidget(self.hints)
        right.addWidget(self.preview)
        body = QHBoxLayout()
        body.addLayout(left, 0)
        body.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(self.use_after)
        layout.addWidget(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(PREVIEW_DELAY_MS)
        self._timer.timeout.connect(self._apply_preview)

        self._fill_list(selected)

    # --- Hilfen
    @staticmethod
    def _ansi_label(i: int) -> str:
        return tr(ANSI_NAMES[i % 8]) + (" " + tr("hell") if i >= 8 else "")

    def _button(self, text, slot, tip="") -> QPushButton:
        b = QPushButton(text)
        b.setAutoDefault(False)
        b.setToolTip(tip)
        b.clicked.connect(lambda *_: slot())
        return b

    def _all(self) -> list:
        """Mitgelieferte Schemas, danach die eigenen (Arbeitskopie) in Listenreihenfolge."""
        return [*schemes.BUILTIN, *self.work]

    def _current(self):
        item = self.listing.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _current_custom(self) -> ColorScheme | None:
        ident = self._current()
        return next((c for c in self.work if c.id == ident), None)

    def _fill_list(self, select: str = "") -> None:
        self._loading = True
        self.listing.clear()
        for s in schemes.BUILTIN:
            item = QListWidgetItem(tr(s.name))
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            self.listing.addItem(item)
        for c in sorted(self.work, key=lambda c: c.name.casefold()):
            item = QListWidgetItem(c.name)
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            self.listing.addItem(item)
        self._loading = False
        row = next((i for i in range(self.listing.count()) if self.listing.item(i).data(Qt.ItemDataRole.UserRole) == select), -1)
        self.listing.setCurrentRow(row if row >= 0 else (len(schemes.BUILTIN) if self.work else 0))
        self._select()

    # --- Auswahl und Bearbeitung
    def _select(self, *_) -> None:
        if self._loading:
            return
        ident = self._current()
        custom = self._current_custom()
        source = custom.to_scheme() if custom else schemes.by_id(ident) if ident else None
        if source is None:
            return
        self._loading = True
        editable = custom is not None
        self.name.setText(custom.name if custom else tr(source.name))
        self.base.setCurrentIndex(self.base.findData(source.base))
        for k, f in self.ui_fields.items():
            f.set_value(source.ui[k])
        for k, f in self.term_fields.items():
            f.set_value(source.terminal[k])
        for f, c in zip(self.ansi_fields, source.terminal["ansi"]):
            f.set_value(c)
        for w in (self.name, self.base, *self.ui_fields.values(), *self.term_fields.values(), *self.ansi_fields):
            w.setEnabled(editable)
        self.btn_delete.setEnabled(editable)
        self._loading = False
        self._update_info()
        self._schedule_preview()

    def _changed(self) -> None:
        custom = self._current_custom()
        if self._loading or custom is None:
            return
        custom.ui = {k: f.value for k, f in self.ui_fields.items()}
        custom.terminal = {**{k: f.value for k, f in self.term_fields.items()}, "ansi": [f.value for f in self.ansi_fields]}
        self._update_info()
        self._schedule_preview()

    def _base_changed(self, *_) -> None:
        custom = self._current_custom()
        if self._loading or custom is None:
            return
        custom.base = self.base.currentData()
        self._schedule_preview()

    def _name_edited(self, text: str) -> None:
        custom = self._current_custom()
        if custom is None:
            return
        custom.name = " ".join(text.split()) or tr("Farbschema")
        item = self.listing.currentItem()
        item.setText(custom.name)
        self._update_info()

    # --- Hinweise und Vorschau
    def warnings(self, scheme) -> list[str]:
        out = []
        ui, term = scheme.ui, scheme.terminal
        for text, bg, label in ((ui["text"], ui["bg"], tr("Text auf Hintergrund")), (ui["text"], ui["surface"], tr("Text auf Flächen")),
                                (term["fg"], term["bg"], tr("Terminal: Vordergrund auf Hintergrund"))):
            ratio = schemes.contrast(text, bg)
            if ratio < MIN_TEXT_CONTRAST:
                out.append(tr("{label}: Kontrast {ratio} zu 1 ist schwer lesbar (empfohlen: mindestens {min}).",
                              label=label, ratio=f"{ratio:.1f}", min=f"{MIN_TEXT_CONTRAST:.1f}"))
        names = [c.name.casefold() for c in self.work if c is not self._current_custom()]
        custom = self._current_custom()
        if custom is not None and custom.name.casefold() in names:
            out.append(tr("Ein anderes eigenes Schema heißt genauso."))
        return out

    def _update_info(self) -> None:
        custom = self._current_custom()
        scheme = custom.to_scheme() if custom else schemes.by_id(self._current() or "")
        if scheme is None:
            return
        lines = [html.escape(w) for w in self.warnings(scheme)]
        if custom is not None:
            lines.append(html.escape(tr("Erfolg, Warnung, Fehler und Akzent werden bei Bedarf automatisch so angepasst, dass sie lesbar bleiben.")))
        else:
            lines.append(html.escape(tr("Mitgelieferte Schemas lassen sich nicht ändern. „Duplizieren“ erzeugt eine bearbeitbare Kopie.")))
        self.hints.setText("<br>".join(("⚠ " if i < len(lines) - 1 and custom is not None else "") + t for i, t in enumerate(lines)))
        t = scheme.terminal
        ansi = t["ansi"]
        sample = "".join(f'<span style="color:{ansi[i]}">{html.escape(tr(ANSI_NAMES[i]))}</span> ' for i in range(8))
        bright = "".join(f'<span style="color:{ansi[i + 8]}">{html.escape(tr(ANSI_NAMES[i]))}</span> ' for i in range(8))
        self.preview.setText(
            f'<div style="background:{t["bg"]}; color:{t["fg"]}; font-family:monospace; padding:6px">'
            f'user@host:~$ ls -l<br>{sample}<br>{bright}<br>'
            f'<span style="background:{t["selection"]}">{html.escape(tr("ausgewählter Text"))}</span> '
            f'<span style="background:{t["cursor"]}; color:{t["bg"]}">&nbsp;</span></div>')

    def _schedule_preview(self) -> None:
        self._timer.start()

    def _register(self) -> None:
        schemes.set_custom([c.to_scheme() for c in self.work])

    def _apply_preview(self) -> None:
        ident = self._current()
        app = QApplication.instance()
        if not ident or app is None:
            return
        self._register()
        theme.apply(app, "scheme:" + ident)

    def _restore(self) -> None:
        """Zurück zum gespeicherten Zustand: Register und Darstellung gemäß Konfiguration."""
        self._timer.stop()
        schemes.set_custom([c.to_scheme() for c in self.cfg.color_schemes])
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, self._initial_theme)

    # --- Aktionen
    def _add(self, scheme: ColorScheme) -> None:
        scheme.name = scheme_io.unique_name(scheme.name, [c.name for c in self.work] + [tr(s.name) for s in schemes.BUILTIN])
        self.work.append(scheme)
        self._fill_list(scheme.id)

    def new_scheme(self) -> None:
        self._add(ColorScheme.from_scheme(schemes.SESAM_DARK, tr("Neues Farbschema")))

    def duplicate(self) -> None:
        ident = self._current()
        custom = self._current_custom()
        source = custom.to_scheme() if custom else schemes.by_id(ident or "")
        if source is None:
            return
        base_name = custom.name if custom else tr(source.name)
        self._add(ColorScheme.from_scheme(source, base_name))

    def delete(self) -> None:
        custom = self._current_custom()
        if custom is None:
            return
        self.work.remove(custom)
        self._fill_list()

    def _pick_open(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, tr("Farbschema importieren"), "", tr("Farbschema (*.json);;Alle Dateien (*)"))
        return path

    def _pick_save(self, name: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, tr("Farbschema exportieren"), f"{name}.json", tr("Farbschema (*.json);;Alle Dateien (*)"))
        return path

    def import_file(self) -> None:
        path = self._pick_open()
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                raw = fh.read(scheme_io.MAX_BYTES + 1)
            scheme = scheme_io.parse(raw, [c.name for c in self.work] + [tr(s.name) for s in schemes.BUILTIN])
        except (OSError, scheme_io.SchemeFileError) as exc:
            QMessageBox.warning(self, tr("Farbschema importieren"), str(exc))
            return
        self.work.append(scheme)
        self._fill_list(scheme.id)

    def export_file(self) -> None:
        ident = self._current()
        custom = self._current_custom()
        source = custom or (ColorScheme.from_scheme(schemes.by_id(ident), tr(schemes.by_id(ident).name)) if schemes.by_id(ident or "") else None)
        if source is None:
            return
        path = self._pick_save("".join(ch for ch in source.name if ch.isalnum() or ch in " -_").strip() or "farbschema")
        if not path:
            return
        try:
            Path(path).write_text(scheme_io.export_text(source), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, tr("Farbschema exportieren"), str(exc))

    # --- Abschluss
    def accept(self) -> None:
        for c in self.work:  # leere oder zu lange Namen sind schon bereinigt; hier nur die Reihenfolge fürs Speichern
            c.name = c.name.strip() or tr("Farbschema")
        selected = self._current()
        previous = {c.id for c in self.cfg.color_schemes}
        self.cfg.color_schemes = sorted(self.work, key=lambda c: c.name.casefold())
        self._timer.stop()
        schemes.set_custom([c.to_scheme() for c in self.cfg.color_schemes])
        active = self.cfg.theme[len(schemes.PREFIX):] if self.cfg.theme.startswith(schemes.PREFIX) else ""
        self.saved_id = selected or ""
        if active and active in previous and not any(c.id == active for c in self.work):
            self.cfg.theme = "system"  # das aktive eigene Schema wurde gelöscht
        if self.use_after.isChecked() and any(c.id == selected for c in self.work):
            self.cfg.theme = schemes.PREFIX + selected
        self._save()
        self._set_theme(self.cfg.theme)
        super().accept()

    def reject(self) -> None:
        self._restore()
        super().reject()

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)
