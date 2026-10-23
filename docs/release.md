# Release und Installation unter Manjaro

Ablauf von der fertigen Codeänderung bis zum installierten Paket.

## 1. Version setzen und committen
- Version in `pyproject.toml` und `src/sesam/__init__.py` erhöhen, z. B. auf `1.5.3`.
- Committen (Code und Versions-Commit).

## 2. Pushen und Build abwarten
- `git push origin main`
- Auf GitHub warten, bis beide Builds (Linux und Windows) grün sind.

## 3. Tag setzen (löst das Release aus)
- `git tag v1.5.3`
- `git push origin v1.5.3`
- Warten, bis der Lauf für den Tag grün ist und das Release existiert.

## 4. AUR-Dateien aktualisieren
Erst jetzt, weil das Archiv vom Tag existieren muss.
- In `packaging/aur/PKGBUILD` `pkgver=1.5.3` setzen, `pkgrel=1` lassen.
- Prüfsumme neu berechnen: `curl -L https://github.com/focmb/sesam/archive/v1.5.3.tar.gz | sha256sum`
- Den Wert in `sha256sums=('…')` eintragen.
- `.SRCINFO` neu erzeugen, im Ordner `packaging/aur`: `makepkg --printsrcinfo > .SRCINFO`
- Committen und pushen: `AUR/PKGBUILD: Version 1.5.3`

## 5. Auf Manjaro installieren
**Variante A: lokal aus dem Repo (ohne AUR-Upload)**
- Repo klonen oder aktualisieren: `git pull`
- In den Ordner wechseln: `cd packaging/aur`
- Bauen und installieren: `makepkg -si`
- Einmalig nötig: `sudo pacman -S --needed base-devel git`

**Variante B: über das AUR (wenn das Paket dort veröffentlicht ist)**
- Erstinstallation: `yay -S sesam`
- Update später: `yay -Syu` oder `yay -S sesam`
- `PKGBUILD` und `.SRCINFO` müssen vorher ins AUR-Git-Repo (`ssh://aur@aur.archlinux.org/sesam.git`) gepusht sein.

## 6. Prüfen
- `pacman -Qi sesam` zeigt die installierte Version.
- Programm starten mit `sesam`.

## Typische Stolpersteine
- **Prüfsumme falsch:** Sie wurde vor dem Tag berechnet oder der Tag wurde nachträglich verschoben. Neue Summe eintragen und `makepkg` wiederholen.
- **Alte Version bleibt:** Bei Variante A `git pull`, dann `makepkg -si` noch einmal.
- **Abhängigkeiten:** `pyside6`, `python-paramiko` und die übrigen Pakete holt `makepkg -si` selbst aus den Manjaro-Repos.
