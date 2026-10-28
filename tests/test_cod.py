import copy
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request

import numpy as np

from codhud import detect
from codhud.capture import normalise_title, to_reference
from codhud.config import DEFAULTS, Config, set_path
from codhud.demo import DemoGame


def demo_frame(**changes):
    game = DemoGame()
    moment = dict(game.moment(5), **changes)
    return game.draw(moment), moment


class DetectorTests(unittest.TestCase):
    def test_hud_on_score_only_and_off(self):
        cfg = copy.deepcopy(DEFAULTS)
        for hud in ("on", "score", "off"):
            frame, _ = demo_frame(hud=hud)
            self.assertEqual(detect.hud_reading(frame, cfg)[0], hud)
        # Without "score box only", the score box alone means the HUD's away.
        cfg["detector"]["score_box_only"] = False
        self.assertEqual(detect.hud_reading(demo_frame(hud="score")[0], cfg)[0], "off")

    def test_debounce(self):
        d = detect.HudDebounce()
        self.assertEqual(d.feed("on", 0.0), "on")
        # A blip (alt-tab) shorter than the grace time doesn't hide it.
        self.assertEqual(d.feed("off", 0.10), "on")
        self.assertEqual(d.feed("off", 0.30), "on")
        self.assertEqual(d.feed("on", 0.32), "on")
        # Gone for good: hides after the grace time.
        d.feed("off", 1.0)
        self.assertEqual(d.feed("off", 1.2), "on")
        self.assertEqual(d.feed("off", 1.0 + detect.HUD_LOSS_GRACE), "off")
        # Comes back quickly.
        d.feed("on", 2.0)
        self.assertEqual(d.feed("on", 2.0 + detect.HUD_GAIN_CONFIRM), "on")

    def test_spectated(self):
        frame, m = demo_frame(dead={0, 6}, objective={3, 5}, spectated=2)
        self.assertEqual(detect.spectated(frame, DEFAULTS), [i == 2 for i in range(8)])
        frame, _ = demo_frame(spectated=6, dead=set(), objective=set())
        self.assertEqual(detect.spectated(frame, DEFAULTS), [i == 6 for i in range(8)])

    def test_real_screenshot(self):
        """The demo's screenshot of the CoD caster HUD (BO7): HUD up, BigR3aL spectated."""
        from codhud.demo import ScreenshotDemo
        shown, hidden = ScreenshotDemo().frames()
        self.assertEqual(detect.hud_reading(shown, DEFAULTS)[0], "on")
        self.assertEqual(detect.hud_reading(hidden, DEFAULTS)[0], "off")
        self.assertEqual(detect.spectated(shown, DEFAULTS), [i == 3 for i in range(8)])

    def test_name_cells(self):
        """Each name cell, made black on white for the text recognition: the
        name's ink is there (light on dark, dark on a light highlight, and the
        player card's coloured name), and nothing else is."""
        from PIL import Image
        from codhud.demo import ScreenshotDemo
        from codhud.names import ink
        shown, _ = ScreenshotDemo().frames()
        for box in DEFAULTS["names"]["rows"] + [DEFAULTS["names"]["card"]]:
            cell = np.asarray(ink(detect.crop(shown, box["x"], box["y"], box["w"], box["h"]), scale=1))
            dark = (cell < 128).mean()
            self.assertTrue(0.015 < dark < 0.3, (box, dark))
        # (the CDL screenshot: dark on light green for the spectated row)
        cdl = os.path.join(os.path.dirname(__file__), "fixtures", "cdl-top.png")
        if os.path.exists(cdl):
            canvas = Image.new("RGB", (1920, 1080))
            canvas.paste(Image.open(cdl).convert("RGB"))
            frame = np.asarray(canvas)
            box = DEFAULTS["names"]["rows"][5]
            cell = np.asarray(ink(detect.crop(frame, box["x"], box["y"], box["w"], box["h"]), scale=1))
            self.assertTrue(0.015 < (cell < 128).mean() < 0.3)

    def test_names(self):
        from codhud.names import best_match, clean_name, same_player
        self.assertEqual(clean_name("6 SHOTZZY"), "SHOTZZY")
        self.assertEqual(clean_name(" Last__Stand "), "Last__Stand")
        self.assertTrue(same_player("Psych9delic", "Psych9de1ic"))
        self.assertTrue(same_player("OpTic Shotzzy", "Shotzzy"))
        self.assertFalse(same_player("HUKE", "Dashy"))
        self.assertEqual(best_match("BigR3al", ["Zett7abyte", "Last__Stand", "Fri_9thalix", "BigR3aL"]), 3)
        self.assertIsNone(best_match("Nobody", ["Zett7abyte", ""]))

    def test_name_reader_settles(self):
        """A row's name only changes once the new name is read twice."""
        from codhud.names import NameReader
        reader = NameReader()
        reads = iter([["A"] * 8, ["B"] * 8, ["A"] * 8, ["B"] * 8, ["B"] * 8, [""] * 8])

        class Ocr:
            pass
        reader._ocr = Ocr()
        import codhud.names as names
        old = names.read_cell
        try:
            current = {}
            names.read_cell = lambda frame, box, ocr: current["row"][0] if box is not DEFAULTS["names"]["card"] else "card"
            seen = []
            for got in reads:
                current["row"] = got
                reader.read(None, DEFAULTS)
                seen.append(reader.names[0])
            self.assertEqual(seen, ["A", "A", "A", "A", "B", "B"])
        finally:
            names.read_cell = old

    def test_spectated_row(self):
        from codhud.scanner import spectated_row
        names = ["a", "b", "c", "d", "Huke", "Shotzzy", "Mercules", "Dashy"]
        self.assertEqual(spectated_row([i == 2 for i in range(8)], names, ""), 2)
        # No row lit (or two): the player card's name says.
        self.assertEqual(spectated_row([False] * 8, names, "SHOTZZY"), 5)
        self.assertEqual(spectated_row([False] * 8, names, ""), -1)

    def test_cams_match_players(self):
        from codhud.cams import CamHub, clean_cam_name, match_players
        self.assertEqual(clean_cam_name(" Shotzzy#1234567 "), "Shotzzy")
        with self.assertRaises(ValueError):
            clean_cam_name("  ")
        players = [{"team": "right", "slot": 2, "name": "SHOTZZY", "also": []},
                   {"team": "left", "slot": 4, "name": "BigR3aL", "also": ["Scrap", "BigR3aL"]},
                   {"team": "left", "slot": 1, "name": "Zett7abyte", "also": []}]
        got = match_players(["Shotzzy", "Scrap", "someone"], players)
        self.assertEqual(got["Shotzzy"]["slot"], 2)
        self.assertEqual((got["Scrap"]["team"], got["Scrap"]["slot"]), ("left", 4))    # (by the roster link)
        self.assertNotIn("someone", got)
        # Picked by hand wins.
        got = match_players(["Shotzzy", "someone"], players, {"Zett7abyte": "someone"})
        self.assertEqual(got["someone"]["slot"], 1)
        hub = CamHub()
        hub.poll("cam1", "Scrap", "C615", wait=0)
        pubs = hub.publishers(players)
        self.assertEqual((pubs[0]["riot_id"], pubs[0]["player"]["name"]), ("Scrap", "BigR3aL"))

    def test_frames_scaled_to_1080p(self):
        frame, _ = demo_frame()
        from PIL import Image
        big = np.asarray(Image.fromarray(frame).resize((2560, 1440), Image.NEAREST))
        self.assertEqual(detect.hud_reading(to_reference(big), DEFAULTS)[0], "on")

    def test_window_title(self):
        title = "C​a​l​l​ ​o​f​ ​D​u​t​y​®​​​"
        self.assertEqual(normalise_title(title), "call of duty")
        self.assertIn(normalise_title("Call of Duty"), normalise_title(title))


