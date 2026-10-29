"""The HUD in its own window over the game (desktop app, Windows): "Show
HUD on screen".

The window is borderless and covers one whole screen. Only the overlay is
drawn; everything else is see-through, and the mouse passes straight
through it to Call of Duty underneath. It stays on top, so run the game in
Windowed Fullscreen or Windowed mode (exclusive Fullscreen draws over every
other window). It auto hides with the CoD caster HUD like the browser source.
"""

import ctypes
import sys
import threading
import time

from .log import log

# ("CoD HUD" in it: the game window finder skips this app's own windows.)
TITLE = "SCC CoD HUD - on top of the game"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20      # clicks go to the window underneath
WS_EX_TOOLWINDOW = 0x80       # not in the taskbar or Alt+Tab
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x8000000  # never takes focus from the game
LWA_COLORKEY = 0x1
LWA_ALPHA = 0x2
WDA_EXCLUDEFROMCAPTURE = 0x11
# The window's own background is painted in this colour and then cut out, so
# only the HUD shows. Nearly black, so any soft edges blend in.
KEY_RGB = (1, 0, 1)
KEY_COLORREF = KEY_RGB[0] | KEY_RGB[1] << 8 | KEY_RGB[2] << 16
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x10
SWP_SHOWWINDOW = 0x40


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
    found.sort(key=lambda m: (m[0], m[1], m[2]))  # primary first, then left to right
    return [m[1:] for m in found]


def _hwnd(title):
    return ctypes.windll.user32.FindWindowW(None, title) if sys.platform == "win32" else 0


def make_click_through(hwnd, click_through=True):
    """Cut out the background colour and (optionally) let clicks through."""
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.restype = ctypes.c_long
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    want = style | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    if click_through:
        want |= WS_EX_TRANSPARENT
    if style != want:
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, want)
        user32.SetLayeredWindowAttributes(hwnd, KEY_COLORREF, 255, LWA_COLORKEY | LWA_ALPHA)


def paint_background(window):
    """Fill the window (behind the page) with the cut-out colour."""
    from System import Action
    from System.Drawing import Color
    form = window.native
    if form is None:
        return False

    def fill():
        form.BackColor = Color.FromArgb(255, *KEY_RGB)
        form.Invalidate()
    form.Invoke(Action(fill))
    return True


def no_backdrop(hwnd):
    """Windows 11 in dark mode: the window library gives windows a dark Mica
    backdrop, which DWM draws behind the page and the cut-out, so the HUD
    window shows dark grey. Turn it (and dark mode) off for this window."""
    dwm = ctypes.windll.dwmapi
    for attr, value in ((38, 1), (20, 0)):  # SYSTEMBACKDROP_TYPE = none, IMMERSIVE_DARK_MODE = off
        v = ctypes.c_int(value)
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)


def place(hwnd, rect):
    x, y, w, h = rect
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, w, h, SWP_NOACTIVATE | SWP_SHOWWINDOW)
    try:  # square corners on Windows 11
        pref = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception:
        pass


def exclude_from_capture(hwnd, exclude):
    """Leave the window out of screen captures, so that in whole screen mode
    the HUD reads the game and not its own overlay (Windows 10 2004 or later)."""
    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if exclude else 0)


class OverlayWindow:
    def __init__(self, config, port, title=TITLE):
        self.config, self.port, self.title = config, port, title
        self.window = None
        self.rect = None
        self.click_through = True
        # "transparent": a see-through page over the cut-out background.
        # "opaque": the page itself is filled with the cut-out colour.
        self.method = "transparent"
        self.fix_backdrop = True  # off only in the build's self-test, to show the problem
        self._lock = threading.Lock()
        self.available = sys.platform == "win32"

    def info(self):
        screens = monitors()
        return {"available": self.available, "open": self.window is not None,
                "screens": [f"Screen {i + 1} ({w}×{h})" for i, (_, _, w, h) in enumerate(screens)],
                "screen": min(int(self.config.get().get("overlay_screen") or 0), max(len(screens) - 1, 0))}

    def set(self, open_it, screen=None):
        if not self.available:
            raise ValueError("Show HUD on screen needs the Windows desktop app")
        if screen is not None:
            self.config.update({"overlay_screen": int(screen)})
            if self.window:
                self.rect = None  # move it on the next tick
        if open_it and not self.window:
            self.open()
        elif not open_it and self.window:
            self.close()

    def _target(self):
        return f"http://127.0.0.1:{self.port}/overlay?fit=1"

    def _screen_rect(self):
        screens = monitors() or [(0, 0, 1920, 1080)]
        return screens[min(int(self.config.get().get("overlay_screen") or 0), len(screens) - 1)]

    def open(self):
        import webview
        with self._lock:
            if self.window:
                return
            x, y, w, h = self._screen_rect()
            key = "#%02x%02x%02x" % KEY_RGB
            self.window = webview.create_window(
                self.title, self._target(), x=x, y=y, width=w, height=h,
                transparent=self.method == "transparent", frameless=True, easy_drag=False, on_top=True,
                focus=False, resizable=False, shadow=False, background_color=key)
            self.window.events.closed += self._closed
            self.rect = None
            threading.Thread(target=self._keep_on_top, args=(self.window,), daemon=True).start()
        log(f"HUD window opened over screen {self.config.get().get('overlay_screen', 0) + 1}")

    def close(self):
        window = self.window
        if window:
            window.destroy()

    def _closed(self):
        self.window = None
        log("HUD window closed")

    def _keep_on_top(self, window):
        """Size it to the screen, cut out its background, make it
        click-through, and keep it above the game (which comes to the front
        whenever it's clicked). Each step on its own, so one failing doesn't
        stop the rest; each problem is logged once."""
        painted, reported, ready = False, set(), False

        def step(name, fn, *a):
            try:
                return fn(*a)
            except Exception as e:
                if name not in reported:
                    reported.add(name)
                    log(f"HUD window ({name}): {e!r}", "warn")

        while self.window is window:
            hwnd = _hwnd(self.title)
            if not hwnd and window.native is not None:
                hwnd = step("handle", lambda: window.native.Handle.ToInt64()) or 0
            if hwnd:
                if self.fix_backdrop:
                    step("backdrop", no_backdrop, hwnd)
                if not painted:
                    painted = bool(step("background", paint_background, window))
                step("click-through", make_click_through, hwnd, self.click_through)
                step("capture", exclude_from_capture, hwnd, self.config.get()["capture"].get("mode") == "screen")
                rect = self._screen_rect()
                if rect != self.rect:
                    self.rect = rect
                step("position", place, hwnd, rect)
                if not ready and painted:
                    ready = True
                    log(f"HUD window ready: {rect[2]}x{rect[3]} at {rect[0]},{rect[1]}")
            time.sleep(0.5 if not ready else 2)


