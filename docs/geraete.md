# Docker, Proxmox, Cisco und WireGuard

← [Zur README](../README.md) · *[English version](en/geraete.md)*

## Docker-Container

Läuft auf einem Host Docker, kannst du direkt in einen Container verbinden. In der Verbindung (*Bearbeiten*, Reiter *Baum-Erweiterungen*) den Haken
**Docker-Container dieses Hosts im Verbindungsbaum anzeigen** setzen; optional lassen sich Standard-Shell und -Benutzer für die Container festlegen.
- **Im Baum:** Der Host bekommt einen Pfeil. Beim Aufklappen fragt Sesam den Host per SSH nach seinen Containern (`docker ps -a`) und zeigt sie als Unterelemente
  (Name, Image, Status). Gestoppte Container erscheinen ausgegraut. Erst beim Aufklappen wird verbunden; *Container aktualisieren* (Kontextmenü) lädt neu.
- **Steuern:** Im Kontextmenü eines Containers gibt es *Starten*, *Stoppen*, *Neustarten* und *Löschen …* (`docker start`, `stop`, `restart`, `rm`).
  Stoppen, Neustarten und Löschen fragen vorher nach. Löschen entfernt nur den Container, nicht seine Volumes; ein noch laufender Container wird dabei beendet (`docker rm -f`).
  Danach lädt Sesam die Liste neu. Eine Shell lässt sich nur in laufende Container öffnen.
- **Verbinden:** Doppelklick oder Enter öffnet im Terminal eine Shell im Container (`docker exec -it`). Ohne feste Shell nimmt Sesam `bash`, sonst `sh`.
  *Verbinden als …* fragt Shell und Benutzer (z. B. `root`) für diesen einen Aufruf ab; *Verbinden mit …* wählt ein anderes Terminal.
- **Anmeldung:** Passwort und Schlüssel der Verbindung werden wie beim Terminal und bei SFTP verwendet. Ist keins gespeichert, fragt Sesam einmal nach
  (das Passwort bleibt nur im Speicher). Verbindungen über einen Jump-Host zeigen die Container noch nicht an.
- Der Docker-Benutzer braucht Zugriff auf den Docker-Socket (Gruppe `docker`); `sudo docker` wird nicht verwendet. Die Einstellungen werden mit synchronisiert,
  die Container selbst nicht.

## Proxmox

Ist ein Host ein Proxmox-VE-Server, zeigt Sesam seine Gäste im Baum. In der Verbindung (*Bearbeiten*, Reiter *Baum-Erweiterungen*) den Haken
**Proxmox-Gäste (VMs und LXC) dieses Hosts im Verbindungsbaum anzeigen** setzen (ersetzt die Docker-Anzeige); optional den Benutzer für VMs eintragen (Standard `root`).
- **Im Baum:** Beim Aufklappen fragt Sesam den Host per SSH als `root` mit `pvesh` nach den VMs und LXC-Containern **dieses Knotens**. In einem Cluster legst du jeden Knoten als eigene Verbindung an; jede zeigt nur ihre eigenen Gäste, nicht die des ganzen Clusters. Laufende Gäste sind farbig, gestoppte grau; Vorlagen werden ausgeblendet.
- **LXC-Container:** Doppelklick öffnet im Terminal `pct enter <ID>` über den Host (bei Gästen anderer Knoten per SSH über diesen Knoten).
- **VMs:** Sesam erfragt die IP-Adresse über den QEMU-Gast-Agent und verbindet vom Proxmox-Host aus per SSH in die VM. Dafür muss der Agent in der VM laufen.
- **Ein-/Ausschalten:** Rechtsklick → *Starten*, *Herunterfahren*, *Stoppen (hart)*, *Neustarten*. Stoppen und Neustarten fragen vorher nach; danach lädt Sesam den Status neu.
- Dateibrowser und Jump-Hosts funktionieren wie bei Docker nur für den Host selbst. Die Einstellungen werden mit synchronisiert, die Gäste selbst nicht.

## Cisco-Geräte

