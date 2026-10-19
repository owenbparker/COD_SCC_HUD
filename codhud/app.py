"""The running app: the scanner reading the game, and the web server for the
overlay (a browser source in vMix / OBS) and the control panel."""

import base64
import io
import json
import re
import mimetypes
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import detect, fonts
from .config import DATA_DIR, DEFAULTS, LOGO_DIR, WEB_DIR, from_suite
from .enginelink import EngineLink, save_logo_bytes
from .log import log, since
from .scanner import Hub, Scanner
from .updater import Updater, clean_up_old_update

mimetypes.add_type("font/ttf", ".ttf")

PAGES = {"/control": "control.html", "/overlay": "overlay.html"}


class HudApp:
    def __init__(self, config, demo=False):
        self.config = config
        self.hub = Hub()
        self.scanner = Scanner(config, self.hub, demo=demo)
        self.updater = Updater()
        self.server = None
        self._config_version = config.version
        # The SCC GFX Engine: the match and series go to it; its teams can come here.
        self.engine = EngineLink("CoD HUD", "cod", self._engine_snapshot, self._engine_apply_global,
                                 load=lambda: self.config.get().get("gfx_engine"),
                                 save=lambda d: self.config.update({"gfx_engine": d}), log=log)

    def stop(self):
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
        log("Other PCs on your network can now load the overlay" if enabled else "The overlay is now only reachable from this PC")

    def changed_config(self):
        """Settings changed: the overlay pages pick up the new look."""
        self.hub.touch()

    def info(self):
        cfg = self.config.get()
        port = self.server.port if self.server else cfg["http_port"]
        lan = lan_ip() if cfg.get("allow_network") else None
        return {"config": cfg, "defaults": DEFAULTS, "scanner": self.scanner.info(), "update": self.updater.info(),
                "port": port, "lan_url": f"http://{lan}:{port}" if lan else None, "data_dir": DATA_DIR}

    def look(self):
        """What the overlay needs of the settings."""
        cfg = self.config.get()
        h = dict(cfg["highlights"])
        h.pop("pixels", None)
        return {"highlights": h, "scorebox": cfg["scorebox"], "match": cfg["match"], "title": cfg["title"],
                "fonts": cfg["fonts"]}

    def logos(self):
        """The team logos to pick from: the bundled ones and those added here."""
        found = []
        for folder, prefix in ((os.path.join(WEB_DIR, "assets", "logos"), "assets/logos/"), (LOGO_DIR, "logos/")):
            if os.path.isdir(folder):
                found += [prefix + n for n in sorted(os.listdir(folder)) if n.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".svg"))]
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

    def _engine_snapshot(self):
        """What the GFX Engine gets: the match as flat fields, and the series."""
        cfg = self.config.get()
        match, hl = cfg["match"], cfg["highlights"]
        fields = {}
        for side in ("left", "right"):
            t = match[side]
            fields.update({f"{side}_name": t.get("name") or "", f"{side}_short": t.get("tricode") or "",
                           f"{side}_record": t.get("record") or "", f"{side}_wins": int(t.get("wins") or 0),
                           f"{side}_logo": t.get("logo") or "", f"{side}_color": hl.get(f"color_{side}") or ""})
        maps = match.get("maps") or []
        count = max(0, min(int(match.get("map_count") or len(maps)), len(maps)))
        current = int(match.get("current_map") or 0)
        fields.update({"best_of": int(match.get("best_of") or 0), "map_count": count, "map_number": current + 1})
        for i, m in enumerate(maps[:count]):
            name, _, mode = (m.get("name") or "").partition("//")
            winner = m.get("winner") or ""
            fields.update({f"map{i + 1}_name": name.strip(), f"map{i + 1}_mode": mode.strip(),
                           f"map{i + 1}_winner": (match[winner].get("tricode") or match[winner].get("name") or "") if winner in ("left", "right") else ""})
        fields["map_name"] = fields.get(f"map{current + 1}_name", "")
        fields["map_mode"] = fields.get(f"map{current + 1}_mode", "")
        state = self.hub.state
        spec = [i for i, on in enumerate(state.get("spectated") or []) if on]
        fields.update({"hud": state.get("hud") or "off",
                       "spectated_row": spec[0] + 1 if spec else 0,
                       "spectated_side": ("left" if spec[0] < 4 else "right") if spec else ""})
        series = {side: {"name": match[side].get("name") or "", "short": match[side].get("tricode") or "",
                         "score": int(match[side].get("wins") or 0), "color": hl.get(f"color_{side}") or ""}
                  for side in ("left", "right")}
        series["best_of"] = int(match.get("best_of") or 0)
        return {"fields": fields, "series": series}

    def _engine_apply_global(self, g, fetch):
        """The GFX Engine's teams (names, short names, logos) into the HUD."""
        changes, done = {}, []
        for side in ("left", "right"):
            t = g.get(side) or {}
            team = {}
            if t.get("name"):
                team["name"] = t["name"]
            if t.get("short"):
                team["tricode"] = t["short"][:6]
            if t.get("logo_url"):
                try:
                    blob = fetch(t["logo_url"])
                    name = save_logo_bytes(LOGO_DIR, f"engine-{t.get('short') or t.get('name') or side}", blob)
                    team["logo"] = f"logos/{name}"
                except Exception as e:
                    log(f"Couldn't fetch the {side} logo from the GFX Engine: {e}", "warn")
            if team:
                changes[side] = team
                done.append(f"{side}: {team.get('name') or team.get('tricode') or 'logo'}")
        if not changes:
            raise ValueError("The engine's Global configuration has no team names or logos yet")
        self.config.update({"match": changes})
        self.changed_config()
        return ", ".join(done)

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
            """Server-sent events: the reading, every time it changes, with each
            mask sent only when this page doesn't have it yet."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            self.wfile.write(b"retry: 1000\n\n")
            have, version, config_version = {}, -1, None
            try:
                while True:
                    new, state, masks = app.hub.wait(version, timeout=15)
                    if new == version:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    version = new
                    msg = dict(state, masks={k: m["id"] for k, m in masks.items()},
                               detector=app.config.get()["detector"].get("enabled", True))
                    send = {k: m for k, m in masks.items() if have.get(k) != m["id"]}
                    if send:
                        msg["mask_data"] = send
                        have.update({k: m["id"] for k, m in send.items()})
                    if app.config.version != config_version:
                        config_version = app.config.version
                        msg["look"] = app.look()
                    self.wfile.write(b"data: " + json.dumps(msg).encode("utf-8") + b"\n\n")
                    self.wfile.flush()
            except (ConnectionError, OSError):
                pass

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
            elif path == "/api/pixel":
                self._json(app.pixel(int(query["x"][0]), int(query["y"][0])))
            else:
                self._file(unquote(path))

        def do_POST(self):
            if not _is_local(self.client_address[0]):
                return self._json({"error": "settings can only be changed on the HUD's own PC"}, 403)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                path = urlparse(self.path).path
                if path.startswith("/api/engine/"):
                    # Connect to GFX Engine (see enginelink.py).
                    reply = app.engine.handle("POST", path, body)
                    if reply is None:
                        return self._json({"error": "not found"}, 404)
                    return self._json(reply)
                if path == "/api/config":
                    # {"path": "highlights.opacity", "value": 0.3} or {"changes": {...}}
                    if "path" in body:
                        app.config.set(body["path"], body["value"])
                    else:
                        app.config.update(body.get("changes") or {})
                    app.changed_config()
                elif path == "/api/reset":
                    section = body.get("section")
                    if section not in DEFAULTS or not isinstance(DEFAULTS[section], dict):
                        raise ValueError(f"no settings called {section!r}")
                    app.config.set(section, DEFAULTS[section])
                    app.changed_config()
                    log(f"Reset the {section} settings")
                elif path == "/api/import":
                    app.config.replace(from_suite(body, app.config.get()))
                    app.changed_config()
                    log("Imported the CoD Scoreboard Suite's settings")
                elif path == "/api/logo":
                    logo = app.add_logo(body.get("name"), body.get("data"))
                    if body.get("team") in ("left", "right"):
                        app.config.set(f"match.{body['team']}.logo", logo)
                        app.changed_config()
                elif path == "/api/demo":
                    app.scanner.set_demo(bool(body.get("on")))
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
    threading.Thread(target=fonts.installed, daemon=True).start()   # (the font list, ready for the panel)
    clean_up_old_update()
    app.updater.check_in_background()
    log(f"CoD HUD {app.updater.current} running - control panel http://localhost:{port}/control")
    log(f"Overlay (browser source, 1920x1080): http://localhost:{port}/overlay")
    return app.server