# Where the self-test expects the HUD to be drawn, and to be see-through
# (overlay pixels, 1920x1080): the name plates and a logo cover, and empty
# parts of the screen.
HUD_POINTS = [(300, 43), (1600, 43), (769, 72)]
EMPTY_POINTS = [(960, 650), (576, 810), (1344, 540), (960, 486), (192, 324)]


def self_test(port, out_dir):
    """Used by the build: puts a red window on screen with the HUD window over
    it, then checks the red shows through, the HUD is drawn, and a click on an
    empty part of the HUD window reaches the red window. Saves screenshots and
    overlay-test.txt in out_dir. Call from a thread once webview has started;
    returns True if it all worked."""
    import os
    import webview
    from PIL import ImageGrab

    results = []
    x, y, w, h = monitors()[0]
    bg = webview.create_window("overlay-test-red", html=(
        "<body style='margin:0;background:#ff0000;height:100vh' "
        "onmousedown='window.clicks=(window.clicks||0)+1'></body>"), x=x, y=y, width=w, height=h,
        frameless=True, background_color="#ff0000")
    time.sleep(4)
    hwnd_bg = _hwnd("overlay-test-red")
    ctypes.windll.user32.SetWindowPos(hwnd_bg, 0, x, y, w, h, SWP_SHOWWINDOW)
    time.sleep(1)

    class Config:
        def __init__(self):
            self.data = {"overlay_screen": 0, "capture": {"mode": "window"}}

        def get(self):
            return dict(self.data)

        def update(self, d):
            self.data.update(d)

    passed_methods = []
    # "without-fix" only shows whether this PC has the dark backdrop problem.
    for name in ("without-fix", "transparent"):
        ov = OverlayWindow(Config(), port, title=f"overlay-test-{name}")
        ov.fix_backdrop = name != "without-fix"
        ov.open()
        time.sleep(8)
        shot = ImageGrab.grab(all_screens=False)
        shot.save(os.path.join(out_dir, f"overlay-test-{name}.png"))

        # The page's own size and place: the window can come out a little
        # smaller than the screen, and the overlay is scaled to it and centred
        # (overlay.html's ?fit=1).
        try:
            iw, ih, wx, wy, dpr = ov.window.evaluate_js("[innerWidth, innerHeight, screenX, screenY, devicePixelRatio]")
        except Exception as e:
            results.append(f"{name}: page size not read ({e!r}), taking the whole screen")
            iw, ih, wx, wy, dpr = w, h, 0, 0, 1

        def px(ox, oy):
            k = min(iw / 1920, ih / 1080)
            fx, fy = wx + (iw - 1920 * k) / 2 + ox * k, wy + (ih - 1080 * k) / 2 + oy * k
            return shot.getpixel((min(int(fx * dpr), shot.size[0] - 1), min(int(fy * dpr), shot.size[1] - 1)))[:3]

        def red(p):
            return p[0] > 200 and p[1] < 60 and p[2] < 60

        empties = [px(fx, fy) for fx, fy in EMPTY_POINTS]
        huds = [px(fx, fy) for fx, fy in HUD_POINTS]
        see_through = all(red(p) for p in empties)
        drawn = not any(red(p) for p in huds)
        empty, hud = empties, huds
        # Click in the empty middle of the screen.
        before = bg.evaluate_js("window.clicks||0")
        ctypes.windll.user32.SetCursorPos(x + w // 2, y + int(h * 0.6))
        ctypes.windll.user32.mouse_event(0x2, 0, 0, 0, 0)
        ctypes.windll.user32.mouse_event(0x4, 0, 0, 0, 0)
        time.sleep(1)
        after = bg.evaluate_js("window.clicks||0")
        passed = after > before
        results.append(f"{name}: page {iw}x{ih} at {wx},{wy} (x{dpr}) see-through={see_through} {empty} hud-drawn={drawn} {hud} click-passes={passed}")
        if see_through and drawn and passed and name != "without-fix":
            passed_methods.append(name)
        ov.close()
        time.sleep(2)
    bg.destroy()
    ok = bool(passed_methods)
    results.append(f"PASSED ({', '.join(passed_methods)})" if ok else "FAILED")
    with open(os.path.join(out_dir, "overlay-test.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(results) + "\n")
    return ok
