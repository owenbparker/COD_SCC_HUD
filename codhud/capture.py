"""Getting the game's picture (Windows), scaled to 1920x1080 RGB.

"window" reads the Call of Duty window's own content (PrintWindow), so it
keeps working while another window covers the game. "screen" grabs a whole
screen, for when the window can't be read.
"""

import ctypes
import sys
import unicodedata

import numpy as np

REF_W, REF_H = 1920, 1080
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2


def make_dpi_aware():
    """So window sizes and screen grabs are in real pixels on scaled displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def normalise_title(text):
    """Call of Duty pads its window title with invisible characters
    ("C​a​l​l..."): compare titles without them."""
    text = "".join(c for c in (text or "") if unicodedata.category(c) not in ("Cf", "Cc") and c != "®")
    return " ".join(text.split()).casefold()


def to_reference(image):
    """A PIL image or numpy array as a 1920x1080 RGB numpy array."""
    from PIL import Image
    if isinstance(image, np.ndarray):
        if image.shape[1] == REF_W and image.shape[0] == REF_H:
            return np.ascontiguousarray(image[:, :, :3])
        image = Image.fromarray(np.ascontiguousarray(image[:, :, :3]))
    image = image.convert("RGB")
    if image.size != (REF_W, REF_H):
        # Nearest neighbour: colours stay exact for the pixel checks.
        image = image.resize((REF_W, REF_H), Image.NEAREST)
    return np.asarray(image)


def find_window(title):
    """The handle of the visible window whose title contains title (ignoring
    invisible characters and case), the biggest if several; or None."""
    if sys.platform != "win32" or not title:
        return None
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    want = normalise_title(title)
    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        name = normalise_title(buf.value)
        # Not this app's own windows ("CoD HUD ...").
        if want in name and "cod hud" not in name:
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            found.append((rect.right * rect.bottom, hwnd, buf.value))
        return True

    user32.EnumWindows(proc(each), 0)
    if not found:
        return None
    found.sort(key=lambda f: f[0], reverse=True)
    return found[0][1], found[0][2]


def client_size(hwnd):
    from ctypes import wintypes
    rect = wintypes.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    return rect.right, rect.bottom


def print_window(hwnd):
    """The window's client area, as an RGB numpy array, or None."""
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    width, height = client_size(hwnd)
    if width <= 0 or height <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(mem, bmp)
    try:
        if not user32.PrintWindow(hwnd, mem, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                        ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

        hdr = BITMAPINFOHEADER()
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth, hdr.biHeight = width, -height   # top-down
        hdr.biPlanes, hdr.biBitCount = 1, 32
        buf = (ctypes.c_ubyte * (width * height * 4))()
        if not gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(hdr), 0):
            return None
        bgra = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 4)
        return bgra[:, :, 2::-1]
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def grab_window_on_screen(hwnd):
    """The window's client area as it shows on screen (other windows on top
    of it included)."""
    from ctypes import wintypes
    from PIL import ImageGrab
    width, height = client_size(hwnd)
    point = wintypes.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
    return ImageGrab.grab((point.x, point.y, point.x + width, point.y + height), all_screens=True)


def monitors():
    """Each screen's (x, y, width, height), the main screen first."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    found = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def each(hmon, hdc, rect, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        found.append((not info.dwFlags & 1, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return 1

    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc(each), 0)
    found.sort(key=lambda m: (m[0], m[1], m[2]))
    return [m[1:] for m in found]


class Capture:
    """grab() -> (1920x1080 RGB array or None, a status line)."""

    def __init__(self):
        self._hwnd = None
        self._title = None
        self._look_after = 0.0

    def grab(self, cfg, now):
        cap = cfg["capture"]
        if sys.platform != "win32":
            return None, "Reading the game needs Windows"
        if cap.get("mode") == "screen":
            from PIL import ImageGrab
            screens = monitors()
            i = int(cap.get("screen") or 0)
            if not screens:
                return None, "No screens found"
            x, y, w, h = screens[min(max(i, 0), len(screens) - 1)]
            return to_reference(ImageGrab.grab((x, y, x + w, y + h), all_screens=True)), f"Screen {i + 1} ({w}x{h})"
        user32 = ctypes.windll.user32
        if self._hwnd and not user32.IsWindow(self._hwnd):
            self._hwnd = None
        if not self._hwnd:
            if now < self._look_after:
                return None, f"Looking for the “{cap.get('window_title')}” window"
            self._look_after = now + 1.0
            hit = find_window(cap.get("window_title"))
            if not hit:
                return None, f"Looking for the “{cap.get('window_title')}” window"
            self._hwnd, self._title = hit
        if user32.IsIconic(self._hwnd):
            return None, "The game window is minimised"
        frame = print_window(self._hwnd)
        how = "window"
        if frame is None or not frame.any():
            # PrintWindow fails for a moment around alt-tab; the on-screen
            # picture is right as long as the game isn't covered.
            frame = grab_window_on_screen(self._hwnd)
            how = "on screen"
        w, h = client_size(self._hwnd)
        return to_reference(frame), f"{normalise_title(self._title).title()} — {w}x{h} ({how})"
