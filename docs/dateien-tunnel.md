# Dateien und Tunnel

← [Zur README](../README.md) · *[English version](en/dateien-tunnel.md)*

## Dateien übertragen (SFTP)

*Dateien (SFTP)* (F4, Seitenleiste oder Kontextmenü einer Verbindung) öffnet einen Dateibrowser: links dein Rechner, rechts der Server.
- **Übertragen:** Dateien und Ordner per **Drag & Drop** zwischen den Seiten ziehen, mit den Knöpfen *Hochladen* / *Herunterladen* oder über das Kontextmenü.
  Auf der Serverseite kannst du auch Dateien direkt aus dem Dateimanager ablegen; wirfst du auf einen Ordner, landen sie darin.
- **Fortschritt:** Jede Übertragung läuft im Hintergrund mit eigenem Fortschrittsbalken und lässt sich abbrechen (eine halb übertragene Datei wird dabei entfernt). Mehrere Übertragungen laufen gleichzeitig.
- **Vorhandene Dateien:** Gibt es am Ziel schon gleichnamige Einträge, fragt Sesam: *Überschreiben*, *Überspringen* oder *Abbrechen*. Die Wahl gilt für die Einträge der obersten Ebene; Dateien in übertragenen Ordnern werden bei *Überschreiben* ersetzt.
- **Verwalten:** *Neuer Ordner*, *Umbenennen* (F2) auf beiden Seiten, *Löschen* (Entf) auf dem Server (nach Rückfrage, Ordner samt Inhalt). Pfadzeile (Strg+L), „Eine Ebene höher“, Persönlicher Ordner und Aktualisieren (F5) gibt es je Seite; die Spalten lassen sich sortieren.
- **Anmeldung:** wie beim Terminal, also mit Benutzer, Schlüsseldatei oder dem im Schlüsselbund gespeicherten Passwort (auch über eine Identität). Ist nichts hinterlegt oder wird es abgelehnt, fragt Sesam einmal nach; dieses Passwort gilt nur für das geöffnete Fenster und wird nicht gespeichert.
  Unbekannte Hosts werden erst nach Bestätigung des Fingerabdrucks (SHA256) in `known_hosts` aufgenommen, geänderte Schlüssel abgelehnt.
- **Grenzen:** Verbindungen mit **Jump-Host** werden noch nicht unterstützt, „Zusätzliche Optionen“ und *Mit sudo* gelten nicht für die Dateiübertragung (es wird mit dem Benutzer der Verbindung gearbeitet, nicht als root).
  Verknüpfungen auf Ordner werden beim Herunterladen nicht verfolgt (Schutz vor Schleifen), Verknüpfungen auf Dateien als Datei übertragen.
- Die Übertragung nutzt die Python-Bibliothek *paramiko*, braucht also kein `sftp`/`scp` auf dem Rechner.

## Port-Weiterleitungen (Tunnel)

Mit einer Port-Weiterleitung erreichst du Dienste, die nur auf dem Server oder in dessen Netz laufen, über die SSH-Verbindung. Gespeichert werden sie
je Verbindung (*Bearbeiten*, Reiter **Weiterleitungen**), gestartet und beendet im Kontextmenü der Verbindung unter **Weiterleitungen**
(ein Haken zeigt, dass der Tunnel läuft; *Alle starten* und *Alle beenden* gibt es auch).

- **Lokal (`-L`):** Ein Port auf diesem Rechner führt über den Server zu einem Ziel, das der Server erreicht. Beispiel: lokaler Port `5432` →
  Ziel `localhost:5432` öffnet eine Datenbank, die nur auf dem Server lauscht; lokaler Port `8443` → Ziel `192.168.3.20:443` öffnet die Weboberfläche
  eines Geräts im Netz hinter dem Server (im Browser `https://localhost:8443`).
- **Remote (`-R`):** Ein Port auf dem Server führt zu einem Ziel auf diesem Rechner, z. B. um einen lokalen Entwicklungsserver auf dem Server erreichbar zu machen.
- **Dynamisch (`-D`):** Ein SOCKS-Proxy auf diesem Rechner. Programme, die ihn nutzen, erreichen alles, was der Server erreicht.
- **Lauschadresse:** Leer bedeutet nur dieser Rechner (`127.0.0.1`, bei Remote auf dem Server `localhost`). Eine offene Adresse wie `0.0.0.0` macht den
  Tunnel für alle Rechner im Netz erreichbar, und zwar ohne Anmeldung; Sesam fragt deshalb beim Speichern nach.
- **Anzeige:** Läuft ein Tunnel, steht hinter dem Namen der Verbindung ⇄ mit der Anzahl. Sesam meldet, wenn ein Tunnel aktiv ist oder von selbst
  endet (Port belegt, Anmeldung abgelehnt, Verbindung verloren), mit der Fehlermeldung von `ssh`.
- **Zugang:** Benutzer, Schlüssel, Identität, Jump-Host und „Zusätzliche Optionen“ der Verbindung gelten auch für den Tunnel; ein gespeichertes Passwort wird wie beim
  Terminal übergeben. Ein Tunnel hat kein Terminal und kann nichts nachfragen: Ohne gespeichertes Passwort funktioniert er nur mit einem Schlüssel
  ohne Passphrase (bzw. einem geladenen `ssh-agent`). Remote-Befehl und sudo der Verbindung werden ignoriert.
- **Unbekannte Hosts:** Steht der Host noch nicht in `known_hosts`, zeigt Sesam vor dem Start die Fingerabdrücke und fragt, ob ihm vertraut werden soll.
- **Ende:** Jede Weiterleitung läuft als eigener `ssh -N`-Prozess; beim Beenden von Sesam (oder einem Löschen bzw. Ändern der Weiterleitung) werden die Tunnel beendet.
- **Synchronisation:** Die Weiterleitungen gehören zur Verbindung und werden mit synchronisiert (und signiert); gestartet wird nie automatisch.
