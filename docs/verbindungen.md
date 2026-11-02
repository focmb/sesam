# Verbindungen verwalten

← [Zur README](../README.md) · *[English version](en/verbindungen.md)*

## Darstellung

Der Dialog *Verbindung bearbeiten* ist in vier Reiter gegliedert: *Allgemein* (Name, Host, Gruppe, Farbe, Tags, Terminal, Notizen), *Zugang* (Identität, Benutzer, Port, Schlüssel, Passwort), *Erweitert* (Jump-Host, zusätzliche Optionen, Remote-Befehl, sudo) und *Baum-Erweiterungen* (Docker, Proxmox, Cisco). So passt er auch auf kleine Bildschirme.

Es gibt keine Menüleiste: Alle Aktionen (Verbinden, Dateien, Neue Verbindung, Neue Gruppe, Bearbeiten, Duplizieren, Löschen, Synchronisieren, Synchronisation, Identitäten, Schlüssel, Terminals)
stehen untereinander in einer Seitenleiste am rechten Rand (Strg+Q beendet das Programm, *Aus ~/.ssh/config importieren* steht im Rechtsklick-Menü der Verbindungsliste); die Tastenkürzel (Enter, Strg+N, F2, Entf, Strg+D, F5 …) gelten im ganzen Fenster.
*Duplizieren* legt eine Kopie an und öffnet sie sofort zum Bearbeiten, der Name ist markiert und lässt sich gleich überschreiben; bricht man ab, bleibt die Kopie bestehen.

