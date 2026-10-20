"""Gemeinsame Basis für GUI-Tests (ohne Bildschirm, ohne Schlüsselbund, ohne blockierende Dialoge)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 oder Qt-Systembibliotheken fehlen
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 nicht verfügbar")
class GuiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls._close_widgets()

    @staticmethod
    def _close_widgets():
        from PySide6.QtCore import QCoreApplication, QEvent

        for widget in QApplication.topLevelWidgets():
            widget.close()
            widget.deleteLater()
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

    def setUp(self):
        from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

        from sesam import passwords

        for name, fake in (("set_password", lambda i, p: None), ("get_password", lambda i: None), ("delete_password", lambda i: None)):
            patcher = mock.patch.object(passwords, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        def boom(*_a, **_k):  # ein unerwartetes modales Fenster würde den Test endlos blockieren
            raise AssertionError("Unerwartetes modales Fenster im Test (würde hängen)")

        for owner, names in ((QDialog, ["exec"]), (QMessageBox, ["information", "question", "warning", "critical"]),
                             (QInputDialog, ["getText", "getItem", "getInt"])):
            for name in names:
                patcher = mock.patch.object(owner, name, boom)
                patcher.start()
                self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self._close_widgets)
        self.path = Path(self.tmp.name) / "c.json"

    def _window(self):
        """Öffnet ein Hauptfenster mit der Konfiguration von der Platte (wie beim echten Start)."""
        from sesam import storage
        from sesam.gui.main_window import MainWindow

        w = MainWindow(storage.load(self.path), self.path)
        w.resize(1000, 600)
        w.show()
        self.app.processEvents()
        return w

    def _group_item(self, w, path):
        from PySide6.QtWidgets import QTreeWidgetItemIterator

        from sesam.gui.main_window import GROUP_ROLE

        it = QTreeWidgetItemIterator(w.tree)
        while it.value():
            if it.value().data(0, GROUP_ROLE) == path:
                return it.value()
            it += 1
        self.fail(f"Gruppe {path!r} nicht im Baum")
