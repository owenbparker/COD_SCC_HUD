"""Start the CoD HUD without a window:  python -m codhud   (add --demo for a made-up match).

For the desktop app, run CodHUD.exe (or python -m codhud.gui).
"""

import argparse
import os
import time

from .app import HudApp, start
from .config import Config


def build_app(args):
    config = Config()
    if not os.path.exists(config.path):
        config.save()
    if args.port:
        config.update({"http_port": args.port})
    return HudApp(config, demo=args.demo)


def add_arguments(parser):
    parser.add_argument("--demo", action="store_true", help="play a made-up match instead of reading the game")
    parser.add_argument("--port", type=int, help="web server port (saved to config.json)")


def main():
    parser = argparse.ArgumentParser(description="CoD caster HUD overlay")
    add_arguments(parser)
    app = build_app(parser.parse_args())
    start(app)
    print("  Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
