# SSH-Verwaltung

Desktop-Tool (Windows und Linux, Python + PySide6) zum Verwalten von SSH-Verbindungen.
Verbindungen werden in einem **frei konfigurierbaren Terminal** geöffnet.

## Start

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
ssh-verwaltung                   # oder: python -m ssh_verwaltung
```

## Gruppen

Verbindungen lassen sich in verschachtelten Gruppen ordnen (Pfad mit „/“, z. B. `Kunde A/Produktion`).
- *Neue Gruppe …* (Strg+Umschalt+N, Toolbar oder Kontextmenü) legt auch leere Gruppen an; im Kontextmenü einer Gruppe: Untergruppe, Umbenennen/Verschieben, Löschen.
- Per **Drag & Drop** verschiebst du Verbindungen und ganze Gruppen; Ablegen auf freier Fläche = oberste Ebene.
- Eine Gruppe zu löschen entfernt keine Verbindungen, sie rücken in die übergeordnete Gruppe.
- Neue Verbindungen landen in der aktuell ausgewählten Gruppe. Das Suchfeld filtert auch nach Gruppen.

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
- Verwalten unter *Einstellungen → Identitäten …* (Toolbar) oder direkt beim Anlegen einer Verbindung über *Identität → Neu …*.
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

## Daten

Konfiguration: `~/.config/ssh_verwaltung/config.json` (Linux), `%APPDATA%\ssh_verwaltung\config.json` (Windows).
Es werden keine Passwörter gespeichert; Authentifizierung läuft über ssh (Schlüssel, Agent, Passwortabfrage im Terminal).

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests
```
