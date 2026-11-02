# Fernzugriff (VNC und RDP)

← [Zur README](../README.md) · *[English version](en/fernzugriff.md)*

Sesam öffnet für eine Verbindung auch den Bildschirm des Geräts: Es startet den **VNC- oder RDP-Viewer deiner Wahl** (zum Beispiel Remmina, TigerVNC, FreeRDP
oder `mstsc`) und führt ihn auf Wunsch durch einen SSH-Tunnel. Ein eigener Viewer ist in Sesam nicht eingebaut.

## Fernzugriff für eine Verbindung einschalten

*Bearbeiten*, Reiter **Fernzugriff**:

| Feld | Bedeutung |
|---|---|
| **Fernzugriff** | *Aus*, *VNC* oder *RDP* |
| **Art** | *Über SSH-Tunnel* oder *Direkt* (siehe unten) |
| **Ziel vom Server aus** | Nur beim Tunnel: Rechner, den der Server erreicht. Leer = der Server selbst (`localhost`). So erreichst du auch Geräte im Netz hinter dem Server. |
| **Port** | Standard 5900 bei VNC (Anzeige `:0`, 5901 für `:1` usw.), 3389 bei RDP |
| **Viewer** | *Standard-Viewer* oder ein bestimmter Viewer des Protokolls |
| **Benutzer, Domäne, Zusatzargumente** | Nur RDP; die Zusatzargumente (zum Beispiel `/f` für Vollbild) hängt Sesam an den Viewer-Befehl an |
| **Passwort** | Optional; liegt im Schlüsselbund (siehe unten) |

Je Verbindung gibt es genau ein Ziel: entweder VNC oder RDP. Wechselst du das Protokoll, wird das gespeicherte Passwort des alten Protokolls gelöscht.

### Über SSH-Tunnel

Für Server, deren VNC- oder RDP-Port nur lokal lauscht oder nicht freigegeben ist. Sesam startet einen kurzlebigen
`ssh -N -L 127.0.0.1:<freier Port>:<Ziel>:<Port>` mit denselben Zugangsdaten wie die Verbindung (Identität, Schlüssel, Passwort, Jump-Host; unbekannte
Host-Schlüssel werden wie bei den [Weiterleitungen](dateien-tunnel.md) erst nach Rückfrage vertraut). Sobald der Tunnel steht, öffnet sich der Viewer auf
`127.0.0.1:<freier Port>`. Der Tunnel erscheint nicht in der Liste der Weiterleitungen.

- **Ende:** Schließt du den Viewer, endet auch der Tunnel. Übergibt der Viewer die Sitzung an eine schon laufende Instanz und beendet sich selbst (Remmina tut das),
  bleibt der Tunnel bestehen, bis du im Kontextmenü **VNC beenden** bzw. **RDP beenden** wählst oder Sesam schließt.
- **Fehler:** Scheitert der Tunnel (zum Beispiel Zugriff verweigert), zeigt Sesam die Meldung und beendet die Sitzung.
- **RDP-Zertifikat:** Weil der Viewer auf `127.0.0.1` verbindet, passt der Name im Zertifikat des Zielrechners nicht. Je nach Viewer erscheint eine Rückfrage; die
  FreeRDP-Vorlagen merken sich das Zertifikat beim ersten Mal (`/cert:tofu`).

### Direkt

Für Geräte, die nur VNC oder RDP sprechen (Kameras, Industrie-Steuerungen, Thin Clients, Windows-Rechner ohne SSH): Der Viewer verbindet sich unmittelbar mit **Host**
und **Port** der Verbindung. Es ist kein SSH nötig.

- **Doppelklick und Enter** öffnen bei solchen Verbindungen den Fernzugriff statt eines Terminals. SSH bleibt im Kontextmenü erreichbar: **Per SSH verbinden**.
- VNC ist von Haus aus **nicht verschlüsselt**. Verwende diesen Modus nur in einem vertrauenswürdigen Netz oder über ein VPN.
- Schließt du Sesam, bleibt der Viewer offen, wie ein bereits geöffnetes Terminal.

## Öffnen und Beenden

Kontextmenü der Verbindung: **VNC öffnen** bzw. **RDP öffnen** und **… beenden**. Eine Verbindung mit laufender Sitzung trägt wie bei Terminals den grünen Punkt.
Ein zweites Öffnen startet keinen zweiten Viewer.

## Viewer verwalten

