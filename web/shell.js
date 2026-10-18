// The control panel's layout, shared by every SCC HUD: a sidebar of pages
// (Overview, HUD Editor, the event setup, the live game) and one page on
// screen at a time, under a breadcrumb.
//
// The page lists its pages in window.SHELL = {app, pages: [{id, label,
// icon, group}]}, and each <section> says which page(s) it's on with
// data-page="live" (or "editor live"). Everything else on the page (its
// controls and their scripts) is left as it is.
(function () {
  "use strict";
  const cfg = window.SHELL;
  if (!cfg) return;
  const ICONS = {
    overview: '<path d="M12 2l9 5v10l-9 5-9-5V7z" fill="currentColor" opacity=".9"/>',
    editor: '<path d="M3 5h7v14H3zM14 5h7v14h-7z" fill="currentColor"/>',
    teams: '<circle cx="9" cy="8" r="3.2" fill="currentColor"/><circle cx="17" cy="9" r="2.6" fill="currentColor" opacity=".8"/><path d="M3 19c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5zM14.5 19c.2-2.2-.6-3.8-1.8-4.8 3.5-.9 7.3.8 7.3 4.8z" fill="currentColor"/>',
    tournament: '<path d="M4 4l16 16M20 4L4 20M3 9V3h6M21 9V3h-6" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round"/>',
    talent: '<circle cx="10" cy="8" r="4" fill="currentColor"/><path d="M2 21c0-4.4 3.6-7 8-7 1.4 0 2.7.3 3.8.8A5 5 0 0013 21z" fill="currentColor"/><circle cx="18" cy="17" r="3" fill="none" stroke="currentColor" stroke-width="2"/>',
    live: '<rect x="2" y="5" width="20" height="14" rx="3" fill="currentColor" opacity=".35"/><rect x="5" y="8" width="14" height="8" rx="1.5" fill="currentColor"/>',
    shortcuts: '<rect x="2" y="5" width="20" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M6 10h2M10 10h2M14 10h2M6 14h12" stroke="currentColor" stroke-width="2"/>',
    draft: '<path d="M4 4h16v4H4zM4 10h7v10H4zM13 10h7v10h-7z" fill="currentColor"/>',
    sponsors: '<path d="M12 3l2.6 5.6 6 .7-4.5 4.1 1.2 6L12 16.4 6.7 19.4l1.2-6L3.4 9.3l6-.7z" fill="currentColor"/>',
    cams: '<rect x="2" y="6" width="14" height="12" rx="2" fill="currentColor"/><path d="M16 10l6-3v10l-6-3z" fill="currentColor"/>',
    settings: '<circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1" stroke="currentColor" stroke-width="2.2"/>',
    data: '<path d="M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" fill="none" stroke="currentColor" stroke-width="2"/>',
    log: '<path d="M5 4h14v16H5zM8 8h8M8 12h8M8 16h5" fill="none" stroke="currentColor" stroke-width="2"/>',
  };
  const icon = name => `<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">${ICONS[name] || ICONS.overview}</svg>`;
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  document.body.classList.add("shell");
  const header = document.querySelector("header");
  const main = document.querySelector("main");
  const aside = document.querySelector("aside");
  // Everything goes in one content area, one page at a time: the preview,
  // outputs and log first.
  if (aside) { main.prepend(...aside.children); aside.remove(); }
  const layout = document.querySelector(".layout") || main.parentNode;

  // The sidebar.
  const side = document.createElement("nav");
  side.id = "shell-side";
  const brand = `<div class="shell-brand"><img src="${esc(cfg.logo || "assets/app_icon.png")}" alt="">
    <div><b>${esc(cfg.app)}</b><span id="shell-page-name"></span></div></div>`;
  let items = "", group = null;
  for (const p of cfg.pages) {
    if ((p.group || "") !== group) {
      group = p.group || "";
      if (group) items += `<div class="shell-group"><span>${esc(group)}</span></div>`;
    }
    items += `<a class="shell-item" href="#/${p.id}" data-id="${p.id}">${icon(p.icon || p.id)}<span>${esc(p.label)}</span></a>`;
  }
  side.innerHTML = brand + `<div class="shell-items">${items}</div><div class="shell-foot" id="shell-foot"></div>`;
  document.body.insertBefore(side, document.body.firstChild);

  // The top bar: a breadcrumb, then the header's own controls on the right.
  const crumb = document.createElement("div");
  crumb.className = "shell-crumb";
  if (header) {
    const h1 = header.querySelector("h1");
    if (h1) h1.hidden = true;
    for (const img of header.querySelectorAll(":scope > img")) img.hidden = true;
    header.insertBefore(crumb, header.firstChild);
    // "Change client" and the version / update live in the sidebar's foot.
    const foot = document.getElementById("shell-foot");
    for (const el of header.querySelectorAll('a[href="/start"], #switch, .version, #update')) foot.appendChild(el);
  }
  const content = document.createElement("div");
  content.id = "shell-content";
  if (header) content.appendChild(header);
  content.appendChild(layout);
  document.body.appendChild(content);

  function show(id) {
    const page = cfg.pages.find(p => p.id === id) || cfg.pages[0];
    for (const s of main.querySelectorAll(":scope > section")) {
      const on = (s.dataset.page || "").split(/\s+/).includes(page.id);
      s.hidden = !on;
    }
    for (const a of side.querySelectorAll(".shell-item")) a.classList.toggle("on", a.dataset.id === page.id);
    crumb.innerHTML = `<span class="shell-crumb-icon">${icon(page.icon || page.id)}</span><span class="muted">${esc(cfg.app)}</span>` +
      `<span class="sep">/</span><span>${esc(page.label)}</span>`;
    document.getElementById("shell-page-name").textContent = page.label;
    document.body.dataset.page = page.id;
    try { localStorage.setItem("shell-page-" + cfg.app, page.id); } catch (e) { /* private window */ }
    // Previews size themselves to their box: let them, now they're showing.
    requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
    layout.scrollTop = 0;
  }
  function route() {
    let id = location.hash.replace(/^#\/?/, "");
    if (!id) { try { id = localStorage.getItem("shell-page-" + cfg.app) || ""; } catch (e) { id = ""; } }
    show(id);
  }
  window.addEventListener("hashchange", route);
  route();
  window.shellShow = id => { location.hash = "#/" + id; };
})();
