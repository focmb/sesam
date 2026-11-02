"""Zwischenablage-Verlauf: schnelles Popup im Terminal und Dialog mit Reiterauswahl, Anheften und „Alles leeren“."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .. import clipboard_history as ch
from ..clipboard_history import Entry, History
from ..i18n import tr
from .snippets_dialog import monospace

ENTRY_ROLE = int(Qt.ItemDataRole.UserRole)
LARGE_PASTE_CHARS = 10_000  # darüber fragt Sesam vor dem Einfügen nach
PREVIEW_CHARS = 20_000


def meta(entry: Entry) -> str:
    """„3 Zeilen · 1.2 KB · Server“"""
    parts = [tr("{n} Zeilen", n=entry.lines) if entry.lines != 1 else tr("1 Zeile"), ch.human_size(entry.size)]
    if entry.source:
        parts.append(entry.source)
    return " · ".join(parts)


def confirm_paste(parent, text: str, count: int) -> bool:
    """Fragt nach, wenn sehr viel oder mehrzeiliger Text in mehrere Terminals gehen soll (ein Terminal führt ihn womöglich sofort aus)."""
    many_lines = "\n" in text.rstrip("\n")
    if not (len(text) > LARGE_PASTE_CHARS or (count > 1 and many_lines)):
        return True
    what = tr("{n} Zeichen", n=len(text)) if len(text) > LARGE_PASTE_CHARS else tr("{n} Zeilen", n=text.count("\n") + 1)
    answer = QMessageBox.question(
        parent, tr("Einfügen"), tr("{what} in {count} Terminal(s) einfügen?\n\nMehrzeiliger Text kann dort sofort ausgeführt werden.", what=what, count=count))
    return answer == QMessageBox.StandardButton.Yes


def _item_text(entry: Entry, number: int | None = None) -> str:
    mark = "★ " if entry.pinned else ""
    prefix = f"{number}  " if number is not None else "    "
    return f"{prefix}{mark}{entry.first_line(90) or tr('(nur Leerraum)')}\n      {meta(entry)}"


class ClipboardPopup(QDialog):
    """Kleine Liste über dem Terminal (Strg+Umschalt+H): Enter fügt ein, Umschalt+Enter fügt ein und führt aus."""

    chosen = Signal(str, bool)  # Eintrags-ID, danach Enter senden

    def __init__(self, history: History, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.history = history
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Verlauf durchsuchen …"))
        self.search.textChanged.connect(self._refresh)
        self.search.installEventFilter(self)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(lambda _item: self._choose(False))
        self.hint = QLabel(tr("Enter: einfügen · Umschalt+Enter: einfügen und ausführen · Alt+1…9: direkt · Strg+Entf: löschen"))
        self.hint.setObjectName("muted")
        self.hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(8)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.hint)
        for n in range(1, ch.MAX_SLOTS + 1):
            QShortcut(QKeySequence(f"Alt+{n}"), self, activated=lambda n=n: self._choose_slot(n))
        QShortcut(QKeySequence("Ctrl+Del"), self, activated=self._delete_current)
        self.resize(620, 400)
        self._refresh()

    def _refresh(self, *_):
        current = self._current_id()
        self.list.clear()
        entries = self.history.search(self.search.text())
        for number, entry in enumerate(entries, 1):
            item = QListWidgetItem(_item_text(entry, number if number <= ch.MAX_SLOTS else None))
            item.setData(ENTRY_ROLE, entry.id)
            item.setToolTip(entry.text[:1000] + ("…" if len(entry.text) > 1000 else ""))
            self.list.addItem(item)
        if not entries:
            placeholder = QListWidgetItem(tr("Noch nichts kopiert.") if not self.search.text() else tr("Nichts gefunden."))
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(placeholder)
            return
        row = next((i for i, e in enumerate(entries) if e.id == current), 0)
        self.list.setCurrentRow(row)

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(ENTRY_ROLE) if item else None

    def _choose(self, enter: bool):
        entry_id = self._current_id()
        if entry_id:
            self.close()
            self.chosen.emit(entry_id, enter)

    def _choose_slot(self, number: int):
        if 0 < number <= self.list.count():
            self.list.setCurrentRow(number - 1)
            self._choose(False)

    def _delete_current(self):
        entry_id = self._current_id()
        if entry_id:
            self.history.remove(entry_id)
            self._refresh()

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose(bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
                QGuiApplication.sendEvent(self.list, event)  # die Liste bewegt die Auswahl, das Suchfeld behält den Fokus
                return True
        return super().eventFilter(obj, event)


class EntryTextDialog(QDialog):
    """Eigenen Text für den Verlauf eingeben (z. B. ein langer Befehl)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Neuer Eintrag"))
        self.resize(520, 300)
        self.edit = QPlainTextEdit()
        self.edit.setFont(monospace())
        self.edit.setTabChangesFocus(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.addWidget(self.edit, 1)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self.edit.toPlainText()


class ClipboardDialog(QDialog):
    """Verlauf verwalten und den gewählten Text in einen oder mehrere Terminal-Reiter einfügen."""

    def __init__(self, history: History, sessions: Callable[[], list[tuple[str, object]]],
                 paste: Callable[[list, str, bool], None], current: object | None = None, parent=None):
        super().__init__(parent)
        self.history = history
        self._paste_into = paste
        self.setWindowTitle(tr("Zwischenablage"))
        self.resize(900, 600)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Suchen …"))
        self.search.textChanged.connect(self._refresh)
        self.list = QListWidget()
        self.list.setUniformItemSizes(True)
        self.list.currentItemChanged.connect(self._show_entry)
        self.list.itemDoubleClicked.connect(lambda _i: self._paste())
        self.pin_button = QPushButton(tr("Anheften"))
        self.pin_button.clicked.connect(self._toggle_pin)
        self.delete_button = QPushButton(tr("Löschen"))
        self.delete_button.clicked.connect(self._delete)
        new_button = QPushButton(tr("Neu …"))
        new_button.clicked.connect(self._new)
        self.clear_button = QPushButton(tr("Alles leeren …"))
        self.clear_button.clicked.connect(self._clear_all)
        row = QHBoxLayout()
        for b in (self.pin_button, self.delete_button, new_button):
            row.addWidget(b)
        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.list, 1)
        left.addLayout(row)
        left.addWidget(self.clear_button)

        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(monospace())
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.targets = QListWidget()
        self.targets.setMaximumHeight(130)
        for title, view in sessions():
            item = QListWidgetItem(title)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if view is current else Qt.CheckState.Unchecked)
            item.setData(ENTRY_ROLE, view)
            self.targets.addItem(item)
        all_button, none_button = QPushButton(tr("Alle")), QPushButton(tr("Keine"))
        all_button.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        none_button.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(tr("Einfügen in:")))
        target_row.addStretch(1)
        target_row.addWidget(all_button)
        target_row.addWidget(none_button)
        self.no_sessions = QLabel(tr("Keine Terminal-Reiter offen. Öffne eine Verbindung mit dem integrierten Terminal."))
        self.no_sessions.setObjectName("muted")
        self.no_sessions.setWordWrap(True)
        self.no_sessions.setVisible(self.targets.count() == 0)
        self.targets.setVisible(self.targets.count() > 0)
        self.with_enter = QCheckBox(tr("Danach Enter senden (Befehl ausführen)"))
        self.paste_button = QPushButton(tr("Einfügen"))
        self.paste_button.setProperty("primary", True)
        self.paste_button.clicked.connect(self._paste)
        self.copy_button = QPushButton(tr("In die Zwischenablage kopieren"))
        self.copy_button.clicked.connect(self._copy_back)
        action_row = QHBoxLayout()
        action_row.addWidget(self.copy_button)
        action_row.addStretch(1)
        action_row.addWidget(self.paste_button)
        right = QVBoxLayout()
        right.addWidget(self.info)
        right.addWidget(self.preview, 1)
        right.addLayout(target_row)
        right.addWidget(self.targets)
        right.addWidget(self.no_sessions)
        right.addWidget(self.with_enter)
        right.addLayout(action_row)

        body = QHBoxLayout()
        body.addLayout(left, 2)
        body.addLayout(right, 3)

        self.opt_enabled = QCheckBox(tr("Verlauf aufzeichnen"))
        self.opt_persist = QCheckBox(tr("Verlauf über einen Neustart behalten (verschlüsselt)"))
        self.opt_exit = QCheckBox(tr("Beim Beenden leeren (Angeheftetes bleibt)"))
        self.opt_plain = QCheckBox(tr("Ohne Schlüsselbund unverschlüsselt speichern"))
        self.opt_plain.setToolTip(tr("Nur wählen, wenn niemand sonst Zugriff auf die Dateien dieses Benutzers hat."))
        for box, name in ((self.opt_enabled, "enabled"), (self.opt_persist, "persist"), (self.opt_exit, "clear_on_exit"), (self.opt_plain, "allow_plaintext")):
            box.setChecked(getattr(history.settings, name))
            box.toggled.connect(lambda checked, n=name: self._set_option(n, checked))
        self.notice = QLabel(history.notice)
        self.notice.setObjectName("muted")
        self.notice.setWordWrap(True)
        self.notice.setVisible(bool(history.notice))
        self.retry_button = QPushButton(tr("Erneut versuchen"))
        self.retry_button.setVisible(history.locked)
        self.retry_button.clicked.connect(self._retry)
        options = QHBoxLayout()
        options_left = QVBoxLayout()
        for box in (self.opt_enabled, self.opt_persist, self.opt_exit, self.opt_plain):
            options_left.addWidget(box)
        options.addLayout(options_left)
        options.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addLayout(body, 1)
        layout.addLayout(options)
        layout.addWidget(self.notice)
        layout.addWidget(self.retry_button)
        layout.addWidget(buttons)

        self.history.listeners.append(self._refresh)
        self.finished.connect(lambda *_: self.history.listeners.remove(self._refresh) if self._refresh in self.history.listeners else None)
        self._refresh()

    # --- Liste ---
    def _refresh(self, *_):
        current = self._current_id()
        self.list.blockSignals(True)
        self.list.clear()
        entries = self.history.search(self.search.text())
        for entry in entries:
            item = QListWidgetItem(_item_text(entry))
            item.setData(ENTRY_ROLE, entry.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if entries:
            self.list.setCurrentRow(next((i for i, e in enumerate(entries) if e.id == current), 0))
        self._show_entry()

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(ENTRY_ROLE) if item else None

    def _entry(self) -> Entry | None:
        entry_id = self._current_id()
        return self.history.get(entry_id) if entry_id else None

    def _show_entry(self, *_):
        entry = self._entry()
        has = entry is not None
        for w in (self.pin_button, self.delete_button, self.paste_button, self.copy_button):
            w.setEnabled(has)
        self.pin_button.setText(tr("Lösen") if has and entry.pinned else tr("Anheften"))
        self.clear_button.setEnabled(bool(self.history.entries))
        if entry is None:
            self.preview.setPlainText("")
            self.info.setText(tr("Kein Eintrag ausgewählt.") if self.history.entries else tr("Der Verlauf ist leer."))
            return
        text = entry.text
        note = tr("\n\n… gekürzt, der Eintrag hat {n} Zeichen", n=len(text)) if len(text) > PREVIEW_CHARS else ""
        self.preview.setPlainText(text[:PREVIEW_CHARS] + note)
        self.info.setText(meta(entry))

    # --- Aktionen ---
    def _check_all(self, state):
        for i in range(self.targets.count()):
            self.targets.item(i).setCheckState(state)

    def _checked_views(self) -> list:
        return [self.targets.item(i).data(ENTRY_ROLE) for i in range(self.targets.count())
                if self.targets.item(i).checkState() == Qt.CheckState.Checked]

    def _paste(self):
        entry = self._entry()
        if entry is None:
            return
        views = self._checked_views()
        if not views:
            QMessageBox.information(self, tr("Einfügen"), tr("Bitte mindestens einen Terminal-Reiter ankreuzen."))
            return
        if not confirm_paste(self, entry.text, len(views)):
            return
        self._paste_into(views, entry.text, self.with_enter.isChecked())
        self.accept()

    def _copy_back(self):
        entry = self._entry()
        if entry is not None:
            QGuiApplication.clipboard().setText(entry.text)

    def _toggle_pin(self):
        entry = self._entry()
        if entry is not None:
            self.history.set_pinned(entry.id, not entry.pinned)

    def _delete(self):
        entry_id = self._current_id()
        if entry_id:
            self.history.remove(entry_id)

    def _ask_new_text(self) -> str | None:
        dialog = EntryTextDialog(self)
        return dialog.text() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _new(self):
        text = self._ask_new_text()
        if text is None:
            return
        if self.history.too_large(text):
            QMessageBox.warning(self, tr("Neuer Eintrag"), tr("Der Text ist zu groß (höchstens {size}).", size=ch.human_size(ch.MAX_ENTRY_BYTES)))
        elif self.history.add(text) is None:
            QMessageBox.warning(self, tr("Neuer Eintrag"), tr("Leerer Text wird nicht aufgenommen."))

    def _ask_clear(self) -> tuple[str, bool]:
        """(„history“, „all“ oder „“ für Abbruch, zusätzlich die Zwischenablage des Systems leeren)"""
        box = QMessageBox(QMessageBox.Icon.Question, tr("Alles leeren"),
                          tr("Den Verlauf leeren?\n\n„Verlauf leeren“ behält das Angeheftete, „Alles leeren“ löscht auch das."), parent=self)
        system = QCheckBox(tr("Auch die Zwischenablage des Systems leeren"))
        box.setCheckBox(system)
        history = box.addButton(tr("Verlauf leeren"), QMessageBox.ButtonRole.AcceptRole)
        everything = box.addButton(tr("Alles leeren (auch Angeheftetes)"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        return ("history" if clicked is history else "all" if clicked is everything else ""), system.isChecked()

    def _clear_all(self):
        choice, system = self._ask_clear()
        if not choice:
            return
        self.history.clear(include_pinned=choice == "all")
        if system:
            QGuiApplication.clipboard().clear()

    def _set_option(self, name: str, value: bool):
        setattr(self.history.settings, name, value)
        self.history.settings_changed()

    def _retry(self):
        if self.history.unlock():
            self.retry_button.setVisible(False)
        self.notice.setText(self.history.notice)
        self.notice.setVisible(bool(self.history.notice))
