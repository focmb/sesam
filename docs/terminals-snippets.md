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

## Integriertes Terminal

Der Eintrag *Integriert (in Sesam)* öffnet die Verbindung als Reiter im Hauptfenster, ohne ein externes Terminal-Programm.
Er steht neben den anderen Terminals zur Wahl (als Standard oder pro Verbindung, auch über „Verbinden mit …“) und lässt sich nicht löschen.

- **Reiter:** Vorn bleibt die Verbindungsliste, jede Sitzung bekommt einen eigenen Reiter. Sobald `ssh` endet, schließt sich der Reiter; das Schließen einer laufenden Sitzung fragt nach.
- **Bedienung:** Auswahl mit der Maus kopiert in die Auswahl-Zwischenablage (Mittelklick fügt ein), Strg+Umschalt+C / Strg+Umschalt+V bzw. Strg+Einfg / Umschalt+Einfg kopieren und einfügen, Umschalt+Bild↑/↓ und das Mausrad blättern im Verlauf (10 000 Zeilen), Strg+Mausrad ändert die Schriftgröße des Reiters. Rechtsklick zeigt ein Menü. Programme wie vim, less oder htop laufen im Alternativbildschirm.
- **Gleiche Technik wie extern:** Es läuft derselbe Wrapper mit `ssh`, also gelten `~/.ssh/config`, Jump-Hosts, gespeicherte Passwörter, sudo und die Rückfrage bei geänderten Host-Schlüsseln unverändert.
- **Voraussetzungen:** Das Python-Paket `pyte` (unter Windows zusätzlich `pywinpty` für ConPTY). Die fertigen Pakete bringen beides mit; bei Arch ist `python-pyte` eine optionale Abhängigkeit.
- **Grenzen:** Mausunterstützung innerhalb von Programmen (Mausmodi) und Sixel-Grafiken werden nicht ausgewertet.

## Zwischenablage-Verlauf

Text, den du in Sesam kopierst, bleibt abrufbar und lässt sich in mehrere Terminal-Reiter einfügen.

- **Was aufgenommen wird:** Kopien im integrierten Terminal (Strg+Umschalt+C, Rechtsklick → Kopieren) und Text aus anderen Programmen, sobald Sesam im Vordergrund ist. Solange Sesam im Hintergrund liegt, liest es die Zwischenablage nicht mit. Passwortmanager markieren geheime Kopien; die überspringt Sesam. Leerer Text und Einträge über 1 MB werden nicht aufgenommen, der Verlauf hält höchstens 200 Einträge bzw. 20 MB (ältere, nicht angeheftete fallen zuerst heraus).
- **Popup im Terminal:** Strg+Umschalt+H (oder Rechtsklick → Verlauf …) öffnet eine Liste mit Suchfeld. Enter fügt den Eintrag ein, Umschalt+Enter fügt ein und führt aus, Alt+1…9 wählt einen der ersten neun Einträge direkt, Strg+Entf löscht. Angeheftete Einträge stehen oben und behalten so ihre Nummern.
- **Dialog „Zwischenablage …“** (Seitenleiste): Vorschau auch langer Texte, Anheften, Löschen, eigene Einträge anlegen, in die Zwischenablage zurückkopieren und **in mehrere Reiter gleichzeitig einfügen** (Reiter ankreuzen, optional Enter anhängen). Bei sehr langem oder mehrzeiligem Text in mehreren Reitern fragt Sesam vorher nach, weil ein Terminal ihn sofort ausführen kann.
- **Reiter-Menü:** Rechtsklick auf einen Reiter → „Zwischenablage in alle Reiter einfügen“.
- **Alles leeren:** Im Dialog unter „Alles leeren …“. „Verlauf leeren“ behält das Angeheftete, „Alles leeren“ löscht auch das und erneuert den Schlüssel der Datei; optional wird die Zwischenablage des Systems mitgeleert.
- **Speicherung:** Der Verlauf übersteht einen Neustart. Er liegt in `clipboard.json` neben der Konfiguration (nur für den Benutzer lesbar), ist **verschlüsselt** (AES-GCM, der Schlüssel liegt im Schlüsselbund) und läuft **nie durch die Synchronisation**. Ohne Schlüsselbund bleibt er im Arbeitsspeicher, außer du erlaubst im Dialog das unverschlüsselte Speichern. Optionen: Verlauf ganz abschalten, nicht speichern, beim Beenden leeren (Angeheftetes bleibt).

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
