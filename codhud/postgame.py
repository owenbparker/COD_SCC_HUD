"""The post-game stats: read off Call of Duty's end-of-game scoreboard (the
CDL one: both teams' tables, KILLS/DEATHS, ASSISTS, NON-TRADED KILLS,
HIGHEST STREAK, DAMAGE, then the mode's own columns).

The screen is found by its layout: under each team's name a coloured line
right across the table (the team's colour), and grey lines down the table
between the names, the first columns and the mode's columns. The numbers
are read by matching each character against the game's own digits (in
glyphs.py, cut from a real screenshot): no text recognition needed, and no
trouble with lone digits. The names (players' and teams') are read with
Windows' text recognition when it's there; without it the players are
named from the in-game scoreboards, in order.

    python -m codhud.postgame screenshot.png     read a saved screenshot
"""

import difflib
import re
import threading
import time

import numpy as np

import os

from .config import WEB_DIR
from .glyphs import GLYPHS, H as GH, W as GW
from .log import log

# A real end-of-game scoreboard (Hardpoint), for the build's checks.
SAMPLE = os.path.join(WEB_DIR, "assets", "demo", "postgame.png")

# Where things are on a 1920x1080 picture.
TABLE_X = (200, 1820)       # the coloured line under a team's name spans this
DIVIDERS = (257, 670, 1213)  # the grey lines down the table
ROW_DY, ROW_STEP, ROWS = 34, 60, 4   # first row's middle below the line, between rows, rows per team
ROW_H = 40
# Each number's column: (field, left, right).
COLUMNS = (("kd", 676, 772), ("assists", 782, 876), ("ntk", 888, 982), ("streak", 994, 1088), ("damage", 1100, 1194))
NAME_X = (322, 664)          # the player's name (after the number in front of it)
TEAM_NAME = (196, 664, -46, -6)   # the team's name: x from, x to, y from/to the line
STATS = ("kills", "deaths", "assists", "damage", "ntk", "streak")
READ_EVERY = 1.0             # seconds between looks for the screen
STALE_AFTER = 60             # seconds of the caster HUD back up (the next game) before the stats stop going out


# --- finding the screen ------------------------------------------------------------

def find_lines(frame):
    """The tops of the coloured lines under the teams' names (y, top to
    bottom), where the grey lines run down the table below them."""
    f = frame.astype(np.int16)
    band = f[:, TABLE_X[0]:TABLE_X[1]:4]
    mean = band.mean(axis=1)
    flat = band.std(axis=1).max(axis=1) < 14
    out = []
    y = 20
    while y < f.shape[0] - 260:
        if flat[y] and flat[y + 1] and np.abs(mean[y] - mean[y - 6]).max() > 45 and np.abs(mean[y + 1] - mean[y + 8]).max() > 45 \
                and _dividers(f, y):
            out.append(y)
            y += ROW_STEP * ROWS
        else:
            y += 1
    return out


def _dividers(f, y):
    """The grey lines down the table under a line at y."""
    rows = f[y + 12:y + 12 + ROW_STEP * ROWS - 30].mean(axis=2)
    for d in DIVIDERS:
        x = d - 4 + int(rows[:, d - 4:d + 5].mean(axis=0).argmax())    # (a pixel or two either way)
        line = rows[:, x]
        side = (rows[:, x - 5] + rows[:, x + 5]) / 2
        if not ((line - side > 25).mean() > 0.85 and line.std() < 12):
            return False
    return True


def is_postgame(frame):
    return frame is not None and len(find_lines(frame)) >= 2


# --- reading the numbers ------------------------------------------------------------

def _gray(cell):
    return cell.astype(np.float32).mean(axis=2) if cell.ndim == 3 else cell.astype(np.float32)


def _ink(cell):
    """Light text on the table's dark background: True where it's ink."""
    g = _gray(cell)
    bg = float(np.median(g))
    return (g - bg) > max(45.0, (float(g.max()) - bg) * 0.4), g, bg


