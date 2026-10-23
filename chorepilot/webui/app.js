/* ChorePilot dashboard. Plain JS, no framework, no build step, no third-party code.
   Talks only to this ChorePilot server (same origin, CSRF header on every write). */
"use strict";
(() => {

/* ------------------------------------------------------------------ tiny helpers */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const RAW = Symbol("raw");
const raw = s => ({ [RAW]: true, s: String(s) });
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = v => v == null || v === false ? "" : Array.isArray(v) ? v.map(fmt).join("")
  : v[RAW] ? v.s : esc(v);
function html(strs, ...vals) {
  let out = strs[0];
  vals.forEach((v, i) => { out += fmt(v) + strs[i + 1]; });
  return raw(out);
}
const put = (el, t) => { el.innerHTML = t[RAW] ? t.s : esc(t); return el; };
const cap = s => { s = String(s || "").replace(/_/g, " "); return s.charAt(0).toUpperCase() + s.slice(1); };
const plural = (n, a, b) => `${n} ${n === 1 ? a : (b || a + "s")}`;
const store = {
  get(k, d) { try { const v = localStorage.getItem("cp." + k); return v == null ? d : v; } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("cp." + k, v); } catch (e) { /* private mode */ } },
};
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const phone = () => matchMedia("(max-width: 699px)").matches;

/* ------------------------------------------------------------------ icons (24px, 1.75 stroke) */
const P = {
  home: '<path d="M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z"/>',
  devices: '<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>',
  chores: '<path d="M10 6.5h9.5M10 12h9.5M10 17.5h9.5"/><path d="m4 6.6 1.4 1.4 2.4-2.6M4 12.1l1.4 1.4 2.4-2.6M4 17.6 5.4 19l2.4-2.6"/>',
  activity: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  inbox: '<path d="M4 13.5 6.5 5h11l2.5 8.5V19a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1z"/><path d="M4 13.5h4.5a3.5 3.5 0 0 0 7 0H20"/>',
  settings: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  bell: '<path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 1.5H5z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
  mic: '<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5"/>',
  send: '<path d="M12 19V5M6 11l6-6 6 6"/>',
  ask: '<path d="M20 11.5a8 8 0 0 1-11.7 7.1L4 20l1.3-4.1A8 8 0 1 1 20 11.5z"/>',
  light: '<path d="M9.5 18h5M10.5 21h3"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.2h5c0-.8.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
  climate: '<path d="M14 14.3V5a2 2 0 0 0-4 0v9.3a4 4 0 1 0 4 0z"/><path d="M12 9v7"/>',
  valve: '<path d="M12 3.5s-5.5 6-5.5 10a5.5 5.5 0 0 0 11 0c0-4-5.5-10-5.5-10z"/><path d="M9.5 14a2.5 2.5 0 0 0 2.5 2.5"/>',
  coffee_maker: '<path d="M5 9.5h11v4.5a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5z"/><path d="M16 11h1.5a2.5 2.5 0 0 1 0 5H16M8 4v2.5M11 4v2.5M14 4v2.5"/>',
  oven: '<rect x="4" y="4" width="16" height="16" rx="2.5"/><path d="M4 9h16"/><rect x="7.5" y="12" width="9" height="5" rx="1"/><path d="M7.5 6.5h.01M10.5 6.5h.01"/>',
  fridge: '<rect x="6" y="3" width="12" height="18" rx="2.5"/><path d="M6 10h12M9 6v1.5M9 13v2.5"/>',
  vacuum: '<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="3"/><path d="M8 6.8h8"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2"/>',
  unlock: '<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8 10.5V8a4 4 0 0 1 7.7-1.5"/><path d="M12 14.5v2"/>',
  garage_door: '<path d="M3.5 9.5 12 4l8.5 5.5V20M3.5 9.5V20"/><path d="M7 20v-8h10v8M7 15h10M7 17.5h10"/>',
  cover: '<path d="M4 4h16M6 4v10M18 4v10M6 7.5h12M6 11h12M6 14h12M12 14v3.5"/><circle cx="12" cy="19" r="1.2"/>',
  alarm_panel: '<path d="M12 3.5 19 6v5.5c0 4.3-2.9 7.8-7 9-4.1-1.2-7-4.7-7-9V6z"/><path d="m9 12 2 2 4-4"/>',
  ev_charger: '<path d="M13 3 6 13.5h5L10 21l7-10.5h-5z"/>',
  water_heater: '<path d="M12 21c3.3 0 6-2.5 6-6 0-4-3-6-4.5-9.5C12 8 10 9 9.5 11c-.8-1-1-2-1-3C6.9 9.6 6 12 6 15c0 3.5 2.7 6 6 6z"/>',
  air_purifier: '<path d="M3.5 9h11a2.5 2.5 0 1 0-2.5-2.5M3.5 13.5h15a2.5 2.5 0 1 1-2.5 2.5M3.5 18h7"/>',
  humidifier: '<path d="M12 4s-5 5.6-5 9.5a5 5 0 0 0 10 0C17 9.6 12 4 12 4z"/><path d="M15 2.5v2M18.5 5l-1.4 1.4M19.5 9h-2"/>',
  media_player: '<rect x="6" y="3" width="12" height="18" rx="2.5"/><circle cx="12" cy="14.5" r="3"/><path d="M12 7.5h.01"/>',
  dishwasher: '<rect x="4.5" y="3.5" width="15" height="17" rx="2.5"/><path d="M4.5 8h15M8 5.8h.01M11 5.8h.01"/><path d="M8.5 13.2c1.2-1 2.3-1 3.5 0s2.3 1 3.5 0M8.5 16.4c1.2-1 2.3-1 3.5 0s2.3 1 3.5 0"/>',
  washer: '<rect x="4.5" y="3" width="15" height="18" rx="2.5"/><circle cx="12" cy="13.5" r="4.5"/><path d="M7.5 6.3h.01M10.5 6.3h.01M9.6 14.2c.8-.7 1.6-.7 2.4 0s1.6.7 2.4 0"/>',
  dryer: '<rect x="4.5" y="3" width="15" height="18" rx="2.5"/><circle cx="12" cy="13.5" r="4.5"/><path d="M7.5 6.3h.01M10.5 6.3h.01M10.7 11.8c.7.9.7 2.5 0 3.4M13.3 11.8c.7.9.7 2.5 0 3.4"/>',
  device: '<rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  chev: '<path d="m9 6 6 6-6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20c1.2-3.8 4-5.5 7.5-5.5s6.3 1.7 7.5 5.5"/>',
  users: '<circle cx="9" cy="8.5" r="3.5"/><path d="M3 19.5c.9-3.2 3.2-4.7 6-4.7s5.1 1.5 6 4.7M15.5 5.2a3.5 3.5 0 0 1 0 6.6M17.5 14.9c1.7.6 2.9 2 3.5 4.6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  moon: '<path d="M19.5 14.5A8 8 0 1 1 9.5 4.5a6.5 6.5 0 0 0 10 10z"/>',
  contrast: '<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17a8.5 8.5 0 0 0 0-17z" fill="currentColor" stroke="none"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9.5 2.5V6M14.5 2.5V6M9.5 18v3.5M14.5 18v3.5M2.5 9.5H6M2.5 14.5H6M18 9.5h3.5M18 14.5h3.5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  phone: '<rect x="7" y="2.5" width="10" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  star: '<path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4L12 16.3l-4.8 2.6.9-5.4-3.9-3.8 5.4-.8z"/>',
  flame: '<path d="M12 21c3.3 0 6-2.5 6-6 0-4-3-6-4.5-9.5C12 8 10 9 9.5 11c-.8-1-1-2-1-3C6.9 9.6 6 12 6 15c0 3.5 2.7 6 6 6z"/>',
  spin: '<path d="M12 3.5a8.5 8.5 0 1 0 8.5 8.5"/>',
  alert: '<path d="M10.3 4.6 3 18a2 2 0 0 0 1.7 3h14.6A2 2 0 0 0 21 18L13.7 4.6a2 2 0 0 0-3.4 0z"/><path d="M12 10v4M12 17.2h.01"/>',
  shield: '<path d="M12 3.5 19 6v5.5c0 4.3-2.9 7.8-7 9-4.1-1.2-7-4.7-7-9V6z"/>',
  key: '<circle cx="8" cy="15.5" r="3.5"/><path d="m10.5 13 8-8M16 7.5l2.5 2.5M13.8 9.7l2 2"/>',
  power: '<path d="M12 3.5v8"/><path d="M7 6.5a7 7 0 1 0 10 0"/>',
  play: '<path d="M8 5.5v13l10.5-6.5z"/>',
  pause: '<path d="M8.5 5.5v13M15.5 5.5v13"/>',
  logout: '<path d="M14 4.5h4a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5h-4M10 16l-4-4 4-4M6 12h10"/>',
  copy: '<rect x="8" y="8" width="11" height="11" rx="2"/><path d="M16 8V6.5A1.5 1.5 0 0 0 14.5 5h-8A1.5 1.5 0 0 0 5 6.5v8A1.5 1.5 0 0 0 6.5 16H8"/>',
  plug: '<path d="M9 3.5V8M15 3.5V8M6.5 8h11v3a5.5 5.5 0 0 1-11 0zM12 16.5v4"/>',
  list: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  mark: '<path d="M4.5 10.5 12 4.5l7.5 6V19a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1z"/><path d="m8.8 13.6 2.3 2.3 4.3-4.4"/>',
  swap: '<path d="M7 7.5h11.5l-3-3M17 16.5H5.5l3 3"/>',
  timer: '<circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2 1.5M10 2.5h4"/>',
  refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3L19.5 9"/><path d="M19.5 4.5V9H15"/>',
  undo: '<path d="M9 14.5 4.5 10 9 5.5"/><path d="M4.5 10H14a5.5 5.5 0 0 1 0 11h-3"/>',
};
const ICON_OF = { garage: "garage_door", alarm: "alarm_panel" };
function icon(name, size = 24, sw = 1.75) {
  const p = P[name] || P[ICON_OF[name]] || P.device;
  return raw(`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${p}</svg>`);
}
function ring(frac, size = 36, stroke = 3.5, cls = "") {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac || 0));
  return raw(`<svg class="${cls}" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" aria-hidden="true">` +
    `<circle class="ring-track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>` +
    `<circle class="ring-fill" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}" ` +
    `stroke-linecap="round" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${(c * (1 - f)).toFixed(2)}"/></svg>`);
}
const avatar = (p, cls = "") => html`<span class="avatar ${cls}${p.me || (S.me && S.me.name && p.name === S.me.name) ? " me" : ""}" aria-hidden="true">${p.initials || "?"}</span>`;

/* ------------------------------------------------------------------ state + API */
const S = {
  me: null, home: null, route: "home", sub: null, roomFilter: "All", person: null,
  turns: [], timer: null, sheetDev: null, rawLog: false, settings: null, setupStep: 0,
};
class ApiError extends Error { constructor(msg, status, data) { super(msg); this.status = status; this.data = data; } }
async function api(path, body, method) {
  const h = { "Content-Type": "application/json" };
  if (S.me && S.me.csrf) h["X-ChorePilot-CSRF"] = S.me.csrf;
  const r = await fetch(path, { method: method || (body !== undefined ? "POST" : "GET"), headers: h,
    credentials: "same-origin", body: body !== undefined ? JSON.stringify(body) : undefined });
  let js = {};
  try { js = await r.json(); } catch (e) { /* empty */ }
  if (r.status === 401 && !path.startsWith("/api/login")) { signedOut(); throw new ApiError("Please sign in again.", 401, js); }
  if (!r.ok) throw new ApiError(js.error || `Something went wrong (HTTP ${r.status}).`, r.status, js);
  return js;
}

/* ------------------------------------------------------------------ theme */
function applyTheme() {
  let t = store.get("theme", "dark");
  if (t === "system") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", t === "light" ? "light" : "dark");
}
applyTheme();
/* ------------------------------------------------------------------ toasts */
function toast(msg, bad, action) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  put(el, html`${icon(bad ? "alert" : "check", 18, 2)}<span class="toast-msg">${msg}</span>${action ? html`<button class="toast-act" type="button">${action.label}</button>` : ""}`);
  const box = $("#toasts");
  while (box.children.length >= 2) box.firstElementChild.remove();
  box.appendChild(el);
  const life = action ? Math.min(action.ms || 8000, 15000) : bad ? 5200 : 2800;
  const fade = setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, life);
  const gone = setTimeout(() => el.remove(), life + 400);
  if (action) $(".toast-act", el).onclick = () => { clearTimeout(fade); clearTimeout(gone); el.remove(); action.run(); };
}

