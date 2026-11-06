"""Pseudoterminal für das integrierte Terminal: startet ein Programm und reicht Ein- und Ausgabe als Bytes weiter.

Linux: ``openpty`` + ``subprocess`` (der Prozess bekommt das Pseudoterminal als steuerndes Terminal).
Windows: ConPTY über das Paket ``pywinpty``.
"""
from __future__ import annotations

import collections
import os
import signal
import sys
import threading

from PySide6.QtCore import QObject, QSocketNotifier, QTimer, Signal

from .i18n import tr

IS_WINDOWS = sys.platform.startswith("win")


class PtyError(Exception):
    pass


class PtySession(QObject):
    """Ein Programm in einem Pseudoterminal. ``output`` liefert die Ausgabe, ``finished`` den Exit-Code (genau einmal)."""

    output = Signal(bytes)
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exit_code: int | None = None

    def start(self, args: list[str], env: dict[str, str], cols: int, rows: int) -> None:
        raise NotImplementedError

    def write(self, data: bytes) -> None:
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:
        raise NotImplementedError

    def terminate(self, force: bool = False) -> None:
        """Beendet den Prozess (zuerst höflich, ``force`` sofort)."""
        raise NotImplementedError

    def poll(self) -> int | None:
        """``None``, solange der Prozess läuft, sonst der Exit-Code."""
        return self._exit_code

    def _finish(self, code: int) -> None:
        if self._exit_code is None:
            self._exit_code = code
            self.finished.emit(code)


def create(parent=None) -> PtySession:
    return _WinPty(parent) if IS_WINDOWS else _PosixPty(parent)


