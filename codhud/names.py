"""The players' in-game names, read off the CoD caster HUD with Windows' own
text recognition (Windows.Media.Ocr, the winrt packages): each scoreboard
row's name, and the name on the spectated player's card at the bottom right.

The text can be light on dark (most rows), dark on a light highlight (the
spectated player's row) or coloured (the player card), so each cell is
turned into black text on white by how far each pixel is from the cell's
own background colour, then enlarged: what Windows reads best.

A row's name only changes after the same new name is read twice in a row,
so one misread doesn't flicker it.
"""

import asyncio
import difflib
import re
import threading
import time
import unicodedata

import numpy as np

from .detect import crop
from .log import log

# Names in the demo screenshot (web/assets/demo/game.png), for a PC without
# text recognition.
DEMO_NAMES = ["Zett7abyte", "Last__Stand", "Fri_9thalix", "BigR3aL", "LuckyrunnerX", "ChronoK", "Psych9delic", "Jumping66"]
READ_EVERY = 0.5     # seconds between reads of the names


class WindowsOcr:
    """Windows.Media.Ocr through the winrt packages."""

    def __init__(self):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        engine = OcrEngine.try_create_from_language(Language("en-US"))
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows text recognition isn't installed for any of your languages")
        self.engine = engine

    def words(self, image):
        """[(text, x_centre, y_centre), ...] in the image's pixels."""
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        rgba = image.convert("RGBA")
        writer = DataWriter()
        writer.write_bytes(rgba.tobytes("raw", "BGRA"))
        buffer = writer.detach_buffer()
        try:
            bitmap = SoftwareBitmap.create_copy_from_buffer(buffer, BitmapPixelFormat.BGRA8, rgba.width, rgba.height)
        except TypeError:
            bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, rgba.width, rgba.height, BitmapAlphaMode.PREMULTIPLIED)
            bitmap.copy_from_buffer(buffer)

        async def run():
            return await self.engine.recognize_async(bitmap)

        result = asyncio.run(run())
        out = []
        for line in result.lines:
            for word in line.words:
                r = word.bounding_rect
                out.append((word.text, r.x + r.width / 2, r.y + r.height / 2))
        return out


def ink(cell, scale=3, level=90):
    """A cell of the picture as black text on white, enlarged: a pixel is ink
    when it's far (any channel by level or more) from the cell's background
    (its most common colour, roughly)."""
    from PIL import Image
    px = cell.astype(np.int16)
    flat = (px // 16).reshape(-1, 3)
    keys, counts = np.unique(flat[:, 0] * 256 + flat[:, 1] * 16 + flat[:, 2], return_counts=True)
    k = int(keys[counts.argmax()])
    sel = (flat[:, 0] * 256 + flat[:, 1] * 16 + flat[:, 2]) == k
    bg = px.reshape(-1, 3)[sel].mean(axis=0)
    far = np.abs(px - bg).max(axis=-1) >= level
    img = Image.fromarray(np.where(far, 0, 255).astype(np.uint8), "L")
    # (a margin round the text helps the recognition)
    out = Image.new("L", (img.width + 16, img.height + 16), 255)
    out.paste(img, (8, 8))
    return out.resize((out.width * scale, out.height * scale), Image.LANCZOS)


def clean_name(text):
    """What was read -> an in-game name: no row number in front, nothing but
    the characters names have."""
    text = unicodedata.normalize("NFKC", str(text or "")).strip()
    text = re.sub(r"^\d{1,2}\s+(?=\S)", "", text)      # the row number ("6 SHOTZZY")
    text = re.sub(r"[^\w.\-\[\]#|]+", "", text)
    return text[:24]


def read_cell(frame, box, ocr):
    if not box.get("enabled", True):
        return ""
    cell = crop(frame, box["x"], box["y"], box["w"], box["h"])
    words = sorted(ocr.words(ink(cell)), key=lambda w: w[1])
    return clean_name(" ".join(t for t, _, _ in words))


def key(name):
    """A name as it's compared: case, accents and look-alike characters aside."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", text).translate(str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "5": "s", "8": "b"}))


def same_player(a, b):
    """Is name a the player named b? Names are read off the screen, so allow a
    misread letter in longer names, and a clan tag in front."""
    a, b = key(a), key(b)
    if not a or not b:
        return False
    if a == b or (len(a) >= 3 and (b.endswith(a) or a.endswith(b)) and abs(len(b) - len(a)) <= 5):
        return True
    return min(len(a), len(b)) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def best_match(name, names):
    """The index in names of the one that's name (best match), or None."""
    if not key(name):
        return None
    best, score = None, 0.0
    for i, n in enumerate(names):
        if not n:
            continue
        if key(n) == key(name):
            return i
        if same_player(name, n):
            s = difflib.SequenceMatcher(None, key(name), key(n)).ratio()
            if s > score:
                best, score = i, s
    return best


class NameReader:
    """Reads the names in a thread of its own, from the scanner's latest
    picture, while the CoD caster HUD is up."""

    def __init__(self):
        self.names = [""] * 8
        self.card = ""
        self.status = "Starting"
        self.read_at = 0.0
        self._pending = [None] * 8
        self._ocr = None
        self._tried = False
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def available(self):
        if not self._tried:
            self._tried = True
            try:
                self._ocr = WindowsOcr()
                self.status = "Ready"
            except Exception as e:   # (not Windows, or no winrt)
                self._ocr = None
                self.status = f"Text recognition unavailable: {e}"
                log(f"Player names can't be read on this PC ({e})", "warn")
        return self._ocr is not None

    def clear(self):
        with self._lock:
            self.names, self._pending, self.card = [""] * 8, [None] * 8, ""
        log("Player names cleared: they're read again from the scoreboards")

    def info(self):
        with self._lock:
            return {"names": list(self.names), "card": self.card, "status": self.status,
                    "age": round(time.time() - self.read_at, 1) if self.read_at else None}

    def start(self, scanner, config):
        threading.Thread(target=self._run, args=(scanner, config), daemon=True, name="names").start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self, scanner, config):
        while not self._stop.wait(READ_EVERY):
            cfg = config.get()
            if not cfg["names"].get("enabled", True):
                continue
            if scanner.demo and not self.available():
                with self._lock:
                    self.names = list(DEMO_NAMES)
                    self.card = DEMO_NAMES[3]
                    self.status = "Demo names (no text recognition on this PC)"
                continue
            frame, hud = scanner.frame, scanner.hub.state.get("hud")
            if frame is None or hud != "on" or not self.available():
                continue
            try:
                self.read(frame, cfg)
            except Exception as e:     # (keep going: the next picture may read)
                self.status = f"Read error: {e!r}"

    def read(self, frame, cfg):
        n = cfg["names"]
        got = [read_cell(frame, box, self._ocr) for box in n["rows"][:8]]
        card = read_cell(frame, n["card"], self._ocr)
        with self._lock:
            for i, name in enumerate(got):
                if not name or name == self.names[i]:
                    self._pending[i] = None
                elif not self.names[i] or self._pending[i] == name:
                    if self.names[i] != name:
                        log(f"{'Left' if i < 4 else 'Right'} player {i % 4 + 1}: {name}")
                    self.names[i], self._pending[i] = name, None
                else:
                    self._pending[i] = name
            self.card = card
            self.status = "Reading"
            self.read_at = time.time()
