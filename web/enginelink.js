// The control panel's "GFX Engine" page, the same in every SCC HUD (the GFX
// Engine repository keeps the original, in hud-link/): find SCC GFX Engines on
// the network, connect to one (this HUD's game and series go to it), and take
// its global configuration (team names, short names, logos) into the HUD.
//
// Load it before shell.js, with {"id": "engine", "label": "GFX Engine",
// "icon": "engine"} in window.SHELL.pages. The HUD serves /api/engine
// (see enginelink.py). window.onEngineGlobal(reply), if set, runs after
// "Use Global Information" (to reload the HUD's team fields).
(function () {
  "use strict";
  const main = document.querySelector("main");
  if (!main) return;
  const style = document.createElement("style");
  style.textContent = `
    .eng-status { display: flex; align-items: center; gap: 12px; padding: 14px; border: 1px solid var(--line, #2a2f34); border-radius: 10px; margin: 6px 0 14px; }
    .eng-status img { width: 44px; height: 44px; }
    .eng-status .dot { width: 10px; height: 10px; border-radius: 50%; background: #6b737c; flex: 0 0 10px; }
    .eng-status.ok .dot { background: #22c55e; box-shadow: 0 0 0 4px rgba(34,197,94,.18); }
    .eng-status.bad .dot { background: #ef4444; }
    .eng-status b { display: block; font-size: 15px; }
    .eng-status .sub { color: var(--muted, #8b939b); font-size: 12px; }
    .eng-status .grow { flex: 1; min-width: 0; }
    .eng-list { display: flex; flex-direction: column; gap: 6px; margin: 8px 0 12px; }
    .eng-item { display: flex; align-items: center; gap: 10px; padding: 9px 12px; border: 1px solid var(--line, #2a2f34); border-radius: 8px; }
    .eng-item .grow { flex: 1; min-width: 0; }
    .eng-item .sub { color: var(--muted, #8b939b); font-size: 12px; }
    .eng-item.current { border-color: #f5c518; }
    .eng-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .eng-row input { flex: 1; min-width: 180px; }
    .eng-global { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--line, #2a2f34); }
    .eng-global button.big { width: 100%; padding: 12px; font-size: 15px; font-weight: 700; background: #f5c518; color: #141414; border: 0; }
    .eng-global button.big:disabled { opacity: .45; }
    .eng-teams { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
    .eng-team { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; background: rgba(255,255,255,.03); border: 1px solid var(--line, #2a2f34); }
    .eng-team img { width: 34px; height: 34px; object-fit: contain; }
    .eng-team .sub { color: var(--muted, #8b939b); font-size: 12px; }
    .eng-spin { display: inline-block; width: 12px; height: 12px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: eng-spin .8s linear infinite; vertical-align: -2px; }
    @keyframes eng-spin { to { transform: rotate(360deg); } }`;
  document.head.appendChild(style);

  const section = document.createElement("section");
  section.className = "wide";
  section.dataset.page = "engine";
  section.innerHTML = `
    <h2>Connect to GFX Engine</h2>
    <div class="eng-status" id="eng-status"><span class="dot"></span><div class="grow"><b id="eng-title">Not connected</b><span class="sub" id="eng-sub">Find an SCC GFX Engine on the network, then connect: this HUD's game stats and series score go to its graphics.</span></div>
      <button id="eng-disconnect" hidden>Disconnect</button></div>
    <div class="eng-row"><button id="eng-scan">Scan the network</button><span class="note" id="eng-scan-note"></span></div>
    <div class="eng-list" id="eng-list"></div>
    <div class="eng-row"><input id="eng-url" placeholder="Or type its address, e.g. 192.168.1.20" spellcheck="false"><button id="eng-connect">Connect</button></div>
    <div class="eng-global" id="eng-global" hidden>
      <button class="big" id="eng-use">Use Global Information</button>
      <p class="note">Takes the team names, short names and logos from the engine's Global configuration into this HUD (left and right as they are there).</p>
      <div class="eng-teams" id="eng-teams"></div>
    </div>
    <p class="note">Sent to the engine about once a second: the series (team names, short names, scores, best of), which updates its Global configuration, and the live game's stats, which its graphics read as the data table "<span id="eng-source">this HUD</span>" (e.g. <code>{{<span class="eng-src">HUD</span>!Field}}</code>).</p>`;
  main.appendChild(section);

  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  async function call(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.error) throw new Error(data.error || `${path} failed (${r.status})`);
    return data;
  }
  function note(msg, bad) { const n = $("eng-scan-note"); n.textContent = msg || ""; n.style.color = bad ? "#ff8a8a" : ""; }

  let status = null;
  function render(s) {
    status = s;
    const box = $("eng-status");
    box.className = "eng-status" + (s.connected ? (s.ok ? " ok" : " bad") : "");
    $("eng-title").textContent = s.connected ? `Connected to ${s.name || s.url}` : "Not connected";
    $("eng-sub").textContent = !s.connected
      ? "Find an SCC GFX Engine on the network, then connect: this HUD's game stats and series score go to its graphics."
      : s.ok ? `${s.url} · sending (last ${s.age == null ? "–" : s.age + " s"} ago)` : `${s.url} · ${s.error || "connecting…"}`;
    $("eng-disconnect").hidden = !s.connected;
    $("eng-global").hidden = !s.url;
    $("eng-use").disabled = !s.url;
    for (const el of document.querySelectorAll("#eng-source, .eng-src")) el.textContent = s.source || "HUD";
    if (document.activeElement !== $("eng-url") && !$("eng-url").value && s.url) $("eng-url").value = s.url.replace(/^http:\/\//, "");
    const list = $("eng-list");
    list.innerHTML = (s.found || []).map(e => `
      <div class="eng-item${s.connected && e.url === s.url ? " current" : ""}">
        <div class="grow"><b>${esc(e.name)}</b>${e.this_pc ? ' <span class="badge">THIS PC</span>' : ""}
          <div class="sub">${esc(e.url)}${e.version ? " · " + esc(e.version) : ""}</div></div>
        <button data-url="${esc(e.url)}">${s.connected && e.url === s.url ? "Connected" : "Connect"}</button></div>`).join("");
  }
  async function load() {
    try { render(await fetch("/api/engine").then(r => r.json())); } catch (e) { /* the HUD is restarting */ }
  }
  async function connect(url) {
    note("Connecting…");
    try { render(await call("/api/engine/connect", { url })); note(""); } catch (e) { note(e.message, true); }
  }
  $("eng-scan").addEventListener("click", async () => {
    const btn = $("eng-scan");
    btn.disabled = true;
    note("");
    btn.innerHTML = '<span class="eng-spin"></span> Scanning…';
    try {
      const s = await call("/api/engine/scan");
      render(s);
      note(s.found.length ? `Found ${s.found.length}` : "None found. Is the engine running, with 'Let other PCs use this engine' on (Outputs)? You can also type its address.", !s.found.length);
    } catch (e) { note(e.message, true); }
    btn.disabled = false;
    btn.textContent = "Scan the network";
  });
  $("eng-list").addEventListener("click", e => { const b = e.target.closest("button[data-url]"); if (b) connect(b.dataset.url); });
  $("eng-connect").addEventListener("click", () => connect($("eng-url").value));
  $("eng-url").addEventListener("keydown", e => { if (e.key === "Enter") connect($("eng-url").value); });
  $("eng-disconnect").addEventListener("click", async () => { render(await call("/api/engine/disconnect")); });
  $("eng-use").addEventListener("click", async () => {
    const btn = $("eng-use");
    btn.disabled = true;
    try {
      const r = await call("/api/engine/use-global");
      const g = r.global || {};
      $("eng-teams").innerHTML = ["left", "right"].map(side => {
        const t = g[side] || {};
        return `<div class="eng-team">${t.logo_url ? `<img src="${esc(t.logo_url)}" alt="">` : ""}<div><b>${esc(t.name || "—")}</b>
          <div class="sub">${esc(t.short || "")} · ${side}</div></div></div>`;
      }).join("");
      note(r.summary || "Done");
      if (window.onEngineGlobal) window.onEngineGlobal(r);
    } catch (e) { note(e.message, true); }
    btn.disabled = false;
  });
  load();
  setInterval(() => { if (!section.hidden) load(); }, 2000);
})();
