"""Settings for the CoD HUD, saved to config.json next to the project (or
next to CodHUD.exe in the desktop build).

Every position is in 1920x1080 screen pixels: the game's picture is scaled
to that size before it's read, whatever resolution it runs at. The
defaults are the CoD Scoreboard Suite's (cod_scoreboard_suite_config.json),
and a Suite settings file can be imported from the control panel.
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

ROW_YS = (110, 142, 174, 206)
BOX_YS = (97, 129, 161, 193)
SPEC_YS = (115, 145, 179, 209)


def _rows(xs, ys, **extra):
    return [dict({"x": x, "y": y, "enabled": True}, **extra) for x in xs for y in ys]


DEFAULTS = {
    # Not the League (8777) or VALORANT (8778) HUD's port, so all three can run at once.
    "http_host": "127.0.0.1",
    "http_port": 8779,
    # Let other PCs on the network load the overlay (vMix / OBS on another
    # PC). Settings can still only be changed on this PC.
    "allow_network": False,

    # Where the game's picture comes from. "window": the Call of Duty window
    # itself (read even while other windows cover it); "screen": a whole
    # screen, for when the window can't be read.
    "capture": {"mode": "window", "window_title": "Call of Duty", "screen": 0,
                # How often the picture is read, in milliseconds.
                "poll_ms": 20},

    # Auto hide: the overlay only shows while the CoD caster HUD is on
    # screen, told by two pixels of it. Pixel 1 is in the score box at the
    # top; pixel 2 in the right team's panel. With score_box_only, pixel 1
    # alone (the panels away) still shows the score box overlay.
    "detector": {"enabled": True,
                 "pixel1": {"x": 960, "y": 57, "color": "#23252D", "tolerance": 1},
                 "pixel2": {"enabled": True, "x": 1500, "y": 80, "color": "#333540", "tolerance": 1},
                 "score_box_only": True},

    # A glow in each scoreboard row, around the player's name, in their
    # team's colour. Each row has a pixel in its icon column: the row's own
    # colour (base_color) leaves the glow as it is; a skull (dim_colors, white
    # or black) dims it; any other icon (on the objective) brightens it.
    "highlights": {
        "enabled": True,
        "color_left": "#AE2626", "color_right": "#771116",
        "pixels": _rows((55, 1279), ROW_YS, tolerance=1),
        "boxes": _rows((43, 1267), BOX_YS, w=610, h=32),
        "base_color": "#13141B",
        "dim_colors": "#FFFFFF, #000000",
        # Only the row's background is painted (its text, icons and the
        # spectated player's coloured row show through untouched).
        "use_mask": True,
        "mask_colors": [{"hex": "#14151B", "tolerance": 0, "enabled": True}],
        "feather": 0,
        # The glow: the team colour at center_opacity in the middle, rising to
        # edge_opacity (and edge_brighten lighter) at the row's edges.
        "opacity": 0.25,
        "center_opacity": 15, "edge_opacity": 100,
        "edge_brighten": 35, "edge_hue": 0, "edge_saturation": 100,
        "falloff": 2.5, "core": 0, "reach": 100, "oval": 0, "side_depth": 100, "corner": 0,
        "edges": {"top": True, "bottom": True, "left": True, "right": True},
        "bright_opacity": 10, "bright_lightness": 10,
        "dim_opacity": 10, "dim_lightness": 25,
        "fade": 0.15,
    },

    # The score box at the top: its background recoloured with a gradient
    # from the left team's colour to the right's (its text and numbers show
    # through untouched).
    "scorebox": {
        "enabled": True,
        "x": 723, "y": 20, "w": 474, "h": 179,
        "color_left": "#AE2626", "color_right": "#841319",
        "mid_enabled": True, "color_mid": "#14151B", "mid_position": 0.5,
        "opacity": 0.2, "angle": 0, "squish": 1.0, "steps": 1,
        # The box's background colours, and how softly their edges blend.
        "mask_colors": [{"hex": "#14151B", "tolerance": 1, "enabled": True},
                        {"hex": "#23252D", "tolerance": 1, "enabled": True},
                        {"hex": "#333540", "tolerance": 1, "enabled": True}],
        "feather": 15,
        # Areas always covered, whatever their colour (e.g. the game's team logos).
        "overrides": [{"x": 729, "y": 50, "w": 80, "h": 100, "enabled": True},
                      {"x": 1111, "y": 50, "w": 80, "h": 100, "enabled": True},
                      {"x": 723, "y": 15, "w": 474, "h": 31, "enabled": True}],
        # Under the gradient, the covered area is first filled with this.
        "base_fill": True, "base_color": "#14151B",
        # The side of the gradient of the team whose player is being
        # spectated lights up (opacity and lightness, in percent).
        "boost": {"enabled": True, "opacity_left": 15, "opacity_right": 15,
                  "lightness_left": 10, "lightness_right": 10, "fade": 0.2},
    },

    # Who's being spectated: their scoreboard row turns from idle_color to the
    # game's highlight. One pixel per row, in the name cell.
    "spectated": {"idle_color": "#14151B", "tolerance": 2, "pixels": _rows((444, 1664), SPEC_YS)},

    # The match, shown by the title over the CoD caster HUD (the BO7 In Game
    # Scoreboard): each team's name plate, logo and record line, the series
    # score, and the maps of the series along the bottom.
    "match": {
        "left": {"name": "FUEL ESPORTS", "tricode": "FUEL", "record": "SEASON RECORD: 0-0", "logo": "", "wins": 0},
        "right": {"name": "ST. CLAIR SAINTS", "tricode": "SCC", "record": "SEASON RECORD: 5-4",
                  "logo": "assets/logos/st-clair-saints.png", "wins": 0},
        "best_of": 5,
        # Each map: "NAME // MODE" and who won it ("left", "right" or "").
        "maps": [{"name": n, "winner": ""} for n in (
            "COLOSSUS // HARDPOINT", "COLOSSUS // HARDPOINT", "COLOSSUS // HARDPOINT", "COLOSSUS // HARDPOINT",
            "GRIDLOCK // SEARCH & DESTROY", "COLOSSUS // HARDPOINT", "COLOSSUS // HARDPOINT")],
        "map_count": 7,
        "current_map": 0,
    },
    # Which parts of the title show.
    "title": {"enabled": True, "plates": True, "score_box": True, "best_of_text": True, "maps": True},
    # The title's fonts: installed on this PC (sent to the overlay, so it
    # shows them on any PC); Bebas Neue when one isn't installed.
    "fonts": {"name": "Hitmarker", "tricode": "Hitmarker Normal", "record": "Dharma Gothic E",
              "maps": "Dharma Gothic E", "best_of": "Competition Solid"},
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
                self.data = _merge(DEFAULTS, json.load(f))
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


def from_suite(suite, current=None):
    """Settings from a CoD Scoreboard Suite settings file
    (cod_scoreboard_suite_config.json), over the current ones."""
    out = copy.deepcopy(current or DEFAULTS)
    g = suite.get("global") or {}
    det = suite.get("detector") or {}
    hl = suite.get("scoreboard_highlights") or {}
    go = suite.get("gradient_overlay") or {}
    sw = suite.get("switcher") or {}

    cap = out["capture"]
    if g.get("capture_mode") or det.get("capture_mode"):
        cap["mode"] = "window" if (g.get("capture_mode") or det.get("capture_mode")) == "window" else "screen"
    title = g.get("window_title") or det.get("window_title")
    if title:
        cap["window_title"] = title
    if "monitor_index" in g:
        cap["screen"] = max(0, int(g["monitor_index"]) - 1)
    if "poll_interval_ms" in g:
        cap["poll_ms"] = int(g["poll_interval_ms"])

    if det:
        d = out["detector"]
        d["enabled"] = bool(g.get("require_hud_match", sw.get("require_hud_match", True)))
        d["pixel1"] = {"x": det.get("pixel_x", 960), "y": det.get("pixel_y", 57),
                       "color": det.get("target_color", "#23252D"), "tolerance": det.get("tolerance", 1)}
        d["pixel2"] = {"enabled": bool(det.get("pixel2_enabled", True)), "x": det.get("pixel2_x", 1500),
                       "y": det.get("pixel2_y", 80), "color": det.get("pixel2_target_color", "#333540"),
                       "tolerance": det.get("pixel2_tolerance", 1)}
        d["score_box_only"] = bool(det.get("gradient_only_on_pixel1", False))

    if hl:
        h = out["highlights"]
        h["enabled"] = bool(hl.get("ndi_enabled", True))
        if hl.get("pixels"):
            h["pixels"] = [{"x": p["x"], "y": p["y"], "enabled": bool(p.get("enabled", True)),
                            "tolerance": p.get("tolerance", 1)} for p in hl["pixels"][:8]]
        if hl.get("gradients"):
            h["boxes"] = [{"x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"], "enabled": bool(b.get("enabled", True))}
                          for b in hl["gradients"][:8]]
        if hl.get("swatches"):
            h["mask_colors"] = [{"hex": s["hex"], "tolerance": s.get("tolerance", 0), "enabled": bool(s.get("enabled", True))}
                                for s in hl["swatches"]]
        for ours, theirs in (("color_left", "color_left"), ("color_right", "color_right"), ("base_color", "base_hex"),
                             ("dim_colors", "dim_hexes"), ("use_mask", "use_mask"), ("feather", "feather"),
                             ("opacity", "opacity"), ("center_opacity", "grad_center_opacity_pct"),
                             ("edge_opacity", "grad_edge_opacity_pct"), ("edge_brighten", "edge_brighten_pct"),
                             ("edge_hue", "grad_edge_hue"), ("edge_saturation", "grad_edge_sat"),
                             ("falloff", "grad_falloff"), ("core", "grad_core_pct"), ("reach", "grad_radius_pct"),
                             ("oval", "grad_oval_pct"), ("side_depth", "grad_side_depth_pct"), ("corner", "grad_corner_px"),
                             ("bright_opacity", "highlight_opacity_pct"), ("bright_lightness", "highlight_lum_pct"),
                             ("dim_opacity", "dim_opacity_pct"), ("dim_lightness", "dim_lum_pct")):
            if theirs in hl:
                h[ours] = hl[theirs]
        for side in ("top", "bottom", "left", "right"):
            if f"light_{side}" in hl:
                h["edges"][side] = bool(hl[f"light_{side}"])
        if "smooth_duration" in hl:
            h["fade"] = float(hl["smooth_duration"]) if hl.get("smooth_enabled", True) else 0

    if go:
        s = out["scorebox"]
        s["enabled"] = bool(go.get("ndi_enabled", True))
        if go.get("crop_enabled", True):
            for k in ("x", "y", "w", "h"):
                if f"crop_{k}" in go:
                    s[k] = go[f"crop_{k}"]
        for ours, theirs in (("color_left", "color_left"), ("color_right", "color_right"), ("mid_enabled", "mid_enabled"),
                             ("color_mid", "color_mid"), ("mid_position", "mid_position"), ("opacity", "opacity"),
                             ("angle", "angle_deg"), ("squish", "squish"), ("steps", "gradient_steps"),
                             ("feather", "feather"), ("base_fill", "base_fill_enabled")):
            if theirs in go:
                s[ours] = go[theirs]
        if go.get("swatches"):
            s["mask_colors"] = [{"hex": w["hex"], "tolerance": w.get("tolerance", 1), "enabled": bool(w.get("enabled", True))}
                                for w in go["swatches"]]
        if "overrides" in go:
            s["overrides"] = [{"x": o["x"], "y": o["y"], "w": o["w"], "h": o["h"], "enabled": bool(o.get("enabled", True))}
                              for o in go["overrides"]]
        b = s["boost"]
        b["enabled"] = bool(go.get("switcher_boost_enabled", b["enabled"]))
        for ours, theirs in (("opacity_left", "switcher_opacity_boost_left"), ("opacity_right", "switcher_opacity_boost_right"),
                             ("lightness_left", "switcher_lum_boost_left"), ("lightness_right", "switcher_lum_boost_right")):
            if theirs in go:
                b[ours] = go[theirs]
        if "switcher_smooth_duration" in go:
            b["fade"] = float(go["switcher_smooth_duration"]) if go.get("switcher_smooth_enabled", True) else 0

    if sw:
        sp = out["spectated"]
        sp["idle_color"] = sw.get("idle_color", sp["idle_color"])
        if sw.get("pixels"):
            sp["pixels"] = [{"x": p["x"], "y": p["y"], "enabled": bool(p.get("enabled", True))} for p in sw["pixels"][:8]]
    return out