class _PosixPty(PtySession):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._fd: int | None = None
        self._proc = None
        self._pending = b""
        self._reader: QSocketNotifier | None = None
        self._writer: QSocketNotifier | None = None
        # Prozessende erkennen, auch wenn das Pseudoterminal noch von Hintergrundprozessen offen gehalten wird
        self._watch = QTimer(self)
        self._watch.setInterval(150)
        self._watch.timeout.connect(self._check_exit)

    @staticmethod
    def _set_size(fd: int, cols: int, rows: int) -> None:
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(rows, 1), max(cols, 1), 0, 0))

    def start(self, args, env, cols, rows):
        import fcntl
        import pty
        import subprocess
        import termios

        master, slave = pty.openpty()
        try:
            self._set_size(master, cols, rows)

            def child_setup():  # läuft im Kindprozess nach setsid(): das Pseudoterminal wird zum steuernden Terminal (ssh liest Passwörter über /dev/tty)
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            self._proc = subprocess.Popen(
                args, stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
                preexec_fn=child_setup, close_fds=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            os.close(master)
            raise PtyError(str(exc)) from exc
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        self._fd = master
        self._reader = QSocketNotifier(master, QSocketNotifier.Type.Read, self)
        self._reader.activated.connect(self._readable)
        self._writer = QSocketNotifier(master, QSocketNotifier.Type.Write, self)
        self._writer.setEnabled(False)
        self._writer.activated.connect(self._flush)
        self._watch.start()

    # --- Lesen ---
    def _read_chunk(self) -> bytes | None:
        """Gelesene Bytes, ``b""`` wenn gerade nichts da ist, ``None`` am Ende."""
        if self._fd is None:
            return None
        try:
            data = os.read(self._fd, 65536)
        except BlockingIOError:
            return b""
        except OSError:  # EIO: die Gegenseite ist geschlossen
            return None
        return data or None

    def _readable(self):
        data = self._read_chunk()
        if data is None:
            self._end()
        elif data:
            self.output.emit(data)

    def _check_exit(self):
        if self._proc is not None and self._proc.poll() is not None:
            self._end()

    def _end(self):
        if self._exit_code is not None or self._fd is None:
            return
        self._watch.stop()
        # Rest der Ausgabe abholen (z. B. die letzte Fehlermeldung)
        while True:
            data = self._read_chunk()
            if not data:
                break
            self.output.emit(data)
        for notifier in (self._reader, self._writer):
            if notifier is not None:
                notifier.setEnabled(False)
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None
        try:
            code = self._proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 – hängt der Prozess, wird er beendet
            self.terminate(force=True)
            code = -signal.SIGKILL
        self._finish(code if code >= 0 else 128 - code)

    # --- Schreiben ---
    def write(self, data: bytes) -> None:
        if self._fd is None or not data:
            return
        self._pending += data
        self._flush()

    def _flush(self):
        if self._fd is None:
            return
        while self._pending:
            try:
                n = os.write(self._fd, self._pending)
            except BlockingIOError:
                if self._writer is not None:
                    self._writer.setEnabled(True)
                return
            except OSError:
                self._pending = b""
                break
            self._pending = self._pending[n:]
        if self._writer is not None:
            self._writer.setEnabled(False)

    def resize(self, cols: int, rows: int) -> None:
        if self._fd is not None:
            try:
                self._set_size(self._fd, cols, rows)
            except OSError:
                pass

    def terminate(self, force: bool = False) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        sig = signal.SIGKILL if force else signal.SIGHUP
        try:
            os.killpg(proc.pid, sig)  # der Prozess ist Sitzungsführer: seine Prozessgruppe enthält auch ssh
        except OSError:
            try:
                proc.send_signal(sig)
            except OSError:
                pass
        if not force:  # reagiert er nicht, kurz darauf hart beenden
            QTimer.singleShot(2000, lambda: self.terminate(force=True) if self._exit_code is None else None)


class _WinPty(PtySession):
    """ConPTY über pywinpty. Der Lese-Thread löst keine Qt-Signale aus (Absturz „stack overflow“ in pyside6.abi3.dll
    beim Sitzungsende im gebauten Programm), sondern legt Bytes in eine Warteschlange, die ein Zeitgeber im Hauptthread leert."""

    _END = None  # Marke in der Warteschlange: der Prozess hat keine Ausgabe mehr

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc = None
        self._queue: collections.deque = collections.deque()  # append/popleft sind thread-sicher
        self._drain = QTimer(self)
        self._drain.setInterval(10)
        self._drain.timeout.connect(self._drain_queue)

    def start(self, args, env, cols, rows):
        try:
            from winpty import PtyProcess
        except ImportError as exc:
            raise PtyError(tr("Das Paket „pywinpty“ ist nicht installiert (pip install pywinpty).")) from exc
        try:
            self._proc = PtyProcess.spawn(args, env=env, dimensions=(max(rows, 1), max(cols, 1)))
        except Exception as exc:  # noqa: BLE001 – pywinpty wirft je nach Fehler verschiedene Typen
            raise PtyError(str(exc)) from exc
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        self._drain.start()

    def _pump(self, proc):
        """Läuft im eigenen Thread: nur lesen und einreihen, nichts von Qt anfassen."""
        try:
            while True:
                try:
                    data = proc.read(65536)
                except Exception:  # noqa: BLE001 – EOFError am Ende, sonst ebenfalls Schluss
                    break
                if data:
                    self._queue.append(data.encode("utf-8", "replace") if isinstance(data, str) else bytes(data))
        finally:
            self._queue.append(self._END)

    def _drain_queue(self):
        for _ in range(64):  # nicht zu viel auf einmal, damit die Oberfläche bedienbar bleibt
            try:
                item = self._queue.popleft()
            except IndexError:
                return
            if item is self._END:
                self._drain.stop()
                self._end()
                return
            self.output.emit(item)

    def _end(self):
        code = 0
        try:
            code = int(getattr(self._proc, "exitstatus", 0) or 0)
        except Exception:  # noqa: BLE001 – pywinpty kann hier verschiedene Fehler werfen
            pass
        self._finish(code)

    def write(self, data: bytes) -> None:
        if self._proc is not None and data and self._exit_code is None:
            try:
                self._proc.write(data.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                pass

    def resize(self, cols: int, rows: int) -> None:
        if self._proc is not None and self._exit_code is None:
            try:
                self._proc.setwinsize(max(rows, 1), max(cols, 1))
            except Exception:  # noqa: BLE001
                pass

    def terminate(self, force: bool = False) -> None:
        if self._proc is not None and self._exit_code is None:
            try:
                self._proc.terminate(force=True)
            except Exception:  # noqa: BLE001
                pass
