// Player webcams on the overlay: while the observer is spectating a player
// whose cam is connected (the SCC Player Cam app, Call of Duty mode, with
// their in-game name), their cam shows in CDL's place, left of the game's
// player card. Switching between players with cams is a straight cut; the
// box slides in and out when there's a cam to show or not.
//
// The cams connect straight to this page over the local network (WebRTC,
// peer to peer); the app only passes the connection messages along (see
// codhud/cams.py). Every connected cam is kept open, so switching players
// is instant.
(function () {
  var ME = Math.random().toString(16).slice(2, 18);
  var box = document.getElementById("cam"), videos = box.querySelector(".videos"), dummy = box.querySelector(".dummy");
  var peers = {};     // cam (publisher) id -> { pc, video, started }
  var registered = false;

  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || r.status); return d; }); });
  }

  function drop(id) {
    var p = peers[id];
    if (!p) return;
    delete peers[id];
    try { p.pc.close(); } catch (e) { /* already closed */ }
    p.video.remove();
  }

  function gathered(pc) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise(function (resolve) {
      pc.addEventListener("icegatheringstatechange", function () { if (pc.iceGatheringState === "complete") resolve(); });
      setTimeout(resolve, 2500);   // the local network's addresses come quickly
    });
  }

  // WebRTC hides each PC's address behind a random "<id>.local" name, which only
  // resolves on that PC's own network: a cam on another network (VLAN) never
  // connects. The HUD passes on where each message came from (m.ip), and that
  // address goes in place of the hidden one.
  function unhide(sdp, ip, hubHost) {
    if (ip && /^(127\.|::1$|::ffff:127\.)/.test(ip)) ip = /^localhost$/i.test(hubHost) ? "127.0.0.1" : hubHost;
    if (!sdp || !ip || !/^\d+\.\d+\.\d+\.\d+$/.test(ip)) return sdp;
    return sdp.replace(/^(a=candidate:\S+ \d+ \S+ \d+ )\S+\.local( \d+ typ host)/gm, "$1" + ip + "$2");
  }

  function connect(id) {
    var video = document.createElement("video");
    video.autoplay = video.muted = video.playsInline = true;
    videos.appendChild(video);
    var pc = new RTCPeerConnection({ iceServers: [] });
    peers[id] = { pc: pc, video: video, started: Date.now() };
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = function (e) {
      video.srcObject = e.streams[0] || new MediaStream([e.track]);
      video.play().catch(function () {});
    };
    pc.onconnectionstatechange = function () {
      if (pc.connectionState === "failed" || pc.connectionState === "closed") drop(id);
    };
    pc.createOffer()
      .then(function (offer) { return pc.setLocalDescription(offer); })
      .then(function () { return gathered(pc); })
      .then(function () { return post("/api/cam/send", { to: id, from: ME, data: { type: "offer", sdp: pc.localDescription.sdp } }); })
      .catch(function () { drop(id); });
  }

  (async function inbox() {
    for (;;) {
      try {
        var r = await post("/api/cam/poll", { id: ME, wait: registered ? 20 : 0 });
        registered = true;
        (r.messages || []).forEach(function (m) {
          var p = peers[m.from];
          if (!p) return;
          if (m.type === "answer" && p.pc.signalingState === "have-local-offer") {
            p.pc.setRemoteDescription({ type: "answer", sdp: unhide(m.sdp, m.ip, location.hostname) }).catch(function () { drop(m.from); });
          } else if (m.type === "bye") drop(m.from);
        });
      } catch (e) { await sleep(2000); }
    }
  })();
  addEventListener("pagehide", function () {
    Object.keys(peers).forEach(function (id) {
      if (navigator.sendBeacon) navigator.sendBeacon("/api/cam/send",
        new Blob([JSON.stringify({ to: id, from: ME, data: { type: "bye" } })], { type: "text/plain" }));
    });
  });

  var lastKey = "";
  // Called by the overlay every frame; show: the CoD caster HUD is up and cams are on.
  window.renderCam = function (s, look, show) {
    var pubs = s.cams || [], key = pubs.map(function (p) { return p.id; }).join(",") + "|" + (look.cams !== false) + "|" + registered;
    if (key !== lastKey) {
      lastKey = key;
      var wanted = {};
      pubs.forEach(function (p) {
        if (look.cams === false) return;
        wanted[p.id] = true;
        if (registered && !peers[p.id]) connect(p.id);
      });
      Object.keys(peers).forEach(function (id) { if (!wanted[id]) drop(id); });
    }
    pubs.forEach(function (p) {
      var peer = peers[p.id];
      if (peer && peer.pc.connectionState !== "connected" && Date.now() - peer.started > 10000) { drop(p.id); lastKey = ""; }
    });
    // The spectated player, and their cam if they have one.
    var row = typeof s.spec === "number" ? s.spec : -1, team = row < 0 ? "" : row < 4 ? "left" : "right", slot = row % 4 + 1;
    var cam = row < 0 ? null : pubs.filter(function (p) { return p.player && p.player.team === team && p.player.slot === slot; })[0] || null;
    var peer = cam && peers[cam.id], ready = !!(peer && peer.video.readyState >= 2);
    Object.keys(peers).forEach(function (id) { peers[id].video.classList.toggle("on", !!cam && id === cam.id); });
    // The demo: everyone has a stand-in cam (so its place can be seen without real ones).
    var fake = !!s.demo && row >= 0 && !ready;
    dummy.classList.toggle("on", fake);
    if (fake) dummy.querySelector("b").textContent = (s.names || [])[row] || "";
    box.classList.toggle("on", show && (ready || fake));
  };
})();