/* ------------------------------------------------------------------ sheets (dialogs) */
const sheets = [];
function openSheet(build, opts = {}) {
  const scrim = document.createElement("div");
  scrim.className = "sheet-scrim";
  const sheet = document.createElement("div");
  sheet.className = "sheet" + (opts.wide ? " wide" : "");
  sheet.setAttribute("role", opts.alert ? "alertdialog" : "dialog");
  sheet.setAttribute("aria-modal", "true");
  const id = "sh" + Math.random().toString(36).slice(2, 8);
  sheet.setAttribute("aria-labelledby", id);
  scrim.appendChild(sheet);
  const entry = { scrim, sheet, id, back: document.activeElement, onClose: opts.onClose };
  sheets.push(entry);
  $("#sheet-root").appendChild(scrim);
  document.body.style.overflow = "hidden";
  scrim.addEventListener("mousedown", e => { if (e.target === scrim) entry.downOnScrim = true; });
  scrim.addEventListener("click", e => { if (e.target === scrim && entry.downOnScrim) closeSheet(entry); entry.downOnScrim = false; });
  build(sheet, id, () => closeSheet(entry));
  requestAnimationFrame(() => {
    const f = sheet.querySelector("[autofocus]") || sheet.querySelector("button, input, select, textarea, [tabindex]");
    if (f) f.focus({ preventScroll: true });
  });
  return entry;
}
function closeSheet(entry) {
  entry = entry || sheets[sheets.length - 1];
  if (!entry) return;
  const i = sheets.indexOf(entry);
  if (i < 0) return;
  sheets.splice(i, 1);
  entry.scrim.remove();
  if (!sheets.length) document.body.style.overflow = "";
  if (entry.onClose) entry.onClose();
  if (entry.back && entry.back.focus && document.contains(entry.back)) entry.back.focus({ preventScroll: true });
}
document.addEventListener("keydown", e => {
  const top = sheets[sheets.length - 1];
  if (!top) return;
  if (e.key === "Escape") { e.preventDefault(); closeSheet(top); return; }
  if (e.key === "Tab") {
    const f = $$("button:not([disabled]), input:not([disabled]), select, textarea, a[href], [tabindex]:not([tabindex='-1'])", top.sheet)
      .filter(x => x.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
});
const sheetHead = (id, ico, title, sub, on) => html`<div class="sheet-grab" aria-hidden="true"></div>
  <div class="sheet-head"><span class="tile-ico ${on ? "on" : ""}">${icon(ico, 24)}</span>
  <div><h2 id="${id}">${title}</h2>${sub ? html`<p>${sub}</p>` : ""}</div>
  <button class="icon-btn sheet-close" type="button" data-close aria-label="Close">${icon("x", 20)}</button></div>`;

/* ------------------------------------------------------------------ confirmations (the human's answer) */
function askHuman({ kind, question, phrase, tier, why }) {
  return new Promise(resolve => {
    let done = false;
    const finish = v => { if (!done) { done = true; resolve(v); } };
    const ent = openSheet((sh, id, close) => {
      const sec = kind === "code";
      put(sh, html`<div class="sheet-grab" aria-hidden="true"></div>
        <div class="sheet-head"><span class="tile-ico">${icon(sec ? "key" : kind === "phrase" ? "alert" : "shield", 24)}</span>
        <div><h2 id="${id}">${sec ? "Enter your PIN" : "Confirm"}</h2>
        <p>${sec ? (why || "Security action: your PIN or authenticator code") : tier >= 2 ? "Uses water or heat, so it asks first" : "Please confirm"}</p></div></div>
        <p class="lead">${question}</p>
        <form id="hf" autocomplete="off">
        ${kind === "phrase" ? html`<label class="field"><span>Type ${phrase} to confirm</span>
          <input class="input" name="v" autocomplete="off" autocapitalize="characters" spellcheck="false" autofocus required></label>` : ""}
        ${sec ? html`<label class="field"><span>PIN or 6-digit code</span>
          <input class="input num" name="v" type="password" inputmode="numeric" autocomplete="one-time-code" maxlength="12" autofocus required></label>` : ""}
        <div class="btn-row end"><button class="btn btn-quiet" type="button" data-no>Cancel</button>
        <button class="btn btn-primary" type="submit" ${kind === "confirm" ? "autofocus" : ""}>${kind === "confirm" ? "Yes, do it" : "Confirm"}</button></div>
        </form>`);
      $("[data-no]", sh).onclick = () => { finish(null); close(); };
      $("#hf", sh).onsubmit = e => {
        e.preventDefault();
        const v = e.target.v ? e.target.v.value : true;
        finish(v); close();
      };
    }, { alert: true, onClose: () => finish(null) });
    return ent;
  });
}

/* ------------------------------------------------------------------ device actions */
async function act(dev, action, params, extra) {
  let res;
  try {
    res = await api(`/api/devices/${encodeURIComponent(dev.id)}/${encodeURIComponent(action)}`,
      Object.assign({ params: params || {} }, extra || {}));
  } catch (e) { toast(e.message, true); return null; }
  if (res.needs) {
    const v = await askHuman({ kind: res.needs, question: res.question, phrase: res.phrase, tier: res.tier, why: res.why });
    if (v == null || v === "") { toast("Cancelled. Nothing was changed."); return null; }
    const more = res.needs === "confirm" ? { token: res.token } : res.needs === "phrase" ? { phrase: v } : { code: v };
    return act(dev, action, params, more);
  }
  if (res.queued) toast(res.message || "Sent to a parent for approval.");
  else if (res.ok) toast(res.note || doneText(dev, action));
  else toast(res.error || "That didn't work.", true);
  await refresh(true);
  return res;
}
function doneText(dev, action) {
  const a = { turn_on: "On", turn_off: "Off", lock: "Locked", unlock: "Unlocked", open: "Opening", close: "Closing",
    start: "Started", pause: "Paused", resume: "Resumed", cancel: "Cancelled", start_program: "Started", water: "Watering",
    stop: "Stopped", dock: "Heading home", play: "Playing" }[action];
  return `${dev.display}: ${a || "done"}`;
}

/* ------------------------------------------------------------------ boot + gate */
async function boot() {
  $$(".mark").forEach(m => put(m, icon("mark", 18, 2.2)));
  $$("[data-version]").forEach(e => e.textContent = "");
  try {
    S.me = await api("/api/me");
    await signedIn();
  } catch (e) {
    if (e.status !== 401) { showGateError(e.message); return; }
    try {
      const st = await api("/api/setup/state");
      $$("[data-version]").forEach(x => x.textContent = st.version || "");
      if (st.needs_owner) firstRun(st.local); else signInView();
    } catch (e2) { showGateError(e2.message); }
  }
  $("#boot").remove();
}
function gate(wiz) { $("#shell").hidden = true; $("#gate").hidden = false; $("#gate").classList.toggle("wiz", !!wiz); return $("#gate-card"); }
function showGateError(m) { put(gate(), html`<span class="mark">${icon("mark", 26, 2)}</span><h1>Can't reach ChorePilot</h1><p class="lead">${m}</p><button class="btn" id="retry">Try again</button>`); $("#retry").onclick = () => location.reload(); }
function signedOut() {
  S.me = null; clearInterval(S.timer); while (sheets.length) closeSheet();
  signInView();
}
function signInView(msg) {
  const card = gate();
  card.classList.remove("wide");
  put(card, html`<span class="mark">${icon("mark", 26, 2)}</span>
    <p class="eyebrow">Sign in</p><h1>Welcome home.</h1><p class="lead">Use your name and your ChorePilot PIN.</p>
    <form id="login" autocomplete="on" novalidate>
      <label class="field"><span>Name</span><input class="input" id="lname" name="username" autocomplete="username" maxlength="64" required autocapitalize="words" spellcheck="false"></label>
      <label class="field"><span>PIN</span><input class="input num" id="lpin" name="password" type="password" inputmode="numeric" autocomplete="current-password" maxlength="64" required></label>
      <button class="btn btn-primary btn-block" type="submit">Sign in</button>
      <p class="err" id="lerr" role="alert">${msg || ""}</p>
    </form>
    <p class="hint">No PIN yet? Ask the owner to give you one in Settings, People.</p>`);
  const last = store.get("name", "");
  $("#lname").value = last;
  (last ? $("#lpin") : $("#lname")).focus();
  $("#login").onsubmit = async e => {
    e.preventDefault();
    const name = $("#lname").value.trim(), pin = $("#lpin").value.trim();
    if (!name || !pin) { $("#lerr").textContent = "Enter your name and PIN."; return; }
    try {
      const r = await api("/api/login", { name, pin });
      store.set("name", r.name);
      S.me = { csrf: r.csrf };
      S.me = await api("/api/me");
      await signedIn();
    } catch (err) {
      $("#lerr").textContent = err.message; $("#lpin").value = ""; $("#lpin").focus();
      card.classList.remove("shake"); void card.offsetWidth; card.classList.add("shake");
    }
  };
}
function firstRun(local) {
  const card = gate();
  card.classList.remove("wide");
  if (!local) {
    put(card, html`<span class="mark">${icon("mark", 26, 2)}</span><h1>Almost ready</h1>
      <p class="lead">This ChorePilot has no owner yet. For safety, the owner is created on the computer it runs on:
      open <b>http://127.0.0.1:8771</b> there, or run <code>chorepilot setup</code> in its terminal.</p>
      <button class="btn" id="retry">I've done that</button>`);
    $("#retry").onclick = () => location.reload();
    return;
  }
  const unit = /^en-(US|LR|MM)/.test(navigator.language) ? "F" : "C";
  put(card, html`<span class="mark">${icon("mark", 26, 2)}</span>
    <div class="steps-bar" aria-hidden="true"><i class="on"></i><i></i><i></i><i></i><i></i></div>
    <p class="eyebrow">Setup · step 1 of 6</p><h1>Let's set up your home.</h1>
    <p class="lead">You'll be the owner: the person who can unlock doors, add people and connect devices. It takes about two minutes, and every step can be changed later.</p>
    <form id="own" novalidate>
      <label class="field"><span>What should we call your home?</span><input class="input" name="household" placeholder="The Lee home" maxlength="60" autofocus></label>
      <label class="field"><span>Your name</span><input class="input" name="name" autocomplete="given-name" maxlength="40" required></label>
      <div class="grid-2">
        <label class="field"><span>Choose a PIN</span><input class="input num" name="pin" type="password" inputmode="numeric" autocomplete="new-password" minlength="4" maxlength="12" required aria-describedby="pinhelp"></label>
        <label class="field"><span>PIN again</span><input class="input num" name="pin2" type="password" inputmode="numeric" autocomplete="new-password" maxlength="12" required></label>
      </div><p class="hint field-help" id="pinhelp">4 to 12 digits. You'll use it to sign in, and again to unlock doors.</p>
      <div class="field"><span>Temperatures in</span><div class="seg" role="group" aria-label="Temperature unit">
        <button type="button" data-u="C" aria-pressed="${unit === "C"}">Celsius</button><button type="button" data-u="F" aria-pressed="${unit === "F"}">Fahrenheit</button></div></div>
      <p class="err" id="oerr" role="alert"></p>
      <button class="btn btn-primary btn-block" type="submit">Create my home</button>
    </form>`);
  let u = unit;
  $$("[data-u]", card).forEach(b => b.onclick = () => { u = b.dataset.u; $$("[data-u]", card).forEach(x => x.setAttribute("aria-pressed", x === b)); });
  $("#own").onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    if (!f.name.value.trim()) { $("#oerr").textContent = "Your name, please."; f.name.focus(); return; }
    if (!/^\d{4,12}$/.test(f.pin.value)) { $("#oerr").textContent = "The PIN is 4 to 12 digits."; f.pin.focus(); return; }
    if (f.pin.value !== f.pin2.value) { $("#oerr").textContent = "The two PINs don't match."; f.pin2.focus(); return; }
    try {
      const r = await api("/api/setup/owner", { household: f.household.value.trim(), name: f.name.value.trim(), pin: f.pin.value, unit: u });
      store.set("name", r.name);
      S.me = { csrf: r.csrf };
      S.me = await api("/api/me");
      S.setupStep = 1;
      wizard();
    } catch (err) { $("#oerr").textContent = err.message; }
  };
}

/* the onboarding wizard reuses the settings panels */
const WIZ = [null, "people", "devices", "assistant", "chores", "phone"];
async function wizard() {
  const card = gate(true);
  card.classList.add("wide");
  const step = S.setupStep;
  if (step >= WIZ.length) { card.classList.remove("wide"); await signedIn(); return; }
  try { S.settings = await api("/api/settings"); } catch (e) { toast(e.message, true); }
  const titles = { people: ["Who lives here?", "Add the people in your home. Kids can ask for things; a parent approves anything that uses water or heat."],
    devices: ["Connect your devices", "Link Home Assistant, Home Connect or an MQTT broker, or try ChorePilot with simulated devices first."],
    assistant: ["Pick your assistant", "ChorePilot uses a model running on your own computer or network. Nothing is sent to the cloud."],
    chores: ["Start with sensible chores", "We suggest chores that fit your devices and your family. You can edit them any time."],
    phone: ["Use it from your phone", "Add this page to your home screen, link Telegram, or let an agent help."] };
  const [t, lead] = titles[WIZ[step]];
  put(card, html`<span class="mark">${icon("mark", 26, 2)}</span>
    <div class="steps-bar" aria-hidden="true">${WIZ.map((_, i) => html`<i class="${i <= step ? "on" : ""}"></i>`)}</div>
    <p class="eyebrow">Setup · step ${step + 1} of ${WIZ.length}</p><h1>${t}</h1><p class="lead">${lead}</p>
    <div id="wiz-body"></div>
    <div class="wiz-foot"><button class="btn btn-quiet" id="wskip">Skip for now</button>
    <button class="btn btn-primary" id="wnext">${step === WIZ.length - 1 ? "Go to my home" : "Continue"}</button></div>`);
  renderPanel(WIZ[step], $("#wiz-body"), true);
  const next = () => { S.setupStep++; wizard(); };
  $("#wskip").onclick = next; $("#wnext").onclick = next;
}

/* ------------------------------------------------------------------ the signed-in shell */
const NAV = [
  { id: "home", label: "Home", icon: "home" },
  { id: "devices", label: "Devices", icon: "devices" },
  { id: "chores", label: "Chores", icon: "chores" },
  { id: "activity", label: "Activity", icon: "activity" },
  { id: "approvals", label: "Approvals", icon: "inbox", rail: true },
  { id: "settings", label: "Settings", icon: "settings", rail: true, adult: true },
];
const isAdult = () => S.me && (S.me.role === "owner" || S.me.role === "adult");
const isOwner = () => S.me && S.me.role === "owner";
async function signedIn() {
  $("#gate").hidden = true; $("#shell").hidden = false;
  S.me.household = cap(S.me.household);
  put($(".brand .mark"), icon("mark", 18, 2.2));
  put($("#top-me"), avatar(S.me, "sm"));
  put($("#bell"), html`${icon("bell", 22)}<span class="badge-dot" id="bell-count" hidden></span>`);
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  put($("#ask-open"), html`${icon("ask", 18, 2)}<span class="ask-open-l">Ask the house</span><kbd>${mac ? "\u2318K" : "Ctrl K"}</kbd>`);
  $("#ask-open").setAttribute("aria-keyshortcuts", mac ? "Meta+K" : "Control+K");
  $("#ask-open").onclick = () => openPalette();
  $("#top-me").onclick = e => meMenu(e.currentTarget);
  $("#bell").onclick = () => go("approvals");
  drawNav();
  window.addEventListener("hashchange", fromHash);
  fromHash();
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (document.visibilityState === "visible" && S.me) refresh(); }, 5000);
  clearInterval(S.sysTimer);
  loadSystem();
  S.sysTimer = setInterval(() => { if (document.visibilityState === "visible" && S.me) loadSystem(); }, 6000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible" && S.me) { refresh(); loadSystem(); } });
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
function drawNav() {
  const count = S.home ? (S.home.approvals.can_decide ? S.home.approvals.approvals.length : 0) + S.home.approvals.confirmations.length : 0;
  const items = NAV.filter(n => !n.adult || isAdult());
  put($("#rail-nav"), html`${items.map(n => html`<li><a class="nav-item" href="#/${n.id}" ${S.route === n.id ? raw('aria-current="page"') : ""} title="${n.label}">
    ${icon(n.icon, 20)}<span>${n.label}</span>${n.id === "approvals" && count ? html`<span class="nav-count" aria-label="${count} waiting">${count}</span>` : ""}</a></li>`)}`);
  put($("#tabbar"), html`${items.filter(n => !n.rail).map(n => html`<a class="tab" href="#/${n.id}" ${S.route === n.id ? raw('aria-current="page"') : ""}>
    ${icon(n.icon, 22)}<span>${n.label}</span></a>`)}`);
  const bc = $("#bell-count");
  if (bc) { bc.hidden = !count; bc.textContent = count; $("#bell").setAttribute("aria-label", count ? `Approvals, ${count} waiting` : "Approvals"); }
}
function meMenu(anchor) {
  const old = $(".menu"); if (old) { old.remove(); return; }
  const m = document.createElement("div");
  m.className = "menu"; m.setAttribute("role", "menu");
  const theme = store.get("theme", "dark");
  put(m, html`<div class="menu-head">${S.me.name}, ${S.me.role}</div>
    ${isAdult() ? html`<button role="menuitem" data-m="settings">${icon("settings", 20)}Settings</button>` : ""}
    <button role="menuitem" data-m="approvals">${icon("inbox", 20)}Approvals</button>
    <hr><div class="menu-head">Appearance</div>
    ${[["dark", "Dark", "moon"], ["light", "Light", "sun"], ["system", "Match this device", "contrast"]].map(([k, l, ic]) =>
      html`<button role="menuitemradio" aria-checked="${theme === k}" data-t="${k}">${icon(ic, 20)}${l}</button>`)}
    <hr><button role="menuitem" data-m="logout">${icon("logout", 20)}Sign out</button>`);
  document.body.appendChild(m);
  const r = anchor.getBoundingClientRect();
  const w = 240;
  m.style.width = w + "px";
  m.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + "px";
  if (r.top > window.innerHeight / 2) m.style.bottom = (window.innerHeight - r.top + 8) + "px";
  else m.style.top = (r.bottom + 8) + "px";
  $$("[aria-checked='true']", m).forEach(b => b.appendChild(Object.assign(document.createElement("span"), { className: "sr", textContent: " (selected)" })));
  const close = () => { m.remove(); document.removeEventListener("mousedown", out, true); };
  const out = e => { if (!m.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) close(); };
  document.addEventListener("mousedown", out, true);
  m.addEventListener("keydown", e => { if (e.key === "Escape") { close(); anchor.focus(); } });
  $$("[data-t]", m).forEach(b => b.onclick = () => { store.set("theme", b.dataset.t); applyTheme(); close(); });
  $$("[data-m]", m).forEach(b => b.onclick = async () => {
    close();
    if (b.dataset.m === "logout") { try { await api("/api/logout", {}); } catch (e) { /* gone */ } signedOut(); }
    else go(b.dataset.m);
  });
  const first = $("button", m); if (first) first.focus();
}
function go(r) { location.hash = "#/" + r; }
function fromHash() {
  const parts = (location.hash.replace(/^#\/?/, "") || "home").split("/");
  const r = NAV.some(n => n.id === parts[0]) ? parts[0] : "home";
  if (r === "settings" && !isAdult()) { go("home"); return; }
  const changed = r !== S.route || parts[1] !== S.sub;
  if (changed && palette) closeSheet(palette);
  S.route = r; S.sub = parts[1] || null;
  if (changed || !$(".page")) mount();
  refresh(true);
}
function mount() {
  const titles = { home: S.me.household, devices: "Devices", chores: "Chores", activity: "Activity", approvals: "Approvals", settings: "Settings" };
  $("#topbar-title").textContent = titles[S.route];
  document.title = `${S.route === "home" ? S.me.household : titles[S.route]} · ChorePilot`;
  const main = $("#main");
  main.classList.remove("has-composer");
  document.documentElement.classList.remove("composer");
  put(main, html`<div class="page ${S.route === "home" ? "" : "wrap"} ${S.route === "devices" ? "wrap-wide" : ""}" id="page"></div>`);
  ({ home: mountHome, devices: mountDevices, chores: mountChores, activity: mountActivity,
     approvals: mountApprovals, settings: mountSettings })[S.route]($("#page"));
  drawNav();
  window.scrollTo(0, 0);
}
let refreshing = null;
async function refresh(force) {
  if (!S.me) return;
  if (refreshing && !force) return refreshing;
  refreshing = (async () => {
    try {
      S.home = await api("/api/home");
      S.me = Object.assign(S.me, { csrf: S.home.me.csrf });
      update();
      drawNav();
    } catch (e) { if (e.status !== 401) console.warn(e); }
    finally { refreshing = null; }
  })();
  return refreshing;
}
function update() {
  if (!S.home) return;
  ({ home: updateHome, devices: updateDevices, chores: updateChores, activity: updateActivity,
     approvals: updateApprovals, settings: () => {} })[S.route]();
  if (S.sheetDev) updateDeviceSheet();
  if (palette) updateConsole(palette.sheet);
}

/* ------------------------------------------------------------------ HOME */
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}
function mountHome(page) {
  put(page, html`
    <section class="hero" aria-labelledby="greet"><div class="wrap hero-inner">
      <p class="eyebrow live-row"><span class="live-dot" aria-hidden="true"></span>House status · live</p>
      <h1 id="greet">${greeting()}, ${S.me.name}.</h1>
      <div class="now" id="now" aria-live="polite"></div>
      <div class="kpis" id="kpis"></div>
      <div class="running" id="running"></div>
    </div></section>
    <div class="wrap">
      <section class="console" id="console-home" aria-label="Ask the house">${consoleHtml("home")}</section>
      <div id="approvals-home"></div>
      <div class="home-grid">
        <div>
          <section class="section" id="sec-now"></section>
          <section class="section" id="sec-recent"></section>
        </div>
        <div>
          <section class="section" id="sec-ai"></section>
          <section class="section" id="sec-today"></section>
          <section class="section" id="sec-setup"></section>
        </div>
      </div>
    </div>`);
  wireConsole($("#console-home"));
  drawTurns();
  drawAI();
}
const secHead = (eye, title, more, href) => html`<div class="sec-head">${eye ? html`<span class="eyebrow">${eye}</span>` : ""}<h2>${title}</h2>${more ? html`<a class="more" href="${href}">${more}</a>` : ""}</div>`;
function revealOnce(root, sel) {
  if (!root || root.dataset.shown) return;
  root.dataset.shown = "1";
  $$(sel, root).forEach((el, i) => { el.classList.add("reveal"); el.style.setProperty("--d", Math.min(i, 12) * 45 + "ms"); });
}
function paintBars(root) { $$("[data-w]", root).forEach(el => { el.style.width = Math.max(0, Math.min(1, +el.dataset.w)) * 100 + "%"; }); }
function fmtNum(v, dec) { return dec ? (+v).toFixed(dec) : String(Math.round(+v)); }
function tick(el, to, dec) {
  const had = el.dataset.v !== undefined, from = had ? +el.dataset.v : 0;
  el.dataset.v = to;
  if (reduceMotion() || (had && from === to)) { el.textContent = fmtNum(to, dec); return; }
  const start = performance.now(), dur = had ? 600 : 900;
  const step = now => {
    const k = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = fmtNum(from + (to - from) * e, dec);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function kpiCells(H) {
  const devs = H.devices;
  const climate = devs.find(d => d.type === "climate" && !d.offline && d.status.current != null);
  const pw = devs.filter(d => d.status && d.status.power_kw != null);
  const power = pw.reduce((a, d) => a + (+d.status.power_kw || 0), 0);
  const on = devs.filter(d => d.active).length, att = devs.filter(d => d.attention).length, off = devs.filter(d => d.offline).length;
  const tot = H.board.people.reduce((a, p) => a + p.total_week, 0), done = H.board.people.reduce((a, p) => a + p.done_week, 0);
  const cur = climate ? +climate.status.current : null;
  return [
    { id: "temp", k: "Inside", v: cur, dec: cur != null && cur % 1 ? 1 : 0, u: "°", c: climate ? climate.text : "No thermostat connected" },
    { id: "power", k: "Power now", v: pw.length ? power : null, dec: 1, u: "kW", c: pw.length ? `Reported by ${plural(pw.length, "device")}` : "No device reports power" },
    { id: "on", k: "Devices on", v: on, dec: 0, u: `/ ${devs.length}`, c: att ? `${att} need a look` : off ? `${off} offline` : "All responding", bar: devs.length ? on / devs.length : 0 },
    { id: "chores", k: "Chores this week", v: done, dec: 0, u: `/ ${tot}`, c: tot ? `${Math.round(done / tot * 100)}% complete` : "Nothing scheduled", bar: tot ? done / tot : 0 },
  ];
}
function drawKpis(H) {
  const box = $("#kpis");
  const cells = kpiCells(H);
  if (!box.dataset.built) {
    put(box, html`${cells.map(c => html`<div class="kpi" data-kpi="${c.id}"><p class="eyebrow">${c.k}</p>
      <div class="kpi-v"><span class="n">${c.v == null ? "—" : "0"}</span><small class="${c.u === "°" ? "deg" : ""}">${c.u}</small></div>
      <div class="kpi-c"></div>${c.bar != null ? html`<div class="kpi-bar"><i data-w="0"></i></div>` : ""}</div>`)}`);
    box.dataset.built = "1";
    revealOnce(box, ".kpi");
  }
  cells.forEach(c => {
    const el = $(`[data-kpi="${c.id}"]`, box);
    const n = $(".n", el);
    if (c.v == null) { n.textContent = "—"; delete n.dataset.v; } else tick(n, c.v, c.dec);
    $("small", el).textContent = c.v == null ? "" : c.u;
    $(".kpi-c", el).textContent = c.c;
    const b = $(".kpi-bar i", el); if (b) { b.dataset.w = c.bar; }
  });
  requestAnimationFrame(() => paintBars(box));
}
function drawRunning(H) {
  const run = H.devices.filter(d => d.progress && !d.offline);
  const box = $("#running");
  if (!run.length) { put(box, ""); box.hidden = true; return; }
  box.hidden = false;
  put(box, html`${run.map(d => {
    const m = Math.max(0, Math.round(d.progress.remaining_s / 60));
    return html`<div class="run"><div><small>${d.room} · ${cap(d.title)}</small><b>${d.full}</b> <span class="muted">${d.text.split(",")[0]}</span></div>
      <div class="bar" role="progressbar" aria-label="${d.full} progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(d.progress.fraction * 100)}"><i data-w="${d.progress.fraction}"></i></div>
      <div class="run-time">${m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")} h` : `${m} min`}</div></div>`;
  })}`);
  requestAnimationFrame(() => paintBars(box));
}
const SUGGEST_BY_TYPE = { climate: "Set the AC to 72", dishwasher: "Start the dishwasher on eco", light: "Turn the living room lights off",
  valve: "Water the lawn for 10 minutes", washer: "When the washer is done, start the dryer", lock: "Is the front door locked?",
  coffee_maker: "Make me an espresso", vacuum: "Vacuum the living room" };
function updateHome() {
  const H = S.home, me = S.me;
  $("#greet").textContent = `${greeting()}, ${me.name}.`;
  const facts = H.facts || [];
  put($("#now"), facts.length ? html`${facts.slice(0, 4).map(f => f.kind === "approvals"
      ? html`<span class="fact k-approvals"><span class="dot" aria-hidden="true"></span><a href="#/approvals">${f.text}</a></span>`
      : html`<span class="fact k-${f.kind}"><span class="dot" aria-hidden="true"></span>${f.text}</span>`)}`
    : html`<span class="fact"><span class="dot" aria-hidden="true"></span>All quiet. Nothing is running and nothing needs you.</span>`);
  drawKpis(H);
  drawRunning(H);
  updateConsole($("#console-home"));
  const ap = H.approvals, ah = $("#approvals-home");
  const items = (ap.can_decide ? ap.approvals : []).slice(0, 3);
  if (items.length || ap.confirmations.length) {
    put(ah, html`<section class="section">${secHead("Decisions", "Waiting for you", "See all", "#/approvals")}<div class="approvals">${ap.confirmations.map(confirmationCard)}${items.map(approvalCard)}</div></section>`);
    wireApprovals(ah);
  } else put(ah, "");
  const busy = H.devices.filter(d => d.active || d.attention);
  const sn = $("#sec-now");
  put(sn, html`${secHead("Live", "Happening now", "All devices", "#/devices")}
    ${busy.length ? html`<div class="tiles">${busy.map(d => tile(d, true))}</div>`
      : html`<div class="empty"><div class="empty-icon">${icon("home", 24)}</div><h3>Nothing running</h3>
        <p>When the dishwasher, the sprinklers or anything else is on, it shows up here with the time left.</p></div>`}`);
  revealOnce(sn, ".tile");
  paintBars(sn);
  wireTiles(sn);
  const setup = H.setup || [];
  const left = setup.filter(r => !r.done);
  if (isOwner() && left.length) {
    const done = setup.length - left.length;
    const key = { owner: "people", members: "people", model: "assistant" }[left[0].key] || left[0].key;
    put($("#sec-setup"), html`${secHead("Setup", `${done} of ${setup.length} done`)}
      <a class="card setup-card" href="#/settings/${key}"><span class="setup-ring">${ring(done / setup.length, 44, 4)}</span>
      <div><h3>Next: ${left[0].title}</h3><p>${left[0].note}</p></div>${icon("chev", 20)}</a>`);
  } else put($("#sec-setup"), "");
  const b = H.board;
  const mine = b.open.filter(i => i.state === "open" && i.who === me.name && (i.today || i.overdue));
  const anyone = b.open.filter(i => i.state === "open" && !i.who && (i.today || i.overdue));
  put($("#sec-today"), html`${secHead("You", "Chores today", "Board", "#/chores")}
    ${mine.length || anyone.length ? html`<ul class="list">${mine.concat(anyone).slice(0, 6).map(i => choreRow(i, true))}</ul>`
      : html`<div class="empty"><div class="empty-icon">${icon("check", 24)}</div><h3>You're all done</h3><p>Nothing is due for you today.</p></div>`}`);
  wireChores($("#sec-today"));
  const rec = H.activity || [];
  put($("#sec-recent"), html`${secHead("Audit trail", "Recently", "Activity", "#/activity")}
    ${rec.length ? html`<ul class="tl compact">${rec.slice(0, 6).map(tlItem)}</ul>` : html`<p class="muted">Nothing yet today.</p>`}`);
}

/* ------------------------------------------------------------------ the console: Ask the house
   One component, two places: inline on Home, and as a palette sheet on every view (the top
   bar button, Cmd/Ctrl+K). Both share the conversation and the same /api/ask flow. */
function consoleHtml(where, id) {
  const pal = where === "palette";
  return html`<div class="console-head"><p class="eyebrow" ${pal ? raw(`id="${id}"`) : ""}>Ask the house</p><span class="model" data-c="model"></span>
      ${pal ? html`<button class="icon-btn" type="button" data-close aria-label="Close">${icon("x", 20)}</button>` : ""}</div>
    <div class="ask" data-c="ask">
      <form class="ask-bar" data-c="form" role="search">
        <span class="prompt" aria-hidden="true">&gt;</span>
        <label class="sr" for="askq-${where}">Ask the house</label>
        <input class="ask-input" id="askq-${where}" data-c="q" autocomplete="off" enterkeyhint="send" maxlength="500" placeholder="Ask the house" ${pal ? raw("autofocus") : ""}>
        <button class="ask-mic" type="button" data-c="mic" aria-label="Speak" hidden>${icon("mic", 22)}</button>
        <button class="ask-send" type="submit" data-c="send" aria-label="Send" disabled>${icon("send", 18, 2.2)}<span class="lbl" aria-hidden="true">Run</span></button>
      </form>
    </div>
    <div class="chips ask-suggest scroll" data-c="suggest"></div>
    <div class="convo" data-c="convo" aria-live="polite"></div>
    <p class="ask-hint" data-c="hint"></p>`;
}
const C = (root, k) => root && root.querySelector(`[data-c="${k}"]`);
function wireConsole(root) {
  const q = C(root, "q"), send = C(root, "send");
  q.addEventListener("input", () => { send.disabled = !q.value.trim(); });
  C(root, "form").onsubmit = e => { e.preventDefault(); const t = q.value.trim(); if (t) { q.value = ""; send.disabled = true; ask(t, root); } };
  setupVoice(root);
}
function updateConsole(root) {
  if (!root || !S.home) return;
  const H = S.home, llm = H.llm || {}, home = root.id === "console-home";
  const hint = C(root, "hint");
  if (!llm.configured) put(hint, html`${icon("cpu", 16)}<span>No assistant model yet, so asking is off. Device controls still work.${isOwner() ? html` <a href="#/settings/assistant">Set one up</a>` : ""}</span>`);
  else if (!llm.ok) put(hint, html`${icon("alert", 16)}<span>The model server isn't answering right now${isOwner() ? html`. <a href="#/settings/assistant">Check it</a>` : ""}.</span>`);
  else put(hint, html`${icon("shield", 16)}<span>Runs on your network. Water, heat and locks always ask you first. Every action is logged.</span>`);
  const mname = String(llm.model || "").split("/").pop().replace(/\.gguf$/i, "");
  C(root, "model").textContent = llm.configured ? (mname || "local model") + (llm.ok ? " · ready" : " · offline") : "no model";
  C(root, "q").disabled = !llm.configured;
  C(root, "mic").disabled = !llm.configured;
  C(root, "ask").classList.toggle("off", !llm.configured);
  if (home) {
    const dock = phone() && !!llm.configured;
    C(root, "ask").classList.toggle("dock", dock);
    $("#main").classList.toggle("has-composer", dock);
    document.documentElement.classList.toggle("composer", dock);
  }
  const types = new Set(H.devices.map(d => d.type));
  const sugg = ["Who has the dishes this week?"].concat(Object.keys(SUGGEST_BY_TYPE).filter(t => types.has(t)).map(t => SUGGEST_BY_TYPE[t])).slice(0, 5);
  const sg = C(root, "suggest");
  if (!S.turns.length && llm.configured) {
    if (sg.dataset.n !== String(sugg.length)) {
      put(sg, html`${sugg.map(x => html`<button class="chip" type="button" data-s="${x}">${x}</button>`)}`);
      sg.dataset.n = sugg.length;
      $$("[data-s]", sg).forEach(b => b.onclick = () => ask(b.dataset.s, root));
    }
  } else { put(sg, ""); delete sg.dataset.n; }
}
let palette = null;
function openPalette() {
  if (!S.me || $("#shell").hidden) return;
  if (palette) { const q = C(palette.sheet, "q"); if (q && !q.disabled) q.focus(); return; }
  palette = openSheet((sh, id, close) => {
    sh.classList.add("palette");
    put(sh, html`<div class="sheet-grab" aria-hidden="true"></div>${consoleHtml("palette", id)}`);
    $("[data-close]", sh).onclick = close;
    wireConsole(sh);
    updateConsole(sh);
  }, { wide: true, onClose: () => { palette = null; } });
  palette.scrim.classList.add("palette-scrim");
  drawTurns();
}
document.addEventListener("keydown", e => {
  if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey || String(e.key).toLowerCase() !== "k") return;
  if (!S.me || $("#shell").hidden) return;
  e.preventDefault();
  if (palette && sheets[sheets.length - 1] === palette) closeSheet(palette); else openPalette();
});

/* ------------------------------------------------------------------ the Local AI panel */
async function loadSystem() {
  try { S.sys = await api("/api/system"); } catch (e) { return; }
  drawSysPill();
  if (S.route === "home") drawAI();
}
function sysTok() { const m = (S.sys && S.sys.metrics) || {}, me = (S.sys && S.sys.measured) || {}; return m.gen_tok_s || me.tok_s || null; }
function drawSysPill() {
  const pill = $("#sys-pill");
  if (!S.sys || !pill) return;
  const llm = S.sys.llm || {};
  const tok = sysTok();
  pill.hidden = false;
  put(pill, html`<span class="live-dot ${llm.ok ? "" : llm.configured ? "bad" : "off"}" aria-hidden="true"></span><span>${llm.configured ? (llm.ok ? "Local AI" : "AI offline") : "No AI"}</span>${tok ? html`<span class="sys-detail">${Math.round(tok)} tok/s</span>` : ""}`);
  pill.setAttribute("aria-label", llm.configured ? `Local AI ${llm.ok ? "ready" : "offline"}${tok ? `, ${Math.round(tok)} tokens per second` : ""}` : "No assistant model");
}
function spark(vals) {
  if (!vals || vals.length < 2) return "";
  const max = Math.max(...vals) * 1.1 || 1, w = 300, h = 44;
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * w).toFixed(1)},${(h - v / max * (h - 4) - 2).toFixed(1)}`).join(" ");
  return raw(`<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><line class="base" x1="0" y1="${h - 1}" x2="${w}" y2="${h - 1}" stroke="currentColor"/><polyline points="${pts}"/></svg>`);
}
function drawAI() {
  const el = $("#sec-ai");
  if (!el) return;
  if (!S.sys) { put(el, html`${secHead("Local AI", "System")}<div class="ai"><div class="ai-head"><p class="eyebrow dim">Model</p><p class="ai-model">Checking</p></div></div>`); return; }
  const { llm = {}, measured = {}, host = {} } = S.sys;
  const m = S.sys.metrics || {};
  const model = String(llm.model || m.model || "").split("/").pop().replace(/\.gguf$/i, "") || (llm.configured ? "Model server" : "No model yet");
  const tok = sysTok();
  const ttft = measured.ttft_s;
  const mem = m.gpu_gb != null ? { k: "Model in GPU", v: m.gpu_gb, u: "GB" } : m.kv_used != null ? { k: "Cache in use", v: Math.round(m.kv_used * 100), u: "%" }
    : m.slots != null ? { k: "Parallel slots", v: m.slots, u: m.busy ? `${m.busy} busy` : "idle" } : { k: "Memory", v: null, u: "" };
  const cells = [
    { k: "Generation", v: tok != null ? Math.round(tok) : null, u: "tok/s" },
    { k: "First token", v: ttft != null ? Math.round(ttft * 1000) : null, u: "ms" },
    { k: "Context", v: m.ctx ? (m.ctx >= 1024 ? Math.round(m.ctx / 1024) + "K" : m.ctx) : null, u: m.ctx ? "tokens" : "" },
    { k: mem.k, v: mem.v, u: mem.u },
  ];
  const status = !llm.configured ? "not configured" : llm.ok ? (measured.calls ? `${measured.calls} recent call${measured.calls === 1 ? "" : "s"}` : "ready") : "not answering";
  put(el, html`${secHead("Local AI", "System")}
    <div class="ai"><div class="ai-head"><p class="eyebrow dim">Model${m.server ? ` · ${m.server}` : ""}</p>
      <p class="ai-model"><span class="live-dot ${llm.ok ? "" : llm.configured ? "bad" : "off"}" aria-hidden="true"></span>${model}</p>
      <div class="row-meta"><span>${status}</span>${llm.url ? html`<span>${llm.url.replace(/^https?:\/\//, "")}</span>` : ""}</div></div>
    <div class="ai-grid">${cells.map(c => html`<div class="ai-cell"><p class="eyebrow">${c.k}</p><div class="readout ${c.v == null ? "na" : ""}">${c.v == null ? "—" : c.v}<small>${c.v == null ? "" : c.u}</small></div></div>`)}</div>
    ${(measured.history || []).length > 1 ? html`<div class="ai-cell"><p class="eyebrow">Tokens/s, last ${measured.history.length} calls</p>${spark(measured.history)}</div>` : ""}
    <div class="ai-host"><p class="eyebrow dim">${S.sys.local === false ? "This dashboard's host" : "Host"}</p><div class="v">${host.label || "Unknown hardware"}</div>
      <div class="faint">${[host.cpu && host.cpu !== host.gpu ? host.cpu : null, host.free_gb != null ? `${host.free_gb} GB free now` : null, host.mem_bw_gbs ? `${Math.round(host.mem_bw_gbs)} GB/s memory` : null].filter(Boolean).join(" · ")}</div></div></div>`);
}
function ask(text, root) {
  const t = { text, state: "sending", job: null, data: null, at: Date.now() };
  S.turns.unshift(t); S.turns = S.turns.slice(0, 4);
  drawTurns();
  $$("[data-c=suggest]").forEach(sg => { put(sg, ""); delete sg.dataset.n; });
  if (phone() && root && root.id === "console-home") requestAnimationFrame(() => {     // the console sits above the docked bar: bring the trace into view
    const c = C(root, "convo");
    if (c) window.scrollTo({ top: c.getBoundingClientRect().top + window.scrollY - 72, behavior: reduceMotion() ? "auto" : "smooth" });
  });
  run(t);
}
async function run(t) {
  try {
    const r = await api("/api/ask", { text: t.text });
    if (r.no_model) { t.state = "nomodel"; t.error = r.error; drawTurns(); return; }
    t.job = r.job; t.data = r; t.state = r.state; drawTurns();
    while (t.state === "running" || t.state === "waiting") {
      await new Promise(res => setTimeout(res, t.state === "waiting" ? 1200 : 700));
      const d = await api(`/api/ask/${t.job}`);
      const changed = JSON.stringify(d.steps) !== JSON.stringify(t.data.steps) || d.state !== t.state ||
        JSON.stringify(d.pending) !== JSON.stringify(t.data.pending);
      t.data = d; t.state = d.state;
      if (changed) drawTurns();
    }
    drawTurns();
    refresh(true);
  } catch (e) { t.state = "error"; t.error = e.message; drawTurns(); }
}
const TOOL_WORDS = { get_status: "Checked", set_climate: "Thermostat", set_light: "Lights", water_zone: "Sprinklers",
  start_program: "Start a program", pause: "Pause", resume: "Resume", cancel: "Cancel", notify_when_done: "Tell you when it's done",
  start_when_done: "Start when done", list_chores: "Read the chore board", who_has: "Looked up the rotation",
  complete_chore: "Marked a chore done", assign_chore: "Reassigned a chore", remind: "Sent a reminder", door_lock: "Door lock",
  brew_coffee: "Coffee machine", oven: "Oven", vacuum: "Vacuum", garage_door: "Garage door", alarm_panel: "Alarm" };
function argText(k, v) {
  if (typeof v === "object") return Object.entries(v || {}).map(([a, b]) => argText(a, b)).join(", ");
  if (/bright|position|volume|speed|humid/.test(k)) return `${v}%`;
  if (k === "temperature") return `${v}°`;
  if (/minutes/.test(k)) return `${v} min`;
  if (["action", "program", "chore", "person", "hvac_mode", "beverage"].includes(k)) return cap(v).toLowerCase();
  return `${k.replace(/_/g, " ")} ${v}`;
}
function stepText(s) {
  const a = s.args || {};
  const bits = Object.entries(a).filter(([k]) => !["device", "appliance", "unit"].includes(k)).map(([k, v]) => argText(k, v)).filter(Boolean);
  const target = a.device || a.appliance;
  return `${TOOL_WORDS[s.tool] || cap(s.tool)}${target ? ": " + cap(target).toLowerCase() : ""}${bits.length ? " (" + bits.join(", ") + ")" : ""}`;
}
function argsInline(a) {
  return Object.entries(a || {}).filter(([k]) => !["device", "appliance"].includes(k))
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
}
const stepLine = (cls, t, ic, body) => html`<li class="step ${cls}"><span class="t">${t}</span><span class="step-ico">${icon(ic, 14, 2.4)}</span><span>${body}</span></li>`;
function drawTurns() {
  const boxes = $$("[data-c=convo]");
  const top = palette ? C(palette.sheet, "convo") : null;
  boxes.forEach(c => drawTurnsIn(c, !top || c === top));
}
function drawTurnsIn(c, focus) {
  if (!S.turns.length) { put(c, ""); return; }
  put(c, html`${S.turns.map((t, idx) => {
    const d = t.data || {};
    const steps = (d.steps || []).map(s => {
      if (s.kind === "model") {
        if (s.state === "running") return stepLine("running", s.t != null ? `${s.t.toFixed(2)}s` : "", "spin", html`<span class="k">model</span> <span class="faint">generating</span>`);
        return stepLine(s.state === "failed" ? "failed" : "ok", s.t != null ? `${s.t.toFixed(2)}s` : "", s.state === "failed" ? "x" : "cpu",
          html`<span class="k">model</span> ${s.calls ? plural(s.calls, "tool call") : "answer"} <span class="faint">· ${s.s}s${s.tok_s ? ` · ${s.tok_s} tok/s` : ""}${s.ttft_s != null ? ` · first token ${Math.round(s.ttft_s * 1000)} ms` : ""}</span>`);
      }
      if (s.kind === "question") {
        const yes = s.answer === "yes" || s.answer === "code entered";
        return stepLine(yes ? "done" : "declined", "", yes ? "check" : "x", html`<span class="k">confirm</span> ${s.question} <span class="faint">· ${s.answer}</span>`);
      }
      return stepLine(s.state, s.t != null ? `${s.t.toFixed(2)}s` : "", s.state === "running" ? "spin" : s.state === "done" ? "check" : s.state === "queued" ? "inbox" : "x",
        html`<span class="k">${s.tool}</span> ${s.args && (s.args.device || s.args.appliance) ? `${s.args.device || s.args.appliance} ` : ""}<span class="faint">${argsInline(s.args)}</span>${s.state === "queued" ? html` <span class="pill warn">sent to a parent</span>` : ""}${s.error && s.state !== "queued" ? html`<br><span class="faint">${s.error}</span>` : ""}`);
    });
    const p = d.pending;
    const thinking = t.state === "sending" || (t.state === "running" && !(d.steps || []).length);
    return html`<article class="turn" data-turn="${idx}">
      <div class="turn-q"><span class="who" aria-hidden="true">&gt;</span><span>${t.text}</span></div>
      ${thinking ? html`<ul class="turn-steps">${stepLine("running", "0.00s", "spin", html`<span class="k">route</span> <span class="faint">picking tools</span>`)}</ul>` : ""}
      ${steps.length ? html`<ul class="turn-steps">${steps}</ul>` : ""}
      ${p ? pendingCard(p) : ""}
      ${d.answer ? html`<p class="turn-a">${d.answer}</p>` : ""}
      ${t.state === "nomodel" ? html`<div class="confirm-card"><p>${t.error}</p><div class="btn-row"><a class="btn btn-sm" href="#/devices">Open device controls</a>${isOwner() ? html`<a class="btn btn-sm btn-primary" href="#/settings/assistant">Set up a model</a>` : ""}</div></div>` : ""}
      ${t.state === "error" ? html`<div class="confirm-card security"><p>${t.error || d.error}</p></div>` : ""}
      ${t.state === "done" ? html`<div class="turn-foot">${icon("check", 14, 2)}<span>done in ${d.elapsed_s}s · logged to <a href="#/activity">activity</a></span></div>` : ""}
    </article>`;
  })}`);
  $$("[data-pend]", c).forEach(form => {
    const idx = +form.closest("[data-turn]").dataset.turn, t = S.turns[idx];
    form.onsubmit = async e => {
      e.preventDefault();
      const yes = e.submitter ? e.submitter.value !== "no" : true;
      const v = form.v ? form.v.value : "";
      const body = { id: t.data.pending.id, yes };
      if (t.data.pending.kind === "phrase") body.phrase = v;
      if (t.data.pending.kind === "code") body.code = v;
      $$("button", form).forEach(b => b.disabled = true);
      try { t.data = await api(`/api/ask/${t.job}/answer`, body); t.state = t.data.state; drawTurns(); }
      catch (err) { toast(err.message, true); }
    };
  });
  const f = focus && $("[data-pend] input, [data-pend] button[value=yes]", c);
  if (f) f.focus({ preventScroll: true });
}
function pendingCard(p) {
  const sec = p.kind === "code";
  return html`<form class="confirm-card ${sec ? "security" : ""}" data-pend autocomplete="off">
    <p>${p.question}</p>
    <span class="why">${icon(sec ? "key" : "shield", 16)}${sec ? "Tier 3 · security: type your PIN or authenticator code" : p.kind === "phrase" ? `Type ${p.phrase} to confirm` : "Tier 2 · water or heat: it asks you first"}</span>
    ${p.kind === "phrase" ? html`<input class="input" name="v" aria-label="Type ${p.phrase}" autocapitalize="characters" spellcheck="false">` : ""}
    ${sec ? html`<input class="input num" name="v" type="password" inputmode="numeric" maxlength="12" aria-label="PIN or code" autocomplete="one-time-code">` : ""}
    <div class="btn-row"><button class="btn btn-primary no-arrow" value="yes" type="submit">${p.kind === "confirm" ? "Yes, do it" : "Confirm"}</button>
    <button class="btn" value="no" type="submit">No</button></div></form>`;
}
function setupVoice(root) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const mic = C(root, "mic"), q = C(root, "q"), send = C(root, "send");
  if (!SR || !mic) return;
  mic.hidden = false;
  let rec = null;
  mic.onclick = () => {
    if (rec) { rec.stop(); return; }
    rec = new SR();
    rec.lang = navigator.language || "en-US"; rec.interimResults = true; rec.maxAlternatives = 1;
    mic.classList.add("listening"); mic.setAttribute("aria-label", "Stop listening");
    rec.onresult = e => {
      const txt = Array.from(e.results).map(r => r[0].transcript).join(" ");
      q.value = txt; send.disabled = !txt.trim();
    };
    const end = () => { mic.classList.remove("listening"); mic.setAttribute("aria-label", "Speak"); rec = null; q.focus(); };
    rec.onend = end;
    rec.onerror = ev => { end(); if (ev.error === "not-allowed") toast("Microphone access is blocked for this page.", true); };
    try { rec.start(); } catch (e) { end(); }
  };
}

/* ------------------------------------------------------------------ DEVICES */
function tileValue(d) {
  const st = d.status || {};
  const n1 = v => (+v % 1 ? (+v).toFixed(1) : String(+v));
  if (d.offline) return { w: "Offline" };
  if (d.progress) { const m = Math.max(0, Math.round(d.progress.remaining_s / 60)); return m >= 60 ? { v: `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`, u: "h left" } : { v: m, u: "min left" }; }
  if (d.type === "climate" && st.current != null) return { v: n1(st.current), u: "°" };
  if ((d.type === "water_heater" || d.type === "oven") && st.target != null && d.state !== "off") return { v: n1(st.target), u: "°" };
  if (d.type === "oven" && st.current != null) return { v: n1(st.current), u: "°" };
  if (d.type === "humidifier" && st.current != null) return { v: st.current, u: "% RH" };
  if (d.type === "light" && d.state === "on") return { v: st.brightness || 100, u: "%" };
  if (d.type === "ev_charger" && d.state === "charging" && st.power_kw != null) return { v: n1(st.power_kw), u: "kW" };
  if (d.type === "fridge" && st.fridge != null) return { v: n1(st.fridge), u: `° / ${st.freezer != null ? n1(st.freezer) + "°" : ""}` };
  if (d.type === "media_player" && d.state === "playing" && st.volume != null) return { v: st.volume, u: "% vol" };
  if (d.type === "cover" && st.position != null && st.position > 0 && st.position < 100) return { v: st.position, u: "% open" };
  if (d.type === "air_purifier" && st.pm25 != null) return { v: st.pm25, u: "PM2.5" };
  if (d.type === "vacuum" && st.battery != null && d.state === "docked") return { v: st.battery, u: "% batt" };
  return { w: cap(String(d.state).replace(/^armed_/, "armed ")) };
}
function tile(d, showRoom) {
  const prog = d.progress;
  const p = d.primary;
  const ic = d.icon === "lock" && d.state === "unlocked" ? "unlock" : d.icon;
  const tv = tileValue(d);
  return html`<div class="tile ${d.active ? "lit" : ""} ${d.attention ? "attn" : ""} ${d.offline ? "off-line" : ""}" data-dev="${d.id}">
    <button class="tile-open" type="button" data-open="${d.id}" aria-label="${d.full}, ${d.text}. Open controls"></button>
    <div class="tile-top"><span class="tile-eye ${showRoom ? "" : "mono"}">${showRoom ? d.room : d.id}</span>
      ${p ? html`<button class="tile-ico btn-like" type="button" data-primary="${d.id}" aria-label="${p.label}">${icon(ic, 20)}</button>`
          : html`<span class="tile-ico">${icon(ic, 20)}</span>`}</div>
    <div class="tile-name">${showRoom ? d.full : d.display}</div>
    <div class="tile-val ${tv.w ? "word" : ""}">${tv.w || tv.v}${tv.u ? html`<small class="${tv.u.startsWith("°") ? "deg" : ""}">${tv.u}</small>` : ""}</div>
    <div class="tile-state">${d.attention ? html`<span class="sr">Needs a look: </span>` : ""}${tv.w && tv.w.toLowerCase() === d.text.toLowerCase() ? (d.model || cap(d.title)) : d.text}</div>
    ${prog ? html`<div class="bar" aria-hidden="true"><i data-w="${prog.fraction}"></i></div>` : ""}
  </div>`;
}
function wireTiles(root) {
  $$("[data-open]", root).forEach(b => b.onclick = () => openDevice(b.dataset.open));
  $$("[data-primary]", root).forEach(b => b.onclick = async e => {
    e.stopPropagation();
    const d = S.home.devices.find(x => x.id === b.dataset.primary);
    if (!d || !d.primary) return;
    b.disabled = true;
    await act(d, d.primary.action, d.primary.params);
    b.disabled = false;
  });
}
function mountDevices(page) {
  put(page, html`<div class="page-head"><div><p class="eyebrow">Control</p><h1>Devices</h1><p class="sub" id="dev-sub"></p></div></div>
    <div class="tabs" id="rooms" role="group" aria-label="Rooms"></div><div id="dev-list"></div>`);
}
function updateDevices() {
  const devs = S.home.devices;
  const rooms = [...new Set(devs.map(d => d.room))];
  const on = devs.filter(d => d.active).length, att = devs.filter(d => d.attention).length;
  $("#dev-sub").textContent = devs.length ? `${plural(devs.length, "device")} in ${plural(rooms.length, "room")}. ${on} on${att ? `, ${att} need a look` : ""}.` : "";
  if (!rooms.includes(S.roomFilter)) S.roomFilter = "All";
  put($("#rooms"), rooms.length > 1 ? html`${["All"].concat(rooms).map(r => html`<button type="button" data-room="${r}" aria-pressed="${S.roomFilter === r}">${r}<span class="n">${r === "All" ? devs.length : devs.filter(d => d.room === r).length}</span></button>`)}` : "");
  $$("[data-room]").forEach(b => b.onclick = () => { S.roomFilter = b.dataset.room; delete $("#dev-list").dataset.shown; updateDevices(); });
  const shown = rooms.filter(r => S.roomFilter === "All" || r === S.roomFilter);
  const list = $("#dev-list");
  list.classList.toggle("rooms", devs.length > 0);
  put(list, devs.length ? html`${shown.map(r => {
      const ds = devs.filter(d => d.room === r), lit = ds.filter(d => d.active).length;
      return html`<section class="room" data-n="${ds.length}" aria-labelledby="room-${r.replace(/\W/g, "")}">
      <div class="room-head"><h2 id="room-${r.replace(/\W/g, "")}">${r}</h2><span class="faint">${ds.length} · ${lit} on</span></div><div class="tiles">${ds.map(d => tile(d))}</div></section>`; })}`
    : html`<div class="empty"><div class="empty-icon">${icon("plug", 24)}</div><h3>No devices yet</h3>
      <p>Connect Home Assistant, Home Connect or MQTT${isOwner() ? "" : " (the owner can do this in Settings)"}.</p>
      ${isOwner() ? html`<p class="mt-16"><a class="btn btn-primary" href="#/settings/devices">Connect devices</a></p>` : ""}</div>`);
  packRooms(list);
  revealOnce(list, ".tile");
  paintBars(list);
  wireTiles(list);
}
/* Rooms share one column grid (CSS subgrid): each room spans as many columns as it has
   devices, up to the row, and small rooms pack in beside big ones, so wide screens fill. */
function packRooms(list) {
  if (!list || !list.classList.contains("rooms")) return;
  list.style.gridTemplateColumns = "";
  const cs = getComputedStyle(list);
  if (cs.display !== "grid") return;
  let cols = cs.gridTemplateColumns.split(" ").filter(Boolean).length || 1;
  // rooms mostly have 2 or 4 devices: an even column count packs them without orphans
  if (cols > 2 && cols % 2) { cols -= 1; list.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`; }
  $$(".room", list).forEach(r => r.style.setProperty("--span", Math.min(+r.dataset.n || 1, cols)));
}
window.addEventListener("resize", () => { clearTimeout(S.packT); S.packT = setTimeout(() => packRooms($("#dev-list")), 120); });
function statusDefault(d, p) {
  const st = d.status || {};
  const map = { temperature: "target", brightness: "brightness", volume: "volume", position: "position", humidity: "target",
    current: "current", hvac_mode: "state", operation_mode: "state", speed: "speed" };
  let v = st[map[p.name]];
  if (p.name === "temperature" && st.target == null) v = null;
  if (v == null) v = p.default;
  if (v == null && p.type === "enum") v = ["normal", "medium", "auto", "eco"].find(x => p.values.includes(x)) || p.values[0];
  if (v == null && p.type === "bool") v = false;
  if (v == null && (p.type === "int" || p.type === "number")) {
    if (p.lo != null && p.hi != null) v = Math.round(((p.lo + p.hi) / 2) / 5) * 5; else v = p.lo != null ? p.lo : 0;
    if (p.name === "temperature") v = (S.me.unit === "F" ? (d.type === "oven" ? 350 : 72) : (d.type === "oven" ? 180 : 22));
  }
  if (p.type === "enum" && !p.values.includes(v)) v = p.values[0];
  return v;
}
function stepOf(p, d) {
  if (p.name === "temperature") return (d.status && d.status.unit === "C") || S.me.unit === "C" ? 0.5 : 1;
  if (/_ml$/.test(p.name)) return 10;
  if (p.name === "minutes" || p.name === "duration_minutes" || p.name === "delay_minutes" || p.name === "finish_in_minutes") return p.hi > 120 ? 15 : 5;
  return 1;
}
const SLIDERS = /bright|position|volume|speed|humid/;
function paramInput(d, p, val, act) {
  const id = `p-${act}-${p.name}`;
  const label = PARAM_LABEL[p.name] || cap(p.name);
  if (p.type === "enum") {
    const lbl = v => (p.labels && p.labels[v]) || cap(v);
    if (p.values.length <= 7) return html`<div class="field"><span id="${id}-l">${label}</span><div class="seg" role="group" aria-labelledby="${id}-l" data-param="${p.name}" data-kind="seg">
      ${p.values.map(v => html`<button type="button" data-v="${v}" aria-pressed="${v === val}">${lbl(v)}</button>`)}</div></div>`;
    return html`<label class="field"><span>${label}</span><select class="input" data-param="${p.name}">${p.values.map(v => html`<option value="${v}" ${v === val ? raw("selected") : ""}>${lbl(v)}${p.minutes && p.minutes[v] ? `  (${fmtMin(p.minutes[v])})` : ""}</option>`)}</select></label>`;
  }
  if (p.type === "bool") return html`<div class="ctl-head"><span id="${id}-l">${label}</span><button type="button" class="switch" role="switch" aria-checked="${!!val}" aria-labelledby="${id}-l" data-param="${p.name}" data-kind="bool"></button></div>`;
  if (p.type === "int" || p.type === "number") {
    if (p.lo != null && p.hi != null && p.hi - p.lo <= 100 && SLIDERS.test(p.name)) {
      const f = ((val - p.lo) / (p.hi - p.lo)) * 100;
      return html`<label class="field"><span>${label} <b class="num" data-out="${p.name}">${val}${p.name === "volume" || SLIDERS.test(p.name) ? "%" : ""}</b></span>
        <input class="range" type="range" min="${p.lo}" max="${p.hi}" step="1" value="${val}" data-param="${p.name}" data-fill="${f}"></label>`;
    }
    const unit = p.name === "temperature" ? "°" : /minutes/.test(p.name) ? " min" : p.name === "current" ? " A" : "";
    return html`<div class="ctl-head"><span id="${id}-l">${label}</span><div class="stepper" role="group" aria-labelledby="${id}-l">
      <button type="button" data-step="-1" data-for="${p.name}" aria-label="Less">${icon("minus", 18, 2)}</button>
      <output class="num" data-param="${p.name}" data-kind="step" data-v="${val}" data-lo="${p.lo == null ? "" : p.lo}" data-hi="${p.hi == null ? "" : p.hi}" data-st="${stepOf(p, d)}" data-unit="${unit}" aria-live="polite">${val}${unit}</output>
      <button type="button" data-step="1" data-for="${p.name}" aria-label="More">${icon("plus", 18, 2)}</button></div></div>`;
  }
  return html`<label class="field"><span>${label}</span><input class="input" data-param="${p.name}" value="${val == null ? "" : val}"></label>`;
}
const PARAM_LABEL = { hvac_mode: "Mode", operation_mode: "Mode", temperature: "Temperature", minutes: "How long",
  duration_minutes: "Turn off after", brightness: "Brightness", color_temp_kelvin: "Warmth (kelvin)", position: "Open",
  volume: "Volume", source: "Input", speed: "Fan speed", humidity: "Humidity", mode: "Mode", current: "Charging current",
  compartment: "Compartment", beverage: "Drink", strength: "Strength", size_ml: "Cup size (ml)", on: "On", away: "Away mode",
  delay_minutes: "Start in", finish_in_minutes: "Finish in", temperature_c: "Temperature", spin: "Spin", dryness: "Dryness",
  program: "Program" };
