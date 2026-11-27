"""Farbschemas: ein Schema legt die Farben der Oberfläche und des integrierten Terminals gemeinsam fest.

Ein Schema besteht aus wenigen Grundfarben für die Oberfläche (``ui``) und den Farben des Terminals (``terminal``).
Alle weiteren Werte der Oberfläche (Rahmen, Hover, Auswahl, gedämpfter Text …) leitet :func:`tokens` daraus ab und
stellt dabei ausreichenden Kontrast sicher. Die mitgelieferten Sesam-Schemas tragen ihre Werte vollständig selbst
(``tokens``), damit sich ihr Aussehen nicht ändert.

Dieses Modul kennt kein Qt: Es rechnet nur mit Farbwerten der Form ``#rrggbb``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
PREFIX = "scheme:"  # Config.theme = "scheme:<id>" wählt ein Schema; „system“, „light“ und „dark“ bleiben gültig
UI_KEYS = ("bg", "surface", "text", "accent", "ok", "warn", "danger")
TERMINAL_KEYS = ("bg", "fg", "cursor", "selection")
ANSI_COUNT = 16


def is_hex(value) -> bool:
    return isinstance(value, str) and HEX_RE.fullmatch(value) is not None


def _rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """Mischt ``a`` (Anteil 1 − t) mit ``b`` (Anteil t)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex([x * (1 - t) + y * t for x, y in zip(ra, rb)])


