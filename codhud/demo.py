"""A stand-in for the game: draws a CoD caster HUD (the score box and both
teams' scoreboards, in the real HUD's colours and places) over a plain
background, and plays out a match on it. Players die (skulls), stand on
the objective, get spectated, the clock runs, and now and then the HUD goes
away (a killcam) or only the score box stays up.
"""

import os
import random

import numpy as np

from .config import WEB_DIR

ROW_BG = (0x14, 0x15, 0x1B)
STRIP = (0x23, 0x25, 0x2D)
HEADER = (0x33, 0x35, 0x40)
LINE = (0x23, 0x25, 0x2D)
LEFT_RED = (0xA3, 0x0A, 0x27)
OBJECTIVE = (0xC8, 0x24, 0x3C)
NAMES = (["Kaiga", "Vain", "GRIZZ", "CHEWY"], ["snapiie", "Zeon", "Drop", "Ace"])


def _font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(os.path.join(WEB_DIR, "fonts", "Inter.ttf"), size)
    except OSError:
        return ImageFont.load_default()


class DemoGame:
    def __init__(self, seed=7):
        self.seed = seed
        self._t0 = None
        self._cache = (None, None)
        self._background = None

    def background(self):
        if self._background is None:
            from PIL import Image, ImageDraw
            y = np.linspace(0, 1, 1080, dtype=np.float32)[:, None, None]
            sky = np.array([150, 170, 190], np.float32) * (1 - y) + np.array([120, 92, 70], np.float32) * y
            img = np.broadcast_to(sky, (1080, 1920, 3)).copy()
            noise = np.random.default_rng(self.seed).normal(0, 6, (1080, 1920, 1)).astype(np.float32)
            img = Image.fromarray(np.clip(img + noise, 0, 255).astype(np.uint8))
            d = ImageDraw.Draw(img)
            d.rectangle((0, 560, 1920, 1080), fill=(118, 84, 60))
            d.rectangle((960, 260, 1500, 700), fill=(196, 196, 190))
            d.rectangle((300, 420, 900, 520), fill=(160, 168, 172))
            self._background = img
        return self._background.copy()

    def moment(self, t):
        """What's on screen t seconds into the demo."""
        second = int(t)
        rng = random.Random(self.seed * 1000 + second // 3)
        dead = {i for i in range(8) if rng.random() < 0.25}
        objective = {rng.choice([i for i in range(4) if i not in dead] or [0]),
                     rng.choice([i for i in range(4, 8) if i not in dead] or [4])}
        cycle = second % 40
        hud = "off" if 34 <= cycle < 37 else "score" if 28 <= cycle < 31 else "on"
        return {"hud": hud, "spectated": (second // 4) % 8, "dead": dead, "objective": objective,
                "clock": max(0, 300 - second % 300), "scores": (40 + second // 5, 30 + second // 7)}

    def draw(self, m):
        from PIL import ImageDraw
        img = self.background()
        if m["hud"] == "off":
            return np.asarray(img)
        d = ImageDraw.Draw(img)
        small, mid, big = _font(20), _font(28), _font(44)
        # The score box.
        d.rectangle((723, 20, 1196, 198), fill=ROW_BG)
        d.rectangle((723, 20, 1196, 46), fill=ROW_BG)
        d.text((732, 22), "LEG", font=mid, fill="white")
        d.text((910, 24), "BEST OF 9", font=small, fill="white")
        d.text((1132, 22), "S2K", font=mid, fill="white")
        d.rectangle((736, 62, 800, 142), fill=(150, 20, 30))
        d.rectangle((1120, 62, 1184, 142), fill=(170, 20, 30))
        d.text((834, 80), str(m["scores"][0]), font=big, fill="white")
        d.text((1048, 80), str(m["scores"][1]), font=big, fill="white")
        d.rectangle((905, 50, 1015, 100), fill=STRIP)
        d.text((930, 58), "%d:%02d" % divmod(m["clock"], 60), font=mid, fill="white")
        d.text((872, 160), "CDL HARDPOINT", font=mid, fill="white")
        if m["hud"] == "on":
            for side, x0 in ((0, 43), (1, 1267)):
                x1 = x0 + 610
                d.rectangle((x0, 17, x1, 67), fill=(244, 244, 244) if side else ROW_BG)
                d.text((x0 + 10 if side else x0 + 340, 20), "S2K GAMING" if side else "THE LEGION", font=big,
                       fill=(200, 20, 30))
                d.rectangle((x0, 68, x1, 96), fill=HEADER)
                d.text((x0 + 52, 70), "#%d SEED" % (4 if side else 1), font=small, fill="white")
                d.text((x0 + 424, 70), "K/D   STRK  TIME", font=small, fill="white")
                for row in range(4):
                    i = side * 4 + row
                    y0 = 97 + row * 32
                    d.rectangle((x0, y0, x1, y0 + 31), fill=ROW_BG)
                    d.line((x0, y0 + 31, x1, y0 + 31), fill=LINE)
                    ink = "white"
                    if m["spectated"] == i:
                        d.rectangle((x0 + 37, y0, x1, y0 + 31), fill=(250, 250, 250) if side else LEFT_RED)
                        ink = "black" if side else "white"
                    d.line((x0 + 36, y0, x0 + 36, y0 + 31), fill=LINE)
                    d.line((x0 + 410, y0, x0 + 410, y0 + 31), fill=LINE)
                    d.text((x0 + 52, y0 + 3), "%d  %s" % (i + 1, NAMES[side][row]), font=small, fill=ink)
                    d.text((x0 + 424, y0 + 3), "%d/%d     0     0:%02d" % (row + 2, row + 1, (i * 7) % 60),
                           font=small, fill=ink)
                    cx, cy = x0 + 18, y0 + 16
                    if i in m["dead"]:
                        d.ellipse((cx - 10, cy - 11, cx + 10, cy + 7), fill="white")
                        d.rectangle((cx - 6, cy + 4, cx + 6, cy + 11), fill="white")
                        d.ellipse((cx - 6, cy - 1, cx - 2, cy + 3), fill=ROW_BG)
                        d.ellipse((cx + 2, cy - 1, cx + 6, cy + 3), fill=ROW_BG)
                    elif i in m["objective"]:
                        d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=OBJECTIVE)
                        d.polygon((cx - 5, cy - 3, cx, cy + 3, cx + 5, cy - 3), fill="white")
        return np.asarray(img)

    def grab(self, cfg, now):
        if self._t0 is None:
            self._t0 = now
        m = self.moment(now - self._t0)
        key = (m["hud"], m["spectated"], tuple(sorted(m["dead"])), tuple(sorted(m["objective"])), m["clock"], m["scores"])
        if self._cache[0] != key:
            self._cache = (key, self.draw(m))
        return self._cache[1], "Demo match"


class ScreenshotDemo:
    """The demo match the app plays: a real screenshot of the CoD caster HUD
    (web/assets/demo/game.png). Every 40 seconds the HUD goes away for a few
    seconds (blurred out, as in a killcam), so the overlay's auto hide shows."""

    PATH = os.path.join(WEB_DIR, "assets", "demo", "game.png")

    def __init__(self):
        self._t0 = None
        self._frames = None

    def frames(self):
        if self._frames is None:
            from PIL import Image, ImageFilter
            img = Image.open(self.PATH).convert("RGB")
            hidden = img.copy()
            for box in ((0, 0, 1920, 260), (0, 1030, 1920, 1080)):
                hidden.paste(img.crop(box).filter(ImageFilter.GaussianBlur(12)), box[:2])
            self._frames = (np.asarray(img), np.asarray(hidden))
        return self._frames

    def grab(self, cfg, now):
        if self._t0 is None:
            self._t0 = now
        shown, hidden = self.frames()
        return (hidden if 34 <= (now - self._t0) % 40 < 37 else shown), "Demo (a CoD caster HUD screenshot)"