const ACTION_LABEL = n => ({ set_temperature: "Set temperature", set_mode: "Set mode", turn_on: "Turn on", turn_off: "Turn off" }[n] || cap(n));
function fmtMin(m) { m = Math.round(m); const h = Math.floor(m / 60); return h ? `${h} h ${m % 60} min` : `${m} min`; }
function readParams(root) {
  const out = {};
  $$("[data-param]", root).forEach(el => {
    const k = el.dataset.param;
    if (el.dataset.kind === "seg") { const b = $("[aria-pressed=true]", el); if (b) out[k] = b.dataset.v; }
    else if (el.dataset.kind === "bool") out[k] = el.getAttribute("aria-checked") === "true";
    else if (el.dataset.kind === "step") out[k] = +el.dataset.v;
    else if (el.type === "range") out[k] = +el.value;
    else if (el.tagName === "SELECT") out[k] = el.value;
    else if (el.value !== "") out[k] = el.value;
  });
  return out;
}
function wireParams(root, onChange) {
  $$("[data-kind=seg]", root).forEach(g => $$("button", g).forEach(b => b.onclick = () => {
    $$("button", g).forEach(x => x.setAttribute("aria-pressed", x === b)); if (onChange) onChange(g);
  }));
  $$("[data-kind=bool]", root).forEach(sw => sw.onclick = () => { sw.setAttribute("aria-checked", sw.getAttribute("aria-checked") !== "true"); if (onChange) onChange(sw); });
  $$("input[type=range]", root).forEach(r => {
    const paint = () => { r.style.setProperty("--fill", (((r.value - r.min) / (r.max - r.min)) * 100) + "%"); const o = $(`[data-out="${r.dataset.param}"]`, root); if (o) o.textContent = r.value + "%"; };
    paint(); r.oninput = paint; r.onchange = () => { if (onChange) onChange(r); };
  });
  $$("[data-step]", root).forEach(b => b.onclick = () => {
    const o = $(`output[data-param="${b.dataset.for}"]`, root);
    const st = +o.dataset.st, lo = o.dataset.lo === "" ? -Infinity : +o.dataset.lo, hi = o.dataset.hi === "" ? Infinity : +o.dataset.hi;
    let v = Math.round((+o.dataset.v + st * +b.dataset.step) / st) * st;
    v = Math.max(lo, Math.min(hi, +v.toFixed(1)));
    o.dataset.v = v; o.textContent = v + o.dataset.unit;
    if (onChange) onChange(o);
  });
}
function tierNote(c) {
  if (c.tier >= 3) return html`<span class="safety">${icon("key", 16)}${S.me.role === "owner" ? "Asks for your PIN" : "Only the owner, with a PIN"}</span>`;
  if (c.tier === 2) return html`<span class="safety">${icon("shield", 16)}${S.me.role === "kid" ? "Goes to a parent to approve" : `Asks you first${c.uses ? ", uses " + c.uses : ""}`}</span>`;
  return "";
}
function openDevice(id) {
  const d = S.home.devices.find(x => x.id === id);
  if (!d) return;
  S.sheetDev = id;
  openSheet((sh, hid) => { sh.dataset.hid = hid; drawDeviceSheet(sh, d); }, { onClose: () => { S.sheetDev = null; } });
}
function liveBlock(d) {
  const st = d.status || {};
  if (d.progress) {
    const left = Math.max(0, Math.round(d.progress.remaining_s / 60));
    return html`<div class="dial" data-live><svg width="240" height="240" viewBox="0 0 240 240" aria-hidden="true">
      ${raw(ringRaw(d.progress.fraction, 240, 10))}</svg>
      <div class="dial-center"><div class="v num">${raw(bigTime(left))}</div><div class="l">${(d.text.split(",")[0])} · done ${doneAt(left)}</div></div></div>`;
  }
  if (st.target != null && st.current != null && (d.type === "climate" || d.type === "water_heater" || d.type === "humidifier")) {
    const u = st.unit ? "°" : "%";
    return html`<div class="big-state" data-live><div class="v num">${st.target}${u}</div><div class="l">${d.text}</div></div>`;
  }
  return html`<div class="big-state" data-live><div class="v">${cap(d.state === "unknown" ? "" : d.text.split(",")[0])}</div>
    ${d.text.includes(",") ? html`<div class="l">${d.text.split(",").slice(1).join(",").trim()}</div>` : ""}</div>`;
}
function bigTime(m) { const h = Math.floor(m / 60), mm = m % 60; return (h ? `${h}<small>h</small> ` : "") + `${mm}<small>min</small>`; }
function doneAt(m) { const t = new Date(Date.now() + m * 60000); return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function ringRaw(frac, size, stroke) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, f = Math.max(0, Math.min(1, frac || 0));
  return `<g transform="rotate(-90 ${size / 2} ${size / 2})"><circle class="dial-track" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}"/>` +
    `<circle class="dial-fill" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke-width="${stroke}" stroke-linecap="butt" ` +
    `stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${(c * (1 - f)).toFixed(2)}"/></g>`;
}
const G = { cx: 130, cy: 130, r: 100 };
function gaugeSvg(frac) {
  const c = 2 * Math.PI * G.r, L = 0.75 * c, f = Math.max(0, Math.min(1, frac || 0));
  let ticks = "";
  for (let i = 0; i <= 30; i++) {
    const a = (135 + 270 * i / 30) * Math.PI / 180, r1 = G.r + 12, r2 = G.r + (i % 5 ? 17 : 22);
    ticks += `<line class="g-tick" x1="${(G.cx + r1 * Math.cos(a)).toFixed(1)}" y1="${(G.cy + r1 * Math.sin(a)).toFixed(1)}" x2="${(G.cx + r2 * Math.cos(a)).toFixed(1)}" y2="${(G.cy + r2 * Math.sin(a)).toFixed(1)}" stroke-width="${i % 5 ? 1 : 2}"/>`;
  }
  return `<svg viewBox="0 0 260 260" width="260" height="260" aria-hidden="true"><g transform="rotate(135 ${G.cx} ${G.cy})">` +
    `<circle class="g-track" cx="${G.cx}" cy="${G.cy}" r="${G.r}" fill="none" stroke-width="10" stroke-dasharray="${L.toFixed(1)} ${c.toFixed(1)}"/>` +
    `<circle class="g-fill" cx="${G.cx}" cy="${G.cy}" r="${G.r}" fill="none" stroke-width="10" stroke-dasharray="${(L * f).toFixed(1)} ${c.toFixed(1)}"/></g>${ticks}</svg>`;
}
function gaugeFrac(o) {
  const lo = o.dataset.lo === "" ? null : +o.dataset.lo, hi = o.dataset.hi === "" ? null : +o.dataset.hi, v = +o.dataset.v;
  const a = lo != null ? lo : (o.dataset.unit === "%" ? 0 : 50), b = hi != null ? hi : (o.dataset.unit === "%" ? 100 : 90);
  return (v - a) / ((b - a) || 1);
}
function drawDeviceSheet(sh, d) {
  const hid = sh.dataset.hid;
  const ctls = d.controls || [];
  const p = d.primary;
  const st = d.status || {};
  const prog = d.program_appliance;
  // the dial: a target the device reports, set by an action whose first parameter is that number
  const dialCtl = st.target != null ? ctls.find(c => c.params.length && ["temperature", "humidity"].includes(c.params[0].name) && /^set_/.test(c.name)) : null;
  const quick = ctls.filter(c => c !== dialCtl && c.params.length === 1 && !prog && c.tier < 2 &&
    ((c.params[0].type !== "enum" && SLIDERS.test(c.params[0].name)) || (c.params[0].type === "enum" && c.params[0].values.length <= 7)));
  const solo = new Set(ctls.filter(c => c.params.length === 1).map(c => c.params[0].name));
  const rest = ctls.filter(c => c !== dialCtl && !quick.includes(c) && !(p && c.name === p.action && !c.params.length))
    .map(c => c.params.length > 1 ? Object.assign({}, c, { params: c.params.filter(x => x.required || !solo.has(x.name)) }) : c);
  const sub = prog ? `${d.room} · ${d.model || cap(d.title)}` : d.display.toLowerCase() === d.title.toLowerCase() ? d.room : `${d.room} · ${cap(d.title)}`;
  const running = ["running", "paused", "delayed"].includes(d.state);
  const dp = dialCtl && dialCtl.params[0];
  const dv = dp ? statusDefault(d, dp) : null;
  put(sh, html`${sheetHead(hid, d.icon === "lock" && d.state === "unlocked" ? "unlock" : d.icon, d.full, sub, d.active)}
    ${dialCtl ? html`<div class="ctl" data-ctl="${dialCtl.name}" data-live>
        <div class="gauge"><span data-gauge>${raw(gaugeSvg(0))}</span>
          <div class="gauge-c"><output class="v" data-param="${dp.name}" data-kind="step" data-v="${dv}" data-lo="${dp.lo == null ? "" : dp.lo}" data-hi="${dp.hi == null ? "" : dp.hi}" data-st="${stepOf(dp, d)}" data-unit="${st.unit ? "°" : "%"}" aria-live="polite">${dv}${st.unit ? "°" : "%"}</output>
          <div class="l">Target${st.current != null ? ` · now ${st.current}${st.unit ? "°" : "%"}` : ""}</div></div></div>
        <div class="gauge-row"><button type="button" data-step="-1" data-for="${dp.name}" aria-label="Lower">${icon("minus", 24, 2)}</button>
          <span class="muted">${d.text}</span>
          <button type="button" data-step="1" data-for="${dp.name}" aria-label="Higher">${icon("plus", 24, 2)}</button></div>
        <div class="sp-note">${tierNote(dialCtl)}</div></div>` : liveBlock(d)}
    ${p && !prog ? html`<button class="btn ${d.active ? "" : "btn-primary"} btn-block" type="button" data-sp>${icon(d.icon === "lock" && d.state === "locked" ? "unlock" : "power", 20)}${p.label}</button>
      <div class="sp-note">${tierNote(p)}</div>` : ""}
    ${quick.map(c => html`<div class="ctl" data-ctl="${c.name}" data-auto><div class="ctl-params">${c.params.map(pp => paramInput(d, pp, statusDefault(d, pp), c.name))}</div>${tierNote(c)}</div>`)}
    ${prog ? progControls(d, running) : rest.map(c => html`<div class="ctl" data-ctl="${c.name}">
      <div class="ctl-head"><b>${c.label}</b>${!c.params.length ? html`<button class="btn btn-sm" type="button" data-run="${c.name}">${ACTION_LABEL(c.name)}</button>` : ""}</div>
      ${c.params.length ? html`<div class="ctl-params">${c.params.map(pp => paramInput(d, pp, statusDefault(d, pp), c.name))}</div>
        <div class="ctl-head">${tierNote(c) || html`<span></span>`}<button class="btn btn-sm btn-primary" type="button" data-run="${c.name}">${ACTION_LABEL(c.name)}</button></div>` : tierNote(c)}
      </div>`)}
    <details class="adv"><summary>Details</summary><dl class="kv">${Object.entries(st).filter(([k]) => !/_s$/.test(k)).map(([k, v]) => html`<dt>${cap(k)}</dt><dd>${typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>`)}
      ${d.model ? html`<dt>Model</dt><dd>${d.model}</dd>` : ""}<dt>Device id</dt><dd><code>${d.id}</code></dd></dl></details>`);
  $("[data-close]", sh).onclick = () => closeSheet();
  if (p && !prog) $("[data-sp]", sh).onclick = async e => { e.currentTarget.disabled = true; await act(d, p.action, p.params); };
  let timer = null;
  const gz = $("[data-gauge]", sh), go = gz && $("output[data-kind=step]", sh);
  const paintGauge = () => { if (gz && go) put(gz, raw(gaugeSvg(gaugeFrac(go)))); };
  requestAnimationFrame(paintGauge);
  wireParams(sh, el => {
    if (el === go) paintGauge();
    const box = el.closest("[data-ctl]");
    if (!box || !(box.hasAttribute("data-auto") || box.hasAttribute("data-live"))) return;
    clearTimeout(timer);
    sh.dataset.busy = "1";
    timer = setTimeout(async () => { await act(d, box.dataset.ctl, readParams(box)); delete sh.dataset.busy; }, el.type === "range" || el.dataset.kind === "seg" || el.dataset.kind === "bool" ? 0 : 900);
  });
  $$("[data-run]", sh).forEach(b => b.onclick = async () => {
    const box = b.closest("[data-ctl]") || sh;
    const params = readParams(box);
    b.disabled = true;
    await act(d, b.dataset.run, params);
    b.disabled = false;
  });
  const pick = $("#prog-pick", sh);
  if (pick) {
    const showOpts = () => {
      const v = pick.value;
      $$("[data-opt-for]", sh).forEach(el => { const fr = el.dataset.optFor; el.hidden = !!fr && !fr.split(",").includes(v); });
      const lab = $("#prog-time", sh); const o = pick.selectedOptions[0];
      if (lab && o) lab.textContent = o.dataset.min ? `About ${fmtMin(+o.dataset.min)}` : "";
    };
    pick.onchange = showOpts; showOpts();
  }
}
function progControls(d, running) {
  const start = d.controls.find(c => c.name === "start_program");
  const pp = start.params[0];
  const opts = start.params.slice(1);
  if (running) {
    return html`<div class="btn-row mt-12">${d.state === "running" ? html`<button class="btn" data-run="pause">${icon("pause", 18)}Pause</button>` : ""}
      ${d.state === "paused" ? html`<button class="btn btn-primary" data-run="resume">${icon("play", 18)}Resume</button>` : ""}
      <button class="btn btn-danger" data-run="cancel">${icon("x", 18)}Cancel program</button></div>`;
  }
  const def = pp.default;
  return html`<div class="ctl" data-ctl="start_program">
    <label class="field"><span>Program</span><select class="input" id="prog-pick" data-param="program">
      ${pp.values.map(v => html`<option value="${v}" data-min="${pp.minutes[v] || ""}" ${v === def ? raw("selected") : ""}>${pp.labels[v] || cap(v)}</option>`)}</select>
      <small id="prog-time"></small></label>
    ${opts.length ? html`<details class="adv"><summary>Options</summary><div class="ctl-params">${opts.map(o => html`<div data-opt-for="${(o.programs || []).join(",")}">${paramInput(d, Object.assign({}, o, { default: o.type === "bool" ? false : null }), o.type === "bool" ? false : o.type === "enum" ? "" : null, "start_program")}</div>`)}</div></details>` : ""}
    <div class="ctl-head">${tierNote(start)}<button class="btn btn-primary" type="button" data-run="start_program">${icon("play", 18)}Start</button></div></div>`;
}
function updateDeviceSheet() {
  const top = sheets[sheets.length - 1];
  const d = S.home.devices.find(x => x.id === S.sheetDev);
  if (!top || !d || top.sheet.dataset.hid === undefined || top.sheet.dataset.busy) return;
  const live = $("[data-live]", top.sheet);
  if (!live) return;
  const sig = JSON.stringify([d.state, d.text, d.progress && d.progress.fraction, d.primary && d.primary.action]);
  if (top.sheet.dataset.sig && top.sheet.dataset.sig !== sig) {
    if (top.sheet.contains(document.activeElement) && document.activeElement.matches("input, select")) {
      const tmp = document.createElement("div"); put(tmp, liveBlock(d)); live.replaceWith(tmp.firstElementChild);
    } else drawDeviceSheet(top.sheet, d);
  }
  top.sheet.dataset.sig = sig;
}

/* ------------------------------------------------------------------ CHORES */
function choreRow(i, compact) {
  const me = S.me;
  const can = i.state === "open" && (i.who === me.name || !i.who || isAdult());
  const who = S.home.board.members.find(m => m.name === i.who);
  return html`<li class="row" data-iid="${i.id}">
    <button class="chore-check ${i.state !== "open" ? "is-done" : ""}" type="button" ${can ? "" : raw("disabled")} data-done="${i.id}"
      aria-label="${i.state === "open" ? `Mark ${i.chore} done` : `${i.chore} is done`}"><span class="cc">${icon("check", 16, 2.6)}</span></button>
    <div class="row-main"><div class="row-title">${i.chore}</div>
      <div class="row-meta">
        ${!compact || !i.mine ? html`<span class="who-tag">${who ? avatar(who, "sm") : ""}${i.who || "Anyone"}</span>` : ""}
        ${i.state === "pending_approval" ? html`<span class="pill warn">Waiting for a parent</span>` : i.overdue ? html`<span class="pill bad">Overdue, ${i.due}</span>` : i.due ? html`<span>${i.today ? "Today " + i.due.replace("today ", "") : i.due}</span>` : i.state === "open" ? html`<span>Anytime</span>` : ""}
        <span class="num">${plural(i.points, "point")}</span>
        ${i.auto ? html`<span class="verified" title="${i.auto}">${icon("link", 14, 2)}Auto-checks</span>` : ""}
      </div></div>
    ${isAdult() && i.state === "open" && !compact ? html`<div class="row-end"><button class="btn btn-sm btn-quiet" type="button" data-reassign="${i.id}" aria-label="Reassign ${i.chore}">${icon("swap", 18)}<span class="hide-phone">Reassign</span></button></div>` : ""}
    ${i.state === "pending_approval" && i.can_undo ? html`<div class="row-end">${undoBtn(i)}</div>` : ""}
  </li>`;
}
function doneRow(i) {
  const who = S.home.board.members.find(m => m.name === (i.done_by || i.who));
  return html`<li class="row"><span class="chore-check is-done" aria-hidden="true"><span class="cc">${icon("check", 16, 2.6)}</span></span>
    <div class="row-main"><div class="row-title">${i.chore}</div><div class="row-meta">
      <span class="who-tag">${who ? avatar(who, "sm") : ""}${i.done_by || i.who || "Someone"}</span><span>${i.done}</span>
      ${i.verified ? html`<span class="verified">${icon("check", 14, 2.4)}Verified by the ${cap(i.verified_by.split(".")[0]).toLowerCase()}</span>` : ""}
      ${i.state === "pending_approval" ? html`<span class="pill warn">Waiting for a parent</span>` : ""}</div></div>
    <div class="row-end">${i.can_undo ? undoBtn(i) : ""}<span class="pill ok num">+${i.points}</span></div></li>`;
}
const undoBtn = i => html`<button class="btn btn-sm btn-quiet" type="button" data-undo="${i.id}" aria-label="Undo: ${i.chore} is not done">${icon("undo", 18)}<span class="hide-phone">Undo</span></button>`;
async function undoDone(iid, name) {
  try {
    const r = await api(`/api/chores/${iid}/undo`, {});
    const back = r.points && r.credited ? `. ${plural(r.points, "point")} taken back from ${r.credited}` : "";
    toast(`${name || r.chore} is open again${back}.`);
  } catch (e) { toast(e.message, true); }
  refresh(true);
}
function confirmUndo(i) {
  const other = i.done_by && i.done_by !== S.me.name;
  if (!other) { undoDone(i.id, i.chore); return; }
  openSheet((sh, id, close) => {
    put(sh, html`${sheetHead(id, "undo", "Undo this?", i.chore)}
      <p class="lead">${i.done_by} marked it done ${i.done || ""}. Undoing opens it again and takes back ${plural(i.points, "point")}${i.verified ? html`, even though the ${cap(i.verified_by.split(".")[0]).toLowerCase()} checked it off` : ""}. It shows in Activity.</p>
      <div class="btn-row end"><button class="btn btn-quiet" type="button" data-no>Keep it done</button><button class="btn btn-primary no-arrow" type="button" data-yes autofocus>Undo</button></div>`);
    $("[data-close]", sh).onclick = close;
    $("[data-no]", sh).onclick = close;
    $("[data-yes]", sh).onclick = () => { close(); undoDone(i.id, i.chore); };
  }, { alert: true });
}
function wireChores(root) {
  $$("[data-done]", root).forEach(b => b.onclick = async () => {
    const row = b.closest(".row");
    b.disabled = true;
    try {
      const r = await api(`/api/chores/${b.dataset.done}/done`, {});
      b.classList.add("is-done");
      if (r.state === "done" && !reduceMotion()) {
        const f = document.createElement("span"); f.className = "pts-float"; f.textContent = `+${r.points || 1}`; row.appendChild(f);
      }
      toast(r.note || `Nice. ${plural(r.points || 1, "point")}${r.opened && r.opened.length ? `. Next up: ${r.opened.join(", ")}` : ""}`, false,
        { label: "Undo", ms: Math.min((r.undo_s || 120) * 1000, 10000), run: () => undoDone(r.id, r.chore) });
      setTimeout(() => refresh(true), 700);
    } catch (e) { toast(e.message, true); b.disabled = false; }
  });
  $$("[data-reassign]", root).forEach(b => b.onclick = () => reassign(+b.dataset.reassign, b));
  $$("[data-undo]", root).forEach(b => b.onclick = () => {
    const iid = +b.dataset.undo, B = S.home.board;
    const i = B.done.concat(B.open).find(x => x.id === iid);
    if (i) confirmUndo(i);
  });
}
function reassign(iid, anchor) {
  const inst = S.home.board.open.find(i => i.id === iid);
  const people = S.home.board.members.filter(m => m.role !== "guest");
  openSheet((sh, id, close) => {
    put(sh, html`${sheetHead(id, "swap", "Reassign", inst.chore)}
      <div class="choice">${people.map(p => html`<button type="button" data-p="${p.name}" ${p.name === inst.who ? raw('aria-current="true"') : ""}>${avatar(p)}<span><b>${p.name}</b><small>${cap(p.role)}${p.name === inst.who ? ", has it now" : ""}</small></span>${p.name === inst.who ? icon("check", 20) : ""}</button>`)}</div>`);
    $("[data-close]", sh).onclick = close;
    $$("[data-p]", sh).forEach(b => b.onclick = async () => {
      try { await api(`/api/chores/${iid}/assign`, { person: b.dataset.p }); toast(`${inst.chore}: ${b.dataset.p} has it now`); close(); refresh(true); }
      catch (e) { toast(e.message, true); }
    });
  });
}
function mountChores(page) {
  put(page, html`<div class="page-head"><div><p class="eyebrow">Performance board</p><h1>Chores</h1><p class="sub" id="ch-sub"></p></div></div>
    <section class="section">${secHead("This week", "Leaderboard")}<div class="board" id="family"></div></section>
    <div id="ch-body"></div>`);
}
function updateChores() {
  const b = S.home.board;
  const total = b.people.reduce((a, p) => a + p.total_week, 0), done = b.people.reduce((a, p) => a + p.done_week, 0);
  $("#ch-sub").textContent = `Week of ${b.week_of}. ${done} of ${total} done.`;
  const ranked = [...b.people].sort((x, y) => y.points - x.points || y.done_week - x.done_week || x.name.localeCompare(y.name));
  const fam = $("#family");
  put(fam, html`<div class="lb-row lb-head" aria-hidden="true"><span>#</span><span>Member</span><span>This week</span><span>Points</span><span>Streak</span><span class="lb-flag">Today</span></div>
  ${ranked.map((p, i) => html`<button class="lb-row" type="button" data-person="${p.name}" aria-pressed="${S.person === p.name}" aria-label="${p.name}: ${p.done_week} of ${p.total_week} done, ${p.points} points, ${p.streak} day streak. Show only ${p.name}'s chores">
    <span class="lb-rank">${String(i + 1).padStart(2, "0")}</span>
    <span class="lb-who">${avatar(p, "lg")}<span><b>${p.name}</b><small>${p.me ? "You" : p.role}</small></span></span>
    <span class="lb-prog"><span class="bar"><i data-w="${p.total_week ? p.done_week / p.total_week : 0}"></i></span><span>${p.done_week} of ${p.total_week} done</span></span>
    <span class="lb-n">${p.points}<small>pts</small></span><span class="lb-n">${p.streak}<small>d</small></span>
    <span class="lb-flag">${p.overdue ? html`<span class="pill bad">${p.overdue} overdue</span>` : p.due_today ? html`<span class="pill">${p.due_today} due</span>` : html`<span class="pill ok">On track</span>`}</span>
  </button>`)}`);
  revealOnce(fam, ".lb-row");
  paintBars(fam);
  $$("[data-person]").forEach(x => x.onclick = () => { S.person = S.person === x.dataset.person ? null : x.dataset.person; updateChores(); });
  const by = i => !S.person || i.who === S.person || (!i.who && S.person);
  const open = b.open.filter(i => i.state === "open" && by(i));
  const today = open.filter(i => i.today || i.overdue);
  const later = open.filter(i => !(i.today || i.overdue));
  const waiting = b.open.filter(i => i.state === "pending_approval" && by(i));
  const doneList = b.done.filter(i => !S.person || i.done_by === S.person);
  put($("#ch-body"), html`
    ${S.person ? html`<p class="hint mb-16">Showing ${S.person}'s chores. <a href="#" data-clear>Show everyone</a></p>` : ""}
    <section class="section">${secHead("Now", "Due today")}${today.length ? html`<ul class="list">${today.map(i => choreRow(i))}</ul>`
      : html`<div class="empty"><div class="empty-icon">${icon("check", 24)}</div><h3>Nothing due today</h3><p>Enjoy it.</p></div>`}</section>
    ${waiting.length ? html`<section class="section"><div class="sec-head"><h2>Waiting for a parent</h2></div><ul class="list">${waiting.map(i => choreRow(i))}</ul></section>` : ""}
    ${later.length ? html`<section class="section"><div class="sec-head"><h2>Coming up</h2></div><ul class="list">${later.map(i => choreRow(i))}</ul></section>` : ""}
    <section class="section">${secHead("Schedule", "Rotations")}<div class="rot">${b.rotations.map(r => html`<div class="card rot-card">
      <h3>${r.chore}</h3>
      ${r.rotate === "week" ? html`<div class="rot-line"><span class="lbl">This week</span>${personChip(r.this_week)}${isAdult() ? html`<button class="btn btn-sm btn-quiet ml-auto" data-week="${r.chore_id}" aria-label="Change who has ${r.chore} this week">${icon("swap", 16)}</button>` : ""}</div>
        <div class="rot-line"><span class="lbl">Next week</span>${personChip(r.next_week)}</div>`
        : r.now ? html`<div class="rot-line"><span class="lbl">Now</span>${personChip(r.now)}</div>` : html`<div class="rot-line"><span class="lbl">Next up</span>${personChip(r.next_up)}</div>`}
      <p class="faint">${cap(r.schedule)}${r.auto ? html`. ${r.auto}` : ""}</p></div>`)}</div></section>
    <section class="section">${secHead("Results", "Done this week")}${doneList.length ? html`<ul class="list">${doneList.map(doneRow)}</ul>` : html`<p class="muted">Nothing finished yet this week.</p>`}</section>`);
  $$("[data-week]").forEach(x => { x.onclick = () => weekPick(+x.dataset.week); });
  const cl = $("[data-clear]"); if (cl) cl.onclick = e => { e.preventDefault(); S.person = null; updateChores(); };
  wireChores($("#ch-body"));
}
function personChip(name) {
  const m = S.home.board.members.find(x => x.name === name);
  return m ? html`<span class="who-tag">${avatar(m, "sm")}<b>${m.name}</b></span>` : html`<span class="who-tag"><span class="avatar sm" aria-hidden="true">?</span><b>Anyone</b></span>`;
}
function weekPick(cid) {
  const r = S.home.board.rotations.find(x => x.chore_id === cid);
  const people = S.home.board.members.filter(m => m.role !== "guest");
  openSheet((sh, id, close) => {
    put(sh, html`${sheetHead(id, "swap", "Who has it this week?", r.chore)}<div class="choice">${people.map(p => html`<button type="button" data-p="${p.name}">${avatar(p)}<span><b>${p.name}</b><small>${cap(p.role)}${p.name === r.this_week ? ", this week" : ""}</small></span>${p.name === r.this_week ? icon("check", 20) : ""}</button>`)}</div>`);
    $("[data-close]", sh).onclick = close;
    $$("[data-p]", sh).forEach(b => b.onclick = async () => {
      try { await api(`/api/rotations/${cid}/week`, { person: b.dataset.p }); toast(`${r.chore}: ${b.dataset.p} this week`); close(); refresh(true); }
      catch (e) { toast(e.message, true); }
    });
  });
}

/* ------------------------------------------------------------------ APPROVALS */
function approvalCard(a) {
  const ap = S.home.approvals;
  const m = S.home.board.members.find(x => x.name === a.who) || { initials: a.initials, color: 5 };
  return html`<div class="card appr"><span>${avatar(m, "lg")}</span>
    <div><h3>${a.summary}</h3><p class="appr-meta">${icon(a.kind === "chore" ? "chores" : "shield", 14, 2)}<span>${a.why}</span><span class="faint">${a.created}</span></p></div>
    ${ap.can_decide ? html`<div class="btn-row"><button class="btn" type="button" data-ap="${a.id}" data-y="0">Deny</button>
      <button class="btn btn-primary" type="button" data-ap="${a.id}" data-y="1">Approve</button></div>` : html`<span class="pill warn">Waiting</span>`}</div>`;
}
function confirmationCard(c) {
  return html`<form class="card appr" data-conf="${c.id}"><span class="avatar lg" aria-hidden="true">${icon("link", 20, 2)}</span>
    <div><h3>${c.client} asks: ${c.question}</h3><p>${icon("shield", 14, 2)}An agent is waiting for your answer <span class="faint">· ${c.expires_s}s left</span></p>
    ${c.kind === "phrase" ? html`<input class="input" name="v" aria-label="Type ${c.phrase}" placeholder="Type ${c.phrase}">` : ""}</div>
    <div class="btn-row"><button class="btn" type="submit" value="no">No</button><button class="btn btn-primary" type="submit" value="yes">Yes</button></div></form>`;
}
function wireApprovals(root) {
  $$("[data-ap]", root).forEach(b => b.onclick = async () => {
    $$(`[data-ap="${b.dataset.ap}"]`, root).forEach(x => x.disabled = true);
    try {
      const r = await api(`/api/approvals/${b.dataset.ap}`, { approve: b.dataset.y === "1" });
      toast(r.state === "approved" ? "Approved. It's running as you." : "Declined.");
    } catch (e) { toast(e.message, true); }
    refresh(true);
  });
  $$("[data-conf]", root).forEach(f => f.onsubmit = async e => {
    e.preventDefault();
    try { await api(`/api/confirmations/${f.dataset.conf}`, { yes: e.submitter && e.submitter.value === "yes", phrase: f.v ? f.v.value : undefined }); toast("Answered."); }
    catch (err) { toast(err.message, true); }
    refresh(true);
  });
}
function mountApprovals(page) {
  put(page, html`<div class="page-head"><div><p class="eyebrow">Decisions</p><h1>Approvals</h1><p class="sub">${isAdult() ? "Requests from the kids, and agents waiting for your yes." : "What you've asked a parent for."}</p></div></div><div id="ap-body"></div>`);
}
function updateApprovals() {
  const ap = S.home.approvals;
  const n = ap.approvals.length + ap.confirmations.length;
  put($("#ap-body"), n ? html`<div class="approvals">${ap.confirmations.map(confirmationCard)}${ap.approvals.map(approvalCard)}</div>`
    : html`<div class="card empty"><div class="empty-icon">${icon("inbox", 24)}</div><h3>Nothing waiting</h3>
      <p>${isAdult() ? "When a kid asks for something that uses water or heat, it shows up here for you to approve." : "Requests you send to a parent show up here."}</p></div>`);
  wireApprovals($("#ap-body"));
}

/* ------------------------------------------------------------------ ACTIVITY */
function tlItem(it) {
  const dev = it.device && S.home && S.home.devices.find(x => x.id === it.device);
  const ic = it.kind === "chore" ? "check" : it.tool === "undo_chore" ? "undo" : dev ? dev.icon : it.kind === "security" ? "key" : "activity";
  const cls = it.outcome === "done" ? "ok" : ["failed", "denied", "declined"].includes(it.outcome) ? "bad" : ["attention", "queued", "waiting"].includes(it.outcome) ? "warn" : "";
  const word = { failed: "Didn't work", denied: "Not allowed", declined: "Not confirmed", queued: "Sent to a parent", waiting: "Waiting for a parent" }[it.outcome];
  return html`<li class="tl-item"><span class="tl-time">${it.time}</span><span class="tl-ico ${cls}">${icon(ic, 16, 2)}</span>
    <div class="tl-text">${it.text}<small>${[it.via, word, it.detail].filter(Boolean).join(" · ")}</small></div></li>`;
}
function mountActivity(page) {
  put(page, html`<div class="page-head"><div><p class="eyebrow">Audit trail</p><h1>Activity</h1><p class="sub">What happened at home, who did it and how.</p></div><span class="spacer"></span>
    ${isAdult() ? html`<div class="seg" role="group" aria-label="View"><button type="button" data-raw="0" aria-pressed="${!S.rawLog}">Timeline</button><button type="button" data-raw="1" aria-pressed="${S.rawLog}">Raw log</button></div>` : ""}</div>
    <div id="act-body"></div>`);
  $$("[data-raw]", page).forEach(b => b.onclick = () => { S.rawLog = b.dataset.raw === "1"; $$("[data-raw]", page).forEach(x => x.setAttribute("aria-pressed", x === b)); S.actT = 0; updateActivity(); });
}
async function updateActivity() {
  if (S.actT && Date.now() - S.actT < 10000) return;
  S.actT = Date.now();
  const body = $("#act-body");
  try {
    if (S.rawLog) {
      const a = await api("/api/audit?limit=200");
      put(body, html`<div class="card raw-wrap"><table class="raw"><thead><tr><th>When</th><th>Who</th><th>Via</th><th>Tool</th><th>Arguments</th><th>Result</th></tr></thead>
        <tbody>${a.audit.map(r => html`<tr><td class="num">${r.t}</td><td>${r.who || ""}</td><td>${r.source}${r.client ? " / " + r.client : ""}</td><td><code>${r.tool}</code></td>
        <td><code>${JSON.stringify(r.args || {})}</code></td><td>${r.ok ? html`<span class="pill ok">ok</span>` : html`<span class="pill bad">${r.error || r.outcome || "no"}</span>`}</td></tr>`)}</tbody></table></div>`);
      return;
    }
    const a = await api("/api/activity?limit=120");
    const days = [];
    a.items.forEach(it => { let d = days.find(x => x.day === it.day); if (!d) days.push(d = { day: it.day, items: [] }); d.items.push(it); });
    put(body, days.length ? html`${days.map(d => html`<section class="day"><h2>${d.day}</h2><ul class="tl card card-pad">${d.items.map(tlItem)}</ul></section>`)}`
      : html`<div class="card empty"><div class="empty-icon">${icon("activity", 24)}</div><h3>No activity yet</h3><p>Device actions, finished chores and approvals show up here.</p></div>`);
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------------ SETTINGS */
const SECTIONS = [
  ["household", "Household", "home"], ["people", "People", "users"], ["devices", "Devices", "plug"],
  ["assistant", "Assistant", "cpu"], ["chores", "Chores", "chores"], ["phone", "Phone and agents", "phone"],
  ["remote", "Away from home", "globe"], ["about", "About", "mark"],
];
async function mountSettings(page) {
  const sec = SECTIONS.some(s => s[0] === S.sub) ? S.sub : "household";
  put(page, html`<div class="page-head"><div><p class="eyebrow">System</p><h1>Settings</h1><p class="sub">${isOwner() ? "Everything here is also in chorepilot setup." : "Only the owner can change these."}</p></div></div>
    <div class="settings"><nav class="settings-nav" aria-label="Settings sections">${SECTIONS.map(([k, l]) => html`<button type="button" data-sec="${k}" aria-current="${k === sec}">${l}</button>`)}</nav>
    <div id="set-body"><p class="muted">Loading</p></div></div>`);
  $$("[data-sec]", page).forEach(b => b.onclick = () => go("settings/" + b.dataset.sec));
  try { S.settings = await api("/api/settings"); } catch (e) { toast(e.message, true); return; }
  renderPanel(sec, $("#set-body"), false);
}
function cmdLine(text) {
  return html`<div class="cmd"><span>${text}</span><button class="icon-btn" type="button" data-copy="${text}" aria-label="Copy">${icon("copy", 18)}</button></div>`;
}
function wireCopy(root) {
  $$("[data-copy]", root).forEach(b => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); toast("Copied"); } catch (e) { toast("Select the text and copy it.", true); }
  });
}
async function post(path, body) { try { return await api(path, body); } catch (e) { toast(e.message, true); return null; } }
async function reloadSettings() { try { S.settings = await api("/api/settings"); } catch (e) { /* keep */ } }
function renderPanel(sec, el, wiz) {
  const st = S.settings || {};
  const owner = isOwner();
  const again = () => renderPanel(sec, el, wiz);
  const head = (t, l) => wiz ? "" : html`<h2>${t}</h2><p class="lead">${l}</p>`;
  if (sec === "household") {
    put(el, html`${head("Household", "The name everyone sees, and how temperatures are shown.")}
      <div class="card panel-pad panel"><form id="hh"><label class="field"><span>Home name</span><input class="input" name="household" value="${st.household}" ${owner ? "" : raw("disabled")}></label>
      <div class="field"><span>Temperatures</span><div class="seg" role="group" aria-label="Unit">${["C", "F"].map(u => html`<button type="button" data-u="${u}" aria-pressed="${st.unit === u}" ${owner ? "" : raw("disabled")}>${u === "C" ? "Celsius" : "Fahrenheit"}</button>`)}</div></div>
      ${owner ? html`<button class="btn btn-primary" type="submit">Save</button>` : ""}</form></div>
      <div class="card panel-pad panel"><h3 class="panel-title">Who can do what</h3><ul class="check-list">
        ${[["Adults", "Run anything; water and heat ask them first; approve kids' requests."],
          ["Kids", st.policy && st.policy.kids_tier1 ? "Lights, the vacuum and locking a door; anything with water or heat goes to a parent." : "Everything goes to a parent first."],
          ["Guests", st.policy && st.policy.guests_tier1 ? "Simple things like lights." : "Can look, not touch."],
          ["The owner", "Also unlocks doors, opens the garage and disarms the alarm, always with a PIN."],
          ["Agents (MCP)", "Never unlock, open the garage or disarm. Water and heat need your yes on your own phone."]].map(([a, b]) => html`<li class="check-row"><span class="check-dot on">${icon("check", 14, 3)}</span><span><b>${a}</b>. ${b}</span></li>`)}
      </ul></div>`);
    let unit = st.unit;
    $$("[data-u]", el).forEach(b => b.onclick = () => { unit = b.dataset.u; $$("[data-u]", el).forEach(x => x.setAttribute("aria-pressed", x === b)); });
    const f = $("#hh", el);
    f.onsubmit = async e => { e.preventDefault(); if (await post("/api/setup/household", { household: f.household.value, unit })) { toast("Saved"); S.me.household = f.household.value; await reloadSettings(); } };
    return;
  }
  if (sec === "people") {
    put(el, html`${head("People", "Everyone who lives here. A PIN lets them sign in to this dashboard.")}
      <ul class="list panel">${(st.members || []).map(m => html`<li class="row">${avatar(m)}<div class="row-main"><div class="row-title">${m.name}</div>
        <div class="row-meta"><span>${cap(m.role)}</span>${m.pin ? html`<span class="pill ok">PIN</span>` : html`<span class="pill">No PIN</span>`}${m.totp ? html`<span class="pill ok">Authenticator</span>` : ""}${m.telegram ? html`<span class="pill">Telegram</span>` : ""}${m.quiet_hours ? html`<span>Quiet ${m.quiet_hours}</span>` : ""}</div></div>
        ${owner ? html`<div class="row-end"><button class="btn btn-sm" type="button" data-pin="${m.name}">${m.pin ? "Change PIN" : "Set PIN"}</button></div>` : ""}</li>`)}</ul>
      ${owner ? html`<div class="card panel-pad panel"><form id="addm" autocomplete="off"><h3 class="panel-title">Add someone</h3><div class="grid-2">
        <label class="field"><span>Name</span><input class="input" name="name" maxlength="40" required autocomplete="off"></label>
        <label class="field"><span>PIN (optional)</span><input class="input num" name="pin" type="password" inputmode="numeric" maxlength="12" autocomplete="new-password"></label></div>
        <div class="field"><span>Role</span><div class="seg" role="group" aria-label="Role">${["adult", "kid", "guest"].map((r, i) => html`<button type="button" data-r="${r}" aria-pressed="${i === 0}">${cap(r)}</button>`)}</div>
        <small>Kids get quiet hours from 21:00 to 07:00 for reminders.</small></div>
        <button class="btn btn-primary" type="submit">${icon("plus", 18)}Add to the household</button></form></div>` : ""}`);
    let role = "adult";
    $$("[data-r]", el).forEach(b => b.onclick = () => { role = b.dataset.r; $$("[data-r]", el).forEach(x => x.setAttribute("aria-pressed", x === b)); });
    const f = $("#addm", el);
    if (f) f.onsubmit = async e => { e.preventDefault(); const r = await post("/api/setup/members", { name: f.name.value, role, pin: f.pin.value }); if (r) { toast(`${r.person.name} added`); await reloadSettings(); again(); refresh(true); } };
    $$("[data-pin]", el).forEach(b => b.onclick = async () => {
      const v = await askHuman({ kind: "code", question: `New PIN for ${b.dataset.pin}`, why: "4 to 12 digits" });
      if (v && await post("/api/setup/pin", { name: b.dataset.pin, pin: v })) { toast("PIN saved"); await reloadSettings(); again(); }
    });
    return;
  }
  if (sec === "devices") {
    const devs = st.devices || [];
    put(el, html`${head("Devices", "Connect what you already have. ChorePilot talks to them on your network.")}
      ${devs.length && !wiz ? html`<ul class="list panel">${devs.map(d => html`<li class="row"><span class="opt-ico">${icon(d.type, 20)}</span><div class="row-main"><div class="row-title">${cap(d.id)}</div>
        <div class="row-meta"><span>${cap(d.type)}</span><span>${cap(d.backend)}</span>${d.target ? html`<code>${d.target}</code>` : ""}</div></div></li>`)}</ul>` : ""}
      ${st.demo ? html`<p class="hint panel">You are looking at the built-in demo house. Connecting anything below replaces it with your own.</p>` : ""}
      ${owner ? html`<div class="card panel">
        <div class="opt"><span class="opt-ico">${icon("home", 20)}</span><div><h3>Home Assistant</h3><p>The easiest way to bring in lights, thermostats, locks, Matter devices and more.</p>
          <form id="ha" class="grid-2 mt-12" autocomplete="off"><label class="field"><span>Address</span><input class="input" name="url" placeholder="http://homeassistant.local:8123" value="${(st.ha && st.ha.url) || ""}"></label>
          <label class="field"><span>Long-lived access token</span><input class="input" name="token" type="password" autocomplete="off"></label>
          <div><button class="btn" type="submit">Find devices</button></div></form><div id="ha-found"></div></div><span></span></div>
        <div class="opt"><span class="opt-ico">${icon("washer", 20)}</span><div><h3>Home Connect</h3><p>Bosch, Siemens, Neff, Gaggenau and Thermador dishwashers, washers, dryers, ovens and coffee machines.</p>
          <form id="hc" class="mt-12" autocomplete="off"><label class="field"><span>Client ID of your Home Connect developer app</span><input class="input" name="cid"></label><button class="btn" type="submit">Sign in with Home Connect</button></form><div id="hc-state"></div></div><span></span></div>
        <div class="opt"><span class="opt-ico">${icon("link", 20)}</span><div><h3>MQTT broker</h3><p>ESPHome and other MQTT devices.</p>
          <form id="mq" class="grid-2 mt-12" autocomplete="off"><label class="field"><span>Broker</span><input class="input" name="host" placeholder="192.168.1.20:1883"></label>
          <label class="field"><span>User (optional)</span><input class="input" name="user"></label><label class="field"><span>Password</span><input class="input" name="password" type="password"></label><div><button class="btn" type="submit">Save broker</button></div></form></div><span></span></div>
        <div class="opt"><span class="opt-ico">${icon("devices", 20)}</span><div><h3>Try it with simulated devices</h3><p>A dishwasher, thermostat, sprinklers, locks and more, all simulated. Nothing real is touched.</p></div>
          <button class="btn" type="button" id="sim">Add simulated devices</button></div>
      </div>` : ""}`);
    const ha = $("#ha", el);
    if (ha) ha.onsubmit = async e => {
      e.preventDefault();
      const r = await post("/api/setup/ha/discover", { url: ha.url.value, token: ha.token.value });
      if (!r) return;
      const box = $("#ha-found", el);
      put(box, html`<p class="hint">${r.message}: ${plural(r.entities.length, "device")} we can control. Pick the ones to add.</p>
        <div class="ent-list">${r.entities.map(x => html`<label class="ent"><input type="checkbox" checked value="${x.entity}"><span class="opt-ico">${icon(x.type, 18)}</span>
        <span class="row-main"><b>${x.name || x.nick}</b><br><small class="faint">${x.entity} · ${cap(x.type)} · ${x.state}</small></span></label>`)}</div>
        <div class="btn-row mt-12"><button class="btn btn-primary" type="button" id="ha-add">Add selected</button></div>`);
      $("#ha-add", box).onclick = async () => {
        const ents = $$("input[type=checkbox]:checked", box).map(c => c.value);
        if (await post("/api/setup/ha/import", { url: ha.url.value, token: ha.token.value, entities: ents })) { toast(`${plural(ents.length, "device")} added`); await reloadSettings(); again(); refresh(true); }
      };
    };
    const hc = $("#hc", el);
    if (hc) hc.onsubmit = async e => {
      e.preventDefault();
      const r = await post("/api/setup/homeconnect", { client_id: hc.cid.value });
      if (!r) return;
      const box = $("#hc-state", el);
      const poll = async () => {
        let v; try { v = await api("/api/setup/homeconnect"); } catch (er) { return; }
        put(box, html`${v.url ? html`<div class="opt opt-flat"><span class="qr">${raw(v.qr || "")}</span><div><h3>Sign in on your phone</h3><p>Scan the code, or open <a href="${v.url}" target="_blank" rel="noopener">${v.url}</a> and enter <b class="mono">${v.code}</b>.</p></div><span></span></div>` : ""}
          ${v.state === "running" ? html`<p class="hint">Waiting for you to sign in</p>` : v.state === "done" ? html`<p class="hint">Connected. ${v.lines.slice(-1)[0] || ""}</p>` : v.error ? html`<p class="err">${v.error}</p>` : ""}`);
        if (v.state === "running") setTimeout(poll, 2000); else if (v.state === "done") { await reloadSettings(); refresh(true); }
      };
      poll();
    };
    const mq = $("#mq", el);
    if (mq) mq.onsubmit = async e => { e.preventDefault(); if (await post("/api/setup/mqtt", { host: mq.host.value, user: mq.user.value, password: mq.password.value })) { toast("Broker saved"); await reloadSettings(); } };
    const sim = $("#sim", el);
    if (sim) sim.onclick = async () => { if (await post("/api/setup/simulated", {})) { toast("Simulated devices added"); await reloadSettings(); again(); refresh(true); } };
    return;
  }
  if (sec === "assistant") {
    const llm = st.llm || {};
    put(el, html`${head("Assistant", "The model that understands what you ask. It runs on your computer or another one on your network.")}
      <div class="card panel-pad panel"><div class="ctl-head"><div><b>${llm.configured ? (llm.model || "Model server") : "No model yet"}</b>
        <div class="row-meta">${llm.configured ? html`<code>${llm.url}</code>${llm.ok ? html`<span class="pill ok">Answering</span>` : html`<span class="pill bad">Not answering</span>`}` : html`<span>Without one, device controls and chores still work; asking in words is off.</span>`}</div></div>
        ${llm.configured && owner ? html`<button class="btn btn-sm" type="button" id="mtest">Test</button>` : ""}</div><p id="mres" class="hint"></p></div>
      ${owner ? html`<div class="card panel"><div class="opt"><span class="opt-ico">${icon("refresh", 20)}</span><div><h3>Find one on this computer</h3><p>Looks for llama.cpp, Ollama, LM Studio and vLLM on their usual ports.</p><div id="mfound"></div></div><button class="btn" type="button" id="mdetect">Look</button></div>
        <div class="opt"><span class="opt-ico">${icon("link", 20)}</span><div><h3>Use an address</h3><p>Any OpenAI-compatible server, for example on another computer at home.</p>
        <form id="murl" class="grid-2 mt-12"><label class="field"><span>Address</span><input class="input" name="url" placeholder="http://192.168.1.30:8080" value="${llm.url || ""}"></label>
        <label class="field"><span>Model name (optional)</span><input class="input" name="model" value="${llm.model || ""}"></label><div><button class="btn btn-primary" type="submit">Use it</button></div></form></div><span></span></div>
        <div class="opt"><span class="opt-ico">${icon("cpu", 20)}</span><div><h3>Don't have one?</h3><p>A small instruct model with tool calling is enough, for example Qwen2.5 3B. Install Ollama and run:</p>${cmdLine("ollama pull qwen2.5:3b")}<p>or <code>chorepilot recommend --catalog</code> for what fits this machine.</p></div><span></span></div></div>` : ""}`);
    wireCopy(el);
    const t = $("#mtest", el);
    if (t) t.onclick = async () => { $("#mres", el).textContent = "Asking the model to say ready"; const r = await post("/api/setup/model/test", {}); if (r) $("#mres", el).textContent = r.ok ? `It answered "${r.reply}" in ${r.latency_s}s${r.tok_s ? `, ${Math.round(r.tok_s)} tokens/s` : ""}.` : r.error; };
    const dt = $("#mdetect", el);
    if (dt) dt.onclick = async () => {
      dt.disabled = true; let r; try { r = await api("/api/setup/model/detect"); } catch (e) { toast(e.message, true); } dt.disabled = false;
      if (!r) return;
      const box = $("#mfound", el);
      put(box, r.found.length ? html`<div class="choice mt-12">${r.found.map(f => html`<button type="button" data-url="${f.url}" data-model="${f.models[0] || ""}">${icon("cpu", 20)}<span><b>${cap(f.kind)}</b><small>${f.url}${f.models.length ? " · " + f.models.slice(0, 3).join(", ") : ""}</small></span>${icon("chev", 18)}</button>`)}</div>`
        : html`<p class="hint">Nothing found on the usual ports.</p>`);
      $$("[data-url]", box).forEach(b => b.onclick = async () => { if (await post("/api/setup/model", { url: b.dataset.url, model: b.dataset.model })) { toast("Assistant set"); await reloadSettings(); again(); refresh(true); } });
    };
    const mu = $("#murl", el);
    if (mu) mu.onsubmit = async e => { e.preventDefault(); const r = await post("/api/setup/model", { url: mu.url.value, model: mu.model.value }); if (r) { toast(r.log.slice(-1)[0] || "Saved"); await reloadSettings(); again(); refresh(true); } };
    return;
  }
  if (sec === "chores") {
    const b = S.home && S.home.board;
    put(el, html`${head("Chores", "Chores rotate between people and can check themselves off when a device finishes.")}
      <div class="card panel"><div class="opt"><span class="opt-ico">${icon("chores", 20)}</span><div><h3>Suggested chores</h3><p>Dishes, laundry, bins, vacuuming and more, fitted to your devices and the people you added. Adds what is missing; keeps what you have.</p></div>
        ${owner ? html`<button class="btn btn-primary" type="button" id="defch">Add suggestions</button>` : ""}</div>
        <div class="opt"><span class="opt-ico">${icon("list", 20)}</span><div><h3>Edit by hand</h3><p>Add or change a chore from the terminal, or ask the house: "add a chore to water the plants every Sunday".</p>${cmdLine('chorepilot chores add "Water the plants" --schedule "every sunday" --rotation Sam,Jordan')}</div><span></span></div></div>
      ${b && b.rotations.length ? html`<ul class="list panel">${b.rotations.map(r => html`<li class="row"><div class="row-main"><div class="row-title">${r.chore}</div><div class="row-meta"><span>${r.schedule}</span><span>${r.people.join(", ") || "Anyone"}</span>${r.auto ? html`<span class="verified">${icon("link", 14, 2)}${r.auto}</span>` : ""}</div></div></li>`)}</ul>` : ""}`);
    wireCopy(el);
    const dc = $("#defch", el);
    if (dc) dc.onclick = async () => { const r = await post("/api/setup/chores", {}); if (r) { toast(r.added.length ? `${plural(r.added.length, "chore")} ready` : "Your chores are already set"); await refresh(true); again(); } };
    return;
  }
  if (sec === "phone") {
    const tg = st.telegram || {};
    const people = (st.members || []);
    put(el, html`${head("Phone and agents", "Use ChorePilot from your phone, from Telegram, or let an AI agent help, always with your rules.")}
      <div class="card panel">
        <div class="opt"><span class="opt-ico">${icon("phone", 20)}</span><div><h3>Add to your home screen</h3><p>On iPhone, tap Share then Add to Home Screen. On Android, open the browser menu and choose Install app. It opens like an app; your data stays on your ChorePilot.</p></div><span></span></div>
        ${owner ? html`<div class="opt"><span class="opt-ico">${icon("ask", 20)}</span><div><h3>Telegram ${tg.configured ? html`<span class="pill ok">${tg.bot ? "@" + tg.bot : "Connected"}</span>` : ""}</h3>
          <p>${tg.configured ? "Each person links their own Telegram once. Scan the code with their phone, or send the code to the bot." : "Create your own bot: in Telegram, open @BotFather, send /newbot, then paste the token here. The bot is yours alone."}</p>
          ${tg.configured ? html`<div class="chips mt-12">${people.map(p => html`<button class="chip" type="button" data-link="${p.name}">${p.telegram ? icon("check", 16, 2) : ""}${p.name}</button>`)}</div><div id="tg-link"></div>`
            : html`<form id="tgf" class="mt-12" autocomplete="off"><label class="field"><span>Bot token</span><input class="input" name="tok" type="password" placeholder="123456789:AA..."></label><button class="btn btn-primary" type="submit">Connect</button></form>`}</div><span></span></div>
        <div class="opt"><span class="opt-ico">${icon("link", 20)}</span><div><h3>AI agents (MCP)</h3><p>Let Claude, Grok or any MCP client use the house as one person. They can never unlock, open the garage or disarm; water and heat still need that person's yes.</p>
          ${(st.agents || []).length ? html`<p class="hint">${st.agents.map(a => `${a.client} acts as ${a.person}`).join("; ")}</p>` : ""}
          <form id="mcpf" class="grid-2 mt-12" autocomplete="off"><label class="field"><span>Agent name</span><input class="input" name="client" placeholder="claude"></label>
          <label class="field"><span>Acts as</span><select class="input" name="person">${people.map(p => html`<option>${p.name}</option>`)}</select></label><div><button class="btn" type="submit">Create a token</button></div></form><div id="mcp-out"></div></div><span></span></div>` : ""}
      </div>`);
    const tgf = $("#tgf", el);
    if (tgf) tgf.onsubmit = async e => { e.preventDefault(); const r = await post("/api/setup/telegram", { token: tgf.tok.value }); if (r) { toast(r.bot ? `Connected to @${r.bot}` : "Token saved"); await reloadSettings(); again(); } };
    $$("[data-link]", el).forEach(b => b.onclick = async () => {
      const r = await post("/api/setup/telegram/link", { name: b.dataset.link });
      if (!r) return;
      put($("#tg-link", el), html`<div class="opt opt-flat">${r.qr ? html`<span class="qr">${raw(r.qr)}</span>` : html`<span></span>`}<div><h3>${r.name}'s link code</h3>
        <p>${r.link ? "Scan with " + r.name + "'s phone camera, or send this in a private chat with the bot:" : "Send this in a private chat with the bot:"}</p>${cmdLine("/link " + r.code)}<p class="faint">Works once, for 10 minutes.</p></div><span></span></div>`);
      wireCopy(el);
    });
    const mf = $("#mcpf", el);
    if (mf) mf.onsubmit = async e => {
      e.preventDefault();
      const r = await post("/api/setup/mcp", { client: mf.client.value, person: mf.person.value });
      if (!r) return;
      put($("#mcp-out", el), html`<div class="mt-12" id="mcpo"><p class="hint"><b>${r.client}</b> acts as ${r.person}. The token is shown once; copy it now.</p>
        ${cmdLine(r.token)}<p class="hint">Claude Code:</p>${cmdLine(r.commands[0])}<p class="hint">Or on this computer, without a token:</p>${cmdLine(r.commands[1])}
        ${r.qr ? html`<p class="hint">Phone agents can scan the configuration:</p><span class="qr">${raw(r.qr)}</span>` : ""}</div>`);
      wireCopy(el); await reloadSettings();
    };
    return;
  }
  if (sec === "remote") {
    put(el, html`${head("Away from home", "Reach your home from your phone or your office without opening your router to the internet. ChorePilot never relays your data through anyone's cloud.")}<div id="rem"><p class="muted">Checking</p></div>`);
    if (!owner) { put($("#rem", el), html`<p class="muted">The owner sets this up.</p>`); return; }
    api("/api/settings/remote").then(r => {
      const box = $("#rem", el);
      put(box, html`<div class="card panel-pad panel"><dl class="kv">
        <dt>Listening on</dt><dd><code>${r.bind}</code>${r.loopback_only ? " (this computer only)" : ""}</dd>
        <dt>Public address</dt><dd>${r.public_url ? html`<code>${r.public_url}</code>` : "None"}</dd>
        <dt>Secure cookies</dt><dd>${r.secure_cookie ? "On for the public address" : "Off (not served over HTTPS)"}</dd>
        <dt>This page came via</dt><dd><code>${r.this_request.host}</code>${r.this_request.https ? " over HTTPS" : ""}</dd>
        <dt>Accepted host names</dt><dd>${r.allowed_hosts.length ? r.allowed_hosts.join(", ") : "Any (LAN mode)"}</dd></dl></div>
        <div class="card panel">
          <div class="opt"><span class="opt-ico">${icon("shield", 20)}</span><div><h3>Tailscale <span class="pill accent">Recommended</span>
            ${r.tailscale ? html`<span class="pill ${r.tailscale.running ? "ok" : ""}">${r.tailscale.running ? "Running here" : "Installed"}</span>` : html`<span class="pill">Not installed</span>`}</h3>
            <p>A private network between your own devices. Install Tailscale on this computer and your phone, sign in to both, then run:</p>
            ${r.commands.tailscale.map(cmdLine)}<p class="faint">tailscale serve gives this dashboard an HTTPS address only your devices can open. ChorePilot then accepts that name and sets secure cookies on it.</p></div><span></span></div>
          <div class="opt"><span class="opt-ico">${icon("globe", 20)}</span><div><h3>Cloudflare Tunnel with Access ${r.cloudflared ? html`<span class="pill ok">cloudflared found</span>` : ""}</h3>
            <p>For a business or a shared address. Put Cloudflare Access (an email code or your company sign-in) in front, so only your people reach the sign-in page.</p>
            ${r.commands.cloudflare.map(cmdLine)}<p class="faint">Never use a quick tunnel without Access: anyone with the link could try PINs.</p></div><span></span></div>
          <div class="opt"><span class="opt-ico">${icon("home", 20)}</span><div><h3>Only at home (LAN)</h3><p>Every device on your Wi-Fi can open it. There is no HTTPS on the LAN, so only use this on a network you trust.</p>${r.commands.lan.map(cmdLine)}</div><span></span></div>
        </div>
        <div class="card panel-pad panel"><form id="pub"><label class="field"><span>Public address (saved for chorepilot run)</span>
          <input class="input" name="u" placeholder="${r.suggested_public_url || "https://house.your-tailnet.ts.net"}" value="${r.public_url || ""}"></label>
          <div class="btn-row"><button class="btn btn-primary" type="submit">Save</button>${r.suggested_public_url && !r.public_url ? html`<button class="btn" type="button" id="usets">Use ${r.suggested_public_url}</button>` : ""}</div>
          <p class="hint">Takes effect after ChorePilot restarts.</p></form></div>`);
      wireCopy(box);
      const f = $("#pub", box);
      f.onsubmit = async e => { e.preventDefault(); if (await post("/api/setup/remote", { public_url: f.u.value.trim() })) toast("Saved. Restart ChorePilot to apply."); };
      const u = $("#usets", box); if (u) u.onclick = () => { f.u.value = r.suggested_public_url; };
    }).catch(e => put($("#rem", el), html`<p class="err">${e.message}</p>`));
    return;
  }
  if (sec === "about") {
    put(el, html`${head("About", "")}<div class="card panel-pad panel"><dl class="kv"><dt>Version</dt><dd>${st.version}</dd><dt>Signed in as</dt><dd>${S.me.name} (${S.me.role})</dd>
      <dt>Assistant</dt><dd>${(st.llm && st.llm.url) || "None"}</dd><dt>Devices</dt><dd>${(st.devices || []).length}</dd></dl></div>
      <p class="hint">ChorePilot runs entirely on your own hardware. This page loads nothing from the internet, stores nothing in anyone's cloud, and every action is in the Activity log.</p>`);
  }
}

boot();
})();
