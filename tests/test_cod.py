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
                frame = urllib.request.urlopen(base + "/api/frame.jpg", timeout=5).read()
                self.assertEqual(frame[:2], b"\xff\xd8")
                px = json.loads(urllib.request.urlopen(base + "/api/pixel?x=960&y=57", timeout=5).read())
                self.assertEqual(px["hex"], "#23252D")
            finally:
                app.stop()


if __name__ == "__main__":
    unittest.main()
