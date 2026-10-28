"""The scanner: reads the game's picture over and over, and keeps what the
overlay needs (is the CoD caster HUD up, who's spectated, the players'
names) in a Hub the overlay pages listen to.
"""

import threading
import time

from . import detect
from .capture import Capture, make_dpi_aware
from .demo import ScreenshotDemo
from .log import log
from .names import NameReader, best_match


class Hub:
    """The latest reading, with a version that goes up when it changes; the
    overlay's event stream waits on it."""

    def __init__(self):
        self.version = 0
        self.state = {"hud": "off", "spectated": [False] * 8, "names": [""] * 8, "spec": -1, "spec_name": ""}
        self._cond = threading.Condition()

    def publish(self, state):
        with self._cond:
            changed = state != self.state
            if changed:
                self.state = state
                self.version += 1
                self._cond.notify_all()
        return changed

    def touch(self):
        with self._cond:
            self.version += 1
            self._cond.notify_all()

    def wait(self, after, timeout):
        with self._cond:
            self._cond.wait_for(lambda: self.version != after, timeout)
            return self.version, dict(self.state)


def spectated_row(spec, names, card):
    """Whose row is the spectated player's: the one row lit up, else the row
    whose name is on the player card; -1 when neither tells."""
    lit = [i for i, on in enumerate(spec) if on]
    if len(lit) == 1:
        return lit[0]
    i = best_match(card, names)
    return -1 if i is None else i


class Scanner:
    def __init__(self, config, hub, demo=False):
        self.config = config
        self.hub = hub
        self.demo = demo
        self.capture = Capture()
        self.game = ScreenshotDemo()
        self.debounce = detect.HudDebounce()
        self.names = NameReader()
        self.frame = None
        self.status = "Starting"
        self.raw = "off"
        self.colours = [None, None]
        self.fps = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._last_status = None

    def set_demo(self, on):
        self.demo = bool(on)
        self.game = ScreenshotDemo()
        self.debounce = detect.HudDebounce()
        self.names.clear()
        log("Demo match started" if on else "Reading the game")

    def start(self):
        make_dpi_aware()
        self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")
        self._thread.start()
        self.names.start(self, self.config)
        return self

    def stop(self):
        self._stop.set()
        self.names.stop()

    def info(self):
        s = self.hub.state
        return {"demo": self.demo, "status": self.status, "hud": s["hud"], "raw": self.raw,
                "colours": self.colours, "fps": round(self.fps, 1), "spectated": s["spectated"],
                "spec": s["spec"], "names": self.names.info()}

    def _run(self):
        frames, counted_at = 0, time.monotonic()
        while not self._stop.is_set():
            t0 = time.monotonic()
            cfg = self.config.get()
            try:
                self.step(cfg, t0)
            except Exception as e:     # keep going: one bad read mustn't stop the overlay
                self._set_status(f"Read error: {e!r}")
            frames += 1
            if t0 - counted_at >= 1:
                self.fps, frames, counted_at = frames / (t0 - counted_at), 0, t0
            poll = max(5, int(cfg["capture"].get("poll_ms") or 33)) / 1000
            self._stop.wait(max(poll - (time.monotonic() - t0), 0.003))

    def _set_status(self, text):
        self.status = text
        if text != self._last_status:
            self._last_status = text
            log(text)

    def step(self, cfg, now):
        source = self.game if self.demo else self.capture
        frame, status = source.grab(cfg, now)
        self._set_status(status)
        self.frame = frame
        if frame is None:
            raw, self.colours = "off", [None, None]
        elif cfg["detector"].get("enabled", True):
            raw, self.colours = detect.hud_reading(frame, cfg)
        else:
            raw, self.colours = "on", detect.hud_reading(frame, cfg)[1]
        self.raw = raw
        hud = self.debounce.feed(raw, now)
        spec = detect.spectated(frame, cfg) if frame is not None and hud == "on" else [False] * 8
        n = self.names.info()
        row = spectated_row(spec, n["names"], n["card"]) if hud == "on" else -1
        self.hub.publish({"hud": hud, "spectated": spec, "names": n["names"], "spec": row,
                          "spec_name": n["names"][row] if row >= 0 and n["names"][row] else (n["card"] if hud == "on" else "")})
