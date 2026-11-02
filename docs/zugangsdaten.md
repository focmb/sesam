# Zugangsdaten und sudo

← [Zur README](../README.md) · *[English version](en/zugangsdaten.md)*

## Identitäten

Eine Identität bündelt **Benutzer, Schlüsseldatei und Passwort** und kann von beliebig vielen Verbindungen verwendet werden.
- Verwalten unter *Identitäten …* in der Seitenleiste oder direkt beim Anlegen einer Verbindung über *Identität → Neu …*.
- Wählst du in einer Verbindung eine Identität, ersetzt sie Benutzer, Schlüsseldatei und Passwort der Verbindung (die Felder sind dann gesperrt, die eigenen Werte bleiben erhalten und gelten wieder bei „Keine“).
- Das Passwort liegt im Schlüsselbund, einmal pro Identität. Ändert sich ein Passwort, wird es nur an einer Stelle angepasst. Das gilt auch für sudo.
- Eine Identität zu löschen entfernt sie auch aus allen Verbindungen, die sie nutzen (diese brauchen danach wieder eigene Zugangsdaten).

## SSH-Schlüssel

*Schlüssel …* in der Seitenleiste verwaltet Schlüssel direkt in Sesam – unter Windows und Linux gleich.
- **Erzeugen:** Ed25519 (empfohlen) oder RSA 4096, optional mit Passphrase. Das Format ist das normale OpenSSH-Format, `ssh-keygen` und Terminals verstehen es.
- **Importieren:** eine vorhandene private Schlüsseldatei einlesen (OpenSSH oder klassisches PEM). Liegt daneben eine `.pub`-Datei, wird sie für geschützte Schlüssel genutzt, sonst fragt Sesam einmal nach der Passphrase.
- **Ansehen:** Typ, Fingerabdruck (SHA256 wie bei `ssh-keygen -l`), Kommentar, Erstelldatum, öffentlicher Schlüssel und wer den Schlüssel verwendet. *Öffentlichen Schlüssel kopieren* legt die Zeile für `authorized_keys` in die Zwischenablage.
- **Auf Server installieren:** wie `ssh-copy-id`. Du wählst eine Verbindung, Sesam trägt den Schlüssel in `~/.ssh/authorized_keys` ein (Rechte werden gesetzt, doppelte Einträge vermieden). Die Anmeldung muss dafür einmalig klappen; bei Bedarf wird nach dem Passwort gefragt.
- **Zuweisen:** in Verbindung und Identität unter *Gespeicherter Schlüssel*. Er ersetzt dann die Schlüsseldatei und gilt für Terminal, SFTP und Docker. Eine Identität hat Vorrang vor der Verbindung.
- **Löschen:** entfernt den Schlüssel (privat und öffentlich) und hebt Zuweisungen auf. Auf Servern eingetragene öffentliche Schlüssel bleiben dort bestehen.
- **Speicherort:** der private Teil liegt als Datei (Rechte 0600) im Konfigurationsordner unter `keys/`, nicht im Schlüsselbund, weil `ssh -i` eine Datei braucht und der Windows-Speicher für große Schlüssel zu klein ist.
- **Synchronisation:** Name, Typ, Fingerabdruck und öffentlicher Schlüssel werden immer abgeglichen. Der **private Teil** wandert nur verschlüsselt und nur, wenn die Passwort-Synchronisation (mit Sync-Passwort) aktiv ist. Ohne sie zeigt Sesam auf anderen Geräten „privater Schlüssel auf diesem Gerät: nein“.

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
- Bei gespeichertem Passwort fragt Sesam bei unbekannten Hosts (Ziel und Jump-Hosts) im Terminal mit Fingerabdruck nach und trägt sie erst nach „j“ in `known_hosts` ein (nötig `ssh-keygen` und `ssh-keyscan`); geänderte Schlüssel werden weiterhin abgelehnt. Die automatische Passwort-Eingabe antwortet nur auf Abfragen des Zielhosts, nicht auf die eines Jump-Hosts. Setzt du die Option selbst unter „Zusätzliche Optionen“, hat sie Vorrang.