Für Switches mit Cisco IOS, IOS-XE oder NX-OS zeigt Sesam die Ports im Baum. In der Verbindung (*Bearbeiten*, Reiter *Baum-Erweiterungen*) den Haken
**Ports dieses Cisco-Geräts … im Verbindungsbaum anzeigen** setzen (ersetzt die Docker- und Proxmox-Anzeige). Der Benutzer braucht Privilegstufe 15.
- **Im Baum:** Beim Aufklappen öffnet Sesam per SSH eine Shell auf dem Gerät und liest `show interfaces status`. Jeder Port zeigt Zustand (*verbunden*, *notconnect*, *aus*, *err-disabled* …), Beschreibung, VLAN und Geschwindigkeit.
- **Ein-/Ausschalten:** Rechtsklick → *Port ausschalten* (`shutdown`, mit Rückfrage) bzw. *Port einschalten* (`no shutdown`).
- **Beschreibung:** Rechtsklick → *Beschreibung ändern …* setzt `description`; leer entfernt sie.
- **Speichern:** *Konfiguration speichern* (am Gerät oder an einem Port) schreibt die laufende Konfiguration dauerhaft (`write memory`, auf NX-OS `copy running-config startup-config`). Änderungen an Ports wirken sofort, bleiben aber ohne Speichern nur bis zum Neustart des Geräts.
- Doppelklick auf einen Port öffnet das Gerät im Terminal. Wie bei Docker und Proxmox gehen Dateibrowser und Jump-Hosts nur für das Gerät selbst.

## WireGuard-Anzeige

Wer WireGuard nutzt, schaltet die Anzeige unter **Einstellungen › WireGuard** ein. Standardmäßig ist sie aus: Sesam fragt dann nichts ab und zeigt nichts an.

Unten rechts in der Statuszeile zeigt Sesam, ob dieser Rechner mit einem WireGuard-Tunnel verbunden ist, zum Beispiel `WireGuard · VZS · verbunden`. Der Tunnelname ist der Name der WireGuard-Schnittstelle (Linux) bzw. des Tunnels (Windows).

| Anzeige | Bedeutung |
|---|---|
| grün, *verbunden* | Ein Tunnel ist aktiv. |
| gelb, *veraltet* | Ein Tunnel ist aktiv, aber der letzte Handshake ist älter als drei Minuten. Der Tunnel hängt eventuell. |
| grau, *getrennt* | Es ist kein Tunnel aktiv. |
| gestrichelt, *nicht verfügbar* | Der Zustand lässt sich auf diesem System nicht ermitteln. |

Ein Klick auf die Anzeige zeigt Einzelheiten: Endpunkt, Tunnel-Adresse, erlaubte IPs, letzter Handshake und Datenmenge. Die Datenmenge liest Sesam ohne Administratorrechte aus den Zählern der Schnittstelle (Linux) bzw. des Netzwerkadapters (Windows). Endpunkt und Handshake liefert WireGuard (`wg show`) nur mit Administratorrechten und nur, wenn das Programm `wg` installiert ist (Paket `wireguard-tools`). Ohne sie erkennt Sesam nur, *dass* ein Tunnel läuft, und zeigt „veraltet“ nie an.

**sudo-Passwort (Linux, optional):** Beim Einschalten der Anzeige fragt Sesam nach dem sudo-Passwort. Mit gültigem Passwort liest Sesam Endpunkt und Handshake per `sudo wg show` (höchstens alle 30 Sekunden; der private Schlüssel wird nie abgefragt). Das Passwort wird nur über die Standardeingabe an sudo übergeben und im Schlüsselbund des Systems gespeichert; beim Ausschalten wird es gelöscht. Bleibt das Feld leer, wird abgebrochen oder ist das Passwort falsch, zeigt Sesam die einfache Anzeige; ein abgelehntes Passwort probiert Sesam nicht erneut, damit sudo den Zugang nicht sperrt. Bei mehreren Tunneln steht der erste Name mit Zusatz, etwa `VZS +1`.

Der Zustand wird alle 5 Sekunden im Hintergrund geprüft (Windows: alle 15 Sekunden). Linux: Sesam liest `/sys/class/net`. Windows: Sesam fragt per PowerShell die Netzwerkadapter „WireGuard Tunnel“ ab. Sesam baut selbst keine Tunnel auf und trennt keine.
