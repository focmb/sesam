# Sesam

Desktop-Tool (Windows und Linux, Python + PySide6) zum Verwalten von SSH-Verbindungen.
Verbindungen werden in einem **frei konfigurierbaren Terminal** geöffnet.

## Start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
sesam                            # oder: python -m sesam
```

Für einen Starter im Anwendungsmenü/Dash siehe „Im Anwendungsmenü / Dash installieren“.

## Darstellung

Alle Hauptaktionen (Verbinden, Neue Verbindung, Neue Gruppe, Bearbeiten, Duplizieren, Löschen, Synchronisieren, Identitäten, Terminals)
stehen untereinander in einer Seitenleiste am rechten Rand; die Tastenkürzel (Enter, Strg+N, F2, Entf, Strg+D, F5 …) gelten im ganzen Fenster.
*Duplizieren* legt eine Kopie an und öffnet sie sofort zum Bearbeiten, der Name ist markiert und lässt sich gleich überschreiben; bricht man ab, bleibt die Kopie bestehen.

*Einstellungen → Darstellung* schaltet zwischen **Systemvorgabe**, **Hell** und **Dunkel** um (wirkt sofort, wird gespeichert).
Bei *Systemvorgabe* folgt Sesam dem Hell/Dunkel-Wechsel des Betriebssystems, sofern Qt ihn meldet (Windows, macOS, Linux je nach Desktop);
andernfalls stellst du es manuell ein.

Das Erscheinungsbild (Farben, Abstände, Icons) kommt aus `src/sesam/gui/style.py`: ein gemeinsames Stylesheet für Hell und Dunkel, das aus einer
Farbtabelle (`TOKENS`) erzeugt wird. Farben änderst du dort an einer Stelle; Icons sind einfache Linienzeichnungen im gleichen Modul.
Sesam nutzt dafür überall den Qt-Stil „Fusion“, damit es unter Windows und Linux gleich aussieht.

## Im Anwendungsmenü / Dash installieren (Linux)

Ein Skript legt Sesam für deinen Benutzer an (ohne root): Starter im Anwendungsmenü bzw. Dash, Icon und den Befehl `sesam`.

```bash
./packaging/linux/install.sh --dev        # startet direkt aus dem Projektordner (.venv), immer aktueller Code
./packaging/linux/install.sh              # oder: aus dem gebauten Programm (dist/sesam, siehe „Programm bauen“)
./packaging/linux/install.sh --uninstall  # entfernt alles wieder; ~/.config/sesam bleibt erhalten
```

Nach Änderungen am Code genügt bei `--dev` ein Neustart von Sesam; beim gebauten Programm musst du neu bauen und das Skript erneut ausführen.
Der Befehl `sesam` liegt in `~/.local/bin` (bei manchen Distributionen muss der Ordner erst im `PATH` stehen).

## Gruppen

Verbindungen lassen sich in verschachtelten Gruppen ordnen (Pfad mit „/“, z. B. `Kunde A/Produktion`).
- *Neue Gruppe …* (Strg+Umschalt+N, Seitenleiste oder Kontextmenü) legt auch leere Gruppen an; im Kontextmenü einer Gruppe: Untergruppe, Umbenennen/Verschieben, Löschen.
- Per **Drag & Drop** verschiebst du Verbindungen und ganze Gruppen; Ablegen auf freier Fläche = oberste Ebene.
- Eine Gruppe zu löschen entfernt keine Verbindungen, sie rücken in die übergeordnete Gruppe.
- Neue Verbindungen landen in der aktuell ausgewählten Gruppe. Das Suchfeld filtert auch nach Gruppen.
- Auf- und Zugeklapptes merkt sich Sesam: Beim nächsten Start sind genau die Gruppen zu, die beim Beenden zu waren. Dieser Zustand (wie die Spaltenbreiten) bleibt lokal und wird nicht per Git synchronisiert.

## Terminals konfigurieren

Unter *Einstellungen → Terminals …* legst du Terminals als Befehlsvorlage an und wählst ein Standardterminal.
Pro Verbindung kann ein abweichendes Terminal gewählt werden; im Kontextmenü gibt es „Verbinden mit …“.

| Platzhalter | Bedeutung |
|---|---|
| `{ssh_args}` | ssh-Befehl als einzelne Argumente |
| `{ssh_command}` | ssh-Befehl als ein String |
| `{title}` `{host}` `{user}` `{port}` `{destination}` | Verbindungsdaten |

Beispiele: `gnome-terminal --title {title} -- {ssh_args}`, `wt.exe new-tab --title {title} {ssh_args}`,
`cmd.exe /k {ssh_command}`, `putty.exe -ssh -P {port} {destination}`.

Beim ersten Start werden auf dem System gefundene Terminals vorbelegt.

Eigene Terminals (z. B. Ghostty) legst du über *Neu ▾ → Eigenes Terminal* an. Mit „Programm wählen …“ kannst du die
ausführbare Datei auswählen, falls sie nicht im `PATH` liegt (z. B. AppImage). Die Vorlagen für nicht installierte Terminals
findest du ebenfalls unter *Neu ▾*.

## Identitäten

Eine Identität bündelt **Benutzer, Schlüsseldatei und Passwort** und kann von beliebig vielen Verbindungen verwendet werden.
- Verwalten unter *Einstellungen → Identitäten …* (oder Seitenleiste) oder direkt beim Anlegen einer Verbindung über *Identität → Neu …*.
- Wählst du in einer Verbindung eine Identität, ersetzt sie Benutzer, Schlüsseldatei und Passwort der Verbindung (die Felder sind dann gesperrt, die eigenen Werte bleiben erhalten und gelten wieder bei „Keine“).
- Das Passwort liegt im Schlüsselbund, einmal pro Identität. Ändert sich ein Passwort, wird es nur an einer Stelle angepasst. Das gilt auch für sudo.
- Eine Identität zu löschen entfernt sie auch aus allen Verbindungen, die sie nutzen (diese brauchen danach wieder eigene Zugangsdaten).

## sudo

Pro Verbindung kannst du *Mit sudo* aktivieren: nach dem Login wechselt die Sitzung direkt zu root oder einem anderen Benutzer
(`sudo -i` = Login-Shell, `sudo -s` = Shell mit bisheriger Umgebung; Zielbenutzer optional). Zusätzlich gibt es im Kontextmenü
*Als root verbinden (sudo -i)* für einen einmaligen Wechsel, ohne die Verbindung zu ändern.

**sudo ohne erneute Eingabe:** Ist für die Verbindung ein Passwort gespeichert und *Gespeichertes Passwort auch für sudo verwenden*
aktiv (Standard), wird es für sudo automatisch genutzt. Es wird nie als Kommandozeilenargument übergeben, sondern verschlüsselt
als Umgebungsvariable `LC_SSHV_PW` (`ssh -o SendEnv=…`) in die Sitzung gereicht. Auf dem Server prüft `sudo -S -v` es genau
einmal, danach startet `sudo -i`/`sudo -s` ohne Abfrage.
- Der Server muss `LC_*` per `AcceptEnv` zulassen (Debian/Ubuntu: Standard). Sonst, oder bei falschem Passwort, fragt sudo wie gewohnt im Terminal.
- Das Passwort ist in der Sitzungsumgebung kurz für den eigenen Benutzer und root auf dem Server lesbar. Für sudo selbst wird die Variable entfernt.
- Funktioniert auch bei Anmeldung per Schlüssel: Das gespeicherte Passwort wird dann nur für sudo gebraucht.

## Passwörter

Pro Verbindung kann ein Passwort hinterlegt werden. Es wird im Schlüsselbund des Betriebssystems gespeichert
(Secret Service/GNOME Keyring/KWallet unter Linux, Anmeldeinformationsverwaltung unter Windows), **nicht** in `config.json`.
Beim Verbinden startet das Terminal `ssh` über einen kleinen Wrapper, der das Passwort per `SSH_ASKPASS` übergibt
(OpenSSH ≥ 8.4). Hinweise:
- Auf Passwortabfragen wird automatisch geantwortet, auf andere Abfragen (Passphrase, 2FA-Code) nicht.
- Unbekannte Hosts werden automatisch in `known_hosts` aufgenommen (`StrictHostKeyChecking=accept-new`), geänderte Schlüssel werden weiterhin abgelehnt. Setzt du die Option selbst unter „Zusätzliche Optionen“, hat sie Vorrang.

## Synchronisation über Git

*Einstellungen → Synchronisation …* gleicht Verbindungen, Gruppen und Identitäten über ein Git-Repository ab (Terminals und
Fensterbreiten bleiben pro Gerät). Das Tool nutzt das installierte `git`; die Anmeldung läuft also über deine SSH-Schlüssel bzw.
gespeicherten HTTPS-Zugangsdaten (im Hintergrund ohne Rückfragen, einmal vorher per *Verbindung testen* oder `git ls-remote` anmelden).

- **Automatisch:** beim Start und kurz nach jeder Änderung. *F5* bzw. *Jetzt synchronisieren* löst den Abgleich manuell aus; der Status steht rechts unten.
- **Repository-Aufbau:** eine JSON-Datei pro Eintrag (`connections/`, `identities/`), `groups.json`, `meta.json`. Das Repository darf privat sein, ist aber für Klartext-Daten wie Hostnamen und Benutzer sichtbar.
- **Passwörter** werden nur verschlüsselt abgelegt (scrypt + AES-256-GCM) und auf jedem Gerät in den Schlüsselbund geschrieben. Das **Sync-Passwort** legst du auf dem ersten Gerät fest und gibst es auf jedem weiteren ein. Es lässt sich nicht wiederherstellen und auch nicht ohne Weiteres ändern. Ohne Passwort-Sync bleiben vorhandene verschlüsselte Einträge im Repository erhalten.
- **Zusammenführen:** je Eintrag ein Dreiwege-Vergleich mit dem Stand des letzten Abgleichs. Änderungen an verschiedenen Einträgen werden kombiniert, Löschungen übernommen. Wurde derselbe Eintrag auf zwei Geräten unterschiedlich geändert, bleibt deine lokale Version und die andere erscheint als „… (Konflikt)“.
- Wird eine Identität gelöscht, verlieren Verbindungen, die sie nutzen, den Verweis.
- Leeres Repository anlegen (z. B. bei GitHub/GitLab/Gitea) genügt, das Tool erstellt den Branch selbst.

## Daten

Konfiguration: `~/.config/sesam/config.json` (Linux), `%APPDATA%\sesam\config.json` (Windows).
Passwörter stehen nie in dieser Datei, sondern im Schlüsselbund des Betriebssystems; ohne gespeichertes Passwort läuft die Anmeldung über ssh (Schlüssel, Agent, Abfrage im Terminal).

Ist `config.json` beschädigt (kein gültiges JSON o. Ä.), löscht Sesam sie nicht: Die Datei wird als `config.json.defekt-<Datum>-<Uhrzeit>` daneben gesichert,
Sesam zeigt eine Meldung mit dem Grund und startet mit leerer Konfiguration. Du kannst die Sicherung reparieren und als `config.json` zurückkopieren.
Mit aktiver Git-Synchronisation richtest du sie wieder ein; die Verbindungen kommen dann aus dem Repository zurück. Kann die Datei nicht gelesen oder nicht gesichert werden,
startet Sesam nicht und ändert nichts.

Alte Daten aus der früheren Version („ssh_verwaltung“) werden beim ersten Start automatisch übernommen: der Konfigurationsordner wird umbenannt, Passwörter ziehen im Schlüsselbund um.

## Programm bauen (Linux und Windows)

Mit PyInstaller entsteht ein Programmordner, der ohne installiertes Python läuft. Gebaut wird auf dem jeweiligen Zielsystem
(Linux auf Linux, Windows auf Windows; Cross-Kompilieren geht nicht):

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e . pyinstaller
python packaging/build.py
```

