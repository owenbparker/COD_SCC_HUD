"""Reading the CoD caster HUD off the game's picture (a 1920x1080 RGB numpy
array): is the HUD on screen, each scoreboard row's state, who's being
spectated, and which pixels of the score box and rows are background (the
parts the overlay paints over).

The rules are the CoD Scoreboard Suite's: a pixel "matches" a colour when
no channel is further from it than the tolerance.
"""

import re
import time
import zlib

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
    score box is up, when score_box_only is set) or "off"; with the two
    pixels' colours."""
    d = cfg["detector"]
    p1, p2 = d["pixel1"], d["pixel2"]
    rgb1 = pixel(frame, p1["x"], p1["y"])
    one = matches(rgb1, hex_rgb(p1["color"]), float(p1["tolerance"]))
    rgb2 = None
    two = True
    if p2.get("enabled", True):
        rgb2 = pixel(frame, p2["x"], p2["y"])
        two = matches(rgb2, hex_rgb(p2["color"]), float(p2["tolerance"]))
    if one and two:
        state = "on"
    elif one and p2.get("enabled", True) and d.get("score_box_only"):
        state = "score"
    else:
        state = "off"
    return state, [rgb_hex(c) if c else None for c in (rgb1, rgb2)]


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


def row_states(frame, cfg):
    """Each row's glow: "normal" (its icon pixel is the row colour), "dim" (a
    skull: white or black) or "bright" (any other icon)."""
    h = cfg["highlights"]
    try:
        base = hex_rgb(h["base_color"])
    except ValueError:
        base = (0x13, 0x14, 0x1B)
    rules = [("normal", base)] + [("dim", c) for c in (hex_list(h["dim_colors"]) or [(255, 255, 255), (0, 0, 0)])]
    out = []
    for p in h["pixels"][:8]:
        rgb = pixel(frame, p["x"], p["y"]) if p.get("enabled", True) else None
        if rgb is None:
            out.append("normal")
            continue
        kind, best = "bright", None
        for name, colour in rules:
            d = max(abs(a - b) for a, b in zip(rgb, colour))
            if best is None or d < best:
                kind, best = name, d
        out.append(kind if best <= float(p.get("tolerance", 1)) else "bright")
    return out


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


def colour_mask(region, colours, feather):
    """0-255 per pixel: how much it is one of the colours ([{hex, tolerance,
    enabled}]); within tolerance is 255, fading to 0 over feather more."""
    feather = max(float(feather), 1e-3)
    mask = np.zeros(region.shape[:2], dtype=np.float32)
    px = region[:, :, :3].astype(np.int16)
    for c in colours:
        if not c.get("enabled", True):
            continue
        try:
            rgb = np.array(hex_rgb(c["hex"]), dtype=np.int16)
        except ValueError:
            continue
        diff = np.abs(px - rgb).max(axis=-1).astype(np.float32)
        m = 1.0 - np.clip((diff - float(c.get("tolerance", 0))) / feather, 0.0, 1.0)
        np.maximum(mask, m, out=mask)
    return (mask * 255.0 + 0.5).astype(np.uint8)


def crop(frame, x, y, w, h):
    """frame[y:y+h, x:x+w], padded with black where it runs off the picture."""
    x, y, w, h = int(x), int(y), max(1, int(w)), max(1, int(h))
    out = np.zeros((h, w, 3), dtype=np.uint8)
    fh, fw = frame.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(fw, x + w), min(fh, y + h)
    if x1 > x0 and y1 > y0:
        out[y0 - y:y1 - y, x0 - x:x1 - x] = frame[y0:y1, x0:x1, :3]
    return out


def row_masks(frame, cfg):
    """The background of each row's glow box (None for a box that's off).
    Without use_mask, the whole box."""
    h = cfg["highlights"]
    out = []
    for b in h["boxes"][:8]:
        if not b.get("enabled", True):
            out.append(None)
            continue
        if h.get("use_mask", True):
            out.append(colour_mask(crop(frame, b["x"], b["y"], b["w"], b["h"]), h["mask_colors"], h.get("feather", 0)))
        else:
            out.append(np.full((max(1, int(b["h"])), max(1, int(b["w"]))), 255, dtype=np.uint8))
    return out


def score_mask(frame, cfg):
    s = cfg["scorebox"]
    x, y, w, h = int(s["x"]), int(s["y"]), max(1, int(s["w"])), max(1, int(s["h"]))
    mask = colour_mask(crop(frame, x, y, w, h), s["mask_colors"], s.get("feather", 15))
    for o in s.get("overrides") or []:
        if not o.get("enabled", True):
            continue
        x0, y0 = max(0, int(o["x"]) - x), max(0, int(o["y"]) - y)
        x1, y1 = min(w, int(o["x"]) + int(o["w"]) - x), min(h, int(o["y"]) + int(o["h"]) - y)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
    return mask


def mask_id(mask):
    return None if mask is None else "%08x%dx%d" % (zlib.adler32(mask.tobytes()), mask.shape[1], mask.shape[0])