def segments(cell):
    """The pieces of text in a cell: [(x0, x1, y0, y1)], left to right (a
    piece can be two characters run together in a scaled picture)."""
    ink = _ink(cell)[0]
    cols = ink.sum(axis=0)
    out, start = [], None
    for x, c in enumerate(list(cols) + [0]):
        if c and start is None:
            start = x
        elif not c and start is not None:
            if ink[:, start:x].sum() >= 4:
                out.append(_box(ink, start, x))
            start = None
    return out


def _box(ink, x0, x1):
    ys = np.where(ink[:, x0:x1].any(axis=1))[0]
    return (x0, x1, int(ys[0]), int(ys[-1]) + 1) if len(ys) else (x0, x1, 0, 0)


def glyph(cell, seg):
    """A character cut out and scaled to the templates' size (0..1 ink)."""
    from PIL import Image
    x0, x1, y0, y1 = seg
    _, g, bg = _ink(cell)
    top = float(g[y0:y1, x0:x1].max()) - bg
    piece = np.clip((g[y0:y1, x0:x1] - bg) / max(60.0, top * 0.65), 0, 1)
    img = Image.fromarray((piece * 255).astype(np.uint8), "L").resize((GW, GH), Image.BILINEAR)
    return np.asarray(img, np.float32) / 255


def match(cell, seg, height):
    """(character, distance): the template it looks most like."""
    x0, x1, y0, y1 = seg
    if y1 - y0 < 2:
        return None, 9.0
    g = glyph(cell, seg)
    aspect = (x1 - x0) / max(1, y1 - y0)
    tall = (y1 - y0) / max(1, height)
    best = (None, 9.0)
    for ch, looks in GLYPHS.items():
        for tmpl, t_aspect, t_tall in looks:
            d = float(np.abs(g - tmpl).mean()) + abs(aspect - t_aspect) * 0.4 + abs(tall - t_tall) * 0.6
            if d < best[1]:
                best = (ch, d)
    return best


LIMIT = 0.25     # (how unlike the game's characters a piece can be and still count)


def split(cell, seg, height, ink):
    """A piece read as one or more characters: (text, worst distance). Two
    characters run together are cut where the ink is thinnest."""
    ch, d = match(cell, seg, height)
    x0, x1 = seg[0], seg[1]
    if d <= LIMIT or x1 - x0 < height * 0.6:
        return (ch or "", d)
    cols = ink[:, x0:x1].sum(axis=0)
    cuts = [x0] + [x0 + i for i in range(2, len(cols) - 1) if cols[i] <= max(1, cols.max() * 0.35)] + [x1]
    best = {0: ("", 0.0)}
    for j in range(1, len(cuts)):
        for i in range(j):
            if i not in best or not (height * 0.15 <= cuts[j] - cuts[i] <= height * 0.9):
                continue
            c, dd = match(cell, _box(ink, cuts[i], cuts[j]), height)
            if dd > LIMIT:
                continue
            text, worst = best[i]
            cand = (text + c, max(worst, dd))
            if j not in best or (len(cand[0]), cand[1]) < (len(best[j][0]), best[j][1]):
                best[j] = cand
    return best.get(len(cuts) - 1, (ch or "", d))


def read_text(cell):
    """The number(s) in a cell as text ("17/10"), or None when a character
    doesn't look like any of the game's."""
    segs = segments(cell)
    if not segs:
        return ""
    ink = _ink(cell)[0]
    height = max(s[3] - s[2] for s in segs)
    out = ""
    for s in segs:
        if s[3] - s[2] < height * 0.3:
            continue     # (a speck)
        text, d = split(cell, s, height, ink)
        if d > LIMIT:
            return None
        out += text
    return out


def number(text):
    return int(text) if text and text.isdigit() else None


def read_row(frame, y):
    """A player's numbers on the row whose middle is at y: {"kills", ...},
    None for an empty row, or {"error": ...} when one doesn't read."""
    top, bottom = y - ROW_H // 2, y + ROW_H // 2
    texts = {}
    for field, x0, x1 in COLUMNS:
        texts[field] = read_text(frame[top:bottom, x0:x1])
    if not any(texts.values()):
        return None
    m = re.fullmatch(r"(\d+)/(\d+)", texts["kd"] or "")
    row = {"kills": int(m.group(1)) if m else None, "deaths": int(m.group(2)) if m else None,
           **{k: number(texts[k]) for k in ("assists", "ntk", "streak", "damage")}}
    bad = [k for k in ("kills", "deaths", "assists", "damage") if row[k] is None]
    if bad:
        return {"error": f"couldn't read {', '.join(bad)} ({texts})"}
    return row


