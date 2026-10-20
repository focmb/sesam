#!/usr/bin/env bash
# Installiert Sesam für den aktuellen Benutzer (ohne root): Starter im Anwendungsmenü/Dash,
# Icon und der Befehl "sesam". Die Konfiguration (~/.config/sesam) wird nie angefasst.
#
#   ./packaging/linux/install.sh              aus dem gebauten Programm (dist/sesam, siehe README)
#   ./packaging/linux/install.sh --dev        aus diesem Projektordner mit der .venv (immer aktueller Code)
#   ./packaging/linux/install.sh --uninstall  entfernt alles wieder
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"
APP="$DATA/sesam-app"
APPS="$DATA/applications"
ICONS="$DATA/icons/hicolor/scalable/apps"

mode="build"
case "${1:-}" in
  "") ;;
  --dev) mode="dev" ;;
  --uninstall) mode="uninstall" ;;
  *) echo "Aufruf: $0 [--dev | --uninstall]" >&2; exit 2 ;;
esac

refresh_caches() {
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -f -t "$DATA/icons/hicolor" >/dev/null 2>&1 || true
}

remove_all() {
  rm -f "$BIN/sesam" "$APPS/sesam.desktop" "$ICONS/sesam.svg"
  rm -rf "$APP"
}

if [ "$mode" = "uninstall" ]; then
  remove_all
  refresh_caches
  echo "Sesam wurde entfernt (Konfiguration in ~/.config/sesam bleibt erhalten)."
  exit 0
fi

if [ "$mode" = "build" ]; then
  if [ ! -x "$ROOT/dist/sesam/sesam" ]; then
    echo "Kein gebautes Programm gefunden ($ROOT/dist/sesam/sesam)." >&2
    echo "Bauen:  pip install -e . pyinstaller && python packaging/build.py" >&2
    echo "oder:   $0 --dev   (startet direkt aus dem Projektordner)" >&2
    exit 1
  fi
else
  PY="$ROOT/.venv/bin/python"
  if [ ! -x "$PY" ] || ! "$PY" -c "import sesam, PySide6, keyring, cryptography" >/dev/null 2>&1; then
    echo "Die Umgebung $ROOT/.venv ist nicht vollständig eingerichtet." >&2
    echo "Einrichten:  python -m venv .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
  fi
fi

remove_all  # saubere Neuinstallation, auch beim Wechsel zwischen den Modi
mkdir -p "$BIN" "$APP" "$APPS" "$ICONS"

if [ "$mode" = "build" ]; then
  cp -a "$ROOT/dist/sesam/." "$APP/"
  EXEC="$APP/sesam"
  ln -s "$EXEC" "$BIN/sesam"
else
  EXEC="$BIN/sesam"
  printf '#!/bin/sh\nexec "%s" -m sesam "$@"\n' "$PY" > "$EXEC"
  chmod +x "$EXEC"
fi

sed "s|@EXEC@|$EXEC|" "$HERE/sesam.desktop" > "$APPS/sesam.desktop"
cp "$HERE/sesam.svg" "$ICONS/sesam.svg"
refresh_caches

echo "Sesam ist installiert ($mode). Du findest es jetzt im Anwendungsmenü/Dash unter „Sesam“."
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Hinweis: $BIN steht nicht in deinem PATH; der Befehl „sesam“ im Terminal funktioniert dann erst nach einer Anmeldung bzw. Anpassung des PATH." ;;
esac
