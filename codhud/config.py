"""Settings for the CoD HUD, saved to config.json next to the project (or
next to CodHUD.exe in the desktop build).

Every position is in 1920x1080 screen pixels: the game's picture is scaled
to that size before it's read, whatever resolution it runs at. Settings
from older builds (the row glow, the score box gradient, the BO7 title)
are dropped when the file is loaded.
"""

import copy
import json
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FROZEN = getattr(sys, "frozen", False)
# Read-only files shipped with the app (overlay, control panel, fonts).
RESOURCE_DIR = getattr(sys, "_MEIPASS", ROOT) if FROZEN else ROOT
# Things the app writes: settings.
DATA_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else ROOT
WEB_DIR = os.path.join(RESOURCE_DIR, "web")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
# Team logos added from the control panel (served as logos/...).
LOGO_DIR = os.path.join(DATA_DIR, "logos")

SPEC_YS = (115, 145, 179, 209)
# Each scoreboard row's name (after the row number), and the spectated
# player's name on the player card at the bottom right.
NAME_YS = (99, 131, 163, 195)


def _rows(xs, ys, **extra):
    return [dict({"x": x, "y": y, "enabled": True}, **extra) for x in xs for y in ys]


DEFAULTS = {
    # Not the League (8777, and 8779 for its player cams) or VALORANT (8778)
    # HUD's port, so they can all run at once.
    "http_host": "127.0.0.1",
    "http_port": 8780,
    # Let other PCs on the network load the overlay (vMix / OBS on another
    # PC) and connect their player cams. Settings can still only be changed
    # on this PC.
    "allow_network": False,
    # Which screen "Show HUD on screen" covers (0 = the main one).
    "overlay_screen": 0,

    # Where the game's picture comes from. "window": the Call of Duty window
    # itself (read even while other windows cover it); "screen": a whole
    # screen, for when the window can't be read.
    "capture": {"mode": "window", "window_title": "Call of Duty", "screen": 0,
                # How often the picture is read, in milliseconds.
                "poll_ms": 33},

    # Auto hide: the overlay only shows while the CoD caster HUD is on
    # screen, told by pixels of it. Pixel 1 is in the score box at the top;
    # pixels 2 and 3 in the right and left teams' panels (pixel2.enabled
    # switches both). With score_box_only, pixel 1 alone (the panels away)
    # still shows the score box parts.
    "detector": {"enabled": True,
                 "pixel1": {"x": 960, "y": 57, "color": "#23252D", "tolerance": 1},
                 "pixel2": {"enabled": True, "x": 1500, "y": 80, "color": "#333540", "tolerance": 1},
                 "pixel3": {"x": 420, "y": 80, "color": "#333540", "tolerance": 1},
                 "score_box_only": True},

    # Who's being spectated: their scoreboard row turns from idle_color to the
    # game's highlight. One pixel per row, in the name cell.
    "spectated": {"idle_color": "#14151B", "tolerance": 2, "pixels": _rows((444, 1664), SPEC_YS)},

    # Reading the players' in-game names off the scoreboards (Windows text
    # recognition): each row's name cell, and the player card's name.
    "names": {"enabled": True,
              "rows": [dict(r, w=338, h=28) for r in _rows((113, 1337), NAME_YS)],
              "card": {"x": 1418, "y": 900, "w": 298, "h": 54}},

    # The match: each team's name, short name, logo, colour and maps won.
    # With global.follow, these follow the GFX Engine's Global information.
    "match": {
        "left": {"name": "FUEL ESPORTS", "tricode": "FUEL", "logo": "", "wins": 0, "color": "#0B6B3A"},
        "right": {"name": "ST. CLAIR SAINTS", "tricode": "SCC", "logo": "assets/logos/st-clair-saints.png",
                  "wins": 0, "color": "#B8191C"},
        "best_of": 5,
    },
    "global": {"follow": True},

    # What the overlay shows over the CoD caster HUD (the NEW COD OVERLAY
    # vMix title): the name plates over the team bars, the logos in the score
    # box, the series score in place of the game's pips, the spectated
    # player's webcam beside their player card and the sponsors under the
    # middle of the screen (where CDL has them).
    "overlay": {"plates": True, "logos": True, "pips": True, "cam": True, "sponsor": True},
    # The team names' font (installed on this PC, sent to the overlay so it
    # shows on any PC; Inter when it isn't installed).
    "fonts": {"name": "Gotham"},

    # The sponsors, one after another (each for its seconds); from the GFX
    # Engine's Global information.
    "sponsors": {"logos": []},

    # Player webcams (the SCC Player Cam app, Call of Duty mode, with the
    # player's in-game name). assign: {"<in-game name>": "<cam's name>"} for
    # a cam picked by hand on the Webcams page.
    "cams": {"enabled": True, "assign": {}},

    # The SCC GFX Engine this HUD sends its match and series to (see codhud/enginelink.py).
    "gfx_engine": {"url": "", "name": "", "connected": False},
}

_lock = threading.Lock()


def _merge(base, extra):
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def set_path(data, path, value):
    """data with the setting at "a.b.0.c" set to value (a copy)."""
    data = copy.deepcopy(data)
    keys = path.split(".")
    node = data
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    if isinstance(node, list):
        node[int(last)] = value
    elif last in node or isinstance(node, dict):
        node[last] = value
    return data


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            # (only what this build has: older builds' settings are dropped)
            self.data = _merge(DEFAULTS, {k: v for k, v in saved.items() if k in DEFAULTS})
            for side in ("left", "right"):
                team = self.data["match"][side]
                for k in list(team):
                    if k not in DEFAULTS["match"]["left"]:
                        team.pop(k)
            for k in list(self.data["match"]):
                if k not in DEFAULTS["match"]:
                    self.data["match"].pop(k)
            # The first builds used 8779, which the League HUD's player cam page uses.
            if self.data.get("http_port") == 8779:
                self.data["http_port"] = 8780
                self.save(self.data)
        self.version = 0

    def get(self):
        with _lock:
            return copy.deepcopy(self.data)

    def update(self, changes):
        with _lock:
            self.data = _merge(self.data, changes)
            self.version += 1
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def set(self, path, value):
        with _lock:
            self.data = set_path(self.data, path, value)
            self.version += 1
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def replace(self, data):
        with _lock:
            self.data = _merge(DEFAULTS, data)
            self.version += 1
            snapshot = copy.deepcopy(self.data)
        self.save(snapshot)
        return snapshot

    def save(self, snapshot=None):
        snapshot = snapshot if snapshot is not None else self.get()
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, indent=2)
        os.replace(tmp, self.path)