def luminance(value: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in _rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """Kontrastverhältnis nach WCAG (1 bis 21)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def ensure_contrast(color: str, backgrounds, minimum: float, toward: str) -> str:
    """Schiebt ``color`` in Schritten Richtung ``toward``, bis der Kontrast zu allen Hintergründen reicht."""
    for step in range(0, 26):
        candidate = mix(color, toward, step * 0.04)
        if all(contrast(candidate, bg) >= minimum for bg in backgrounds):
            return candidate
    return toward


def rgba(value: str, alpha: float) -> str:
    r, g, b = _rgb(value)
    return f"rgba({r}, {g}, {b}, {alpha})"


@dataclass(frozen=True)
class Scheme:
    id: str
    name: str  # deutscher Anzeigename (wird mit tr() übersetzt)
    base: str  # "light" oder "dark": Grundlage für Palette, Systemfarbschema und Icons
    ui: dict
    terminal: dict
    tokens: dict = field(default_factory=dict)  # fertige Werte, die die Ableitung überschreiben
    builtin: bool = True

    @property
    def is_dark(self) -> bool:
        return self.base == "dark"


def derive(ui: dict, base: str, magenta: str) -> dict[str, str]:
    """Leitet alle Oberflächenwerte aus den Grundfarben ab."""
    dark = base == "dark"
    bg, surface, text = ui["bg"], ui["surface"], ui["text"]
    accent = ensure_contrast(ui["accent"], [surface], 3.0, text)
    surface_alt = mix(surface, text, 0.06)
    ok = ensure_contrast(ui["ok"], [surface], 4.5, text)
    warn = ensure_contrast(ui["warn"], [surface], 4.5, text)
    danger = ensure_contrast(ui["danger"], [surface], 4.5, text)
    return {
        "bg": bg,
        "surface": surface,
        "surface_alt": surface_alt,
        "border": mix(surface, text, 0.14),
        "text": text,
        "muted": ensure_contrast(mix(bg, text, 0.6), [bg, surface], 4.5, text),
        "accent": accent,
        "accent_hover": mix(accent, "#ffffff", 0.2) if dark else mix(accent, "#000000", 0.14),
        "accent_text": max(("#ffffff", "#111318"), key=lambda c: contrast(c, accent)),
        "accent_soft": rgba(accent, 0.22 if dark else 0.14),
        "badge_rdp": ensure_contrast(magenta, [surface, surface_alt], 4.5, text),
        "sel": mix(surface, accent, 0.22 if dark else 0.14),
        "row_hover": mix(surface, text, 0.09 if dark else 0.06),
        "hover": rgba(text, 0.07),
        "pressed": rgba(text, 0.12 if dark else 0.13),
        "danger": danger,
        "ok": ok,
        "ok_bg": rgba(ok, 0.12),
        "ok_border": rgba(ok, 0.45),
        "warn": warn,
        "warn_bg": rgba(warn, 0.12),
        "warn_border": rgba(warn, 0.45),
    }


def tokens(scheme: Scheme) -> dict[str, str]:
    """Alle Oberflächenwerte des Schemas (Platzhalter des Stylesheets)."""
    return {**derive(scheme.ui, scheme.base, scheme.terminal["ansi"][5]), **scheme.tokens}


# --- Sesam: das bisherige Aussehen, Werte unverändert ---------------------------------------------
SESAM_LIGHT = Scheme(
    "sesam-light", "Sesam hell", "light",
    ui={"bg": "#f4f5f8", "surface": "#ffffff", "text": "#1d2230", "accent": "#3a6cf0", "ok": "#166534", "warn": "#92400e", "danger": "#d92d20"},
    terminal={
        "bg": "#fbfbfc", "fg": "#2b2f36", "cursor": "#b8780a", "selection": "#bcd3f5",
        "ansi": ["#2b2f36", "#c4332f", "#2e7d32", "#9a6700", "#1f5fbf", "#8e3bb0", "#0e7c86", "#7b8190",
                 "#5c6370", "#e5484d", "#3d9a42", "#b8860b", "#3b82e0", "#a855c9", "#1596a2", "#4b5060"],
    },
    tokens={
        "bg": "#f4f5f8", "surface": "#ffffff", "surface_alt": "#f0f2f6", "border": "#dde1e8", "text": "#1d2230",
        "muted": "#667085", "accent": "#3a6cf0", "accent_hover": "#2f5bd6", "accent_text": "#ffffff",
        "accent_soft": "rgba(58, 108, 240, 0.14)", "badge_rdp": "#7a3fd1", "sel": "#e3eafd", "row_hover": "#f0f0f1",
        "hover": "rgba(29, 34, 48, 0.07)", "pressed": "rgba(29, 34, 48, 0.13)", "danger": "#d92d20",
        "ok": "#166534", "ok_bg": "#e3f5e9", "ok_border": "#a7dcb7", "warn": "#92400e", "warn_bg": "#fdf2d6", "warn_border": "#e8c777",
    },
)
SESAM_DARK = Scheme(
    "sesam-dark", "Sesam dunkel", "dark",
    ui={"bg": "#1a1c22", "surface": "#22252d", "text": "#e7e9ef", "accent": "#5b8cff", "ok": "#4ade80", "warn": "#fbbf24", "danger": "#f97066"},
    terminal={
        "bg": "#1b1d23", "fg": "#d5d8df", "cursor": "#e8a317", "selection": "#3b4a6b",
        "ansi": ["#2b2e37", "#e06c75", "#8fc07a", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                 "#5c6370", "#ff7b86", "#a6d98f", "#f0d28a", "#7cc0ff", "#d78cf0", "#6fd0dc", "#ffffff"],
    },
    tokens={
        "bg": "#1a1c22", "surface": "#22252d", "surface_alt": "#292d36", "border": "#343944", "text": "#e7e9ef",
        "muted": "#9aa1b1", "accent": "#5b8cff", "accent_hover": "#76a0ff", "accent_text": "#0f1420",
        "accent_soft": "rgba(91, 140, 255, 0.22)", "badge_rdp": "#b995f5", "sel": "#2f3c5b", "row_hover": "#32343c",
        "hover": "rgba(255, 255, 255, 0.07)", "pressed": "rgba(255, 255, 255, 0.12)", "danger": "#f97066",
        "ok": "#4ade80", "ok_bg": "rgba(74, 222, 128, 0.12)", "ok_border": "rgba(74, 222, 128, 0.45)",
        "warn": "#fbbf24", "warn_bg": "rgba(251, 191, 36, 0.12)", "warn_border": "rgba(251, 191, 36, 0.45)",
    },
)

# --- Bekannte Farbschemas (Farbwerte der jeweiligen Paletten) -------------------------------------
MONOKAI = Scheme(
    "monokai", "Monokai", "dark",
    ui={"bg": "#1e1f1c", "surface": "#272822", "text": "#f8f8f2", "accent": "#66d9ef", "ok": "#a6e22e", "warn": "#e6db74", "danger": "#f92672"},
    terminal={
        "bg": "#272822", "fg": "#f8f8f2", "cursor": "#f8f8f0", "selection": "#49483e",
        "ansi": ["#272822", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
                 "#75715e", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5"],
    },
)
DRACULA = Scheme(
    "dracula", "Dracula", "dark",
    ui={"bg": "#21222c", "surface": "#282a36", "text": "#f8f8f2", "accent": "#bd93f9", "ok": "#50fa7b", "warn": "#f1fa8c", "danger": "#ff5555"},
    terminal={
        "bg": "#282a36", "fg": "#f8f8f2", "cursor": "#f8f8f2", "selection": "#44475a",
        "ansi": ["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
                 "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"],
    },
)
NORD = Scheme(
    "nord", "Nord", "dark",
    ui={"bg": "#2e3440", "surface": "#3b4252", "text": "#eceff4", "accent": "#88c0d0", "ok": "#a3be8c", "warn": "#ebcb8b", "danger": "#bf616a"},
    terminal={
        "bg": "#2e3440", "fg": "#d8dee9", "cursor": "#d8dee9", "selection": "#434c5e",
        "ansi": ["#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                 "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"],
    },
)
SOLARIZED_DARK = Scheme(
    "solarized-dark", "Solarized dunkel", "dark",
    ui={"bg": "#002b36", "surface": "#073642", "text": "#93a1a1", "accent": "#268bd2", "ok": "#859900", "warn": "#b58900", "danger": "#dc322f"},
    terminal={
        "bg": "#002b36", "fg": "#839496", "cursor": "#93a1a1", "selection": "#073642",
        "ansi": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                 "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
)
SOLARIZED_LIGHT = Scheme(
    "solarized-light", "Solarized hell", "light",
    ui={"bg": "#eee8d5", "surface": "#fdf6e3", "text": "#073642", "accent": "#268bd2", "ok": "#859900", "warn": "#b58900", "danger": "#dc322f"},
    terminal={
        "bg": "#fdf6e3", "fg": "#657b83", "cursor": "#586e75", "selection": "#eee8d5",
        "ansi": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                 "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
)
GRUVBOX_DARK = Scheme(
    "gruvbox-dark", "Gruvbox dunkel", "dark",
    ui={"bg": "#1d2021", "surface": "#282828", "text": "#ebdbb2", "accent": "#fabd2f", "ok": "#b8bb26", "warn": "#fe8019", "danger": "#fb4934"},
    terminal={
        "bg": "#282828", "fg": "#ebdbb2", "cursor": "#ebdbb2", "selection": "#504945",
        "ansi": ["#282828", "#cc241d", "#98971a", "#d79921", "#458588", "#b16286", "#689d6a", "#a89984",
                 "#928374", "#fb4934", "#b8bb26", "#fabd2f", "#83a598", "#d3869b", "#8ec07c", "#ebdbb2"],
    },
)
ONE_DARK = Scheme(
    "one-dark", "One Dark", "dark",
    ui={"bg": "#21252b", "surface": "#282c34", "text": "#c8ccd4", "accent": "#61afef", "ok": "#98c379", "warn": "#e5c07b", "danger": "#e06c75"},
    terminal={
        "bg": "#282c34", "fg": "#abb2bf", "cursor": "#528bff", "selection": "#3e4451",
        "ansi": ["#3f4451", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#abb2bf",
                 "#4f5666", "#ff616e", "#a5e075", "#f0a45d", "#4dc4ff", "#de73ff", "#4cd1e0", "#ffffff"],
    },
)
TOKYO_NIGHT = Scheme(
    "tokyo-night", "Tokyo Night", "dark",
    ui={"bg": "#16161e", "surface": "#1a1b26", "text": "#c0caf5", "accent": "#7aa2f7", "ok": "#9ece6a", "warn": "#e0af68", "danger": "#f7768e"},
    terminal={
        "bg": "#1a1b26", "fg": "#c0caf5", "cursor": "#c0caf5", "selection": "#33467c",
        "ansi": ["#15161e", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#a9b1d6",
                 "#414868", "#f7768e", "#9ece6a", "#e0af68", "#7aa2f7", "#bb9af7", "#7dcfff", "#c0caf5"],
    },
)

# Reihenfolge der Anzeige in den Einstellungen (die beiden Sesam-Schemas stehen für „Hell“ und „Dunkel“)
BUILTIN: tuple[Scheme, ...] = (SESAM_LIGHT, SESAM_DARK, MONOKAI, DRACULA, NORD, SOLARIZED_LIGHT, SOLARIZED_DARK,
                               GRUVBOX_DARK, ONE_DARK, TOKYO_NIGHT)
_BY_ID = {s.id: s for s in BUILTIN}
_MODE_IDS = {"light": SESAM_LIGHT.id, "dark": SESAM_DARK.id}  # die klassischen Werte von Config.theme
_custom: dict[str, Scheme] = {}  # eigene Schemas (aus der Konfiguration), siehe set_custom()
NAME_MAX = 60


def set_custom(items) -> None:
    """Setzt die eigenen Schemas. Eine ID, die einem mitgelieferten Schema gehört, wird nicht übernommen."""
    global _custom
    _custom = {s.id: s for s in items if s.id not in _BY_ID}


def custom() -> list[Scheme]:
    return sorted(_custom.values(), key=lambda s: s.name.casefold())


def all_schemes() -> list[Scheme]:
    """Mitgelieferte Schemas in fester Reihenfolge, danach die eigenen nach Namen."""
    return [*BUILTIN, *custom()]


def by_id(scheme_id: str) -> Scheme | None:
    return _BY_ID.get(scheme_id) or _custom.get(scheme_id)


def for_mode(mode: str) -> Scheme | None:
    """Das Schema zu ``light``, ``dark`` oder ``scheme:<id>``; ``None`` für „system“ und Unbekanntes."""
    if mode in _MODE_IDS:
        return _BY_ID[_MODE_IDS[mode]]
    if isinstance(mode, str) and mode.startswith(PREFIX):
        return by_id(mode[len(PREFIX):])
    return None


def mode_for(scheme: Scheme) -> str:
    """Wert für ``Config.theme``: die Sesam-Schemas behalten ihre klassischen Namen."""
    for mode, scheme_id in _MODE_IDS.items():
        if scheme.id == scheme_id:
            return mode
    return PREFIX + scheme.id


def normalize_mode(value) -> str:
    """Wert aus der Konfiguration: Unbrauchbares ergibt „system“. Unbekannte Schema-IDs bleiben erhalten (sie
    könnten von einer anderen Version stammen) und fallen beim Anwenden auf „system“ zurück."""
    if value in ("system", "light", "dark"):
        return value
    if isinstance(value, str) and value.startswith(PREFIX) and len(value) > len(PREFIX):
        return value
    return "system"
