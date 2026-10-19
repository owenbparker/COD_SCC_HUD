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
from codhud.config import DEFAULTS, Config, from_suite, set_path
from codhud.demo import DemoGame

SUITE = os.path.join(os.path.dirname(__file__), "fixtures", "cod_scoreboard_suite_config.json")


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

    def test_rows_and_spectated(self):
        frame, m = demo_frame(dead={0, 6}, objective={3, 5}, spectated=2)
        rows = detect.row_states(frame, DEFAULTS)
        self.assertEqual(rows, ["dim", "normal", "normal", "bright", "normal", "bright", "dim", "normal"])
        self.assertEqual(detect.spectated(frame, DEFAULTS), [i == 2 for i in range(8)])
        frame, _ = demo_frame(spectated=6, dead=set(), objective=set())
        self.assertEqual(detect.spectated(frame, DEFAULTS), [i == 6 for i in range(8)])
        self.assertEqual(detect.row_states(frame, DEFAULTS), ["normal"] * 8)

    def test_masks_cover_background_not_text(self):
        frame, _ = demo_frame(spectated=1)
        masks = detect.row_masks(frame, DEFAULTS)
        self.assertEqual(masks[0].shape, (32, 610))
        # Row 1: mostly background, but its name's letters aren't.
        self.assertGreater((masks[0] == 255).mean(), 0.7)
        self.assertLess((masks[0] == 255).mean(), 0.99)
        # The spectated row is filled with the game's highlight: no glow on it.
        self.assertLess((masks[1][:, 60:] == 255).mean(), 0.05)
        score = detect.score_mask(frame, DEFAULTS)
        self.assertEqual(score.shape, (179, 474))
        # The team logo area is always covered; the clock's digits aren't.
        self.assertTrue((score[30:130, 6:86] == 255).all())
        digits = detect.crop(frame, 930, 62, 60, 30)
        self.assertTrue((digits.max(axis=-1) > 200).any())
        self.assertLess(score[42:72, 207:267].min(), 255)

    def test_real_screenshot(self):
        """The demo's screenshot of the CoD caster HUD (BO7): HUD up, the bomb
        carrier's and the spectated player's icon cells lit, BigR3aL spectated."""
        from codhud.demo import ScreenshotDemo
        shown, hidden = ScreenshotDemo().frames()
        self.assertEqual(detect.hud_reading(shown, DEFAULTS)[0], "on")
        self.assertEqual(detect.hud_reading(hidden, DEFAULTS)[0], "off")
        self.assertEqual(detect.row_states(shown, DEFAULTS), ["normal", "bright", "normal", "bright"] + ["normal"] * 4)
        self.assertEqual(detect.spectated(shown, DEFAULTS), [i == 3 for i in range(8)])
        masks = detect.row_masks(shown, DEFAULTS)
        self.assertEqual(masks[3].max(), 0)               # the spectated row: no glow
        self.assertGreater((masks[0] == 255).mean(), 0.8)

    def test_colour_mask_feather(self):
        region = np.array([[[0x14, 0x15, 0x1B], [0x14 + 5, 0x15, 0x1B], [0x14 + 40, 0x15, 0x1B]]], dtype=np.uint8)
        m = detect.colour_mask(region, [{"hex": "#14151B", "tolerance": 1}], 15)
        self.assertEqual(m[0, 0], 255)
        self.assertTrue(0 < m[0, 1] < 255)
        self.assertEqual(m[0, 2], 0)

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
    def test_import_suite(self):
        with open(SUITE, encoding="utf-8") as f:
            suite = json.load(f)
        cfg = from_suite(suite)
        self.assertEqual(cfg["detector"]["pixel1"], {"x": 960, "y": 57, "color": "#23252D", "tolerance": 1})
        self.assertTrue(cfg["detector"]["score_box_only"])
        self.assertEqual(cfg["highlights"]["boxes"][4], {"x": 1267, "y": 97, "w": 610, "h": 32, "enabled": True})
        self.assertEqual(cfg["highlights"]["color_right"], "#771116")
        self.assertEqual(cfg["highlights"]["fade"], 0.15)
        self.assertEqual(cfg["scorebox"]["overrides"][2], {"x": 723, "y": 15, "w": 474, "h": 31, "enabled": True})
        self.assertEqual(cfg["scorebox"]["boost"]["lightness_left"], 10.0)
        self.assertEqual(cfg["spectated"]["pixels"][5], {"x": 1664, "y": 145, "enabled": True})
        self.assertEqual(normalise_title(cfg["capture"]["window_title"]), "call of duty")
        self.assertEqual(cfg["capture"]["screen"], 0)
        # The defaults are the Suite's settings already.
        for section in ("detector", "highlights", "scorebox", "spectated"):
            self.assertEqual(cfg[section], DEFAULTS[section], section)

    def test_set_path(self):
        cfg = set_path(DEFAULTS, "highlights.boxes.2.w", 500)
        self.assertEqual(cfg["highlights"]["boxes"][2]["w"], 500)
        self.assertEqual(DEFAULTS["highlights"]["boxes"][2]["w"], 610)


