"""Hilfe für Tests: der ssh-Befehl, den der Wrapper (``sesam --connect``) im Terminal ausführen würde."""
from dataclasses import replace

from sesam import connect_cli


def wrapped_ssh(argv, conn):
    """Zerlegt den Terminal-Befehl ab ``--connect`` wie ``connect_cli`` und liefert die ssh-Argumente."""
    i = argv.index("--connect")
    assert argv[i + 1] == conn.id, "Wrapper soll die Verbindung über ihre ID laden"
    eff = replace(conn, **connect_cli.parse_overrides(argv[i + 2:]))
    args, _env = connect_cli.prepare(eff, None, "")
    return args
