"""The desktop app: runs the HUD and shows the control panel in its own
window (no command prompt).

    CodHUD.exe              the built app
    python -m codhud.gui    the same, from source (needs: pip install pywebview)

Takes the same options as python -m codhud (--demo, --port).
"""

import argparse
import json
import os
import sys
import threading
import urllib.request
import webbrowser

from . import theme
from .__main__ import add_arguments, build_app
from .app import start
from .config import DATA_DIR
from .log import log

TITLE = "SCC CoD HUD"


def message_box(text, title=TITLE, error=False):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10 if error else 0x40)
    else:
        print(f"{title}: {text}")


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return "scanner" in json.loads(r.read())
    except Exception:
        return False


def smoke_test(port):
    """Used by the build: check the packaged app serves its pages, reads its
    demo match and can load its window library. Writes smoke-test.txt next
    to the app."""
    import time
    results, ok = [], True
    for path in ("/control", "/overlay", "/api/state", "/api/log", "/api/fonts", "/api/logos", "/fonts/Inter.ttf",
                 "/fonts/BebasNeue.ttf", "/assets/icon-white.ico", "/assets/demo/game.png", "/assets/overlay/box-left.png",
                 "/assets/overlay/box-right.png", "/cams.js"):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
                results.append(f"OK   {path} ({len(r.read())} bytes)")
        except Exception as e:
            ok = False
            results.append(f"FAIL {path}: {e}")
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/demo", b'{"on": true}',
                                                      {"Content-Type": "application/json"}), timeout=5).read()
        hud = None
        for _ in range(40):
            time.sleep(0.25)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
                hud = json.loads(r.read())["scanner"]["hud"]
            if hud == "on":
                break
        results.append(("OK  " if hud == "on" else "FAIL") + f" demo match read (HUD {hud})")
        ok = ok and hud == "on"
    except Exception as e:
        ok = False
        results.append(f"FAIL demo match: {e}")
    # Windows text recognition reading the demo screenshot's names (a warning
    # only: the build PC may not have the language for it).
    try:
        from PIL import Image
        from .capture import to_reference
        from .config import DEFAULTS
        from .names import DEMO_NAMES, WindowsOcr, read_cell
        from .demo import ScreenshotDemo
        ocr, frame = WindowsOcr(), to_reference(Image.open(ScreenshotDemo.PATH))
        got = [read_cell(frame, box, ocr) for box in DEFAULTS["names"]["rows"]] + [read_cell(frame, DEFAULTS["names"]["card"], ocr)]
        right = sum(a == b for a, b in zip(got, DEMO_NAMES + [DEMO_NAMES[3]]))
        results.append(("OK  " if right >= 7 else "WARN") + f" names read {right}/9: {', '.join(got)}")
    except Exception as e:
        results.append(f"WARN names not read: {e!r}")
    # The end-of-game scoreboard (a real screenshot): the numbers must read;
    # the names need Windows text recognition (a warning only).
    try:
        from PIL import Image
        from .capture import to_reference
        from . import postgame
        try:
            from .names import WindowsOcr
            ocr = WindowsOcr()
        except Exception:
            ocr = None
        got = postgame.read(to_reference(Image.open(postgame.SAMPLE)), ocr)
        rows = [p for t in got["teams"] for p in t["players"]]
        k = [p["kills"] for p in rows]
        right = k == [17, 21, 11, 23, 6, 10, 11, 7] and [p["damage"] for p in rows][:2] == [1866, 2398]
        ok = ok and right
        results.append(("OK  " if right else "FAIL") + f" post-game stats read: kills {k}")
        results.append(f"INFO post-game names: {[t['name'] for t in got['teams']]} {[p['name'] for p in rows]}")
    except Exception as e:
        ok = False
        results.append(f"FAIL post-game stats: {e!r}")
    try:
        import webview  # noqa: F401
        if sys.platform == "win32":
            import webview.platforms.winforms  # noqa: F401  (loads the Edge WebView2 bridge)
        results.append("OK   window library")
    except Exception as e:
        ok = False
        results.append(f"FAIL window library: {e!r}")
    with open(os.path.join(DATA_DIR, "smoke-test.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(results + ["PASSED" if ok else "FAILED"]) + "\n")
    return ok


def main():
    # A windowed .exe has no console; give print() somewhere harmless to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    parser = argparse.ArgumentParser(description="CoD HUD desktop app")
    add_arguments(parser)
    parser.add_argument("--debug", action="store_true", help="allow right-click > Inspect in the window")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--overlay-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    app = build_app(args)
    port = app.config.get()["http_port"]
    own_server = True
    try:
        start(app)
    except OSError as e:
        if already_running(port):
            own_server = False   # another copy is running: just open a window onto it
        else:
            message_box(f"Couldn't start the HUD on port {port}:\n{e}\n\n"
                        "Another program may be using that port. Change http_port in config.json.", error=True)
            return 1

    if args.smoke_test:
        ok = smoke_test(port)
        app.stop()
        return 0 if ok else 1

    if args.overlay_test:
        import webview
        from .overlay_window import self_test
        app.scanner.game.hides = False   # (the demo's killcam would hide the HUD mid-check)
        result = {}
        holder = webview.create_window("overlay-test", html="", hidden=True)

        def run():
            try:
                result["ok"] = self_test(port, DATA_DIR)
            finally:
                holder.destroy()
        webview.start(run)
        app.stop()
        return 0 if result.get("ok") else 1

    url = f"http://127.0.0.1:{port}/control"
    stop_theme = threading.Event()
    try:
        import webview
        from .overlay_window import OverlayWindow
        window_title = f"{TITLE} {app.updater.current}"
        window = webview.create_window(window_title, url, width=1280, height=760,
                                       min_size=(960, 600), background_color="#0e1012", text_select=True)
        if own_server:
            app.overlay_window = OverlayWindow(app.config, port)
            # Closing the control panel closes the HUD window too.
            window.events.closed += app.overlay_window.close
        # The skull in white on a dark taskbar / title bar, black on a light one.
        theme.update_shortcut_icon()
        theme.follow_theme(window_title, stop_theme)
        # Installing an update closes the window; the updater restarts the app.
        app.updater.on_exit = window.destroy
        webview.start(debug=args.debug)
    except Exception as e:
        # No WebView2 (very old Windows 10) or no window library: use the browser.
        log(f"Couldn't open the app window ({e!r}); using the web browser instead", "warn")
        webbrowser.open(url)
        if own_server:
            message_box("The control panel has opened in your web browser.\n\n"
                        "The HUD keeps running until you close this box.")
    finally:
        stop_theme.set()
        if own_server:
            app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
