"""Connect to GFX Engine: a game HUD sends its game and series to an SCC GFX
Engine on the network, and can take the engine's global configuration (the
teams' names, short names and logos).

The same file is in every SCC HUD (the GFX Engine repository keeps the
original, in hud-link/). A HUD makes one EngineLink with:

    source        its name on the engine, e.g. "League HUD" (the engine's data
                  table of its fields has this name)
    game          "lol", "valorant", "cod", "rl" or "smash"
    snapshot()    -> {"fields": {name: value}, "series": {"left": {"name",
                  "short", "score", "color"}, "right": {...}, "best_of": n},
                  and optionally "hud": {"port", "network" (other PCs can
                  read the HUD), "graphics": [{"id", "name", "path", "kind"
                  ("board" / "overlay")}]}: its own pages, which the engine
                  adds to its graphics (e.g. VALORANT's agent select)}
    apply_global(g, fetch) -> a short summary of what changed: takes the
                  engine's global configuration (g["left"]["name"], "short",
                  "logo_url", "color"..., g["tournament_logo_url"],
                  g["sponsors"]: [{"logo_url", "secs", "white"}]); fetch(url)
                  -> bytes for the logos (see save_sponsors())
    load() / save(dict)   where the chosen engine is remembered

and serves its routes (see handle()). While connected it sends the snapshot
about once a second (at once when it changes, at least every 4 seconds).

A HUD is paired with the one engine it connected to: it remembers that
engine's ID and sends it with everything, so another engine on the network
(one that got the old address, or a second engine on the same PC) turns it
away rather than mixing two shows' data. When its engine moves to another
address the HUD finds it again by its ID. The HUD has its own ID too, so an
engine keeps one copy of each HUD and turns a second one away.
"""

import concurrent.futures
import ipaddress
import json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid

APP = "scc-gfx-engine"
PORT = 8782             # the engine's web port
DISCOVERY_PORT = 8783   # where engines announce themselves

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # (the LAN, never a proxy)


def _get(url, timeout=3):
    with _opener.open(url, timeout=timeout) as r:
        return r.read()


def _post(url, body, timeout=3):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:   # (the engine's {"error", "code"}, not just "Bad Request")
        try:
            reply = json.loads(e.read() or b"{}")
        except ValueError:
            reply = {}
        if not reply.get("error"):
            raise
        return reply


class Paired(ValueError):
    """The engine at the address isn't the one this HUD is paired with
    (code "wrong_engine"), or already has another copy of this HUD ("taken")."""

    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code


