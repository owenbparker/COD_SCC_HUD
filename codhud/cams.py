"""Player webcams on the HUD.

Each player can run the SCC Player Cam app on their PC, pick Call of Duty
and enter their in-game name. Their cam connects straight to the HUD over
the local network (WebRTC, peer to peer); this app only passes the
connection messages between the cam and the overlays.

A cam is matched to a player on the scoreboards by name: the name read off
the scoreboard, or the player that name is linked to in the GFX Engine's
team rosters ("Save IGN to player"), or a cam picked by hand. The overlay
shows the cam of the player being spectated.

(The name travels as "riot_id", the field Player Cam's VALORANT mode uses.)
"""

import json
import re
import threading
import time

from .log import log
from .names import same_player

LONG_POLL = 20          # seconds a poll waits for a message
GONE_AFTER = LONG_POLL + 15


def clean_cam_name(text):
    """A cam's name: the player's in-game name ("Name", or "Name#1234" with
    the Activision ID's number, which is dropped); raises ValueError when empty."""
    name = re.sub(r"\s*#.*$", "", str(text or "")).strip()
    if not name or len(name) > 32:
        raise ValueError("Enter your in-game name")
    return name


def match_players(cam_names, players, assign=None):
    """{cam name: {"team", "slot", "name"}} for the cams that are a player on
    the scoreboards. players: [{"team", "slot", "name", "also": [names]}]
    ("also": the roster player's name and in-game ID it's linked to).
    assign: {player name: cam name} picked by hand, which wins."""
    out, taken = {}, set()
    assign = assign or {}
    for p in players:
        cam = assign.get(p.get("name") or "")
        if cam and cam in cam_names and cam not in out:
            out[cam] = {"team": p["team"], "slot": p["slot"], "name": p["name"]}
            taken.add((p["team"], p["slot"]))
    for cam in cam_names:
        if cam in out:
            continue
        for p in players:
            k = (p.get("team"), p.get("slot"))
            if k in taken or not p.get("name"):
                continue
            if any(same_player(cam, n) for n in [p["name"]] + list(p.get("also") or []) if n):
                out[cam] = {"team": k[0], "slot": k[1], "name": p["name"]}
                taken.add(k)
                break
    return out


class CamHub:
    """Passes WebRTC connection messages between player cams (publishers)
    and the overlays showing them (viewers), and keeps track of whose cam is
    whose."""

    def __init__(self):
        self._cond = threading.Condition()
        self._inbox = {}       # peer id -> [messages]
        self._seen = {}        # peer id -> last poll
        self._riot = {}        # publisher id -> the player's in-game name
        self._label = {}       # publisher id -> camera name

    def poll(self, peer, riot_id=None, label=None, wait=LONG_POLL):
        """A cam or overlay checking in; waits up to `wait` seconds for
        messages. A cam passes its player's name ("" when it leaves)."""
        if not peer or len(peer) > 64:
            raise ValueError("Bad peer id")
        deadline = time.time() + max(0.0, min(float(wait or 0), LONG_POLL))
        with self._cond:
            self._touch(peer)
            if riot_id is not None:
                rid = clean_cam_name(riot_id) if riot_id else ""
                if rid:
                    for other, r in list(self._riot.items()):
                        if other != peer and r.lower() == rid.lower():
                            self._riot.pop(other)   # one cam per player: the newest wins
                            self._inbox.setdefault(other, []).append({"from": "hub", "type": "replaced"})
                if self._riot.get(peer, "") != rid:
                    log(f"Player cam {'connected' if rid else 'left'}: {rid or self._riot.get(peer, '')}"
                        + (f" ({label})" if label and rid else ""))
                if rid:
                    self._riot[peer] = rid
                else:
                    self._riot.pop(peer, None)
                if label is not None:
                    self._label[peer] = str(label)[:60]
                elif not rid:
                    self._label.pop(peer, None)   # (a cam leaving)
            while not self._inbox.get(peer) and time.time() < deadline:
                self._cond.wait(deadline - time.time())
                self._touch(peer)
            return {"riot_id": self._riot.get(peer), "messages": self._inbox.pop(peer, [])}

    def send(self, to, sender, data, ip=None):
        """A message for a peer. ip: where the sender is, as this app sees it
        (WebRTC hides a PC's own address behind a ".local" name that doesn't
        reach across networks: the other end puts this address in its place)."""
        if not isinstance(data, dict) or len(json.dumps(data)) > 64 * 1024:
            raise ValueError("Bad message")
        with self._cond:
            if to not in self._seen:
                raise ValueError("That cam or overlay isn't connected")
            box = self._inbox.setdefault(to, [])
            box.append({**data, "from": sender, **({"ip": ip} if ip else {})})
            del box[:-50]
            self._cond.notify_all()

    def _touch(self, peer):
        now = time.time()
        self._seen[peer] = now
        for p, t in list(self._seen.items()):
            if now - t > GONE_AFTER:
                for d in (self._seen, self._inbox, self._label):
                    d.pop(p, None)
                rid = self._riot.pop(p, None)
                if rid:
                    log(f"Player cam disconnected: {rid}")

    def all_cams(self, players=(), assign=None):
        """Every cam connected, with or without a name yet (the GFX Engine's
        Camera Control Room): [{"id", "riot_id", "label", "player"}]."""
        with self._cond:
            now = time.time()
            live = [(p, self._riot.get(p, ""), label) for p, label in self._label.items()
                    if now - self._seen.get(p, 0) < GONE_AFTER]
        matched = match_players([rid for _, rid, _ in live if rid], players, assign)
        return [{"id": p, "riot_id": rid, "label": label, "player": matched.get(rid)} for p, rid, label in live]

    def publishers(self, players=(), assign=None):
        """[{"id", "riot_id", "label", "player": {"team", "slot", "name"} or None}]."""
        with self._cond:
            now = time.time()
            live = [(p, rid) for p, rid in self._riot.items() if now - self._seen.get(p, 0) < GONE_AFTER]
            labels = dict(self._label)
        matched = match_players([rid for _, rid in live], players, assign)
        return [{"id": p, "riot_id": rid, "label": labels.get(p, ""), "player": matched.get(rid)} for p, rid in live]
