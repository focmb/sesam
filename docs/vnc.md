# VNC

← [Zur README](../README.md) · *[English version](en/vnc.md)*

Sesam öffnet für eine Verbindung auch den Bildschirm des Geräts: Es startet den **VNC-Viewer deiner Wahl** (zum Beispiel Remmina, TigerVNC oder KRDC) und führt ihn
auf Wunsch durch einen SSH-Tunnel. Ein eigener Viewer ist in Sesam nicht eingebaut.

## VNC für eine Verbindung einschalten

*Bearbeiten*, Reiter **VNC**:

| Feld | Bedeutung |
|---|---|
| **VNC** | *Aus*, *Über SSH-Tunnel* oder *Direkt* (siehe unten) |
| **Ziel vom Server aus** | Nur beim Tunnel: Rechner, den der Server erreicht. Leer = der Server selbst (`localhost`). So erreichst du auch Geräte im Netz hinter dem Server. |
| **VNC-Port** | Standard 5900 (Anzeige `:0`), 5901 für `:1` usw. |
| **Viewer** | *Standard-Viewer* oder ein bestimmter Viewer aus der Liste |
| **VNC-Passwort** | Optional; liegt im Schlüsselbund (siehe unten) |

Je Verbindung gibt es genau ein VNC-Ziel.

### Über SSH-Tunnel

Für Server, deren VNC-Port nur lokal lauscht oder nicht freigegeben ist. Sesam startet einen kurzlebigen `ssh -N -L 127.0.0.1:<freier Port>:<Ziel>:<VNC-Port>`
mit denselben Zugangsdaten wie die Verbindung (Identität, Schlüssel, Passwort, Jump-Host; unbekannte Host-Schlüssel werden wie bei den
[Weiterleitungen](dateien-tunnel.md) erst nach Rückfrage vertraut). Sobald der Tunnel steht, öffnet sich der Viewer auf `127.0.0.1:<freier Port>`.
Der Tunnel erscheint nicht in der Liste der Weiterleitungen.

- **Ende:** Schließt du den Viewer, endet auch der Tunnel. Übergibt der Viewer die Sitzung an eine schon laufende Instanz und beendet sich selbst (Remmina tut das),
  bleibt der Tunnel bestehen, bis du im Kontextmenü **VNC beenden** wählst oder Sesam schließt.
- **Fehler:** Scheitert der Tunnel (zum Beispiel Zugriff verweigert), zeigt Sesam die Meldung und beendet die Sitzung.

### Direkt

Für Geräte, die nur VNC sprechen (Kameras, Industrie-Steuerungen, Thin Clients): Der Viewer verbindet sich unmittelbar mit **Host** und **VNC-Port** der Verbindung.
Es ist kein SSH nötig.

- **Doppelklick und Enter** öffnen bei solchen Verbindungen VNC statt eines Terminals. SSH bleibt im Kontextmenü erreichbar: **Per SSH verbinden**.
- VNC ist von Haus aus **nicht verschlüsselt**. Verwende diesen Modus nur in einem vertrauenswürdigen Netz oder über ein VPN.
- Schließt du Sesam, bleibt der Viewer offen, wie ein bereits geöffnetes Terminal.

## Öffnen und Beenden

Kontextmenü der Verbindung: **VNC öffnen** und **VNC beenden**. Eine Verbindung mit laufender VNC-Sitzung trägt wie bei Terminals den grünen Punkt.
Ein zweites **VNC öffnen** startet keinen zweiten Viewer.

## Viewer verwalten

Seitenleiste **VNC-Viewer …**. Wie bei den Terminals ist ein Viewer eine **Befehlsvorlage**; Sesam startet das Programm direkt (nicht über eine Shell). Unter
**Neu ▾** gibt es Vorlagen für die gängigen Viewer der Plattform, neu eingerichtete Installationen enthalten die gefundenen Viewer bereits.

| Platzhalter | Inhalt |
|---|---|
| `{host}` `{port}` | Ziel des Viewers (beim Tunnel `127.0.0.1` und der lokale Tunnelport) |
| `{display}` | Anzeigenummer (`port - 5900`) |
| `{url}` | `vnc://host:port` |
| `{title}` | Name der Verbindung |
| `{profile}` | Remmina-Profildatei, die Sesam erzeugt |
| `{password_file}` | Passwortdatei im Format von `vncpasswd` (zum Beispiel für TigerVNC `-passwd`) |

Beispiele:

```text
remmina -c {profile}
vncviewer -passwd {password_file} {host}::{port}
krdc {url}
```

Hat eine Verbindung kein gespeichertes VNC-Passwort, entfällt `{password_file}` samt der Option davor (`-passwd`, `-PasswordFile=…`).

## VNC-Passwort

Das Passwort wird im Schlüsselbund des Betriebssystems gespeichert, getrennt vom SSH-Passwort der Verbindung, und **nicht synchronisiert**: Auf jedem Gerät
gibst du es einmal ein. VNC wertet nur die **ersten 8 Zeichen** aus.

Dem Viewer wird es nie als Argument übergeben (sichtbar in der Prozessliste), sondern über eine Datei in einem nur für dich lesbaren temporären Ordner:

- **Remmina:** Sesam schreibt für die Sitzung ein Profil (`{profile}`) und trägt das Passwort so verschlüsselt ein, wie Remmina es selbst speichert; der Schlüssel dafür
  steht in Remminas `remmina.pref`. Das Profil wird beim Beenden der Sitzung gelöscht.
- **Andere Viewer:** `{password_file}` zeigt auf eine Datei im `vncpasswd`-Format; sie wird 30 Sekunden nach dem Start des Viewers gelöscht.

> **Hinweis:** Die Übergabe an Remmina richtet sich nach dessen Profilformat und ist nicht mit jeder Remmina-Version geprüft. Klappt sie bei dir nicht, öffnet sich
> Remmina trotzdem und fragt nach dem Passwort; Sesam zeigt dazu einen Hinweis.

## Synchronisation

Die VNC-Einstellungen der Verbindung (Modus, Ziel, Port, Viewer) werden mit synchronisiert. **Nicht** synchronisiert werden das VNC-Passwort und die Liste der Viewer,
denn Viewer und ihre Pfade sind je Rechner verschieden. Ist der gewählte Viewer auf einem Gerät nicht vorhanden, nimmt Sesam dort den Standard-Viewer.