Seitenleiste **Viewer …**. Oben wählst du das Protokoll (VNC oder RDP); jedes hat seine eigene Liste und seinen eigenen Standard-Viewer. Wie bei den Terminals ist ein
Viewer eine **Befehlsvorlage**; Sesam startet das Programm direkt (nicht über eine Shell). Unter **Neu ▾** gibt es Vorlagen für die gängigen Viewer der Plattform,
neu eingerichtete Installationen und Konfigurationen aus älteren Versionen enthalten die gefundenen Viewer bereits.

| Platzhalter | Inhalt |
|---|---|
| `{host}` `{port}` | Ziel des Viewers (beim Tunnel `127.0.0.1` und der lokale Tunnelport) |
| `{display}` | VNC: Anzeigenummer (`port - 5900`) |
| `{url}` | `vnc://host:port` bzw. `rdp://host:port` |
| `{title}` | Name der Verbindung |
| `{user}` `{domain}` | RDP: Benutzer und Domäne; sind sie leer, entfällt das ganze Argument (`/u:{user}`) |
| `{profile}` | Remmina-Profildatei, die Sesam erzeugt |
| `{password_file}` | VNC: Passwortdatei im Format von `vncpasswd` (zum Beispiel für TigerVNC `-passwd`) |
| `{rdp_file}` | RDP: `.rdp`-Datei, die Sesam erzeugt (für `mstsc`) |
| `/from-stdin` | RDP (FreeRDP): Sesam übergibt das Passwort über die Standardeingabe |

Beispiele:

```text
remmina -c {profile}
vncviewer -passwd {password_file} {host}::{port}
xfreerdp /v:{host}:{port} /u:{user} /d:{domain} /cert:tofu +clipboard /dynamic-resolution /from-stdin
mstsc {rdp_file}
```

Hat eine Verbindung kein gespeichertes Passwort, entfällt `{password_file}` samt der Option davor (`-passwd`, `-PasswordFile=…`). Die Vorlagen für FreeRDP
gibt es für `xfreerdp` (FreeRDP 2) und `xfreerdp3` (FreeRDP 3); die Option `/cert:tofu` kennt FreeRDP 3, bei FreeRDP 2 heißt sie `/cert-tofu`.

## Passwort

Das Passwort wird im Schlüsselbund des Betriebssystems gespeichert, getrennt vom SSH-Passwort der Verbindung, und **nicht synchronisiert**: Auf jedem Gerät
gibst du es einmal ein. VNC wertet nur die **ersten 8 Zeichen** aus.

Dem Viewer wird es nie als Argument übergeben (sichtbar in der Prozessliste). Stattdessen:

| Viewer | Übergabe |
|---|---|
| Remmina (VNC und RDP) | Sesam schreibt für die Sitzung ein Profil (`{profile}`) in einen nur für dich lesbaren temporären Ordner und trägt das Passwort so verschlüsselt ein, wie Remmina es selbst speichert; der Schlüssel dafür steht in Remminas `remmina.pref`. Das Profil wird beim Beenden der Sitzung gelöscht. |
| Viewer mit `{password_file}` (VNC) | Datei im `vncpasswd`-Format im temporären Ordner; sie wird 30 Sekunden nach dem Start des Viewers gelöscht. |
| FreeRDP (`/from-stdin`) | Über die Standardeingabe des Viewers. |
| `mstsc` und andere | Das Passwort wird **nicht** übergeben, der Viewer fragt danach (Sesam zeigt dazu einen Hinweis). Die `.rdp`-Datei enthält Adresse und Benutzer, nie das Passwort. |

> **Hinweis:** Die Übergabe an Remmina und FreeRDP richtet sich nach deren Profilformat bzw. Parametern und ist nicht mit jeder Version geprüft. Klappt sie bei dir
> nicht, öffnet sich der Viewer trotzdem und fragt nach dem Passwort.

## Synchronisation

Die Einstellungen der Verbindung (Protokoll, Art, Ziel, Port, Viewer, Benutzer, Domäne, Zusatzargumente) werden mit synchronisiert. **Nicht** synchronisiert werden das
Passwort und die Liste der Viewer, denn Viewer und ihre Pfade sind je Rechner verschieden. Ist der gewählte Viewer auf einem Gerät nicht vorhanden, nimmt Sesam dort den
Standard-Viewer des Protokolls.

## Von Version 3.0

Konfigurationen mit VNC aus Version 3.0 werden beim Start automatisch übernommen (Protokoll VNC, gleiche Art, gleicher Port); das gespeicherte VNC-Passwort bleibt erhalten.
