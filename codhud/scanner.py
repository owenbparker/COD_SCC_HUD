"""The scanner: reads the game's picture over and over, and keeps what the
overlay needs (is the HUD up, each row's state, who's spectated, and the
background masks of the rows and score box) in a Hub the overlay pages
listen to.
"""

import base64
import io
import threading
import time

from . import detect
from .capture import Capture, make_dpi_aware
from .demo import ScreenshotDemo
from .log import log

# Masks (the expensive part) are worked out at most this often.
MASK_INTERVAL = 1 / 30


def png_data_url(mask):
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(mask, "L").save(buf, "PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


class Hub:
    """The latest reading, with a version that goes up when it changes; the
    overlay's event stream waits on it."""

    def __init__(self):
        self.version = 0
        self.state = {"hud": "off", "rows": ["normal"] * 8, "spectated": [False] * 8}
        self.masks = {}          # key -> {"id", "png", "x", "y", "w", "h"}
        self._cond = threading.Condition()

    def publish(self, state, masks=None):
        with self._cond:
            changed = state != self.state
            if masks is not None:
                ids = {k: m["id"] for k, m in masks.items()}
                if ids != {k: m["id"] for k, m in self.masks.items()}:
                    self.masks = masks
                    changed = True
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
            return self.version, dict(self.state), dict(self.masks)


class Scanner:
    def __init__(self, config, hub, demo=False):
        self.config = config
        self.hub = hub
        self.demo = demo
        self.capture = Capture()
        self.game = ScreenshotDemo()
        self.debounce = detect.HudDebounce()
        self.frame = None
        self.status = "Starting"
        self.raw = "off"
        self.colours = [None, None]
        self.fps = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._masks = {}
        self._last_mask = 0.0
        self._last_status = None

    def set_demo(self, on):
        self.demo = bool(on)
        self.game = ScreenshotDemo()
        self.debounce = detect.HudDebounce()
        log("Demo match started" if on else "Reading the game")

    def start(self):
        make_dpi_aware()
        self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def info(self):
        return {"demo": self.demo, "status": self.status, "hud": self.hub.state["hud"], "raw": self.raw,
                "colours": self.colours, "fps": round(self.fps, 1), "rows": self.hub.state["rows"],
                "spectated": self.hub.state["spectated"]}

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
            poll = max(5, int(cfg["capture"].get("poll_ms") or 20)) / 1000
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
        rows, spec = ["normal"] * 8, [False] * 8
        if frame is not None and hud == "on":
            rows, spec = detect.row_states(frame, cfg), detect.spectated(frame, cfg)
        if frame is not None and hud != "off" and now - self._last_mask >= MASK_INTERVAL:
            self._last_mask = now
            self._masks = self.masks(frame, cfg, hud)
        self.hub.publish({"hud": hud, "rows": rows, "spectated": spec}, self._masks)

    def masks(self, frame, cfg, hud):
        """{key: mask} for what's showing, reusing the encoded PNG of any
        mask that hasn't changed."""
        out = {}
        wanted = []
        if cfg["scorebox"].get("enabled", True):
            s = cfg["scorebox"]
            wanted.append(("score", detect.score_mask(frame, cfg), s))
        if hud == "on" and cfg["highlights"].get("enabled", True):
            for i, (mask, box) in enumerate(zip(detect.row_masks(frame, cfg), cfg["highlights"]["boxes"])):
                if mask is not None:
                    wanted.append((f"row{i}", mask, box))
        for key, mask, box in wanted:
            mid = detect.mask_id(mask) + "@%d,%d" % (int(box["x"]), int(box["y"]))
            old = self._masks.get(key)
            png = old["png"] if old and old["id"] == mid else png_data_url(mask)
            out[key] = {"id": mid, "png": png, "x": int(box["x"]), "y": int(box["y"]),
                        "w": int(mask.shape[1]), "h": int(mask.shape[0])}
        return out