class ConfigTests(unittest.TestCase):
    def test_old_settings_dropped(self):
        """A config.json from an older build (the glow, gradient and BO7 title)
        keeps what still applies and drops the rest."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"highlights": {"opacity": 0.3}, "scorebox": {}, "title": {"maps": True},
                           "detector": {"enabled": False},
                           "match": {"left": {"name": "FUEL", "record": "SEASON RECORD"}, "maps": [], "map_count": 7,
                                     "best_of": 7}}, f)
            cfg = Config(path).get()
            self.assertNotIn("highlights", cfg)
            self.assertNotIn("title", cfg)
            self.assertFalse(cfg["detector"]["enabled"])
            self.assertEqual(cfg["match"]["left"]["name"], "FUEL")
            self.assertNotIn("record", cfg["match"]["left"])
            self.assertNotIn("maps", cfg["match"])
            self.assertEqual(cfg["match"]["best_of"], 7)

    def test_set_path(self):
        cfg = set_path(DEFAULTS, "names.rows.2.w", 500)
        self.assertEqual(cfg["names"]["rows"][2]["w"], 500)
        self.assertEqual(DEFAULTS["names"]["rows"][2]["w"], 338)


class ServerTests(unittest.TestCase):
    def test_overlay_stream_and_settings(self):
        from codhud.app import HudApp, start
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(os.path.join(tmp, "config.json"))
            cfg.update({"http_port": 8795})
            app = HudApp(cfg, demo=True)
            app.scanner.names.available = lambda: False   # (the demo's names, on any PC)
            app.updater.check_in_background = lambda: None
            start(app)
            base = "http://127.0.0.1:8795"
            try:
                def post(path, body):
                    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
                    return json.loads(urllib.request.urlopen(req, timeout=5).read())

                for path in ("/control", "/overlay", "/update.js", "/fonts/Inter.ttf"):
                    self.assertEqual(urllib.request.urlopen(base + path, timeout=5).status, 200, path)
                deadline = time.time() + 5
                while time.time() < deadline and app.scanner.info()["hud"] != "on":
                    time.sleep(0.05)
                self.assertEqual(app.scanner.info()["hud"], "on")
                stream = urllib.request.urlopen(base + "/api/stream", timeout=5)
                line = b""
                while not line.startswith(b"data:"):
                    line = stream.readline()
                msg = json.loads(line[5:])
                self.assertEqual(msg["hud"], "on")
                self.assertIn("overlay", msg["look"])
                self.assertEqual(msg["cams"], [])
                # A change of look reaches the overlay.
                post("/api/config", {"path": "overlay.sponsor", "value": False})
                deadline = time.time() + 5
                while time.time() < deadline:
                    line = stream.readline()
                    if line.startswith(b"data:") and "look" in json.loads(line[5:]):
                        break
                self.assertFalse(json.loads(line[5:])["look"]["overlay"]["sponsor"])
                # The demo's names, and who's spectated.
                deadline = time.time() + 5
                while time.time() < deadline and not app.hub.state["names"][3]:
                    time.sleep(0.05)
                self.assertEqual(app.hub.state["names"][3], "BigR3aL")
                self.assertEqual(app.hub.state["spec"], 3)
                stream.close()
                self.assertFalse(cfg.get()["overlay"]["sponsor"])
                post("/api/reset", {"section": "overlay"})
                self.assertTrue(cfg.get()["overlay"]["sponsor"])
                # Swap sides: names, short names, logos, colours and series scores.
                post("/api/match", {"changes": {"left": {"name": "FUEL", "wins": 2, "color": "#111111"},
                                                "right": {"name": "SAINTS", "wins": 1, "color": "#222222"}}})
                c = post("/api/swap", {})["config"]["match"]
                self.assertEqual((c["left"]["name"], c["right"]["name"], c["left"]["wins"], c["right"]["wins"]),
                                 ("SAINTS", "FUEL", 1, 2))
                self.assertEqual((c["left"]["color"], c["right"]["color"]), ("#222222", "#111111"))
                self.assertEqual(c["left"]["logo"], "assets/logos/st-clair-saints.png")
                # A cam picked by hand for a player.
                post("/api/cams/assign", {"name": "BigR3aL", "cam": "Scrap"})
                self.assertEqual(cfg.get()["cams"]["assign"], {"BigR3aL": "Scrap"})
                post("/api/cams/assign", {"name": "BigR3aL", "cam": ""})
                self.assertEqual(cfg.get()["cams"]["assign"], {})
                # A player cam connecting (as the Player Cam app does).
                r = post("/api/cam/poll", {"id": "cam1", "riot_id": "BigR3aL", "label": "C615", "wait": 0})
                self.assertEqual(r["player"]["slot"], 4)
                self.assertEqual(r["player"]["team_name"], "SAINTS")
                cams = post("/api/cam/list", {})
                self.assertEqual((cams["game"], cams["cams"][0]["name"]), ("cod", "BigR3aL"))
                with self.assertRaises(urllib.error.HTTPError):
                    post("/api/reset", {"section": "nope"})
                self.assertIn("assets/logos/st-clair-saints.png", json.loads(urllib.request.urlopen(base + "/api/logos").read())["logos"])
                png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                import codhud.app as appmod
                old = appmod.LOGO_DIR
                appmod.LOGO_DIR = os.path.join(tmp, "logos")
                try:
                    got = post("/api/logo", {"name": "Fuel Logo.png", "data": png, "team": "left"})
                    self.assertEqual(got["config"]["match"]["left"]["logo"], "logos/Fuel Logo.png")
                    self.assertTrue(os.path.isfile(os.path.join(tmp, "logos", "Fuel Logo.png")))
                finally:
                    appmod.LOGO_DIR = old
                self.assertIsInstance(json.loads(urllib.request.urlopen(base + "/api/fonts", timeout=20).read())["fonts"], list)
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + "/fonts/local/No%20Such%20Font", timeout=5)
                frame = urllib.request.urlopen(base + "/api/frame.jpg", timeout=5).read()
                self.assertEqual(frame[:2], b"\xff\xd8")
                px = json.loads(urllib.request.urlopen(base + "/api/pixel?x=960&y=57", timeout=5).read())
                self.assertEqual(px["hex"], "#23252D")
            finally:
                app.stop()



class EngineLinkTests(unittest.TestCase):
    def test_connect_send_and_use_global(self):
        """A fake SCC GFX Engine: the HUD connects, sends its match and series,
        and takes the engine's team names, short names and logos."""
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import codhud.app as appmod
        from codhud.app import HudApp, start

        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c63f8cf000000030101005a4d5b3c0000000049454e44ae426082")
        ingests, globals_set, state = [], [], {"right_score": 2}

        class Engine(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, body, ctype="application/json"):
                body = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                port = self.server.server_address[1]
                if self.path == "/api/hello":
                    self._send({"app": "scc-gfx-engine", "name": "Test"})
                elif self.path == "/api/global":
                    self._send({"global": {
                        "left": {"name": "OPTIC TEXAS", "short": "OPTX", "score": 1, "logo_url": f"http://127.0.0.1:{port}/logo.png"},
                        "right": {"name": "ATLANTA FAZE", "short": "ATL", "score": state["right_score"]}, "best_of": 7,
                        "sponsors": [{"logo": "sponsors/logo.png", "secs": 8, "logo_url": f"http://127.0.0.1:{port}/logo.png"}]}})
                elif self.path == "/logo.png":
                    self._send(png, "image/png")
                else:
                    self.send_error(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/ingest":
                    ingests.append(json.loads(body))
                    self._send({"ok": True, "global": {}})
                elif self.path == "/api/global":
                    globals_set.append(json.loads(body)["global"])
                    self._send({"global": {}})
                else:
                    self.send_error(404)

        engine = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
        engine.daemon_threads = True
        threading.Thread(target=engine.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            old = appmod.LOGO_DIR
            appmod.LOGO_DIR = os.path.join(tmp, "logos")
            cfg = Config(os.path.join(tmp, "config.json"))
            cfg.update({"http_port": 8796, "global": {"follow": False}})
            app = HudApp(cfg, demo=True)
            app.scanner.names.available = lambda: False
            app.updater.check_in_background = lambda: None
            start(app)
            base = "http://127.0.0.1:8796"
            try:
                def post(path, body=None):
                    req = urllib.request.Request(base + path, json.dumps(body or {}).encode(), {"Content-Type": "application/json"})
                    return json.loads(urllib.request.urlopen(req, timeout=10).read())

                self.assertFalse(json.loads(urllib.request.urlopen(base + "/api/engine", timeout=5).read())["connected"])
                self.assertEqual(urllib.request.urlopen(base + "/enginelink.js", timeout=5).status, 200)
                got = post("/api/engine/connect", {"url": f"127.0.0.1:{engine.server_address[1]}"})
                self.assertTrue(got["connected"])
                self.assertEqual(got["name"], "Test")
                self.assertTrue(cfg.get()["gfx_engine"]["connected"])
                deadline = time.time() + 10
                while time.time() < deadline and not ingests:
                    time.sleep(0.05)
                self.assertTrue(ingests, "the HUD sent nothing to the engine")
                sent = ingests[-1]
                self.assertEqual((sent["source"], sent["game"]), ("CoD HUD", "cod"))
                self.assertEqual(sent["series"]["left"]["short"], "FUEL")
                self.assertEqual(sent["series"]["right"]["name"], "ST. CLAIR SAINTS")
                self.assertEqual(sent["series"]["best_of"], 5)
                self.assertEqual(sent["fields"]["left_name"], "FUEL ESPORTS")
                deadline = time.time() + 10
                while time.time() < deadline and not ingests[-1]["fields"].get("spectated_name"):
                    time.sleep(0.05)
                self.assertEqual(ingests[-1]["fields"]["left_p4_ign"], "BigR3aL")
                self.assertEqual(ingests[-1]["fields"]["spectated_name"], "BigR3aL")

                reply = post("/api/engine/use-global")
                self.assertIn("OPTIC TEXAS", reply["summary"])
                match = cfg.get()["match"]
                self.assertEqual((match["left"]["name"], match["left"]["tricode"]), ("OPTIC TEXAS", "OPTX"))
                self.assertEqual((match["right"]["name"], match["right"]["tricode"]), ("ATLANTA FAZE", "ATL"))
                self.assertEqual((match["left"]["wins"], match["right"]["wins"], match["best_of"]), (1, 2, 7))
                self.assertEqual(match["left"]["logo"], "logos/engine-OPTX.png")
                self.assertEqual(match["right"]["logo"], "")    # (the engine's team has none)
                self.assertEqual(cfg.get()["sponsors"]["logos"], [{"src": "logos/sponsor-1-logo.png", "secs": 8}])
                # Following the engine: a change there shows here.
                cfg.update({"global": {"follow": True}})
                state["right_score"] = 3
                deadline = time.time() + 10
                while time.time() < deadline and cfg.get()["match"]["right"]["wins"] != 3:
                    time.sleep(0.05)
                self.assertEqual(cfg.get()["match"]["right"]["wins"], 3)
                # Swap sides swaps the engine's Global information too.
                post("/api/swap")
                self.assertEqual(globals_set[-1]["left"]["name"], "ATLANTA FAZE")
                self.assertEqual(globals_set[-1]["right"]["short"], "OPTX")
                self.assertEqual(cfg.get()["match"]["left"]["wins"], 3)
                with open(os.path.join(tmp, "logos", "engine-OPTX.png"), "rb") as f:
                    self.assertEqual(f.read(), png)
                self.assertEqual(urllib.request.urlopen(base + "/logos/engine-OPTX.png", timeout=5).read(), png)
                with self.assertRaises(urllib.error.HTTPError) as e:
                    post("/api/engine/nope")
                self.assertEqual(e.exception.code, 404)
                post("/api/engine/disconnect")
                self.assertFalse(cfg.get()["gfx_engine"]["connected"])
            finally:
                app.stop()
                appmod.LOGO_DIR = old
                engine.shutdown()
                engine.server_close()


class PortTests(unittest.TestCase):
    def test_old_port_moves_off_the_league_cam_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"http_port": 8779}, f)
            self.assertEqual(Config(path).get()["http_port"], 8780)
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["http_port"], 8780)
            self.assertEqual(DEFAULTS["http_port"], 8780)


if __name__ == "__main__":
    unittest.main()
