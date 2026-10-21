"""Kleiner SSH/SFTP-Server im selben Prozess für Tests (nur Loopback, Passwortanmeldung, Dateien unter einem Wurzelordner)."""
from __future__ import annotations

import logging
import os
import socket
import threading

import paramiko
from paramiko import AUTH_FAILED, AUTH_SUCCESSFUL, OPEN_SUCCEEDED, SFTP_OK, SFTPAttributes, SFTPHandle, SFTPServer, SFTPServerInterface


logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)  # Abbrüche beim Test-Aufräumen sind erwartet, kein Lärm


class _Auth(paramiko.ServerInterface):
    def __init__(self, user: str, password: str):
        self.user, self.password = user, password

    def check_auth_password(self, username, password):
        return AUTH_SUCCESSFUL if (username, password) == (self.user, self.password) else AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_request(self, kind, chanid):
        return OPEN_SUCCEEDED


class _Handle(SFTPHandle):
    def stat(self):
        try:
            return SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def chattr(self, attr):
        return SFTP_OK


class _Files(SFTPServerInterface):
    ROOT = ""

    def _real(self, path):
        return os.path.join(self.ROOT, self.canonicalize(path).lstrip("/"))

    def _attrs(self, fn, path):
        try:
            return SFTPAttributes.from_stat(fn(self._real(path)))
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def list_folder(self, path):
        real = self._real(path)
        try:
            out = []
            for name in os.listdir(real):
                attr = SFTPAttributes.from_stat(os.lstat(os.path.join(real, name)))
                attr.filename = name
                out.append(attr)
            return out
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)

    def stat(self, path):
        return self._attrs(os.stat, path)

    def lstat(self, path):
        return self._attrs(os.lstat, path)

    def open(self, path, flags, attr):
        real = self._real(path)
        try:
            fd = os.open(real, flags | getattr(os, "O_BINARY", 0), 0o666)
            if flags & os.O_WRONLY:
                mode = "ab" if flags & os.O_APPEND else "wb"
            elif flags & os.O_RDWR:
                mode = "a+b" if flags & os.O_APPEND else "r+b"
            else:
                mode = "rb"
            f = os.fdopen(fd, mode)
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)
        handle = _Handle(flags)
        handle.filename, handle.readfile, handle.writefile = real, f, f
        return handle

    def _do(self, fn, *args):
        try:
            fn(*args)
        except OSError as exc:
            return SFTPServer.convert_errno(exc.errno)
        return SFTP_OK

    def remove(self, path):
        return self._do(os.remove, self._real(path))

    def rename(self, oldpath, newpath):
        return self._do(os.rename, self._real(oldpath), self._real(newpath))

    def mkdir(self, path, attr):
        return self._do(os.mkdir, self._real(path))

    def rmdir(self, path):
        return self._do(os.rmdir, self._real(path))


class SftpTestServer:
    def __init__(self, root: str, user: str = "tester", password: str = "geheim", host_key=None):
        self.user, self.password = user, password
        self.host_key = host_key or paramiko.RSAKey.generate(2048)
        self._files = type("Files", (_Files,), {"ROOT": root})
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._transports: list[paramiko.Transport] = []
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            transport = paramiko.Transport(conn)
            transport.add_server_key(self.host_key)
            transport.set_subsystem_handler("sftp", SFTPServer, sftp_si=self._files)
            try:
                transport.start_server(server=_Auth(self.user, self.password))
            except (paramiko.SSHException, EOFError, OSError):
                continue
            self._transports.append(transport)

    def close(self):
        self._sock.close()
        for t in self._transports:
            t.close()