Ganz unten in der Seitenleiste schalten drei Symbole zwischen **Hell** (Sonne), **Dunkel** (Mond) und **Wie das System** (Monitor) um (wirkt sofort, wird gespeichert).
Darunter öffnet der Knopf **Einstellungen** den Einstellungsdialog (Schriftgröße, Sprache und WireGuard-Anzeige, siehe [WireGuard-Anzeige](geraete.md#wireguard-anzeige)).
Bei *Wie das System* folgt Sesam dem Hell/Dunkel-Wechsel des Betriebssystems, sofern Qt ihn meldet (Windows, macOS, Linux je nach Desktop);
andernfalls stellst du es manuell ein.

**Schriftgröße:** im Dialog *Einstellungen* (80 bis 200 % der Systemschrift), oder per Tastatur **Strg + Plus** (größer), **Strg + Minus** (kleiner) und **Strg + 0** (zurück auf 100 %).
Icons und Seitenleiste wachsen mit. Die Einstellung gilt sofort, wird gespeichert und bleibt lokal (wird nicht per Git synchronisiert).

**Sprache:** Sesam gibt es auf Deutsch und Englisch. Im Dialog *Einstellungen* wählst du *Systemvorgabe* (Deutsch, wenn das System auf Deutsch läuft, sonst Englisch), *Deutsch* oder *English*.
Die Sprache gilt nach dem nächsten Start von Sesam und wird lokal gespeichert (wird nicht per Git synchronisiert). Das gilt auch für den Befehlszeilenmodus (`sesam --connect`).
Die Quelltexte sind deutsch; `src/sesam/i18n.py` liefert über `tr("deutscher Text")` die englische Fassung aus `src/sesam/translations_en.py`. Neue Texte trägst du dort ein –
der Test `tests/test_i18n.py` schlägt fehl, solange ein Text im Katalog fehlt. Eine weitere Sprache braucht eine zweite Katalogdatei und einen Eintrag in `i18n.LANGUAGES`.

Das Erscheinungsbild (Farben, Abstände, Icons) kommt aus `src/sesam/gui/style.py`: ein gemeinsames Stylesheet für Hell und Dunkel, das aus einer
Farbtabelle (`TOKENS`) erzeugt wird. Farben änderst du dort an einer Stelle; Icons sind einfache Linienzeichnungen im gleichen Modul.
Sesam nutzt dafür überall den Qt-Stil „Fusion“, damit es unter Windows und Linux gleich aussieht.

## Gruppen

Verbindungen lassen sich in verschachtelten Gruppen ordnen (Pfad mit „/“, z. B. `Kunde A/Produktion`).
- *Neue Gruppe …* (Strg+Umschalt+N, Seitenleiste oder Kontextmenü) legt auch leere Gruppen an; im Kontextmenü einer Gruppe: Untergruppe, Umbenennen/Verschieben, Löschen.
- Per **Drag & Drop** verschiebst du Verbindungen und ganze Gruppen; Ablegen auf freier Fläche = oberste Ebene.
- **Mehrfachauswahl:** Mit Strg- bzw. Umschalt-Klick (oder Strg+A) wählst du mehrere Verbindungen. Im Kontextmenü (Rechtsklick in die Auswahl) setzt du allen gemeinsam eine *Farbe* oder löschst sie auf einmal; mit *Entf* geht das Löschen ebenfalls. Per Drag & Drop wandern alle ausgewählten Verbindungen zusammen in die Zielgruppe. Gruppen in der Auswahl bleiben bei diesen Aktionen unberührt; Verbinden, Bearbeiten und Duplizieren wirken weiterhin auf den zuletzt angeklickten Eintrag.
- Eine Gruppe zu löschen entfernt keine Verbindungen, sie rücken in die übergeordnete Gruppe.
- Neue Verbindungen landen in der aktuell ausgewählten Gruppe. Das Suchfeld filtert auch nach Gruppen.
- **Spalten:** Ein Rechtsklick auf die Spaltenköpfe blendet *Typ*, *Ziel*, *Terminal*, *Tags* und *Betriebssystem* ein oder aus (der Name bleibt immer sichtbar). Die Auswahl bleibt lokal gespeichert. *Betriebssystem* ist zunächst ausgeblendet.
- **Art der Verbindung (Typ):** Die Spalte *Typ* hinter dem Namen zeigt kleine Abzeichen: **SSH**, **SSH · VNC** oder **SSH · RDP** (SSH mit [Fernzugriff](fernzugriff.md) über den Tunnel) sowie nur **VNC** oder **RDP** bei Geräten, die direkt angesprochen werden. Solche Direktverbindungen tragen einen Bildschirm statt des Server-Symbols, und die Spalte *Ziel* zeigt `vnc://host:port` bzw. `rdp://host:port`. Ein Tooltip auf dem Abzeichen erklärt die Art. Im Suchfeld zeigt `typ:vnc`, `typ:rdp` oder `typ:ssh` nur diese Verbindungen; mit weiteren Wörtern lässt sich das verbinden (`typ:vnc kiosk`).
- **Betriebssystem des Hosts:** Beim ersten Verbinden fragt Sesam im Hintergrund über eine zweite SSH-Verbindung das Betriebssystem ab (`/etc/os-release`, macOS, `uname`, Windows `ver`, bei Cisco-Switches `show version | include ^Cisco IOS`) und speichert es bei der Verbindung, sodass es bei jedem Start in der Spalte steht. Ist die letzte Abfrage älter als 7 Tage, erneuert Sesam den Wert beim nächsten Verbinden (schlägt das fehl, bleibt der alte stehen). Unbekannte Host-Schlüssel übernimmt diese Abfrage nie selbst: Sie wartet, bis du im Terminal bestätigt hast. Verbindungen mit Jump-Host werden nicht abgefragt. Im Kontextmenü der Verbindung ermittelt *Betriebssystem ermitteln* den Wert erneut, auch mit Passwortabfrage.
- Auf- und Zugeklapptes merkt sich Sesam: Beim nächsten Start sind genau die Gruppen zu, die beim Beenden zu waren. Dieser Zustand (wie die Spaltenbreiten und die Fenstergröße, auch maximiert) bleibt lokal und wird nicht per Git synchronisiert.

## Favoriten und Schnellstarter

- **Favoriten:** *Favorit* (Strg+B, Seitenleiste oder Kontextmenü) markiert Verbindungen. Sie stehen zusätzlich in einem Abschnitt *Favoriten* oben im Baum (ein-/ausklappbar) und tragen in ihrer Gruppe ein ★. Das Merkmal gehört zur Verbindung und wird deshalb **per Git mit synchronisiert**. Duplikate sind keine Favoriten. Unter **Einstellungen › Favoriten anzeigen** lassen sich die Favoriten abschalten: Abschnitt, ★ und die Favoriten-Einträge in den Menüs verschwinden dann im Hauptfenster, die Markierungen bleiben erhalten. Die Einstellung gilt nur für diesen Rechner.
- **Schnellstarter (Strg+K):** Ein Suchfenster über alle Verbindungen. Die Suche ist unscharf (`wbpd` findet `web-prod`), mehrere Wörter müssen alle passen, durchsucht werden Name, Host, Benutzer, Gruppe und Tags. Ohne Suchbegriff erscheinen *Favoriten*, *Zuletzt verwendet* (die jüngsten acht, mit „vor 5 Min.“ usw.) und *Alle Verbindungen*.
  - ↵ verbindet, Strg+↵ öffnet die Dateien (SFTP), Strg+B schaltet den Favoriten um, Esc schließt.
  - **Zuletzt verwendet** gibt es nur hier, nicht im Baum. Die Zeiten bleiben lokal (nicht synchronisiert) und zählen beim Verbinden, bei SFTP, Docker/Proxmox/Cisco und beim Starten eines Tunnels.

## Farben und Tags

- **Farbe:** Verbindungen und Gruppen können eine von acht Farben bekommen; sie färbt das Symbol im Baum (die Zeile selbst bleibt unverändert, auch im dunklen Design gut lesbar).
  Bei Verbindungen wählst du sie im Bearbeiten-Dialog (*Farbe*) oder per Rechtsklick → *Farbe*, bei Gruppen per Rechtsklick → *Farbe*. Eine Gruppenfarbe wandert beim Umbenennen und Verschieben mit.
- **Tags:** Eine Verbindung kann mehrere Tags tragen (*Tags* im Bearbeiten-Dialog, mit Komma trennen, z. B. `prod, kunde-a`). Der Dialog schlägt bereits vergebene Tags vor.
  Der Baum zeigt sie in der Spalte *Tags*, und das Suchfeld findet sie wie Namen, Hosts und Gruppen. Groß-/Kleinschreibung spielt keine Rolle.
- Farben und Tags werden mit synchronisiert (Gruppenfarben in `groups.json`). Bei widersprüchlichen Änderungen derselben Gruppenfarbe gewinnt die lokale.
- Die Spaltenbreiten der Hauptansicht werden einmalig auf die Standardwerte zurückgesetzt, weil eine Spalte dazukam.

## Offene Verbindungen

Eine Verbindung, die Sesam in einem Terminal geöffnet hat, zeigt am Server-Symbol einen **grünen Punkt** – auch wenn sie über ein Snippet gestartet wurde.
Der Punkt verschwindet, sobald das Terminal geschlossen wird.

- **Doppelklick oder Enter** auf eine bereits offene Verbindung öffnet kein zweites Terminal, sondern zeigt nur einen Hinweis.
- **Rechtsklick → *Neue Sitzung*** öffnet bewusst ein weiteres Terminal. *Als root verbinden* und *Verbinden mit …* öffnen ebenfalls immer ein neues.
- Sesam erkennt offene Verbindungen am laufenden Terminal-Prozess. Terminals, die sich sofort vom Startprozess lösen (z. B. GNOME Terminal), werden nicht erkannt; dort gibt es keinen Punkt und Doppelklick öffnet wie gewohnt ein neues Terminal.
- Sitzungen in Docker-Containern, Proxmox-Gästen und auf Cisco-Ports zählen nicht für den Host.
