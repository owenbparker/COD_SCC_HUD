"""The app's icon follows the Windows theme: the white skull on a dark
taskbar / title bar, the black one on a light one (Windows only).

The window's icons are swapped while it runs (and again if the theme
changes), and the desktop shortcut points at shortcut.ico next to the app,
which is kept as the right one for the theme.
"""

import os
import shutil
import sys
import threading

from .config import DATA_DIR, FROZEN, WEB_DIR

ICONS = {"dark": os.path.join(WEB_DIR, "assets", "icon-white.ico"),
         "light": os.path.join(WEB_DIR, "assets", "icon-black.ico")}
SHORTCUT_ICON = os.path.join(DATA_DIR, "shortcut.ico")
WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1


def uses_light(which):
    """Is Windows' "apps" or "system" (taskbar) theme light?"""
    if sys.platform != "win32":
        return False
    import winreg
    name = "AppsUseLightTheme" if which == "apps" else "SystemUsesLightTheme"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return bool(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return which == "apps"   # Windows' default: light apps, dark taskbar


def icon_for(which):
    return ICONS["light" if uses_light(which) else "dark"]


def _load(path, size):
    import ctypes
    LR_LOADFROMFILE = 0x10
    load = ctypes.windll.user32.LoadImageW
    load.restype = ctypes.c_void_p
    return ctypes.c_void_p(load(None, path, 1, size, size, LR_LOADFROMFILE))


def set_window_icons(hwnd):
    """Title bar (apps theme) and taskbar (system theme) icons."""
    import ctypes
    send = ctypes.windll.user32.SendMessageW
    hwnd = ctypes.c_void_p(hwnd)
    send(hwnd, WM_SETICON, ICON_SMALL, _load(icon_for("apps"), 16))
    send(hwnd, WM_SETICON, ICON_BIG, _load(icon_for("system"), 32))


def update_shortcut_icon():
    """shortcut.ico (the desktop shortcut's icon) for the taskbar's theme."""
    if sys.platform != "win32" or not FROZEN:
        return
    want = icon_for("system")
    try:
        with open(want, "rb") as a:
            new = a.read()
        if os.path.exists(SHORTCUT_ICON):
            with open(SHORTCUT_ICON, "rb") as b:
                if b.read() == new:
                    return
        shutil.copyfile(want, SHORTCUT_ICON)
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # refresh Explorer's icons
    except OSError:
        pass


def follow_theme(title, stop):
    """Keeps the window titled title (once it's open) on the theme's icons,
    checking every few seconds until stop is set."""
    if sys.platform != "win32":
        return

    def run():
        import ctypes
        last = None
        while not stop.wait(1.5 if last is None else 3):
            find = ctypes.windll.user32.FindWindowW
            find.restype = ctypes.c_void_p
            hwnd = find(None, title)
            if not hwnd:
                continue
            now = (uses_light("apps"), uses_light("system"))
            if now != last:
                last = now
                set_window_icons(hwnd)
                update_shortcut_icon()

    threading.Thread(target=run, daemon=True).start()