Ergebnis in `dist/`: der Ordner `sesam/` und ein Archiv (`sesam-<Version>-linux-x86_64.tar.gz` bzw. `…-windows-x86_64.zip`).
- **Linux:** `sesam/sesam` starten. Zielsysteme brauchen die üblichen Qt-Bibliotheken, bei Ubuntu/Debian z. B. `libxcb-cursor0` und `libegl1`
  (`QT_DEBUG_PLUGINS=1 ./sesam` zeigt, was fehlt). Gebaut wird gegen die glibc des Build-Systems; für möglichst viele Systeme auf einer älteren
  Distribution bauen (getestet: Ubuntu 22.04).
- **Windows:** `sesam\sesam.exe` starten (Fenster ohne Konsole). Der Ordner enthält zusätzlich `sesam-cli.exe`; der ssh-Wrapper (Passwort/sudo)
  braucht eine Konsole und nutzt diese Variante automatisch. Voraussetzungen: OpenSSH-Client (Windows-Feature) und Git. Unsignierte Programme
  löst SmartScreen u. U. eine Warnung aus.
- Es ist bewusst keine Ein-Datei-Variante: der Wrapper startet Sesam bei jeder Verbindung erneut, Entpacken würde jedes Mal dauern.

## Automatischer Build (Gitea Actions)

`.gitea/workflows/build.yml` läuft bei jedem Tag `v*` (z. B. `git tag v0.1.0 && git push --tags`) und auf Knopfdruck
(*Actions → Build → Run workflow*). Der Linux-Job installiert die Qt-Systembibliotheken, führt die Tests aus und baut mit
`packaging/build.py`; das Archiv hängt als Artefakt `sesam-linux` am Lauf.

Voraussetzung ist ein Gitea-Runner (`act_runner`, z. B. als Container neben Gitea) mit dem Label `ubuntu-22.04`,
etwa `ubuntu-22.04:docker://catthehacker/ubuntu:act-22.04`. Der Windows-Job steht auskommentiert im Workflow, bis ein Windows-Runner
(Label `windows`, mit Python und Git) existiert; bis dahin wird Windows von Hand gebaut (siehe oben).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests      # oder: python -m pytest
```

Die GUI-Tests laufen ohne Bildschirm (`QT_QPA_PLATFORM=offscreen` wird gesetzt) und werden übersprungen, wenn PySide6 oder die
Qt-Systembibliotheken fehlen. Die Sync-Tests brauchen `git`.