def own_addresses():
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # (no traffic is sent)
            found.add(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for a in found:
        try:
            ip = ipaddress.IPv4Address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            out.append(str(ip))
    return sorted(out)


def normalise(url):
    """"192.168.1.20", "pc:8782" or "http://pc:8782/control" -> "http://192.168.1.20:8782"."""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("Enter the engine's address")
    if "://" not in url:
        url = "http://" + url
    scheme, rest = url.split("://", 1)
    host = rest.split("/", 1)[0]
    if ":" not in host.rsplit("]", 1)[-1]:
        host += f":{PORT}"
    return f"{scheme}://{host}"


def hello(url, timeout=2):
    """The engine's {"app", "name", "version", "id"} at url, or None."""
    try:
        info = json.loads(_get(url + "/api/hello", timeout))
        return info if info.get("app") == APP else None
    except Exception:
        return None


def _listen(seconds):
    """Engines announcing themselves (UDP broadcast) in the next few seconds."""
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", DISCOVERY_PORT))
    except OSError:
        return found   # (an engine on this PC has the port; the scan finds it)
    s.settimeout(0.5)
    end = time.time() + seconds
    while time.time() < end:
        try:
            data, (ip, _) = s.recvfrom(2048)
            msg = json.loads(data)
        except (socket.timeout, ValueError, OSError):
            continue
        if msg.get("app") == APP:
            port = int(msg.get("port") or PORT)
            found[f"http://{ip}:{port}"] = {"name": str(msg.get("name") or ip)[:60], "version": str(msg.get("version") or ""),
                                            "id": str(msg.get("id") or "")}
    s.close()
    return found


def scan(port=PORT):
    """Every engine this PC can find: on this PC, announcing itself, or
    answering on port anywhere on this PC's networks (each /24)."""
    listening = concurrent.futures.ThreadPoolExecutor(1).submit(_listen, 3.5)
    mine = own_addresses()
    candidates = ["127.0.0.1"]
    for a in mine:
        net = ipaddress.IPv4Network(a + "/24", strict=False)
        candidates += [str(h) for h in net.hosts()]

    def is_open(ip):
        try:
            with socket.create_connection((ip, port), timeout=0.35):
                return True
        except OSError:
            return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        open_ips = [ip for ip, ok in zip(candidates, pool.map(is_open, candidates)) if ok]
        hits = {f"http://{ip}:{port}": h for ip, h in zip(open_ips, pool.map(lambda ip: hello(f"http://{ip}:{port}"), open_ips)) if h}
    hits.update({u: h for u, h in listening.result().items() if u not in hits})
    out, seen_local, seen_ids = [], False, set()
    for url, h in sorted(hits.items(), key=lambda kv: not kv[0].startswith("http://127.0.0.1:")):
        ip = url.split("://", 1)[1].rsplit(":", 1)[0]
        local = ip == "127.0.0.1" or ip in mine
        if (local and seen_local) or (h.get("id") and h["id"] in seen_ids):
            continue   # the same engine, found by two addresses
        seen_local = seen_local or local
        seen_ids.add(h.get("id") or url)
        out.append({"url": url, "name": h.get("name") or ip, "version": h.get("version", ""), "ip": ip,
                    "this_pc": local, "id": h.get("id") or ""})
    out.sort(key=lambda e: (not e["this_pc"], e["name"].lower()))
    return out


class EngineLink:
    def __init__(self, source, game, snapshot, apply_global, load, save, log=None):
        self.source = source
        self.game = game
        self.snapshot = snapshot
        self.apply_global = apply_global
        self.load = load
        self.save = save
        self.log = log or (lambda msg, level="info": None)
        saved = load() or {}
        self.url = saved.get("url") or ""
        self.name = saved.get("name") or ""
        self.connected = bool(saved.get("connected") and self.url)
        self.engine_id = saved.get("engine_id") or ""   # the engine this HUD is paired with
        same_pc = saved.get("hud_pc") == socket.gethostname()   # (settings copied from another PC: a new ID)
        self.hud_id = (saved.get("hud_id") if same_pc else "") or uuid.uuid4().hex[:16]   # this HUD, to the engine
        self._looked_at = 0.0      # when it last looked for its engine at another address
        self.ok = False            # the last send got through
        self.error = ""
        self.sent_at = 0.0
        self.found = []            # the last scan
        self.scanning = False
        self.engine_global = None  # the engine's global configuration (from its replies)
        self._last_body = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        if self.hud_id != saved.get("hud_id"):
            self._remember()
        threading.Thread(target=self._run, daemon=True).start()

    # --- what the control panel shows ----------------------------------------

    def status(self):
        return {"url": self.url, "name": self.name, "connected": self.connected, "ok": self.ok, "error": self.error,
                "age": round(time.time() - self.sent_at, 1) if self.sent_at else None,
                "found": self.found, "scanning": self.scanning, "source": self.source, "engine_id": self.engine_id}

    def _remember(self):
        self.save({"url": self.url, "name": self.name, "connected": self.connected, "engine_id": self.engine_id,
                   "hud_id": self.hud_id, "hud_pc": socket.gethostname()})

    # --- staying with the engine it's paired with ------------------------------

    def _adopt(self, engine_id, name=""):
        """An engine answered as the paired one: remember its ID (a HUD paired
        before engines had IDs takes the one at its address)."""
        changed = False
        if engine_id and not self.engine_id:
            self.engine_id, changed = engine_id, True
        if name and name != self.name:
            self.name, changed = name, True
        if changed:
            self._remember()

    def _find_engine(self, force=False):
        """Looks for the paired engine at another address (its address
        changed); moves there if it's found. Not more than every 30 seconds."""
        if not self.engine_id or (not force and time.time() - self._looked_at < 30):
            return False
        self._looked_at = time.time()
        try:
            found = scan()
        except Exception:
            return False
        for e in found:
            if e.get("id") == self.engine_id and e["url"] != self.url:
                self.log(f"The GFX Engine {self.name} is at {e['url']} now (was {self.url})")
                self.url, self.name, self._last_body = e["url"], e.get("name") or self.name, None
                self._remember()
                return True
        return False

    def _paired_error(self, reply):
        if reply.get("code") == "wrong_engine":
            return Paired(f"The GFX Engine at {self.url} isn't {self.name or 'the one this HUD connected to'} "
                          f"(another engine has that address now). Looking for {self.name or 'it'} on the network; "
                          f"or connect again to choose.", "wrong_engine")
        return Paired(reply.get("error") or "The GFX Engine turned this HUD away", reply.get("code") or "")

    # --- actions ---------------------------------------------------------------

    def do_scan(self):
        self.scanning = True
        try:
            self.found = scan()
        finally:
            self.scanning = False
        return self.found

    def connect(self, url):
        url = normalise(url)
        info = hello(url, timeout=4)
        if not info:
            raise ValueError(f"No GFX Engine answered at {url}. Is it running, with 'Let other PCs use this engine' on?")
        self.url, self.name, self.connected = url, info.get("name") or url, True
        self.engine_id = info.get("id") or ""   # paired with this engine (and only this one) from now on
        self.error, self._last_body, self._looked_at = "", None, 0.0
        self._remember()
        self.log(f"Connected to the GFX Engine {self.name} ({url})")
        self._wake.set()
        return self.status()

    def disconnect(self):
        self.connected = False
        self.ok = False
        self._remember()
        self.log("Disconnected from the GFX Engine")
        return self.status()

    def use_global(self):
        """Takes the engine's teams (names, short names, logos) into the HUD."""
        if not self.url:
            raise ValueError("Connect to a GFX Engine first")
        reply = self._get_global()
        if reply.get("code") == "wrong_engine" and self._find_engine(force=True):
            reply = self._get_global()
        if reply.get("error"):
            raise self._paired_error(reply) if reply.get("code") else ValueError(reply["error"])
        if self.engine_id and reply.get("id") and reply["id"] != self.engine_id:
            raise self._paired_error({"code": "wrong_engine"})
        self._adopt(reply.get("id"))
        g = reply.get("global") or {}
        summary = self.apply_global(g, lambda u: _get(u, 10))
        self.log(f"Took the global configuration from {self.name}: {summary}")
        self._last_body = None
        self._wake.set()
        return {"summary": summary, "global": g}

    def _get_global(self):
        url = self.url + "/api/global" + (f"?engine={self.engine_id}" if self.engine_id else "")
        try:
            return json.loads(_get(url, 5))
        except urllib.error.HTTPError as e:
            try:
                reply = json.loads(e.read() or b"{}")
            except ValueError:
                reply = {}
            if reply.get("error"):
                return reply
            raise ValueError(f"Couldn't reach the GFX Engine: {e}")
        except Exception as e:
            raise ValueError(f"Couldn't reach the GFX Engine: {getattr(e, 'reason', e)}")

    def roster(self, link=None):
        """The engine's rosters for the two teams, with which of the HUD's
        players are linked to a roster player by in-game ID ("Save IGN to
        player"). link={"side", "player" (roster index), "ign"} saves one first."""
        if not self.url or not self.connected:
            raise ValueError("Connect to a GFX Engine first")
        try:
            snap = self.snapshot() or {}
        except Exception:
            snap = {}
        body = {"game": self.game, "fields": snap.get("fields") or {}, "engine": self.engine_id}
        try:
            if link:
                reply = _post(self.url + "/api/roster/link", dict(body, **link), 5)
                if not reply.get("error"):
                    self.log(f"Saved {link.get('ign')} as a player's in-game ID on {self.name}")
            else:
                reply = _post(self.url + "/api/roster/links", body, 5)
        except Exception as e:
            raise ValueError(f"Couldn't reach the GFX Engine: {getattr(e, 'reason', e)}")
        if reply.get("error"):
            raise self._paired_error(reply) if reply.get("code") else ValueError(reply["error"])
        return reply

    def send_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # --- sending ------------------------------------------------------------------

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if not self.connected or not self.url:
                continue
            try:
                snap = self.snapshot() or {}
            except Exception as e:
                self.error = f"Couldn't read the HUD: {e!r}"
                continue
            body = {"source": self.source, "game": self.game, "fields": snap.get("fields") or {},
                    "series": snap.get("series") or {}, "hud": snap.get("hud"),
                    "engine": self.engine_id, "hud_id": self.hud_id}
            if body == self._last_body and time.time() - self.sent_at < 4:
                continue
            try:
                reply = _post(self.url + "/api/ingest", body)
                if reply.get("error"):
                    raise self._paired_error(reply) if reply.get("code") else ValueError(reply["error"])
                if self.engine_id and reply.get("id") and reply["id"] != self.engine_id:
                    raise self._paired_error({"code": "wrong_engine"})
                self._adopt(reply.get("id"), reply.get("name") or "")
                self.engine_global = reply.get("global")
                if not self.ok:
                    self.log(f"Sending to the GFX Engine {self.name}")
                self.ok, self.error, self.sent_at, self._last_body = True, "", time.time(), body
            except Exception as e:
                msg = str(getattr(e, "reason", e))
                if self.ok or msg != self.error:
                    self.log(f"Couldn't send to the GFX Engine {self.name}: {msg}", "warn")
                self.ok, self.error = False, msg
                if getattr(e, "code", "") != "taken" and self._find_engine():
                    continue   # (found it at its new address: send there now)
                self._stop.wait(2)

    # --- the HUD's web server --------------------------------------------------

    def handle(self, method, path, body=None):
        """The HUD's routes for this: returns a JSON-able reply, or None if path isn't one.

            GET  /api/engine            status
            POST /api/engine/scan       find engines (takes a few seconds)
            POST /api/engine/connect    {"url"}
            POST /api/engine/disconnect
            POST /api/engine/use-global
            POST /api/engine/roster     the teams' rosters, and who's linked
            POST /api/engine/link       {"side", "player", "ign"}: Save IGN to player
        """
        if not path.startswith("/api/engine"):
            return None
        if method == "GET" and path == "/api/engine":
            return self.status()
        if method != "POST":
            return None
        if path == "/api/engine/scan":
            self.do_scan()
            return self.status()
        if path == "/api/engine/connect":
            return self.connect((body or {}).get("url", ""))
        if path == "/api/engine/disconnect":
            return self.disconnect()
        if path == "/api/engine/use-global":
            return self.use_global()
        if path == "/api/engine/roster":
            return self.roster()
        if path == "/api/engine/link":
            b = body or {}
            return self.roster({"side": b.get("side"), "player": b.get("player"), "ign": b.get("ign")})
        return None


def save_sponsors(g, fetch, folder, prefix, log=None):
    """The engine's sponsors (g["sponsors"]: {"logo_url", "secs"...}) downloaded
    into folder. Returns their paths (prefix + file name), in order, and the
    seconds each shows (the first one's), or ([], None) when it has none."""
    import os
    import urllib.parse
    paths = []
    sponsors = [sp for sp in g.get("sponsors") or [] if sp.get("logo_url")]
    for i, sp in enumerate(sponsors, 1):
        base = os.path.splitext(os.path.basename(urllib.parse.unquote(sp["logo_url"].split("?")[0])))[0]
        try:
            paths.append(prefix + save_logo_bytes(folder, f"engine-{i}-{base}", fetch(sp["logo_url"])))
        except Exception as e:
            if log:
                log(f"Couldn't fetch the sponsor {base} from the GFX Engine: {e}", "warn")
    return paths, (sponsors[0].get("secs") if sponsors else None)


def save_logo_bytes(folder, name, blob):
    """Writes a downloaded logo into folder as <name>.<ext> (by its bytes) and returns the file name."""
    import os
    import re
    ext = ".png"
    if blob[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        ext = ".webp"
    elif blob[:6] in (b"GIF87a", b"GIF89a"):
        ext = ".gif"
    elif b"<svg" in blob[:512]:
        ext = ".svg"
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "logo"
    os.makedirs(folder, exist_ok=True)
    fname = f"{base}{ext}"
    with open(os.path.join(folder, fname), "wb") as f:
        f.write(blob)
    return fname