# --- reading the names --------------------------------------------------------------

def read_words(frame, box, ocr):
    """Text in a box (x0, y0, x1, y1) with Windows' text recognition, words in order."""
    from .names import ink
    x0, y0, x1, y1 = box
    cell = frame[y0:y1, x0:x1]
    text = " ".join(t for t, _, _ in sorted(ocr.words(ink(cell)), key=lambda w: w[1])).strip()
    if not text:
        # (a short name in a wide cell can go unread: again, cut to the text and bigger)
        light = np.where(_gray(cell) - float(np.median(_gray(cell))) > 70)
        if len(light[1]):
            cx0, cx1 = max(0, int(light[1].min()) - 10), min(cell.shape[1], int(light[1].max()) + 11)
            text = " ".join(t for t, _, _ in sorted(ocr.words(ink(cell[:, cx0:cx1], scale=4)), key=lambda w: w[1])).strip()
    return text


def read(frame, ocr=None):
    """The post-game screen -> {"teams": [top, bottom]} where each team is
    {"name", "players": [{"name", "kills", "deaths", "assists", "damage",
    "ntk", "streak"}]}; None when it isn't on screen; raises ValueError when
    it is but doesn't read."""
    from .names import clean_name
    lines = find_lines(frame)
    if len(lines) < 2:
        return None
    teams = []
    for line in lines[:2]:
        name = ""
        if ocr:
            x0, x1, dy0, dy1 = TEAM_NAME
            name = read_words(frame, (x0, max(0, line + dy0), x1, line + dy1), ocr)
        players = []
        for k in range(ROWS):
            y = line + ROW_DY + k * ROW_STEP
            row = read_row(frame, y)
            if row is None:
                continue
            if "error" in row:
                raise ValueError(f"{'top' if not teams else 'bottom'} team, row {k + 1}: {row['error']}")
            row["name"] = clean_name(read_words(frame, (NAME_X[0], y - ROW_H // 2, NAME_X[1], y + ROW_H // 2), ocr)) if ocr else ""
            players.append(row)
        teams.append({"name": re.sub(r"\s+", " ", name), "players": players})
    if not any(t["players"] for t in teams):
        raise ValueError("no players' numbers on the scoreboard")
    return {"teams": teams}


# --- whose side ----------------------------------------------------------------------

def _team_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def name_likeness(a, b):
    a, b = _team_key(a), _team_key(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def sides(teams, match, live_names):
    """Which side (left, right) the top table is: by the teams' names, else
    by the players' names (the in-game scoreboards: left team first), else
    the top one is the left team."""
    from .names import same_player
    top, bottom = teams[0], teams[1]
    straight = name_likeness(top["name"], match["left"].get("name")) + name_likeness(bottom["name"], match["right"].get("name"))
    crossed = name_likeness(top["name"], match["right"].get("name")) + name_likeness(bottom["name"], match["left"].get("name"))
    if abs(straight - crossed) >= 0.3:
        return ("left", "right") if straight > crossed else ("right", "left")
    left, right = [n for n in live_names[:4] if n], [n for n in live_names[4:8] if n]

    def hits(team, names):
        return sum(any(same_player(p["name"], n) for n in names) for p in team["players"] if p["name"])
    straight = hits(top, left) + hits(bottom, right)
    crossed = hits(top, right) + hits(bottom, left)
    return ("right", "left") if crossed > straight else ("left", "right")


def assign(result, match, live_names):
    """The read screen with each table on its side, players without a name
    read named from the in-game scoreboards in order: {"left": team, "right": team}."""
    order = sides(result["teams"], match, live_names)
    out = {}
    for team, side in zip(result["teams"], order):
        names = live_names[:4] if side == "left" else live_names[4:8]
        players = [dict(p, name=p["name"] or (names[i] if i < len(names) else "")) for i, p in enumerate(team["players"])]
        out[side] = {"name": team["name"], "players": players}
    return out


# --- watching for the screen ----------------------------------------------------------

class PostgameReader:
    """Looks at the scanner's picture every second while the caster HUD is
    away; the post-game screen read the same twice in a row becomes the
    stats (and goes to the GFX Engine)."""

    def __init__(self, on_read=None):
        self.result = None      # {"at", "from", "left", "right", "stale"}
        self.status = "Waiting for the end-of-game scoreboard"
        self.on_read = on_read
        self._last = None
        self._hud_on_since = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def info(self):
        with self._lock:
            return {"status": self.status, "result": self.result}

    def start(self, scanner, config):
        threading.Thread(target=self._run, args=(scanner, config), daemon=True, name="postgame").start()
        return self

    def stop(self):
        self._stop.set()

    def clear(self):
        with self._lock:
            self.result, self._last = None, None
            self.status = "Cleared: waiting for the end-of-game scoreboard"
        log("Post-game stats cleared")

    def swap(self):
        with self._lock:
            if self.result:
                self.result = dict(self.result, left=self.result["right"], right=self.result["left"])
        log("Post-game stats: teams swapped")

    def _run(self, scanner, config):
        while not self._stop.wait(READ_EVERY):
            cfg = config.get()
            if not cfg["postgame"].get("enabled", True):
                continue
            hud, frame = scanner.hub.state.get("hud"), scanner.frame
            now = time.time()
            if hud == "on":
                self._hud_on_since = self._hud_on_since or now
                with self._lock:
                    if self.result and not self.result["stale"] and now - self._hud_on_since > STALE_AFTER:
                        self.result["stale"] = True
                        self.status = "Next game started: the last game's stats stay on the GFX Engine"
                continue
            self._hud_on_since = None
            if frame is None:
                continue
            try:
                self.look(frame, cfg, scanner.names)
            except Exception as e:     # (keep going: the next picture may read)
                self.status = f"Read error: {e}"

    def look(self, frame, cfg, names):
        ocr = names._ocr if names.available() else None
        try:
            got = read(frame, ocr)
        except ValueError as e:
            self.status = f"Scoreboard on screen but not read: {e}"
            self._last = None
            return
        if got is None:
            self._last = None
            return
        if got != self._last:
            self._last = got      # (read it again: the screen may still be sliding in)
            return
        live = names.info()["names"]
        sided = assign(got, cfg["match"], live)
        if self.result and not self.result["stale"] and all(self.result[s] == sided[s] for s in ("left", "right")):
            return
        self.take(sided, "the game")

    def take(self, sided, source):
        with self._lock:
            self.result = {"at": time.time(), "from": source, "stale": False, **sided}
            n = sum(len(sided[s]["players"]) for s in ("left", "right"))
            self.status = f"Read {n} players' stats from {source} at {time.strftime('%H:%M')}"
        log(f"Post-game stats read from {source}: " + "; ".join(
            f"{sided[s]['name'] or s}: " + ", ".join(f"{p['name'] or '?'} {p['kills']}/{p['deaths']}/{p['assists']} {p['damage']}"
                                                 for p in sided[s]["players"]) for s in ("left", "right")))
        if self.on_read:
            self.on_read()


def load_picture(data):
    """Picture bytes (a screenshot) -> a 1920x1080 frame."""
    import io
    from PIL import Image
    from .capture import to_reference
    return to_reference(Image.open(io.BytesIO(data)))


def main():
    import sys
    from PIL import Image
    from .capture import to_reference
    from .names import WindowsOcr
    try:
        ocr = WindowsOcr()
    except Exception as e:
        ocr = None
        print(f"(no text recognition: {e})")
    for path in sys.argv[1:]:
        got = read(to_reference(Image.open(path)), ocr)
        print(path)
        for team in (got or {}).get("teams") or []:
            print(f"  {team['name']}")
            for p in team["players"]:
                print(f"    {p['name']:<20} K {p['kills']:>3}  D {p['deaths']:>3}  A {p['assists']:>3}  DMG {p['damage']:>5}"
                      f"  NTK {p['ntk']}  streak {p['streak']}")


if __name__ == "__main__":
    main()
