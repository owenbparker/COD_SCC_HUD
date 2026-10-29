"""The running app: the scanner reading the game, the player cams, the link
to the GFX Engine, and the web server for the overlay (a browser source in
vMix / OBS) and the control panel."""

import base64
import io
import json
import re
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import detect, fonts
from .cams import CamHub
from .config import DATA_DIR, DEFAULTS, LOGO_DIR, WEB_DIR
from .enginelink import EngineLink, _get, _post, save_logo_bytes
from .log import log, since
from .names import same_player
from .scanner import Hub, Scanner
from .updater import Updater, clean_up_old_update

mimetypes.add_type("font/ttf", ".ttf")

PAGES = {"/control": "control.html", "/overlay": "overlay.html"}


class HudApp:
    def __init__(self, config, demo=False):
        self.config = config
        self.hub = Hub()
        self.scanner = Scanner(config, self.hub, demo=demo)
        self.overlay_window = None   # the desktop app's "Show HUD on screen" window
        self.cams = CamHub()   # player webcams (SCC Player Cam, Call of Duty mode)
        self.updater = Updater()
        self.server = None
        self._config_version = config.version
        self.roster = {}           # side -> the GFX Engine's roster of the side's team ({"team", "found", "members"})
        self._applied = {}         # what of the engine's Global information is in the HUD (logo addresses...)
        self._local_change = 0.0   # when the match was last changed here (Global waits a moment before overriding it)
        self._synced = None        # the engine whose Global information the HUD has taken in (while following)
        self._stop = threading.Event()
        # The SCC GFX Engine: the match and series go to it; its teams can come here.
        self.engine = EngineLink("CoD HUD", "cod", self._engine_snapshot, self._engine_apply_global,
                                 load=lambda: self.config.get().get("gfx_engine"),
                                 save=lambda d: self.config.update({"gfx_engine": d}), log=log)

    def stop(self):
        self._stop.set()
        self.scanner.stop()
        self.engine.stop()
        if self.server:
            self.server.stop()

    def host(self):
        cfg = self.config.get()
        return "0.0.0.0" if cfg.get("allow_network") else cfg["http_host"]

    def set_network(self, enabled):
        self.config.update({"allow_network": bool(enabled)})
        if self.server:
            self.server.restart(self.host())
        log("Other PCs on your network can now load the overlay and connect their cams" if enabled
            else "The overlay is now only reachable from this PC")

    # --- the match ---------------------------------------------------------------

    def changed_match(self, changes):
        """The match changed here (wins, a name...): it goes to the engine at once,
        and the engine's Global information doesn't undo it meanwhile."""
        self._local_change = time.time()
        self.config.update({"match": changes})
        self.changed_config()
        self.engine.send_now()

    def swap_sides(self):
        """The teams change sides: their names, short names, logos, colours and
        series scores. In the GFX Engine's Global information too, when it's
        connected (so following it doesn't swap them back)."""
        cfg = self.config.get()
        m = cfg["match"]
        if self.engine.connected and self.engine.url:
            try:
                g = json.loads(_get(self.engine.url + "/api/global", 5)).get("global") or {}
                keys = ("name", "short", "logo", "color", "score", "game")
                left, right = ({k: (g.get(s) or {}).get(k) for k in keys if k in (g.get(s) or {})} for s in ("left", "right"))
                _post(self.engine.url + "/api/global", {"global": {"left": right, "right": left}}, 5)
                log(f"Swapped the sides in {self.engine.name}'s Global information too")
            except Exception as e:
                log(f"Couldn't swap the sides on the GFX Engine: {getattr(e, 'reason', e)}", "warn")
        self._applied = {"left_logo_url": self._applied.get("right_logo_url"),
                         "right_logo_url": self._applied.get("left_logo_url"),
                         "sponsors": self._applied.get("sponsors")}
        self.roster = {"left": self.roster.get("right"), "right": self.roster.get("left")}
        self.changed_match({"left": m["right"], "right": m["left"]})
        log(f"Sides swapped: {m['right']['name'] or 'right team'} on the left")

    def changed_config(self):
        """Settings changed: the overlay pages pick up the new look."""
        self.hub.touch()

    # --- the players ---------------------------------------------------------------

    def players(self):
        """The 8 players on the scoreboards: [{"team", "slot", "name" (as read),
        "linked": the roster player it's linked to ({"index", "name", "ign"}) or
        None, "also": that player's names}]."""
        names = self.hub.state.get("names") or [""] * 8
        out = []
        for i, name in enumerate(names[:8]):
            side = "left" if i < 4 else "right"
            members = (self.roster.get(side) or {}).get("members") or []
            linked = None
            if name:
                # (as the engine matches them; else allowing for a misread letter)
                linked = next((m for m in members if m.get("ign") and ign_key(m["ign"]) == ign_key(name)), None) or \
                    next((m for m in members if m.get("ign") and same_player(ign_key(m["ign"]), name)), None)
            out.append({"team": side, "slot": i % 4 + 1, "name": name, "linked": linked,
                        "also": [x for x in ((linked or {}).get("name"), (linked or {}).get("ign")) if x]})
        return out

    def publishers(self):
        return self.cams.publishers(self.players(), self.config.get()["cams"].get("assign"))

    def refresh_roster(self):
        """The two teams' rosters from the GFX Engine (for linking in-game names)."""
        try:
            reply = self.engine.roster()
        except ValueError:
            return self.roster
        self.roster = {side: {k: s.get(k) for k in ("team", "found", "members")}
                       for side, s in (reply.get("sides") or {}).items()}
        return self.roster

    def player_links(self, link=None):
        """For the Players page ("Save IGN to player", as in the VALORANT HUD):
        each side's team and roster, and the in-game names read, each with the
        roster player it's linked to."""
        if link:
            reply = self.engine.roster(link)
            self.roster = {side: {k: s.get(k) for k in ("team", "found", "members")}
                           for side, s in (reply.get("sides") or {}).items()}
        elif self.engine.connected:
            self.refresh_roster()
        else:
            raise ValueError("Connect to a GFX Engine first")
        players = self.players()
        sides = {}
        for side in ("left", "right"):
            r = self.roster.get(side) or {}
            members = r.get("members") or []
            sides[side] = {"team": r.get("team") or self.config.get()["match"][side]["name"], "found": bool(r.get("found")),
                           "members": members,
                           "players": [{"ign": p["name"], "linked": members.index(p["linked"]) if p["linked"] in members else None}
                                       for p in players if p["team"] == side and p["name"]]}
        return {"sides": sides}

    def cam_post(self, path, body, client=None):
        """Webcam messages (also from other PCs on the network)."""
        if path == "/api/cam/poll":
            result = self.cams.poll(str(body.get("id") or ""), body.get("riot_id"), body.get("label"), body.get("wait", 20))
            if result["riot_id"]:
                result["player"] = next((p["player"] for p in self.publishers() if p["id"] == body.get("id")), None)
                if result["player"]:
                    result["player"]["team_name"] = self.config.get()["match"][result["player"]["team"]]["name"]
            return result
        if path == "/api/cam/send":
            self.cams.send(str(body.get("to") or ""), str(body.get("from") or ""), body.get("data"), client)
            return {"ok": True}
        if path == "/api/cam/list":
            # The cams connected now, for the GFX Engine's Camera Control Room.
            cfg = self.config.get()
            cams = []
            for p in self.cams.all_cams(self.players(), cfg["cams"].get("assign")):
                who = p["player"] or {}
                team = cfg["match"].get(who.get("team")) or {} if who.get("team") in ("left", "right") else {}
                cams.append({"id": p["id"], "label": p["label"], "riot_id": p["riot_id"],
                             "team": team.get("tricode") or team.get("name") or "",
                             "name": who.get("name") or p["riot_id"] or "No name yet"})
            return {"game": "cod", "cams": cams}
        return None

    # --- the overlay and control panel ------------------------------------------------

    def info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        lan = lan_ip() if cfg.get("allow_network") else None
        return {"config": cfg, "defaults": DEFAULTS, "scanner": self.scanner.info(), "update": self.updater.info(),
                "players": self.players(), "cams": self.publishers(),
                "overlay_window": self.overlay_window.info() if self.overlay_window else {"available": False},
                "port": port, "lan_url": f"http://{lan}:{port}" if lan else None, "data_dir": DATA_DIR}

    def look(self):
        """What the overlay needs of the settings."""
        cfg = self.config.get()
        return {"match": cfg["match"], "overlay": cfg["overlay"], "fonts": cfg["fonts"],
                "sponsors": cfg["sponsors"]["logos"], "cams": cfg["cams"]["enabled"]}

    def logos(self):
        """The team logos to pick from: the bundled ones and those added here."""
        found = []
        for folder, prefix in ((os.path.join(WEB_DIR, "assets", "logos"), "assets/logos/"), (LOGO_DIR, "logos/")):
            if os.path.isdir(folder):
                found += [prefix + n for n in sorted(os.listdir(folder)) if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif"))]
        return found

    def add_logo(self, name, data_url):
        """Saves an uploaded logo; returns its path for the settings."""
        m = re.fullmatch(r"data:image/(png|jpeg|webp|svg\+xml);base64,(.+)", data_url or "", re.S)
        if not m:
            raise ValueError("send the logo as a PNG, JPG, WebP or SVG image")
        ext = {"png": ".png", "jpeg": ".jpg", "webp": ".webp", "svg+xml": ".svg"}[m.group(1)]
        stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", os.path.splitext(os.path.basename(name or "logo"))[0]).strip() or "logo"
        os.makedirs(LOGO_DIR, exist_ok=True)
        with open(os.path.join(LOGO_DIR, stem + ext), "wb") as f:
            f.write(base64.b64decode(m.group(2)))
        log(f"Added the logo {stem}{ext}")
        return "logos/" + stem + ext

    # --- the GFX Engine -------------------------------------------------------------

    def _engine_snapshot(self):
        """What the GFX Engine gets: the match and players as flat fields, and the series."""
        cfg = self.config.get()
        match = cfg["match"]
        fields = {}
        for side in ("left", "right"):
            t = match[side]
            fields.update({f"{side}_name": t.get("name") or "", f"{side}_short": t.get("tricode") or "",
                           f"{side}_wins": int(t.get("wins") or 0), f"{side}_logo": t.get("logo") or "",
                           f"{side}_color": t.get("color") or ""})
        fields["best_of"] = int(match.get("best_of") or 0)
        players = self.players()
        for p in players:
            fields[f"{p['team']}_p{p['slot']}_ign"] = p["name"]
            fields[f"{p['team']}_p{p['slot']}_name"] = (p["linked"] or {}).get("name") or p["name"]
        state = self.hub.state
        row = state.get("spec", -1)
        spec = players[row] if 0 <= row < len(players) else None
        fields.update({"hud": state.get("hud") or "off",
                       "spectated_row": row + 1 if spec else 0,
                       "spectated_side": spec["team"] if spec else "",
                       "spectated_name": (spec["linked"] or {}).get("name") or spec["name"] if spec else state.get("spec_name") or ""})
        series = {side: {"name": match[side].get("name") or "", "short": match[side].get("tricode") or "",
                         "score": int(match[side].get("wins") or 0), "color": match[side].get("color") or ""}
                  for side in ("left", "right")}
        series["best_of"] = int(match.get("best_of") or 0)
        port = self.server.port if self.server else cfg["http_port"]
        if cfg["global"].get("follow", True) and self._synced != self.engine.url:
            series = {}   # (following: the engine's series first, so connecting doesn't overwrite it with the HUD's)
        return {"fields": fields, "series": series, "hud": {"port": port, "network": bool(cfg.get("allow_network"))}}

    def _engine_apply_global(self, g, fetch, force=True):
        """The GFX Engine's Global information into the HUD: the teams' names,
        short names, colours, logos and series scores, how many it's best of,
        and the sponsors. force: logos fetched again even when unchanged.
        Returns a short summary of what changed ("" for nothing)."""
        cfg = self.config.get()
        changes, done = {}, []
        for side in ("left", "right"):
            t, have = g.get(side) or {}, cfg["match"][side]
            team = {}
            for ours, theirs in (("name", "name"), ("tricode", "short"), ("color", "color")):
                value = str(t.get(theirs) or "")[:6 if ours == "tricode" else 60]
                if value and value != have.get(ours):
                    team[ours] = value
            if "score" in t and int(t.get("score") or 0) != int(have.get("wins") or 0):
                team["wins"] = int(t.get("score") or 0)
            url = t.get("logo_url") or ""
            if url and (force or url != self._applied.get(f"{side}_logo_url")):
                try:
                    name = save_logo_bytes(LOGO_DIR, f"engine-{t.get('short') or t.get('name') or side}", fetch(url))
                    self._applied[f"{side}_logo_url"] = url
                    if f"logos/{name}" != have.get("logo"):
                        team["logo"] = f"logos/{name}"
                except Exception as e:
                    log(f"Couldn't fetch the {side} logo from the GFX Engine: {e}", "warn")
            elif not url and t.get("name") and have.get("logo") and (self._applied.get(f"{side}_logo_url") or "name" in team):
                team["logo"] = ""      # (the engine's team has no logo)
                self._applied[f"{side}_logo_url"] = ""
            if team:
                changes[side] = team
                done.append(f"{side}: " + ", ".join(f"{k} {v}" if k in ("name", "wins") else k for k, v in team.items()))
        if g.get("best_of") and int(g["best_of"]) != int(cfg["match"]["best_of"]):
            changes["best_of"] = int(g["best_of"])
            done.append(f"best of {changes['best_of']}")
        sponsors = [sp for sp in g.get("sponsors") or [] if sp.get("logo_url")]
        key = json.dumps([[sp["logo_url"], sp.get("secs")] for sp in sponsors])
        if force or key != self._applied.get("sponsors"):
            logos = []
            for i, sp in enumerate(sponsors, 1):
                base = os.path.splitext(os.path.basename(unquote(sp["logo_url"].split("?")[0])))[0]
                try:
                    logos.append({"src": "logos/" + save_logo_bytes(LOGO_DIR, f"sponsor-{i}-{base}", fetch(sp["logo_url"])),
                                  "secs": max(2, int(float(sp.get("secs") or 10)))})
                except Exception as e:
                    log(f"Couldn't fetch the sponsor {base} from the GFX Engine: {e}", "warn")
            self._applied["sponsors"] = key
            if logos != cfg["sponsors"]["logos"]:
                self.config.set("sponsors.logos", logos)
                done.append(f"{len(logos)} sponsor{'s' if len(logos) != 1 else ''}")
        if changes:
            self.config.update({"match": changes})
        self._synced = self.engine.url
        if changes or done:
            self.changed_config()
        if force and not done:
            if not any((g.get(s) or {}).get("name") for s in ("left", "right")):
                raise ValueError("The engine's Global configuration has no team names or logos yet")
            return "already up to date"
        return ", ".join(done)

    def _follow(self):
        """While connected and following: the engine's Global information is
        checked every few seconds and taken in when it changes; the rosters
        (for linking in-game names) every 10 seconds."""
        roster_at = 0.0
        while not self._stop.wait(1.5):
            e = self.engine
            if not (e.connected and e.url):
                continue
            if time.time() - roster_at > 10:
                roster_at = time.time()
                self.refresh_roster()
            if not self.config.get()["global"].get("follow", True) or time.time() - self._local_change < 4:
                continue
            try:
                g = json.loads(_get(e.url + "/api/global", 5)).get("global") or {}
                summary = self._engine_apply_global(g, lambda u: _get(u, 10), force=False)
            except Exception:
                continue   # (the engine's away: the link's status says so)
            if summary:
                log(f"From {e.name}'s Global information: {summary}")

    def start_following(self):
        threading.Thread(target=self._follow, daemon=True, name="follow-global").start()

    def frame_jpeg(self, width=960):
        frame = self.scanner.frame
        if frame is None:
            return None
        from PIL import Image
        img = Image.fromarray(frame)
        img = img.resize((width, width * 9 // 16), Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def pixel(self, x, y):
        frame = self.scanner.frame
        rgb = detect.pixel(frame, x, y) if frame is not None else None
        return {"x": x, "y": y, "hex": detect.rgb_hex(rgb) if rgb else None}


def ign_key(v):
    """An in-game ID as the GFX Engine matches it (its teams.ign_key)."""
    return str(v or "").split("#")[0].strip().lower()


def lan_ip():
    """This PC's address on the local network (no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def _is_local(address):
    return address in ("127.0.0.1", "::1") or address.startswith("127.")


def web_file(rel):
    """Path on disk for a URL path; uploaded logos are served as logos/...,
    everything else comes from the web folder. Paths that climb out are refused."""
    rel = rel.replace("\\", "/").lstrip("/")
    base = WEB_DIR
    if rel.startswith("logos/"):
        base, rel = LOGO_DIR, rel[len("logos/"):]
    path = os.path.normpath(os.path.join(base, rel))
    if not path.startswith(os.path.normpath(base) + os.sep) or not os.path.isfile(path):
        return None
    return path


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _send(self, body, ctype="application/json", status=200):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(json.dumps(data), status=status)

        def _file(self, rel, path=None):
            path = path or (web_file(rel) if rel else None)
            if not path:
                return self._json({"error": "not found"}, 404)
            with open(path, "rb") as f:
                self._send(f.read(), mimetypes.guess_type(path)[0] or "application/octet-stream")

        def _stream(self):
            """Server-sent events: the reading every time it changes, the cams
            connected (checked every second), and the look when the settings change."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b"retry: 1000\n\n")
            version, config_version, sent, quiet = -1, None, None, 0.0
            try:
                while True:
                    new, state = app.hub.wait(version, timeout=1)
                    cams = app.publishers()
                    msg = dict(state, cams=cams, demo=app.scanner.demo,
                               detector=app.config.get()["detector"].get("enabled", True))
                    if app.config.version != config_version:
                        config_version = app.config.version
                        msg["look"] = app.look()
                    elif new == version and msg == sent:
                        quiet += 1
                        if quiet >= 15:
                            quiet = 0
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                        continue
                    version, sent, quiet = new, {k: v for k, v in msg.items() if k != "look"}, 0
                    self.wfile.write(b"data: " + json.dumps(msg).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (ConnectionError, OSError):
                pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # (player cams post here from the Player Cam app's own page)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            query = parse_qs(url.query)
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/control")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif path in PAGES:
                self._file(PAGES[path])
            elif path == "/api/stream":
                self._stream()
            elif path == "/api/state":
                self._json(app.info())
            elif path == "/api/engine":
                self._json(app.engine.status())
            elif path == "/api/log":
                self._json({"lines": since(int((query.get("after") or ["0"])[0]))})
            elif path == "/api/frame.jpg":
                body = app.frame_jpeg(min(1920, int((query.get("width") or ["960"])[0])))
                if body is None:
                    return self._json({"error": "no picture yet"}, 404)
                self._send(body, "image/jpeg")
            elif path == "/api/fonts":
                self._json({"fonts": fonts.families()})
            elif path.startswith("/fonts/local/"):
                # An installed font, for the title's text on any PC.
                self._file(None, fonts.path_of(unquote(path[len("/fonts/local/"):])))
            elif path == "/api/logos":
                self._json({"logos": app.logos()})
            elif path == "/api/players":
                try:
                    self._json(app.player_links())
                except ValueError as e:
                    self._json({"error": str(e)})
            elif path == "/api/pixel":
                self._json(app.pixel(int(query["x"][0]), int(query["y"][0])))
            else:
                self._file(unquote(path))

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if path in ("/api/cam/poll", "/api/cam/send", "/api/cam/list"):
                # Player cams, from any PC on the network.
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                    return self._json(app.cam_post(path, body, self.client_address[0]))
                except (ValueError, TypeError, AttributeError) as e:
                    return self._json({"error": str(e)}, 400)
            if not _is_local(self.client_address[0]):
                return self._json({"error": "settings can only be changed on the HUD's own PC"}, 403)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if path.startswith("/api/engine/"):
                    # Connect to GFX Engine (see enginelink.py).
                    reply = app.engine.handle("POST", path, body)
                    if reply is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json(reply)
                if path == "/api/config":
                    # {"path": "overlay.cam", "value": false} or {"changes": {...}}
                    if "path" in body:
                        app.config.set(body["path"], body["value"])
                    else:
                        app.config.update(body.get("changes") or {})
                    app.changed_config()
                elif path == "/api/match":
                    # {"left": {"wins": 2}, ...}: the match, changed here
                    app.changed_match(body.get("changes") or {})
                elif path == "/api/swap":
                    app.swap_sides()
                elif path == "/api/names/clear":
                    app.scanner.names.clear()
                elif path == "/api/players/link":
                    # Save IGN to player: {"side", "player" (roster index), "ign"}
                    return self._json(app.player_links({"side": body.get("side"), "player": body.get("player"),
                                                        "ign": body.get("ign")}))
                elif path == "/api/cams/assign":
                    # {"name": in-game name, "cam": cam's name, or "" for automatic}
                    assign = dict(app.config.get()["cams"].get("assign") or {})
                    name = str(body.get("name") or "")
                    if body.get("cam"):
                        assign[name] = str(body["cam"])
                    else:
                        assign.pop(name, None)
                    app.config.set("cams.assign", assign)
                    app.changed_config()
                elif path == "/api/reset":
                    section = body.get("section")
                    if section not in DEFAULTS or not isinstance(DEFAULTS[section], dict):
                        raise ValueError(f"no settings called {section!r}")
                    app.config.set(section, DEFAULTS[section])
                    app.changed_config()
                    log(f"Reset the {section} settings")
                elif path == "/api/logo":
                    logo = app.add_logo(body.get("name"), body.get("data"))
                    if body.get("team") in ("left", "right"):
                        app.changed_match({body["team"]: {"logo": logo}})
                elif path == "/api/demo":
                    app.scanner.set_demo(bool(body.get("on")))
                elif path == "/api/overlay-window":
                    if not app.overlay_window:
                        raise ValueError("Show HUD on screen needs the Windows desktop app")
                    app.overlay_window.set(bool(body.get("open")), body.get("screen"))
                elif path == "/api/network":
                    app.set_network(bool(body.get("on")))
                elif path == "/api/update/check":
                    app.updater.check()
                elif path == "/api/update/install":
                    app.updater.install_in_background()
                else:
                    return self._json({"error": "not found"}, 404)
                self._json(app.info())
            except (ValueError, KeyError, IndexError, TypeError, OSError) as e:
                self._json({"error": str(e)}, 400)

    return Handler


class Server:
    """The HTTP server in a background thread; can be restarted on a new host."""

    def __init__(self, app, host, port):
        self.app, self.port = app, port
        self.httpd = None
        self._start(host)

    def _start(self, host):
        self.httpd = ThreadingHTTPServer((host, self.port), make_handler(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.host = host

    def restart(self, host):
        if host == self.host:
            return
        old = self.httpd
        old.shutdown()
        old.server_close()
        self._start(host)

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def start(app):
    """Start the web server and the scanner. Raises OSError if the port is taken."""
    port = app.config.get()["http_port"]
    app.server = Server(app, app.host(), port)
    app.scanner.start()
    app.start_following()
    threading.Thread(target=fonts.installed, daemon=True).start()   # (the font list, ready for the panel)
    clean_up_old_update()
    app.updater.check_in_background()
    log(f"CoD HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    return app.server
