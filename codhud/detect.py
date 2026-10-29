"""Reading the CoD caster HUD off the game's picture (a 1920x1080 RGB numpy
array): is the HUD on screen, and whose scoreboard row is the spectated
player's. (The players' names are read in names.py.)

The rules are the CoD Scoreboard Suite's: a pixel "matches" a colour when
no channel is further from it than the tolerance.
"""

import re
import time

import numpy as np

# Debounce for the HUD detector, in seconds: the HUD has to be gone this long
# before the overlay hides (rides out alt-tab and loading glitches), and back
# this long before it shows again.
HUD_LOSS_GRACE = 0.35
HUD_GAIN_CONFIRM = 0.08


def hex_rgb(text):
    text = (text or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", text):
        raise ValueError(f"not a colour: {text!r}")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def hex_list(text):
    out = []
    for token in re.split(r"[,\s;]+", text or ""):
        try:
            out.append(hex_rgb(token))
        except ValueError:
            pass
    return out


def rgb_hex(rgb):
    return "#%02X%02X%02X" % tuple(int(c) for c in rgb[:3])


def pixel(frame, x, y):
    h, w = frame.shape[:2]
    if 0 <= x < w and 0 <= y < h:
        return tuple(int(c) for c in frame[int(y), int(x), :3])
    return None


def matches(rgb, colour, tolerance):
    return rgb is not None and max(abs(a - b) for a, b in zip(rgb, colour)) <= tolerance


def hud_reading(frame, cfg):
    """The detector's raw reading: "on" (the HUD is up), "score" (only the
    score box is up, when score_box_only is set) or "off"; with the
    pixels' colours (score box, right scoreboard, left scoreboard)."""
    d = cfg["detector"]
    p1 = d["pixel1"]
    boards = [d["pixel2"], d["pixel3"]]
    rgb1 = pixel(frame, p1["x"], p1["y"])
    one = matches(rgb1, hex_rgb(p1["color"]), float(p1["tolerance"]))
    check = d["pixel2"].get("enabled", True)   # (one switch for both scoreboards)
    rgbs = [pixel(frame, p["x"], p["y"]) if check else None for p in boards]
    two = not check or all(matches(c, hex_rgb(p["color"]), float(p["tolerance"])) for c, p in zip(rgbs, boards))
    if one and two:
        state = "on"
    elif one and check and d.get("score_box_only"):
        state = "score"
    else:
        state = "off"
    return state, [rgb_hex(c) if c else None for c in [rgb1] + rgbs]


class HudDebounce:
    """A change in the detector's reading only counts once it has lasted:
    HUD_LOSS_GRACE to hide, HUD_GAIN_CONFIRM to show (at least two reads)."""

    def __init__(self):
        self.state = None
        self._cand = None
        self._since = 0.0
        self._count = 0

    def feed(self, raw, now=None):
        now = time.monotonic() if now is None else now
        if self.state is None:
            self.state = raw
        elif raw == self.state:
            self._cand = None
        else:
            if raw != self._cand:
                self._cand, self._since, self._count = raw, now, 0
            self._count += 1
            hold = HUD_LOSS_GRACE if raw == "off" else HUD_GAIN_CONFIRM
            if self._count >= 2 and now - self._since >= hold:
                self.state, self._cand = raw, None
        return self.state


def spectated(frame, cfg):
    """Each row: is it the spectated player's (its name cell isn't the idle colour)."""
    s = cfg["spectated"]
    idle = hex_rgb(s["idle_color"])
    tolerance = float(s.get("tolerance", 0))
    out = []
    for p in s["pixels"][:8]:
        rgb = pixel(frame, p["x"], p["y"]) if p.get("enabled", True) else None
        out.append(rgb is not None and not matches(rgb, idle, tolerance))
    return out


def crop(frame, x, y, w, h):
    """frame[y:y+h, x:x+w], padded with black where it runs off the picture."""
    x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
    out = np.zeros((h, w, 3), dtype=np.uint8)
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + w), min(fh, y + h)
    if x1 > x0 and y1 > y0:
        out[y0 - y:y1 - y, x0 - x:x1 - x] = frame[y0:y1, x0:x1, :3]
    return out
