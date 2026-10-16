"""The fonts installed on this PC, for the title's text.

Each font file's family and style are read from the file itself; a family's
regular style is the one served (at /fonts/local/<family>), so the overlay
shows the font even in OBS / vMix on another PC that doesn't have it.
"""

import os
import sys
import threading

_lock = threading.Lock()
_cache = None
_REGULAR = ("regular", "normal", "book", "roman", "medium", "")


def font_dirs():
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        local = os.environ.get("LOCALAPPDATA", "")
        return [os.path.join(windir, "Fonts"), os.path.join(local, "Microsoft", "Windows", "Fonts")]
    home = os.path.expanduser("~")
    return ["/usr/share/fonts", "/usr/local/share/fonts", os.path.join(home, ".fonts"),
            os.path.join(home, ".local", "share", "fonts"), "/Library/Fonts", os.path.join(home, "Library", "Fonts")]


def _scan(dirs):
    from PIL import ImageFont
    found = {}   # family -> (rank, path): the most regular-looking style wins
    for base in dirs:
        for root, _, files in os.walk(base):
            for name in files:
                if not name.lower().endswith((".ttf", ".otf", ".ttc")):
                    continue
                path = os.path.join(root, name)
                try:
                    family, style = ImageFont.truetype(path, 24).getname()
                except Exception:
                    continue   # bitmap-only or broken files
                if not family:
                    continue
                style = (style or "").strip().lower()
                rank = _REGULAR.index(style) if style in _REGULAR else len(_REGULAR) + len(style)
                if family not in found or rank < found[family][0]:
                    found[family] = (rank, path)
    return {family: path for family, (_, path) in found.items()}


def installed(refresh=False, dirs=None):
    """{family: file} of the fonts installed here (scanned once)."""
    global _cache
    with _lock:
        if _cache is None or refresh or dirs is not None:
            fonts = _scan(dirs or font_dirs())
            if dirs is not None:
                return fonts
            _cache = fonts
        return _cache


def families():
    return sorted(installed(), key=str.lower)


def path_of(family):
    return installed().get(family)