class ServerTests(unittest.TestCase):
    def test_overlay_stream_and_settings(self):
        from codhud.app import HudApp, start
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Config(os.path.join(tmp, "config.json"))
            cfg.update({"http_port": 8795})
            app = HudApp(cfg, demo=True)
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
                self.assertEqual(set(msg["mask_data"]), {"score"} | {f"row{i}" for i in range(8)})
                self.assertTrue(msg["mask_data"]["score"]["png"].startswith("data:image/png;base64,"))
                self.assertIn("highlights", msg["look"])
                # A change of look reaches the overlay.
                post("/api/config", {"path": "highlights.opacity", "value": 0.4})
                deadline = time.time() + 5
                while time.time() < deadline:
                    line = stream.readline()
                    if line.startswith(b"data:") and "look" in json.loads(line[5:]):
                        break
                self.assertEqual(json.loads(line[5:])["look"]["highlights"]["opacity"], 0.4)
                stream.close()
                self.assertEqual(cfg.get()["highlights"]["opacity"], 0.4)
                with open(SUITE, encoding="utf-8") as f:
                    post("/api/import", json.load(f))
                self.assertEqual(cfg.get()["highlights"]["opacity"], 0.25)
                post("/api/reset", {"section": "scorebox"})
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
        ingests = []

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
                        "left": {"name": "OPTIC TEXAS", "short": "OPTX", "logo_url": f"http://127.0.0.1:{port}/logo.png"},
                        "right": {"name": "ATLANTA FAZE", "short": "ATL"}}})
                elif self.path == "/logo.png":
                    self._send(png, "image/png")
                else:
                    self.send_error(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/api/ingest":
                    ingests.append(json.loads(body))
                    self._send({"ok": True, "global": {}})
                else:
                    self.send_error(404)

        engine = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
        engine.daemon_threads = True
        threading.Thread(target=engine.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            old = appmod.LOGO_DIR
            appmod.LOGO_DIR = os.path.join(tmp, "logos")
            cfg = Config(os.path.join(tmp, "config.json"))
            cfg.update({"http_port": 8796})
            app = HudApp(cfg, demo=True)
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
                self.assertEqual(sent["fields"]["map_name"], "COLOSSUS")
                self.assertEqual(sent["fields"]["map_mode"], "HARDPOINT")

                reply = post("/api/engine/use-global")
                self.assertIn("OPTIC TEXAS", reply["summary"])
                match = cfg.get()["match"]
                self.assertEqual((match["left"]["name"], match["left"]["tricode"]), ("OPTIC TEXAS", "OPTX"))
                self.assertEqual((match["right"]["name"], match["right"]["tricode"]), ("ATLANTA FAZE", "ATL"))
                self.assertEqual(match["left"]["logo"], "logos/engine-OPTX.png")
                self.assertEqual(match["right"]["logo"], "assets/logos/st-clair-saints.png")
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
