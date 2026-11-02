# Terminals und Snippets

← [Zur README](../README.md) · *[English version](en/terminals-snippets.md)*

## Terminals konfigurieren

Unter *Terminals …* in der Seitenleiste legst du Terminals als Befehlsvorlage an und wählst ein Standardterminal.
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

## Verbindungsfehler und geänderte Host-Schlüssel

Jede Verbindung startet im Terminal über einen kleinen Wrapper (auch ohne gespeichertes Passwort), der `ssh` als Unterprozess ausführt.
- **Verbindung klappt nicht** (Server abgeschaltet, Zeitüberschreitung, Anmeldung abgelehnt …): Das Terminal bleibt mit der Meldung von `ssh` offen, bis du Enter drückst. Normal beendete Sitzungen schließen sich wie gewohnt.
- **Schlüssel des Servers hat sich geändert** (z. B. neuer Server mit gleicher IP): Nach der Meldung fragt der Wrapper, ob der alte Eintrag per `ssh-keygen -R` aus `known_hosts` entfernt und neu verbunden werden soll. Bei „j“ wird der neue Schlüssel übernommen, bei „N“ (Standard) bleibt alles unverändert. Der Eintrag sollte nur entfernt werden, wenn die Änderung erwartet war – sonst könnte es ein Angriff sein.
- Terminal-Vorlagen ohne `{ssh_args}`/`{ssh_command}` (z. B. PuTTY) starten die Verbindung selbst und laufen nicht über den Wrapper.

## Snippets

*Snippets …* (Strg+Umschalt+S, Seitenleiste) speichert Befehle, die im Terminal auf einem oder mehreren Hosts laufen.
- **Anlegen:** Name, Befehl (auch mehrzeilig, z. B. `df -h && free -m`), optional eine **Kategorie** (Unterordner mit „/“, z. B. `Docker/Logs`) und eine Notiz. Der Befehl gilt für alle Verbindungen.
- **Ausführen:** Rechtsklick auf eine Verbindung → *Snippet ausführen* (die Kategorien erscheinen als Untermenüs) oder im Snippet-Fenster unten die Ziele ankreuzen. Mit Mehrfachauswahl (Strg-/Umschalt-Klick) läuft das Snippet auf allen ausgewählten Verbindungen; je Verbindung öffnet sich ein Terminal, vorher fragt Sesam nach. Ein Fehler bei einem Ziel (z. B. fehlender Schlüssel) stoppt die anderen nicht.
- **Mit sudo:** Nur der Befehl läuft per `sudo -i` (Zielbenutzer der Verbindung, sonst root). Das gespeicherte Passwort wird wie bei *Mit sudo* der Verbindung genutzt. Eine danach offene Shell gehört wieder dem normalen Benutzer.
- **Danach:** *Shell offen lassen* (Standard), *Auf eine Taste warten* oder *Terminal sofort schließen*.
- Der Befehl läuft in `sh -c` auf dem Zielsystem. „Remote-Befehl“ und „Mit sudo“ der Verbindung werden für das Snippet ersetzt.
- **Synchronisation:** Snippets liegen im Git-Repository (`snippets/`) im Klartext und kommen auf alle Geräte. Schreibe keine Passwörter oder Tokens in Befehle.
