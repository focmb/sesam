"""Tests für Docker-Container im Verbindungsbaum (Hauptfenster) gegen einen lokalen SSH-Server mit docker-Antwort."""
import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from gui_base import GuiTestCase

try:
    import paramiko
except ImportError:
    paramiko = None

from sesam import storage
from sesam.models import Config, Connection


def ps(name, image="nginx", status="Up 1 hour", state="running"):
    return json.dumps({"ID": name, "Names": name, "Image": image, "Status": status, "Ports": "", "State": state})


@unittest.skipIf(paramiko is None, "paramiko nicht verfügbar")
class DockerTreeTests(GuiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if paramiko is not None:
            cls.host_key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        super().setUp()
        from sesam import passwords, sftp
        from sftp_server import SftpTestServer

        base = Path(self.tmp.name)
        (base / "remote").mkdir()
        self.reply = ((ps("web") + "\n" + ps("db", "postgres:16")).encode(), b"", 0)
        self.server = SftpTestServer(str(base / "remote"), host_key=self.host_key, exec_handler=lambda cmd: self.reply)
        self.addCleanup(self.server.close)
        self.conn = Connection(name="Host", host="127.0.0.1", port=self.server.port, user="tester", use_password=True,
                               docker=True, docker_shell="/bin/ash", docker_user="app")
        self.plain = Connection(name="Ohne", host="example.org")
        cfg = Config(connections=[self.conn, self.plain])
        storage.save(cfg, self.path)
        for target, name, fake in ((passwords, "get_password", lambda _i: "geheim"), (sftp, "default_known_hosts", lambda: base / "known_hosts")):
            patcher = mock.patch.object(target, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.w = self._window()
        self.addCleanup(self.w.close)

    def wait(self, condition, timeout=8.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition(), "Bedingung wurde nicht rechtzeitig erfüllt")

    def host_item(self, name="Host"):
        for i in range(self.w.tree.topLevelItemCount()):
            if self.w.tree.topLevelItem(i).text(0) == name:
                return self.w.tree.topLevelItem(i)
        self.fail(name)

    def child_texts(self, item):
        return [item.child(i).text(0) for i in range(item.childCount())]

    def expand(self):
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["db", "web"])

    # --- Tests ---
    def test_only_docker_hosts_have_children_and_nothing_is_queried_before_expanding(self):
        self.assertEqual(self.child_texts(self.host_item()), ["…"])
        self.assertEqual(self.host_item("Ohne").childCount(), 0)
        self.app.processEvents()
        self.assertEqual(self.server.commands, [])

    def test_expanding_loads_running_containers(self):
        self.expand()
        item = self.host_item().child(0)
        self.assertEqual(item.text(1), "postgres:16 · Up 1 hour")
        self.assertEqual(self.server.commands.count("docker ps -a --format '{{json .}}'"), 1)
        self.host_item().setExpanded(False)
        self.host_item().setExpanded(True)  # erneutes Aufklappen lädt nicht noch einmal
        self.app.processEvents()
        self.assertEqual(len(self.server.commands), 1)

    def test_error_is_shown_in_tree_and_retried_on_next_expand(self):
        self.reply = (b"", b"bash: docker: command not found\n", 127)
        self.host_item().setExpanded(True)
        self.wait(lambda: "nicht installiert" in self.host_item().child(0).text(0))
        self.reply = (ps("web").encode(), b"", 0)
        self.host_item().setExpanded(False)
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["web"])

    def test_failed_load_does_not_loop(self):
        self.reply = (b"", b"bash: docker: command not found\n", 127)
        self.host_item().setExpanded(True)
        self.wait(lambda: "nicht installiert" in self.host_item().child(0).text(0))
        end = time.monotonic() + 0.5
        while time.monotonic() < end:  # Fehler anzeigen löst keine weiteren Abfragen aus
            self.app.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(self.server.commands), 1)

    def test_selection_survives_loading(self):
        self.expand()
        self.w.tree.setCurrentItem(self.host_item().child(1))
        self.reply = ((ps("web") + "\n" + ps("zzz")).encode(), b"", 0)
        self.w.reload_containers(self.conn.id)
        self.wait(lambda: self.child_texts(self.host_item()) == ["web", "zzz"])
        self.assertEqual(self.w._selected_container()[1], "web")

    def test_no_containers(self):
        self.reply = (b"", b"", 0)
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["Keine Container"])

    def test_containers_survive_refresh_and_expanded_state(self):
        self.expand()
        self.w.refresh()
        self.assertTrue(self.host_item().isExpanded())
        self.assertEqual(self.child_texts(self.host_item()), ["db", "web"])
        self.assertEqual(len(self.server.commands), 1)

    def test_reload_action_queries_again(self):
        self.expand()
        self.reply = (ps("neu").encode(), b"", 0)
        self.w.reload_containers(self.conn.id)
        self.wait(lambda: self.child_texts(self.host_item()) == ["neu"])

    def test_container_rows_are_not_connections(self):
        from sesam.gui.main_window import CONTAINER_ROLE, ID_ROLE

        self.expand()
        child = self.host_item().child(1)
        self.assertEqual(child.data(0, CONTAINER_ROLE), f"{self.conn.id}\x1fweb")
        self.assertIsNone(child.data(0, ID_ROLE))
        self.w.tree.setCurrentItem(child)
        self.assertIsNone(self.w._selected())
        self.assertEqual(self.w._selected_container()[1], "web")
        before = len(self.w.cfg.connections)
        self.w.duplicate_selected()
        self.w.delete_selected()
        self.assertEqual(len(self.w.cfg.connections), before)

    def test_stopped_container_is_listed_and_not_connectable(self):
        from PySide6.QtWidgets import QMessageBox

        self.reply = ((ps("web") + "\n" + ps("old", status="Exited (0) 2 days ago", state="exited")).encode(), b"", 0)
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["old", "web"])
        self.w.tree.setCurrentItem(self.host_item().child(0))
        with mock.patch("sesam.gui.main_window.launcher.launch") as launch, \
                mock.patch.object(QMessageBox, "information") as info:
            self.w.connect_container()
        launch.assert_not_called()
        info.assert_called_once()

    def _act(self, name, action, answer=True, **reply_after):
        from PySide6.QtWidgets import QMessageBox

        self.w.tree.setCurrentItem(next(self.host_item().child(i) for i in range(self.host_item().childCount())
                                        if self.host_item().child(i).text(0) == name))
        answer_button = QMessageBox.StandardButton.Yes if answer else QMessageBox.StandardButton.No
        with mock.patch.object(QMessageBox, "question", return_value=answer_button) as question:
            self.w.container_action(action)
        return question

    def test_actions_run_the_right_docker_command_and_reload(self):
        self.reply = ((ps("web") + "\n" + ps("old", status="Exited (0) 2 days ago", state="exited")).encode(), b"", 0)
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["old", "web"])
        before = len(self.server.commands)
        self._act("old", "start")  # Starten fragt nicht nach
        self.wait(lambda: len(self.server.commands) >= before + 2)  # Aktion + anschließendes Neuladen
        self.assertEqual(self.server.commands[before], "docker start old")
        self.assertEqual(self.server.commands[before + 1], "docker ps -a --format '{{json .}}'")
        before = len(self.server.commands)
        question = self._act("web", "stop")
        question.assert_called_once()
        self.wait(lambda: len(self.server.commands) >= before + 2)
        self.assertEqual(self.server.commands[before], "docker stop web")

    def test_remove_asks_and_forces_only_for_running_containers(self):
        self.reply = ((ps("web") + "\n" + ps("old", status="Exited (0) 2 days ago", state="exited")).encode(), b"", 0)
        self.host_item().setExpanded(True)
        self.wait(lambda: self.child_texts(self.host_item()) == ["old", "web"])
        before = len(self.server.commands)
        self._act("old", "remove", answer=False)  # abgebrochen: nichts passiert
        self.app.processEvents()
        self.assertEqual(len(self.server.commands), before)
        self._act("old", "remove")
        self.wait(lambda: len(self.server.commands) >= before + 2)
        self.assertEqual(self.server.commands[before], "docker rm old")
        before = len(self.server.commands)
        self._act("web", "remove")
        self.wait(lambda: len(self.server.commands) >= before + 2)
        self.assertEqual(self.server.commands[before], "docker rm -f web")

    def test_failed_action_shows_error_and_reloads(self):
        from PySide6.QtWidgets import QMessageBox

        self.expand()
        before = len(self.server.commands)
        self.reply = (b"", b"Error response from daemon: No such container: web\n", 1)
        with mock.patch.object(QMessageBox, "critical") as critical:
            self._act("web", "restart")
            self.wait(lambda: critical.called)
        self.assertIn("No such container", critical.call_args.args[2])
        self.assertEqual(self.server.commands[before], "docker restart web")

    def test_double_click_opens_shell_in_container_with_defaults(self):
        import shlex

        self.expand()
        child = self.host_item().child(1)
        self.w.tree.setCurrentItem(child)
        with mock.patch("sesam.gui.main_window.launcher.launch") as launch:
            self.w._double_clicked(child)
        cfg, titled, term = launch.call_args.args
        self.assertEqual(titled.name, "Host · web")
        self.assertIsNone(term)
        self.assertEqual(shlex.split(launch.call_args.kwargs["remote_command"]),
                         ["docker", "exec", "-it", "-u", "app", "web", "/bin/ash"])

    def test_connect_as_uses_dialog_values(self):
        import shlex

        from PySide6.QtWidgets import QDialog

        from sesam.gui import main_window

        self.expand()
        self.w.tree.setCurrentItem(self.host_item().child(0))
        seen = {}

        class FakeDialog:
            def __init__(self, container, shell, user, parent=None):
                seen.update(container=container, shell=shell, user=user)

            def exec(self):
                return QDialog.DialogCode.Accepted

            def values(self):
                return "", "root"  # Shell automatisch, Benutzer root

        with mock.patch.object(main_window, "DockerConnectDialog", FakeDialog), \
                mock.patch.object(main_window.launcher, "launch") as launch:
            self.w.connect_container(ask=True)
        self.assertEqual(seen, {"container": "db", "shell": "/bin/ash", "user": "app"})  # vorbelegt
        parts = shlex.split(launch.call_args.kwargs["remote_command"])
        self.assertEqual(parts[:6], ["docker", "exec", "-it", "-u", "root", "db"])
        self.assertEqual(parts[6:8], ["sh", "-c"])

    def test_cancelled_dialog_does_not_connect(self):
        from sesam.gui import main_window

        self.expand()
        self.w.tree.setCurrentItem(self.host_item().child(0))
        fake = mock.Mock(return_value=mock.Mock(exec=mock.Mock(return_value=0)))
        with mock.patch.object(main_window, "DockerConnectDialog", fake), mock.patch.object(main_window.launcher, "launch") as launch:
            self.w.connect_container(ask=True)
        launch.assert_not_called()

    def test_wrong_password_asks_once_then_loads(self):
        from PySide6.QtWidgets import QInputDialog

        from sesam import passwords

        with mock.patch.object(passwords, "get_password", lambda _i: "falsch"), \
                mock.patch.object(QInputDialog, "getText", return_value=("geheim", True)) as ask:
            self.host_item().setExpanded(True)
            self.wait(lambda: self.child_texts(self.host_item()) == ["db", "web"])
        ask.assert_called_once()

    def test_editing_host_invalidates_cache(self):
        self.expand()
        self.w._docker_forget(self.conn.id)
        self.w.refresh()
        # aufgeklappt gebliebener Host lädt nach dem Verwerfen selbständig neu
        self.wait(lambda: self.child_texts(self.host_item()) == ["db", "web"])
        self.assertEqual(len(self.server.commands), 2)


class ConnectionDialogDockerTests(GuiTestCase):
    def test_docker_fields_roundtrip_and_enable_state(self):
        from sesam.gui.connection_dialog import ConnectionDialog

        conn = Connection(name="A", host="h")
        dlg = ConnectionDialog(Config(connections=[conn]), conn)
        self.addCleanup(dlg.close)
        self.assertFalse(dlg.docker_shell.isEnabled())
        dlg.docker.setChecked(True)
        self.assertTrue(dlg.docker_user.isEnabled())
        dlg.docker_shell.setText(" /bin/zsh ")
        dlg.docker_user.setText("root")
        dlg._accept()
        self.assertEqual((conn.docker, conn.docker_shell, conn.docker_user), (True, "/bin/zsh", "root"))


if __name__ == "__main__":
    unittest.main()
