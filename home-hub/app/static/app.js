/* Home LLM Hub — frontend (vanilla JS, no build step, no CDNs).
 * Consumes the HTTP API exactly as specified in the SHARED CONTRACT.
 */
'use strict';

/* ===========================================================================
 * Global state
 * ========================================================================= */
const State = {
  me: null,            // {username, role, status, privileges:[...]}
  privileges: new Set(),
  activeTab: null,
  conversations: [],
  activeConvId: null,
  filesKind: '',       // '' | 'file' | 'photo'
  chatAbort: null,     // AbortController for active stream
  voiceAvailable: false,   // server STT/TTS reachable + mic usable
  recorder: null,          // active MediaRecorder
  recStream: null,         // active getUserMedia stream
  recChunks: [],           // recorded audio blob parts
  recording: false,
  transcribing: false,
  nextMsgFromVoice: false,  // the message currently being sent came from the mic
  status: { chat: true, vision: true, voice: true, images: false, images_url: '' },
  statusPoll: null,
  imgSelected: new Set(),   // selected image filenames in the Images ribbon
  chatModels: [],           // chat-capable gateway models for the composer picker (admins)
  convModel: {},            // conv id -> chosen model alias ('' = house default); memory only
};

/* ===========================================================================
 * DOM helpers
 * ========================================================================= */
const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (k in node && k !== 'list') { try { node[k] = v; } catch { node.setAttribute(k, v); } }
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ===========================================================================
 * Toast + modal
 * ========================================================================= */
function toast(msg, kind = 'info', ms = 3200) {
  const root = $('#toast-root');
  const t = el('div', { class: `toast toast-${kind}` }, msg);
  root.append(t);
  requestAnimationFrame(() => t.classList.add('show'));
  setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 250); }, ms);
}

function openModal(node) {
  const body = $('#modal-body');
  body.innerHTML = '';
  body.append(node);
  $('#modal-root').hidden = false;
  document.body.classList.add('modal-open');
}
function closeModal() {
  $('#modal-root').hidden = true;
  $('#modal-body').innerHTML = '';
  document.body.classList.remove('modal-open');
}

/* ===========================================================================
 * fetch wrapper — always sends CSRF header on mutating requests + credentials
 * ========================================================================= */
async function api(path, { method = 'GET', body, raw = false, signal } = {}) {
  const opts = {
    method,
    credentials: 'same-origin',
    headers: {},
    signal,
  };
  const mutating = method !== 'GET' && method !== 'HEAD';
  if (mutating) opts.headers['X-Hub-CSRF'] = '1';

  if (body instanceof FormData) {
    opts.body = body; // browser sets multipart boundary
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }

  const res = await fetch(path, opts);
  if (raw) return res;

  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    try { data = await res.json(); } catch { data = null; }
  } else {
    try { data = await res.text(); } catch { data = null; }
  }

  if (!res.ok) {
    const err = (data && data.error) || {};
    const e = new Error(err.message || `Request failed (${res.status})`);
    e.status = res.status;
    e.code = err.code;
    throw e;
  }
  return data;
}

/* ===========================================================================
 * Privilege helpers
 * ========================================================================= */
const can = (priv) => State.privileges.has(priv);
const isAdmin = () => State.me && State.me.role === 'admin';

/* ===========================================================================
 * Boot
 * ========================================================================= */
window.addEventListener('DOMContentLoaded', init);

async function init() {
  initTheme();
  wireGlobalUI();
  setupPWA();
  try {
    const me = await api('/api/me');
    onMe(me);
  } catch (e) {
    if (e.status === 401) showGate();
    else { showGate(); }
  }
}

/* ---- PWA: register the service worker + offer "install as an app" ---- */
let _deferredInstall = null;

function setupPWA() {
  // Service workers only run in a secure context (HTTPS or localhost); over plain
  // http://<lan-ip> this is a no-op, which is expected until HTTPS is set up.
  if ('serviceWorker' in navigator && window.isSecureContext) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }
  const installed = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  if (installed) return;                                   // already installed — nothing to offer

  // Android / desktop Chrome: capture the native prompt, show our own button.
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); _deferredInstall = e; showInstallBanner('prompt'); });
  window.addEventListener('appinstalled', () => { _deferredInstall = null; hideInstallBanner(); });

  // iOS Safari: no prompt event — show the "Add to Home Screen" hint once.
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const iosSafari = isIOS && /safari/i.test(navigator.userAgent) && !/crios|fxios|edgios/i.test(navigator.userAgent);
  if (iosSafari && localStorage.getItem('hub-pwa-ios-dismissed') !== '1') setTimeout(() => showInstallBanner('ios'), 1500);
}

/* The install offer is now a tile in the launcher grid (see renderInstallTile)
 * rather than a pill floating over the page. Kept as the entry point so the
 * existing beforeinstallprompt / iOS call sites need no change. */
function showInstallBanner(kind) {
  renderInstallTile();
}

function showInstallBannerLegacy(kind) {
  if (document.getElementById('pwa-install')) return;
  const bar = el('div', { id: 'pwa-install', class: 'pwa-install' });
  if (kind === 'prompt') {
    bar.append(
      el('span', {}, '📲 Install Home Hub as an app'),
      el('span', { class: 'pwa-actions' },
        el('button', { type: 'button', class: 'btn btn-sm btn-primary', onclick: doInstall }, 'Install'),
        el('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: hideInstallBanner }, 'Later')));
  } else {
    bar.append(
      el('span', {}, '📲 Install: tap Share, then “Add to Home Screen”'),
      el('button', { type: 'button', class: 'btn btn-sm btn-ghost',
        onclick: () => { localStorage.setItem('hub-pwa-ios-dismissed', '1'); hideInstallBanner(); } }, 'Got it'));
  }
  document.body.appendChild(bar);
}
function hideInstallBanner() { const b = document.getElementById('pwa-install'); if (b) b.remove(); }
async function doInstall() {
  if (!_deferredInstall) return hideInstallBanner();
  _deferredInstall.prompt();
  try { await _deferredInstall.userChoice; } catch (e) { /* ignore */ }
  _deferredInstall = null; hideInstallBanner();
}

/* ---- theme: palette (Cubby/Greenhouse/Hearth) + appearance mode ---- */
const PALETTES = [
  { id: 'cubby',      name: 'Cubby',      sw: ['#3B5BD9', '#F2B632'] },
  { id: 'greenhouse', name: 'Greenhouse', sw: ['#356B57', '#E0A56B'] },
  { id: 'hearth',     name: 'Hearth',     sw: ['#C2673B', '#5C7A6B'] },
];
const getPalette = () => localStorage.getItem('hub-palette') || 'cubby';
const getMode = () => localStorage.getItem('hub-mode') || 'system';
const prefersDark = () => !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
const effectiveMode = (m) => (m === 'system' ? (prefersDark() ? 'dark' : 'light') : m);

function applyTheme() {
  const root = document.documentElement;
  root.dataset.palette = getPalette();
  root.dataset.theme = effectiveMode(getMode());
}

function initTheme() {
  applyTheme();
  // Follow the OS when appearance is set to "System".
  try {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      if (getMode() === 'system') applyTheme();
    });
  } catch (e) { /* older Safari: no addEventListener on MediaQueryList */ }
  $('#settings-btn')?.addEventListener('click', openSettings);
}

function openSettings() {
  const palRow = el('div', { class: 'settings-palettes' },
    ...PALETTES.map((p) => {
      const card = el('button',
        { type: 'button', class: `palette-card${p.id === getPalette() ? ' active' : ''}`, dataset: { pal: p.id } },
        el('span', { class: 'palette-sw' }, ...p.sw.map((c) => el('i', { style: `background:${c}` }))),
        el('span', { class: 'palette-name' }, p.name));
      card.addEventListener('click', () => {
        localStorage.setItem('hub-palette', p.id); applyTheme();
        $$('.palette-card', palRow).forEach((b) => b.classList.toggle('active', b.dataset.pal === p.id));
      });
      return card;
    }),
  );

  const modeRow = el('div', { class: 'seg' },
    ...[['light', 'Light'], ['dark', 'Dark'], ['system', 'System']].map(([id, label]) => {
      const b = el('button', { type: 'button', class: `seg-btn${id === getMode() ? ' active' : ''}`, dataset: { mode: id } }, label);
      b.addEventListener('click', () => {
        localStorage.setItem('hub-mode', id); applyTheme();
        $$('.seg-btn', modeRow).forEach((x) => x.classList.toggle('active', x.dataset.mode === id));
      });
      return b;
    }),
  );

  openModal(el('div', { class: 'settings-dialog' },
    el('h3', {}, 'Settings'),
    el('label', { class: 'field-label' }, 'Theme'),
    palRow,
    el('label', { class: 'field-label' }, 'Appearance'),
    modeRow,
    settingsInstallSection(),
    settingsAdminSection(),
    el('div', { class: 'modal-actions' },
      // Log out lives here now instead of taking a permanent slot in the top bar.
      // Delegating to the existing button reuses its exact logic.
      el('button', { class: 'btn btn-ghost', onclick: () => { closeModal(); $('#logout-btn').click(); } }, 'Log out'),
      el('button', { class: 'btn btn-primary', onclick: closeModal }, 'Done')),
  ));
}

/* "Install as an app" in Settings — discoverable for everyone, so install
 * doesn't depend on catching the one-time auto banner. Fires the native prompt
 * when available; otherwise gives the right per-platform instructions. */
function settingsInstallSection() {
  const installed = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (installed) return null;
  const wrap = el('div', { class: 'settings-install' },
    el('hr', { class: 'settings-sep' }),
    el('label', { class: 'field-label' }, 'Install as an app'));
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);

  if (_deferredInstall) {
    wrap.append(
      el('p', { class: 'muted', style: 'margin:0 0 .4rem' }, 'Add Home Hub to your home screen.'),
      el('button', { class: 'btn btn-primary btn-sm', onclick: doInstall }, 'Install app'));
  } else if (isIOS) {
    wrap.append(el('p', { class: 'muted', style: 'margin:0' },
      'On iPhone/iPad, in Safari: tap the Share button, then "Add to Home Screen".'));
  } else if (!window.isSecureContext) {
    wrap.append(el('p', { class: 'muted', style: 'margin:0' },
      'To install on Android/desktop, open the hub over https:// (a secure '
      + 'connection is required). Ask the admin to run: sudo ./homehub.sh https'));
  } else {
    // HTTPS but no install prompt -> the browser doesn't trust the local cert.
    wrap.append(
      el('p', { class: 'muted', style: 'margin:0 0 .4rem' },
        'Almost there — your browser needs to trust this hub’s certificate '
        + 'before it can install. Download it, then install it as a trusted '
        + 'CA in your device settings, and reopen this page.'),
      el('a', { class: 'btn btn-ghost btn-sm', href: '/static/homehub-ca.crt',
        download: 'homehub-ca.crt' }, 'Download hub certificate'));
  }
  return wrap;
}

/* Admin PIN in Settings: non-admins unlock admin from this device with the PIN;
 * admins set/change the PIN. Lets any user act as admin from any device / PWA. */
function settingsAdminSection() {
  const wrap = el('div', { class: 'settings-admin' }, el('hr', { class: 'settings-sep' }));
  const msg = el('p', { class: 'form-error', hidden: true });

  if (isAdmin()) {
    const input = el('input', { class: 'field', type: 'password', inputmode: 'numeric',
      autocomplete: 'off', placeholder: 'New admin PIN (4–32 digits)' });
    const status = el('p', { class: 'muted', style: 'margin:.3rem 0 0' }, 'Admin PIN: …');
    api('/api/admin/pin')
      .then((r) => { status.textContent = r.set ? 'Admin PIN: set' : 'Admin PIN: not set yet'; })
      .catch(() => { status.textContent = ''; });
    const save = el('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
      msg.hidden = true;
      try {
        await api('/api/admin/pin', { method: 'PUT', body: { pin: input.value.trim() } });
        toast('Admin PIN updated', 'success');
        input.value = ''; status.textContent = 'Admin PIN: set';
      } catch (e) { msg.textContent = e.message; msg.hidden = false; }
    } }, 'Save PIN');
    wrap.append(
      el('label', { class: 'field-label' }, 'Admin PIN'),
      el('p', { class: 'muted', style: 'margin:0 0 .4rem' },
        'Anyone can become admin from any device by entering this PIN.'),
      input, save, msg, status);
  } else {
    const input = el('input', { class: 'field', type: 'password', inputmode: 'numeric',
      autocomplete: 'off', placeholder: 'Admin PIN' });
    const unlock = el('button', { class: 'btn btn-primary btn-sm', onclick: async () => {
      msg.hidden = true;
      try {
        const me = await api('/api/session/elevate', { method: 'POST', body: { pin: input.value.trim() } });
        toast('You are now admin on this device', 'success');
        closeModal();
        onMe(me);
      } catch (e) { msg.textContent = e.message; msg.hidden = false; }
    } }, 'Unlock admin');
    wrap.append(
      el('label', { class: 'field-label' }, 'Admin access'),
      el('p', { class: 'muted', style: 'margin:0 0 .4rem' },
        'Enter the admin PIN to manage the hub from this device.'),
      input, unlock, msg);
  }
  return wrap;
}

/* ---- global UI wiring (gate, tabs, modal, logout) ---- */
function wireGlobalUI() {
  // Gate: register
  $('#gate-register-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#gate-username').value.trim();
    if (!username) return;
    await gateAction(() => api('/api/session/register', { method: 'POST', body: { username } }));
  });

  // Gate: first-run setup — create the first admin (no token needed)
  $('#gate-setup-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#gate-setup-username').value.trim();
    const code = ($('#gate-setup-code')?.value || '').trim();
    if (!username) return;
    const err = $('#gate-error'); err.hidden = true;
    try {
      const me = await api('/api/session/setup', { method: 'POST', body: { username, code } });
      onMe(me);
      toast('You’re the admin. Set an admin PIN in Settings so others can join from any device.', 'success');
    } catch (ex) { err.textContent = ex.message || 'Setup failed.'; err.hidden = false; }
  });

  // Gate: toggle admin form
  $('#gate-show-admin').addEventListener('click', () => {
    $('#gate-register-form').hidden = true;
    $('#gate-show-admin').hidden = true;
    $('#gate-admin-form').hidden = false;
    $('#gate-admin-username').value = $('#gate-username').value.trim();
  });
  $('#gate-hide-admin').addEventListener('click', () => {
    $('#gate-admin-form').hidden = true;
    $('#gate-register-form').hidden = false;
    $('#gate-show-admin').hidden = false;
    $('#gate-error').hidden = true;
  });

  // Gate: "Find your Home Hub" connection panel (sits above the username step)
  wireGateConnect();

  // Gate: admin — PIN elevate (primary) or admin token (first-time setup)
  $('#gate-admin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#gate-admin-username').value.trim();
    const pin = ($('#gate-admin-pin')?.value || '').trim();
    const admin_token = ($('#gate-admin-token')?.value || '').trim();
    if (!username) return;
    if (pin) {
      await gateAction(() => api('/api/session/elevate', { method: 'POST', body: { username, pin } }));
    } else if (admin_token) {
      await gateAction(() => api('/api/session/claim', { method: 'POST', body: { username, admin_token } }));
    }
  });

  // Tabs
  $('#tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (btn && !btn.disabled) selectTab(btn.dataset.tab);
  });

  // Launcher: brand mark and the back arrow both return home; tiles open a feature.
  $('#brand-home')?.addEventListener('click', goHome);
  $('#nav-back')?.addEventListener('click', goHome);
  $('#lc-groups')?.addEventListener('click', (e) => {
    const t = e.target.closest('[data-lc-tab]');
    if (t) selectTab(t.dataset.lcTab);
  });

  // Chat header chips open the bottom sheets; anything conclusive closes them.
  $('#chat-model-chip')?.addEventListener('click', () => openSheet('model'));
  $('#chat-history-chip')?.addEventListener('click', () => openSheet('history'));
  $('#sheet-scrim')?.addEventListener('click', closeSheets);
  $('#conversation-list')?.addEventListener('click', closeSheets);
  $('#chat-model')?.addEventListener('change', () => { syncChatChips(); closeSheets(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheets(); });

  // Logout
  $('#logout-btn').addEventListener('click', async () => {
    try { await api('/api/session/logout', { method: 'POST' }); } catch {}
    State.me = null; State.privileges = new Set();
    location.reload();
  });

  // Modal close
  $$('[data-modal-close]').forEach((b) => b.addEventListener('click', closeModal));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  // Feature wiring (handlers are no-ops if their panel never shows)
  wireChat();
  wireVoice();
  wireNotes();
  wireChecklists();
  wireCalendar();
  wireFiles();
  wireKeys();
  wireAdmin();
  wireModels();
  wireStudio();
}

async function gateAction(fn) {
  const err = $('#gate-error');
  err.hidden = true;
  try {
    const me = await fn();
    onMe(me);
  } catch (e) {
    err.textContent = e.message || 'Something went wrong.';
    err.hidden = false;
  }
}

function showGate() {
  $('#gate').hidden = false;
  $('#app').hidden = true;
  // Kick off the origin probe so the "Find your Home Hub" panel reflects
  // whether *this* origin is actually a hub. Non-blocking; never throws.
  probeCurrentOrigin();
}

/* ===========================================================================
 * Gate: "Find your Home Hub"
 * ---------------------------------------------------------------------------
 * A small connection panel that sits ABOVE the username step. It:
 *   (a) probes the CURRENT origin's /api/discovery and, if it's a homehub,
 *       shows "Connected to <name>" and lets the normal register/claim flow run;
 *   (b) offers "Find Home Hub" to probe a few well-known LAN candidates;
 *   (c) accepts a manually typed address and, on success, NAVIGATES the browser
 *       to that hub's base_url (no cross-origin authenticated calls).
 * All probes are CORS fetches with a short timeout and fail gracefully.
 * ========================================================================= */
const DISCOVERY_TIMEOUT_MS = 2500;

/** Fetch <addr>/api/discovery with a timeout; resolve the hub info or null. */
async function probeDiscovery(baseUrl, { credentials } = {}) {
  let url;
  try {
    const u = new URL(baseUrl);
    u.pathname = (u.pathname.replace(/\/+$/, '')) + '/api/discovery';
    u.search = ''; u.hash = '';
    url = u.toString();
  } catch { return null; }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), DISCOVERY_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      method: 'GET',
      mode: 'cors',
      // Same-origin probes may include the session cookie; cross-origin must not.
      credentials: credentials || 'omit',
      cache: 'no-store',
      signal: ctrl.signal,
    });
    if (!res.ok) return null;
    const data = await res.json().catch(() => null);
    if (data && data.service === 'homehub') {
      return {
        name: typeof data.name === 'string' && data.name.trim() ? data.name.trim() : 'Home Hub',
        version: data.version,
        base_url: typeof data.base_url === 'string' ? data.base_url : baseUrl,
        setup_required: !!data.setup_required,
        setup_code_required: !!data.setup_code_required,
        origin: new URL(url).origin,
      };
    }
    return null;
  } catch {
    return null; // timeout, network error, CORS block, bad JSON — all non-fatal
  } finally {
    clearTimeout(timer);
  }
}

/** Normalize user input (host | host:port | full URL) to an http(s) origin. */
function normalizeHubAddress(raw) {
  let s = String(raw || '').trim();
  if (!s) return null;
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s;
  try {
    const u = new URL(s);
    if (!u.hostname) return null;
    // If no explicit port, fall back to the current page's port (e.g. :8090),
    // which matches how the hub is typically served on the LAN.
    if (!u.port && !/^https:/i.test(u.protocol) && location.port) u.port = location.port;
    return u.origin;
  } catch {
    return null;
  }
}

/** Candidate LAN addresses to scan for a hub. */
function discoveryCandidates() {
  const port = location.port || '';
  const withPort = (host) => port ? `http://${host}:${port}` : `http://${host}`;
  const list = [
    withPort('homehub.local'),
    'http://llm.home',
    withPort('llm.local'),
  ];
  // Try the last hub this browser/PWA connected to first (auto-reconnect).
  let remembered = null;
  try { remembered = localStorage.getItem('hub-origin'); } catch (e) { /* ignore */ }
  if (remembered && remembered !== location.origin && !list.includes(remembered)) {
    list.unshift(remembered);
  }
  return list;
}

function connectMsg(text, kind) {
  const node = $('#gate-connect-msg');
  if (!node) return;
  if (!text) { node.hidden = true; node.textContent = ''; node.className = 'connect-msg'; return; }
  node.textContent = text;
  node.className = 'connect-msg' + (kind ? ` is-${kind}` : '');
  node.hidden = false;
}

function setConnectBusy(busy) {
  const panel = $('#gate-connect');
  if (panel) panel.classList.toggle('is-busy', !!busy);
}

function showConnected(name) {
  $('#gate-connected').hidden = false;
  $('#gate-finder').hidden = true;
  $('#gate-hub-name').textContent = name || 'this hub';
}

function showFinder(lead) {
  $('#gate-connected').hidden = true;
  $('#gate-finder').hidden = false;
  if (lead) $('#gate-finder-lead').textContent = lead;
}

let _originProbed = false;
async function probeCurrentOrigin() {
  // Default to the finder so the panel is never blank while we probe.
  if (!_originProbed) showFinder();
  const hub = await probeDiscovery(location.origin, { credentials: 'same-origin' });
  _originProbed = true;
  if (hub) {
    showConnected(hub.name);
    showSetupMode(!!hub.setup_required, hub);
  } else {
    // This origin isn't (yet) answering as a hub — keep the finder visible.
    showFinder("We couldn't confirm a Home Hub at this address. Find yours below.");
  }
}

/* First-run: when the hub has no admin yet, show the setup form (become admin)
 * instead of the normal register/admin choices. Ask for the installer's code
 * only if the hub requires one. */
function showSetupMode(on, hub) {
  const setup = $('#gate-setup-form'), reg = $('#gate-register-form'),
        showAdmin = $('#gate-show-admin'), adminForm = $('#gate-admin-form');
  if (on) {
    if (reg) reg.hidden = true;
    if (showAdmin) showAdmin.hidden = true;
    if (adminForm) adminForm.hidden = true;
    if (setup) setup.hidden = false;
    const codeRow = $('#gate-setup-code-row');
    if (codeRow) codeRow.hidden = !(hub && hub.setup_code_required);
  } else if (setup && !setup.hidden) {
    // Only revert if we had switched into setup mode (avoid clobbering the
    // normal gate on a plain re-probe).
    setup.hidden = true;
    if (reg) reg.hidden = false;
    if (showAdmin) showAdmin.hidden = false;
  }
}

function renderHubList(hubs) {
  const ul = $('#gate-hub-list');
  if (!ul) return;
  ul.innerHTML = '';
  if (!hubs.length) { ul.hidden = true; return; }
  for (const hub of hubs) {
    const li = el('li', {},
      el('button', {
        type: 'button',
        class: 'hub-item',
        onclick: () => { window.location = hub.base_url || hub.origin; },
      },
        el('span', { class: 'hub-icon', html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-9"/><path d="M9.5 20v-5h5v5"/></svg>' }),
        el('span', { class: 'hub-meta' },
          el('span', { class: 'hub-name' }, hub.name),
          el('span', { class: 'hub-addr' }, hub.origin),
        ),
        el('span', { class: 'hub-go', html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>' }),
      ),
    );
    ul.append(li);
  }
  ul.hidden = false;
}

async function findHubs() {
  setConnectBusy(true);
  connectMsg('Scanning your network for a Home Hub…', 'busy');
  renderHubList([]);
  try {
    const candidates = discoveryCandidates();
    const results = await Promise.all(candidates.map((addr) => probeDiscovery(addr)));
    // De-dupe by origin; keep the first hit for each.
    const seen = new Set();
    const hubs = [];
    for (const hub of results) {
      if (hub && !seen.has(hub.origin)) { seen.add(hub.origin); hubs.push(hub); }
    }
    renderHubList(hubs);
    if (hubs.length) {
      connectMsg(`Found ${hubs.length} Home Hub${hubs.length === 1 ? '' : 's'}. Tap one to open it.`, null);
    } else {
      connectMsg("No Home Hub found nearby. Make sure you're on your home WiFi, or enter the address below.", null);
    }
  } finally {
    setConnectBusy(false);
  }
}

async function connectManual(raw) {
  const origin = normalizeHubAddress(raw);
  if (!origin) { connectMsg("That doesn't look like a valid address. Try e.g. homehub.local:8090", 'error'); return; }
  setConnectBusy(true);
  connectMsg(`Checking ${origin}…`, 'busy');
  const hub = await probeDiscovery(origin);
  setConnectBusy(false);
  if (hub) {
    // Found a hub at a different origin — hand the browser over to it so the
    // user authenticates ON that origin (no cross-origin authed API calls).
    connectMsg(`Opening ${hub.name}…`, 'busy');
    window.location = hub.base_url || origin;
  } else {
    connectMsg("Couldn't reach a Home Hub at that address. Double-check it and try again.", 'error');
  }
}

function wireGateConnect() {
  const findBtn = $('#gate-find-btn');
  if (findBtn) findBtn.addEventListener('click', findHubs);

  const manualForm = $('#gate-manual-form');
  if (manualForm) manualForm.addEventListener('submit', (e) => {
    e.preventDefault();
    connectManual($('#gate-manual-input').value);
  });
}

/* ===========================================================================
 * Apply /api/me -> render tabs/privileges
 * ========================================================================= */
function onMe(me) {
  State.me = me;
  State.privileges = new Set(me.privileges || []);
  // Remember this hub so the PWA / a bookmark can auto-reconnect to it later.
  try { localStorage.setItem('hub-origin', location.origin); } catch (e) { /* ignore */ }

  $('#gate').hidden = true;
  $('#app').hidden = false;

  // user badge — full text on desktop; CSS shows just the initial on phones
  $('#me-badge').textContent = `${me.username} · ${me.role}`;
  $('#me-badge').dataset.role = me.role;
  $('#me-badge').dataset.initial = (me.username || '?').trim().charAt(0).toUpperCase();
  $('#me-badge').title = `${me.username} · ${me.role}`;

  // status banner (pending / revoked)
  const banner = $('#status-banner');
  if (me.status !== 'approved') {
    banner.hidden = false;
    banner.className = 'banner banner-warn';
    banner.textContent = me.status === 'pending'
      ? 'This device is pending admin approval. You have limited access until approved.'
      : `This device status is "${me.status}". Ask an admin to approve it.`;
  } else {
    banner.hidden = true;
  }

  renderTabs();

  // Land on the launcher (home screen) — it lists every feature this device may
  // use, so it works even for a pending guest with a single privilege.
  const anyFeature = $$('#tabs .tab').some((t) => !t.hidden);
  if (anyFeature) selectTab('launcher');
  else {
    // No privileges yet (pending guest with nothing) — show banner only
    $$('.panel').forEach((p) => (p.hidden = true));
  }

  // Start capability polling (chat/voice/images online?).
  checkStatus();
  if (State.statusPoll) clearInterval(State.statusPoll);
  State.statusPoll = setInterval(checkStatus, 20000);
}

function tabAllowed(tab) {
  // The launcher is the home screen — always reachable for a registered device.
  if (tab === 'launcher') return true;
  const btn = $(`#tabs .tab[data-tab="${tab}"]`);
  if (!btn) return false;
  if (btn.dataset.role === 'admin') return isAdmin();
  // Image Studio is open to any approved device (it's a separate local service).
  if (tab === 'images') return !!(State.me && State.me.status === 'approved');
  // Studio (art/animation pipeline) is a creator tool -> needs files_write.
  if (tab === 'studio') return can('files_write');
  // files tab needs files_read OR photos_read
  if (tab === 'files') return can('files_read') || can('photos_read');
  // Calendar reads are open to any approved device; its data-priv
  // ('checklists' — household content shares one grant) only gates writes.
  if (tab === 'calendar') return !!(State.me && State.me.status === 'approved');
  return can(btn.dataset.priv);
}

/* ===========================================================================
 * Capability status (which local services are online)
 * ========================================================================= */
async function checkStatus() {
  try {
    const s = await api('/api/status');
    State.status = { ...State.status, ...s };
  } catch (e) {
    State.status = { ...State.status, chat: false, vision: false, voice: false };
  }
  applyStatusUI();
}

function applyStatusUI() {
  const st = State.status;
  // Keep the launcher's status line live while it's on screen.
  if (State.activeTab === 'launcher') renderLauncherStatus();
  // Offline banner (only when the device is approved; pending has its own banner).
  const banner = $('#offline-banner');
  if (banner && State.me && State.me.status === 'approved') {
    if (!st.chat) {
      banner.hidden = false;
      banner.textContent = '🔌 Local AI is offline — the language models are stopped'
        + (st.images ? ' (the Image Studio is running instead).' : ' to free memory.')
        + ' Chat, voice and photo search are paused.';
    } else {
      banner.hidden = true;
    }
  }
  // Chat composer: disable when chat is offline.
  const ci = $('#chat-input'), cs = $('#chat-send');
  if (ci) {
    if (!st.chat) {
      ci.disabled = true;
      ci.placeholder = 'Chat is offline — the local model is stopped.';
    } else if (ci.placeholder && ci.placeholder.startsWith('Chat is offline')) {
      ci.disabled = false;
      ci.placeholder = 'Message the model… or tap the mic';
    }
  }
  if (cs && !st.chat) cs.disabled = true;
  else if (cs && st.chat) cs.disabled = false;
  // Re-render the Images panel ONLY when availability actually flips — never on
  // every status poll (that wiped #images-body under an in-progress drag, which
  // looked like a mid-drag page refresh) and never while a drag is happening.
  if (State.activeTab === 'images' && !DRAGGING && _imagesShownAvail !== st.images) {
    renderImages();
  }
}

function renderTabs() {
  $$('#tabs .tab').forEach((btn) => {
    const tab = btn.dataset.tab;
    btn.hidden = !tabAllowed(tab);
  });
}

/* ===========================================================================
 * LAUNCHER — the home screen.
 * Tiles are DERIVED from the tab rail: same buttons, same icons, same privilege
 * gating (tabAllowed). A feature therefore cannot exist in one and be missing
 * from the other, and any tab not explicitly grouped still gets a tile under
 * "More" — so nothing can become unreachable.
 * ========================================================================= */
const LC_GROUPS = [
  { label: null,          tabs: ['chat', 'calendar', 'home', 'notes', 'checklists'] },
  { label: 'Create',      tabs: ['images', 'studio'] },
  { label: 'Household',   tabs: ['files', 'keys'] },
  { label: 'Admin only',  tabs: ['admin', 'models'], small: true },
];

const LC_HINTS = {
  chat: 'Ask anything',
  calendar: 'Events & chores',
  home: 'Lights, locks, sensors',
  notes: 'Shared noticeboard',
  checklists: 'Lists for the house',
  images: 'Create pictures',
  studio: 'Art & animation',
  files: 'Upload, browse, search',
  keys: 'Keys for other apps',
  admin: 'People & devices',
  models: 'Models & metrics',
};

/** Label + icon lifted from the matching tab button (single source of truth). */
function lcTabMeta(tab) {
  const btn = $(`#tabs .tab[data-tab="${tab}"]`);
  if (!btn) return null;
  const span = btn.querySelector('span');
  const svg = btn.querySelector('svg');
  return {
    label: (span ? span.textContent : tab).trim(),
    icon: svg ? svg.cloneNode(true) : null,
  };
}

function lcHint(tab) {
  // Live counts only where the data is already in memory — otherwise a plain
  // description, which is always accurate and costs no extra request.
  if (tab === 'chat' && State.conversations.length) {
    const n = State.conversations.length;
    return `${n} chat${n === 1 ? '' : 's'}`;
  }
  return LC_HINTS[tab] || '';
}

function lcTile(tab, opts = {}) {
  const meta = lcTabMeta(tab);
  if (!meta) return null;
  const tile = el('button', {
    type: 'button',
    class: 'lc-tile' + (opts.wide ? ' wide' : '') + (opts.small ? ' small' : ''),
    dataset: { lcTab: tab },
  });
  if (meta.icon) tile.append(el('span', { class: 'ic' }, meta.icon));
  if (opts.wide) {
    tile.append(el('span', { class: 'lc-txt' },
      el('span', { class: 'lc-name' }, meta.label),
      el('span', { class: 'lc-hint' }, lcHint(tab))));
  } else {
    tile.append(el('span', { class: 'lc-name' }, meta.label));
    if (!opts.small) tile.append(el('span', { class: 'lc-hint' }, lcHint(tab)));
  }
  return tile;
}

function renderLauncher() {
  if (!State.me) return;
  const h = new Date().getHours();
  const part = h < 12 ? 'Good morning' : (h < 18 ? 'Good afternoon' : 'Good evening');
  $('#lc-greet').textContent = `${part}, ${State.me.username}`;
  renderLauncherStatus();

  const host = $('#lc-groups');
  host.replaceChildren();
  const placed = new Set();

  for (const g of LC_GROUPS) {
    const tiles = [];
    g.tabs.forEach((tab, i) => {
      if (!tabAllowed(tab)) return;
      // First everyday tile spans the row — it's the primary thing you came for.
      const t = lcTile(tab, { wide: g.label === null && i === 0, small: g.small });
      if (t) { tiles.push(t); placed.add(tab); }
    });
    if (!tiles.length) continue;
    if (g.label) host.append(el('p', { class: 'lc-grouplbl' }, g.label));
    host.append(el('div', { class: 'lc-grid' + (g.small ? ' three' : '') }, ...tiles));
  }

  // Safety net: an ungrouped (e.g. newly added) tab still gets a tile.
  const spare = $$('#tabs .tab')
    .map((b) => b.dataset.tab)
    .filter((t) => t && !placed.has(t) && tabAllowed(t));
  if (spare.length) {
    host.append(el('p', { class: 'lc-grouplbl' }, 'More'));
    host.append(el('div', { class: 'lc-grid' }, ...spare.map((t) => lcTile(t)).filter(Boolean)));
  }

  renderInstallTile();
}

function renderLauncherStatus() {
  const node = $('#lc-status');
  if (!node) return;
  const st = State.status || {};
  const bits = [st.chat ? 'AI ready' : 'AI offline'];
  if (st.images) bits.push('Image Studio on');
  if (st.voice) bits.push('Voice ready');
  node.replaceChildren(
    el('span', { class: 'lc-dot' + (st.chat ? '' : ' off') }),
    el('span', {}, bits.join(' · ')));
}

/** Install offer as a calm tile in the launcher grid (replaces the floating pill). */
function renderInstallTile() {
  const slot = $('#lc-install-slot');
  if (!slot) return;
  slot.replaceChildren();
  const installed = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (installed) return;
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (!_deferredInstall && !isIOS) return;   // nothing actionable to offer yet
  slot.append(el('button', {
    type: 'button', class: 'lc-tile wide lc-install',
    onclick: () => { if (_deferredInstall) doInstall(); else openSettings(); },
  },
    el('span', { class: 'ic', html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>' }),
    el('span', { class: 'lc-txt' },
      el('span', { class: 'lc-name' }, 'Add to your home screen'),
      el('span', { class: 'lc-hint' }, _deferredInstall
        ? 'Opens like an app, works offline'
        : 'Tap Share, then “Add to Home Screen”'))));
}

/* ---- navigation helpers ---- */
function goHome() { selectTab('launcher'); }

function updateNavBack() {
  const back = $('#nav-back');
  if (back) back.hidden = !(State.activeTab && State.activeTab !== 'launcher');
}

/* ---- bottom sheets (phone): chat history + model picker ----
 * These re-present the EXISTING #conversation-list sidebar and #chat-model-bar
 * via CSS. No DOM is moved and no ids change, so every existing handler — and
 * the admin-only `hidden` flag on the model bar — keeps working untouched. */
function openSheet(kind) {
  document.body.classList.remove('sheet-history', 'sheet-model');
  document.body.classList.add(kind === 'model' ? 'sheet-model' : 'sheet-history');
  // Anchor the panel directly under the header, so it reads as having opened
  // from the chip you tapped rather than appearing at the far end of the page.
  const bar = document.querySelector('.topbar');
  const top = bar ? Math.round(bar.getBoundingClientRect().bottom) : 58;
  document.documentElement.style.setProperty('--sheet-top', top + 'px');
  const scrim = $('#sheet-scrim');
  if (scrim) scrim.hidden = false;
}

function closeSheets() {
  document.body.classList.remove('sheet-history', 'sheet-model');
  const scrim = $('#sheet-scrim');
  if (scrim) scrim.hidden = true;
}

/** Composer: one line at rest, grows with the message up to 4 lines, then
 *  scrolls internally. Called on input and again after a send clears it. */
const COMPOSER_MAX_LINES = 4;
function autoGrowComposer() {
  const ta = $('#chat-input');
  if (!ta) return;
  const cs = getComputedStyle(ta);
  const line = parseFloat(cs.lineHeight) || 20;
  const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
  const max = Math.round(line * COMPOSER_MAX_LINES + pad);
  ta.style.height = 'auto';                       // measure the natural height
  const next = Math.min(ta.scrollHeight, max);
  ta.style.height = next + 'px';
  ta.style.overflowY = ta.scrollHeight > max ? 'auto' : 'hidden';
}

/** Keep the header chips in step with the model select and the chat list. */
function syncChatChips() {
  const bar = $('#chat-model-bar');
  const sel = $('#chat-model');
  const chip = $('#chat-model-chip');
  if (chip && bar) {
    // Mirror the admin-only gating of the model bar — never widen access.
    chip.hidden = bar.hidden;
    const txt = $('#chat-model-chip-txt');
    if (txt && sel) {
      const opt = sel.options[sel.selectedIndex];
      txt.textContent = (opt && opt.textContent.trim()) || 'Default model';
    }
  }
  const count = $('#chat-history-count');
  if (count) count.textContent = String(State.conversations.length || 0);
}

function selectTab(tab) {
  if (!tabAllowed(tab)) return;
  State.activeTab = tab;
  if (tab !== 'models') stopModelsPoll();
  if (tab !== 'images') stopGenPoll();
  $$('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.panel').forEach((p) => (p.hidden = p.dataset.tab !== tab));
  closeSheets();                 // never leave a sheet open across a tab change
  updateNavBack();               // show "back" only when away from the launcher

  switch (tab) {
    case 'launcher':   renderLauncher(); break;
    case 'chat':       loadConversations(); break;
    case 'notes':      loadNotes(); break;
    case 'checklists': loadChecklists(); break;
    case 'calendar':   loadCalendar(); break;
    case 'home':       loadHome(); break;
    case 'files':      loadFiles(); break;
    case 'keys':       loadKeys(); break;
    case 'admin':      loadDevices(); break;
    case 'models':     loadModels(); break;
    case 'images':     loadImages(); break;
    case 'studio':     loadStudio(); break;
  }
}

/* ===========================================================================
 * IMAGES — link/embed the local Image Studio (FastSD CPU / OpenVINO)
 * ========================================================================= */
let GEN_POLL = null;
let _genSig = '';
let DRAG_FILES = [];
let DRAGGING = false;          // true while a ribbon image is being dragged
let _imagesShownAvail = null;  // last images-availability the panel was built for
function stopGenPoll() { if (GEN_POLL) { clearInterval(GEN_POLL); GEN_POLL = null; } }

// A drop that misses a segment (lands on a gap, the ribbon, the page, the iframe)
// must never trigger the browser's default "open the dragged image" — that looked
// like a page refresh. Accept drags anywhere and swallow stray drops; the real drop
// zones (segments, file upload) run their own handler before this bubbles to window.
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => e.preventDefault());
window.addEventListener('resize', () => { if (State.activeTab === 'images') updateRibbonNav(); });

const IMG_SEGMENTS = [
  { op: 'img2img', label: 'Image → Image', hint: 'opens the Studio below' },
  { op: 'rembg', label: 'Remove background', hint: 'transparent cut-out' },
  { op: 'to_studio', label: 'Send to Studio', hint: 'into the art pipeline' },
  { op: 'upscale', label: 'Upscale 2×', hint: 'Real-ESRGAN (slow)' },
];

function loadImages() {
  checkStatus();
  renderImages();
  stopGenPoll();
  GEN_POLL = setInterval(() => {
    if (State.activeTab !== 'images') { stopGenPoll(); return; }
    if (State.status.images) { loadGeneratedImages(); loadInflight(); }
  }, 6000);
}

function renderImages() {
  const body = $('#images-body');
  if (!body) return;
  body.innerHTML = '';
  const st = State.status;
  _imagesShownAvail = st.images;
  if (!st.images) {
    stopGenPoll();
    body.append(el('div', { class: 'empty-hint' },
      el('p', {}, 'The Image Studio is offline.'),
      el('p', { class: 'muted' }, 'It runs when the language models are stopped (to free memory). Ask an admin to start it.')));
    return;
  }
  State.imgSelected.clear();
  _genSig = '';
  const segRow = el('div', { class: 'img-segments' });
  IMG_SEGMENTS.forEach((s) => segRow.append(segmentZone(s)));
  body.append(
    el('p', { class: 'muted' }, 'Generate below; results land in the ribbon. Tap ribbon images to select (multi-select works), then click an action under the ribbon — or drag onto a segment above. Outputs come back into the ribbon.'),
    el('div', { class: 'images-actions' },
      el('a', { class: 'btn btn-primary', href: st.images_url || '#', target: '_blank', rel: 'noopener' }, 'Open Image Studio ↗'),
      el('button', { id: 'gen-refresh', class: 'btn btn-ghost btn-sm', onclick: () => { _genSig = ''; loadGeneratedImages(); } }, 'Refresh'),
      el('button', { class: 'btn btn-ghost btn-sm', title: 'Restart the Studio to unload any model and free RAM', onclick: freeImageMemory }, 'Free image memory'),
      el('span', { id: 'img-processing', class: 'img-processing muted', hidden: true }),
    ),
    el('div', { class: 'gen-gallery-head' }, el('strong', {}, 'Process — drag images onto a segment')),
    segRow,
    el('div', { class: 'gen-gallery-head' }, el('strong', {}, 'Images ribbon')),
    ribbonWrap(),
    ribbonActions(),
    el('div', { id: 'img-stage', class: 'img-stage', hidden: true }),
    el('div', { class: 'images-frame-wrap' },
      el('iframe', { class: 'images-frame', src: st.images_url || '', title: 'Image Studio', loading: 'lazy' })),
    el('p', { class: 'muted images-note' }, 'CPU is slow — few-step Turbo at 512px. Results always show in the ribbon even if the frame’s own gallery doesn’t update.'),
  );
  loadGeneratedImages();
  loadInflight();
}

// Horizontal ribbon with a ‹ › pager (scrollbar hidden). Edge images peek; the
// pager pushes the current images out and brings the next ones in.
function ribbonWrap() {
  return el('div', { class: 'ribbon-wrap' },
    el('button', { type: 'button', class: 'ribbon-nav prev', 'aria-label': 'Previous images',
      onclick: () => ribbonScroll(-1) }, '‹'),
    el('div', { id: 'generated-gallery', class: 'gen-ribbon', onscroll: updateRibbonNav }),
    el('button', { type: 'button', class: 'ribbon-nav next', 'aria-label': 'Next images',
      onclick: () => ribbonScroll(1) }, '›'));
}

function ribbonScroll(dir) {
  const g = $('#generated-gallery');
  if (g) g.scrollBy({ left: dir * Math.max(160, Math.round(g.clientWidth * 0.85)), behavior: 'smooth' });
}

// Enable/disable the pager arrows and hide them when nothing overflows.
function updateRibbonNav() {
  const g = $('#generated-gallery');
  if (!g) return;
  const wrap = g.closest('.ribbon-wrap');
  if (!wrap) return;
  const scrollable = g.scrollWidth > g.clientWidth + 4;
  wrap.classList.toggle('no-scroll', !scrollable);
  const prev = wrap.querySelector('.ribbon-nav.prev');
  const next = wrap.querySelector('.ribbon-nav.next');
  if (prev) prev.disabled = g.scrollLeft <= 2;
  if (next) next.disabled = g.scrollLeft >= g.scrollWidth - g.clientWidth - 2;
}

// Clicking a half-clipped edge image rolls it to centre (reveal); clicking a
// fully-visible one selects it.
function onRibbonItemClick(file, item) {
  const g = $('#generated-gallery');
  if (g) {
    const gr = g.getBoundingClientRect(), ir = item.getBoundingClientRect();
    if (ir.left < gr.left - 1 || ir.right > gr.right + 1) {
      g.scrollBy({ left: (ir.left + ir.right) / 2 - (gr.left + gr.right) / 2, behavior: 'smooth' });
      return;
    }
  }
  toggleImgSelect(file, item);
}

// Click-based path (reliable fallback to drag/drop): tap images in the ribbon to
// select, then click one of these to run that op on the whole selection.
function ribbonActions() {
  const bar = el('div', { class: 'images-actions ribbon-actions' },
    el('span', { id: 'ribbon-sel-count', class: 'muted' }, 'None selected'));
  IMG_SEGMENTS.forEach((s) => bar.append(el('button', {
    type: 'button', class: 'btn btn-sm btn-ghost', dataset: { op: s.op },
    title: s.hint,
    onclick: () => {
      if (!State.imgSelected.size) return toast('Tap image(s) in the ribbon to select first', 'info');
      onSegmentDrop(s, [...State.imgSelected]);
    },
  }, s.label)));
  return bar;
}

function segmentZone(s) {
  // A drop only fires if dragenter AND dragover preventDefault and the dropEffect
  // is set — otherwise the browser silently rejects the drop (and our window guard
  // then swallows it, so nothing happened at all).
  const accept = (e) => {
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    e.currentTarget.classList.add('drop');
  };
  return el('div', {
    class: 'img-seg', dataset: { op: s.op },
    ondragenter: accept,
    ondragover: accept,
    ondragleave: (e) => { e.currentTarget.classList.remove('drop'); },
    ondrop: (e) => {
      e.preventDefault();
      e.currentTarget.classList.remove('drop');
      DRAGGING = false;
      let files = DRAG_FILES.slice();
      if (!files.length && e.dataTransfer) {           // fallback: read the drag payload
        const t = e.dataTransfer.getData('text/plain');
        if (t) files = [t];
      }
      onSegmentDrop(s, files);
    },
    onclick: () => { if (State.imgSelected.size) onSegmentDrop(s, [...State.imgSelected]); else toast('Select image(s) in the ribbon, or drag one onto a segment', 'info'); },
  },
    el('strong', {}, s.label),
    el('span', { class: 'img-seg-hint muted' }, s.hint));
}

async function loadGeneratedImages() {
  if (DRAGGING) return;   // don't rebuild the ribbon out from under an active drag
  const g = $('#generated-gallery');
  if (!g) return;
  let data;
  try { data = await api('/api/admin/generated-images'); } catch (e) { return; }
  const imgs = (data && data.images) || [];
  const sig = JSON.stringify(imgs.map((i) => i.file));
  if (sig === _genSig) { updateRibbonSelection(); updateRibbonNav(); return; }
  _genSig = sig;
  g.innerHTML = '';
  if (!imgs.length) { g.append(el('p', { class: 'muted' }, 'No images yet — generate one below.')); updateRibbonNav(); return; }
  imgs.forEach((im) => {
    const item = el('div', {
      class: 'ribbon-item', draggable: 'true', dataset: { file: im.file }, title: im.prompt || im.file,
      onclick: () => onRibbonItemClick(im.file, item),
      ondragstart: (e) => {
        DRAGGING = true;
        DRAG_FILES = State.imgSelected.has(im.file) ? [...State.imgSelected] : [im.file];
        if (!State.imgSelected.has(im.file)) { State.imgSelected.clear(); State.imgSelected.add(im.file); updateRibbonSelection(); }
        e.dataTransfer.effectAllowed = 'copy';
        try { e.dataTransfer.setData('text/plain', im.file); } catch (err) { /* ok */ }
      },
      ondragend: () => {
        DRAGGING = false;
        document.querySelectorAll('.img-seg.drop').forEach((z) => z.classList.remove('drop'));
        loadGeneratedImages();   // catch up on anything the poll skipped mid-drag
      },
    },
      el('img', { class: 'ribbon-thumb', src: im.url, alt: im.prompt || '', loading: 'lazy' }),
      im.prompt ? el('span', { class: 'ribbon-caption' }, im.prompt) : null);
    g.append(item);
  });
  updateRibbonSelection();
  updateRibbonNav();
}

function toggleImgSelect(file, item) {
  if (State.imgSelected.has(file)) State.imgSelected.delete(file);
  else State.imgSelected.add(file);
  item.classList.toggle('sel', State.imgSelected.has(file));
  const c = $('#ribbon-sel-count');
  if (c) c.textContent = State.imgSelected.size ? `${State.imgSelected.size} selected` : 'None selected';
}

function updateRibbonSelection() {
  $$('#generated-gallery .ribbon-item').forEach((it) => it.classList.toggle('sel', State.imgSelected.has(it.dataset.file)));
  const c = $('#ribbon-sel-count');
  if (c) c.textContent = State.imgSelected.size ? `${State.imgSelected.size} selected` : 'None selected';
}

async function freeImageMemory() {
  if (!confirm('Restart the Image Studio to unload any model and free memory?\nAny in-progress generation will stop.')) return;
  try {
    await api('/api/admin/images/free-memory', { method: 'POST' });
    toast('Freeing memory — the Studio is restarting (~15s)…', 'success', 5000);
  } catch (e) { toast(e.message, 'error'); }
}

async function loadInflight() {
  const span = $('#img-processing');
  if (!span) return;
  let n = 0;
  try { const r = await api('/api/admin/images/inflight'); n = (r && r.processing) || 0; } catch (e) { /* ok */ }
  if (n > 0) { span.hidden = false; span.textContent = `⏳ processing ${n}…`; } else { span.hidden = true; }
}

function onSegmentDrop(seg, files) {
  if (!files || !files.length) { toast('Select image(s) in the ribbon first', 'info'); return; }
  // Image → Image needs an init image + prompt; the Studio's own box is a cross-
  // origin frame we can't fill, so stage it here (hub-native img2img).
  if (seg.op === 'img2img') { openInitPane(files); return; }
  // Hub-only ops FastSD has no ribbon equivalent for — run straight away, no panel.
  processImages(seg.op, files, seg.op === 'upscale' ? { scale: 2 } : {});
}

// Hub-native "Init image" pane: shows the selected image(s) in a drop-here-style
// box (thumbs wrap + shrink when many, box scrolls), with a prompt + gated Generate.
function openInitPane(files) {
  const stage = $('#img-stage');
  if (!stage) return;
  stage.innerHTML = '';
  stage.hidden = false;

  const box = el('div', { class: `init-box${files.length > 6 ? ' dense' : ''}` });
  files.forEach((f) => box.append(
    el('img', { class: 'init-thumb', src: `/generated-files/${encodeURIComponent(f)}`, alt: f, title: f })));

  const input = el('input', { class: 'field', type: 'text',
    placeholder: 'Describe the new version — e.g. the same owl wearing a blue hat' });
  const gen = el('button', { type: 'button', class: 'btn btn-sm btn-primary' }, 'Generate');
  gen.disabled = true;                                   // enabled only once a prompt is typed
  input.addEventListener('input', () => { gen.disabled = !input.value.trim(); });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !gen.disabled) gen.click(); });
  gen.addEventListener('click', () => { processImages('img2img', files, { prompt: input.value.trim() }); closeStage(); });

  stage.append(
    el('div', { class: 'stage-head' },
      el('strong', {}, 'Image → Image'),
      el('span', { class: 'muted' }, ` — ${files.length} image${files.length > 1 ? 's' : ''} selected`)),
    el('label', { class: 'field-label' }, 'Init image'),
    box,
    el('label', { class: 'field-label' }, 'Prompt for the new version'),
    input,
    el('div', { class: 'stage-actions' },
      gen,
      el('button', { type: 'button', class: 'btn btn-sm btn-ghost', onclick: closeStage }, 'Cancel')));

  stage.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  setTimeout(() => input.focus(), 40);
}

function closeStage() {
  const stage = $('#img-stage');
  if (stage) { stage.innerHTML = ''; stage.hidden = true; }
}

async function processImages(op, files, opts) {
  try {
    const r = await api('/api/admin/images/process', { method: 'POST', body: { op, files, prompt: opts.prompt || '', scale: opts.scale || 2 } });
    if (r && r.error) { toast(r.message || 'Could not start — try again', 'error', 7000); return; }
    const n = (r.started || []).length;
    if (op === 'to_studio') toast(`Sent ${n} image${n > 1 ? 's' : ''} to Studio`, 'success');
    else toast(`Processing ${n} image${n > 1 ? 's' : ''}… results appear in the ribbon`, 'success');
    State.imgSelected.clear(); updateRibbonSelection();
    setTimeout(loadInflight, 500);
    setTimeout(() => { _genSig = ''; loadGeneratedImages(); loadInflight(); }, 3000);
  } catch (e) { toast(e.message, 'error'); }
}

/* ===========================================================================
 * CHAT
 * ========================================================================= */
function wireChat() {
  $('#chat-new').addEventListener('click', newConversation);

  $('#conversation-list').addEventListener('click', (e) => {
    const del = e.target.closest('[data-del-conv]');
    if (del) { e.stopPropagation(); deleteConversation(del.dataset.delConv); return; }
    const item = e.target.closest('[data-conv-id]');
    if (item) openConversation(item.dataset.convId);
  });

  const input = $('#chat-input');
  input.addEventListener('input', autoGrowComposer);
  autoGrowComposer();                       // start at a single line
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chat-form').requestSubmit(); }
  });

  $('#chat-form').addEventListener('submit', (e) => { e.preventDefault(); sendMessage(); });
  $('#chat-stop').addEventListener('click', () => { if (State.chatAbort) State.chatAbort.abort(); });

  // Model picker (admins): remember the choice per conversation, memory only.
  $('#chat-model').addEventListener('change', () => {
    if (State.activeConvId) State.convModel[State.activeConvId] = $('#chat-model').value;
    updateCloudNotice();
  });
}

/* ---- composer model picker (admins only — the catalog is admin-gated) ---- */
const PROVIDER_LABEL = { anthropic: 'Anthropic', openai: 'OpenAI' };
const isCloudModel = (m) => !!(m && m.provider && m.provider !== 'local');

async function loadChatModels() {
  if (!isAdmin()) return;
  try {
    const cat = await api('/api/admin/models-catalog');
    State.chatModels = ((cat && cat.llm) || []).filter((m) => m.role === 'chat');
  } catch (e) { State.chatModels = []; }
  renderChatModelSelect();
}

function renderChatModelSelect() {
  const bar = $('#chat-model-bar');
  const sel = $('#chat-model');
  if (!bar || !sel) return;
  if (!isAdmin() || !State.chatModels.length) { bar.hidden = true; syncChatChips(); return; }
  const current = State.convModel[State.activeConvId] || '';
  sel.innerHTML = '';
  sel.append(el('option', { value: '' }, 'House default'));
  for (const m of State.chatModels) {
    const label = isCloudModel(m)
      ? `${m.display_name || m.alias} · cloud ↗`
      : (m.display_name || m.alias);
    sel.append(el('option', { value: m.alias, selected: m.alias === current }, label));
  }
  bar.hidden = false;
  updateCloudNotice();
  syncChatChips();
}

function updateCloudNotice() {
  const note = $('#chat-cloud-notice');
  if (!note) return;
  const m = State.chatModels.find((x) => x.alias === $('#chat-model')?.value);
  if (m && isCloudModel(m)) {
    note.textContent = `This conversation is sent to ${PROVIDER_LABEL[m.provider] || m.provider} outside your home.`;
    note.hidden = false;
  } else {
    note.hidden = true;
    note.textContent = '';
  }
}

async function loadConversations() {
  if (!can('chat')) return;
  loadChatModels();   // non-blocking; hides the picker for non-admins
  try {
    State.conversations = await api('/api/conversations') || [];
  } catch (e) { toast(e.message, 'error'); State.conversations = []; }
  renderConversationList();
  if (!State.activeConvId && State.conversations.length) {
    openConversation(State.conversations[0].id);
  }
}

function renderConversationList() {
  const ul = $('#conversation-list');
  ul.innerHTML = '';
  for (const c of State.conversations) {
    const li = el('li', {
      class: 'conv-item' + (String(c.id) === String(State.activeConvId) ? ' active' : ''),
      dataset: { convId: c.id },
    },
      el('span', { class: 'conv-title' }, c.title || 'Untitled'),
      el('button', { class: 'icon-btn conv-del', title: 'Delete', dataset: { delConv: c.id } }, '✕'),
    );
    ul.append(li);
  }
  syncChatChips();   // keep the history-count chip truthful
}

async function newConversation() {
  try {
    const conv = await api('/api/conversations', { method: 'POST', body: { title: 'New chat' } });
    State.conversations.unshift(conv);
    renderConversationList();
    openConversation(conv.id);
    $('#chat-input').focus();
  } catch (e) { toast(e.message, 'error'); }
}

async function deleteConversation(id) {
  if (!confirm('Delete this chat?')) return;
  try {
    await api(`/api/conversations/${id}`, { method: 'DELETE' });
    State.conversations = State.conversations.filter((c) => String(c.id) !== String(id));
    if (String(State.activeConvId) === String(id)) {
      State.activeConvId = null;
      $('#chat-messages').innerHTML = '<div class="empty-hint">Start a new chat or pick one from the list.</div>';
    }
    renderConversationList();
  } catch (e) { toast(e.message, 'error'); }
}

async function openConversation(id) {
  State.activeConvId = id;
  renderConversationList();
  // Restore this conversation's model choice (memory only; '' = default).
  const sel = $('#chat-model');
  if (sel && !$('#chat-model-bar').hidden) { sel.value = State.convModel[id] || ''; updateCloudNotice(); }
  const wrap = $('#chat-messages');
  wrap.innerHTML = '<div class="empty-hint">Loading…</div>';
  try {
    const conv = await api(`/api/conversations/${id}`);
    wrap.innerHTML = '';
    const msgs = (conv && conv.messages) || [];
    if (!msgs.length) wrap.append(el('div', { class: 'empty-hint' }, 'Say hello to start the conversation.'));
    for (const m of msgs) appendMessage(m.role, m.content);
    scrollChat();
  } catch (e) {
    wrap.innerHTML = '';
    toast(e.message, 'error');
  }
}

function appendMessage(role, content) {
  const wrap = $('#chat-messages');
  const hint = wrap.querySelector('.empty-hint');
  if (hint) hint.remove();
  const body = el('div', { class: 'msg-body' }, content || '');
  const head = el('div', { class: 'msg-head' },
    el('div', { class: 'msg-role' }, role === 'user' ? 'You' : 'Assistant'),
  );
  if (role !== 'user' && State.voiceAvailable) {
    head.append(makeReadAloudBtn(() => body.textContent));
  }
  const bubble = el('div', { class: `msg msg-${role === 'user' ? 'user' : 'assistant'}` }, head, body);
  wrap.append(bubble);
  return body;
}

function scrollChat() {
  const wrap = $('#chat-messages');
  wrap.scrollTop = wrap.scrollHeight;
}

async function sendMessage() {
  if (!State.status.chat) { toast('Chat is offline — the local model is stopped.', 'error'); return; }
  const input = $('#chat-input');
  const content = input.value.trim();
  if (!content) return;

  // Model override (admins; '' = house default rides as "absent").
  const model = ($('#chat-model-bar').hidden ? '' : $('#chat-model').value) || '';

  if (!State.activeConvId) {
    // auto-create a conversation
    try {
      const conv = await api('/api/conversations', { method: 'POST', body: { title: content.slice(0, 40) } });
      State.conversations.unshift(conv);
      State.activeConvId = conv.id;
      if (model) State.convModel[conv.id] = model;
      $('#chat-messages').innerHTML = '';
      renderConversationList();
    } catch (e) { toast(e.message, 'error'); return; }
  }

  // Did this turn originate from a voice message? (consume the flag)
  const fromVoice = State.nextMsgFromVoice;
  State.nextMsgFromVoice = false;
  let replyOk = false;

  input.value = '';
  autoGrowComposer();          // collapse back to a single line after sending
  appendMessage('user', content);
  scrollChat();

  const target = appendMessage('assistant', '');
  target.classList.add('streaming');
  scrollChat();

  setChatBusy(true);
  const ctrl = new AbortController();
  State.chatAbort = ctrl;

  try {
    const res = await api(`/api/conversations/${State.activeConvId}/messages`, {
      method: 'POST',
      body: model ? { content, stream: true, model } : { content, stream: true },
      raw: true,
      signal: ctrl.signal,
    });

    if (!res.ok) {
      let msg = `Request failed (${res.status})`;
      try { const j = await res.json(); msg = (j.error && j.error.message) || msg; } catch {}
      throw new Error(msg);
    }

    const ct = res.headers.get('content-type') || '';
    if (ct.includes('text/event-stream')) {
      await consumeSSE(res, target);
    } else if (ct.includes('application/json')) {
      const j = await res.json();
      target.textContent = extractContent(j);
    } else {
      target.textContent = await res.text();
    }
    replyOk = true;
  } catch (e) {
    if (e.name === 'AbortError') {
      target.append(el('span', { class: 'muted' }, ' [stopped]'));
    } else {
      target.textContent = '';
      target.append(el('span', { class: 'msg-error' }, e.message || 'Failed to send.'));
    }
  } finally {
    target.classList.remove('streaming');
    setChatBusy(false);
    State.chatAbort = null;
    // refresh title if it changed server-side
    refreshConversationTitle();
    scrollChat();
    // Talk loop: if the request came from a voice message, the "Speak replies"
    // toggle is on, and the reply succeeded, read the reply back automatically.
    if (replyOk && fromVoice && State.voiceAvailable && $('#speak-replies-toggle')?.checked) {
      const reply = (target.textContent || '').trim();
      if (reply) speakText(reply);
    }
  }
}

function setChatBusy(busy) {
  $('#chat-send').hidden = busy;
  $('#chat-stop').hidden = !busy;
  $('#chat-input').disabled = busy;
}

async function refreshConversationTitle() {
  try {
    const list = await api('/api/conversations');
    if (Array.isArray(list)) { State.conversations = list; renderConversationList(); }
  } catch {}
}

function extractContent(j) {
  // Tolerate either a hub-shaped {content} or an OpenAI-shaped message.
  if (j == null) return '';
  if (typeof j.content === 'string') return j.content;
  if (j.message && typeof j.message.content === 'string') return j.message.content;
  if (j.choices && j.choices[0]) {
    const ch = j.choices[0];
    if (ch.message && typeof ch.message.content === 'string') return ch.message.content;
    if (ch.delta && typeof ch.delta.content === 'string') return ch.delta.content;
  }
  return '';
}

function deltaFromSSEJson(j) {
  if (j == null) return '';
  if (typeof j.content === 'string') return j.content;
  if (typeof j.token === 'string') return j.token;
  if (typeof j.delta === 'string') return j.delta;
  if (j.choices && j.choices[0] && j.choices[0].delta && typeof j.choices[0].delta.content === 'string') {
    return j.choices[0].delta.content;
  }
  if (j.message && typeof j.message.content === 'string') return j.message.content;
  return '';
}

async function consumeSSE(res, target) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  target.textContent = '';

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE events are separated by a blank line.
    let idx;
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      handleSSEEvent(rawEvent, target);
    }
  }
  // flush any trailing event without a blank line
  if (buffer.trim()) handleSSEEvent(buffer, target);
}

function handleSSEEvent(rawEvent, target) {
  const dataLines = [];
  for (const line of rawEvent.split('\n')) {
    const s = line.replace(/\r$/, '');
    if (s.startsWith('data:')) dataLines.push(s.slice(5).replace(/^ /, ''));
  }
  if (!dataLines.length) return;
  const data = dataLines.join('\n');
  if (data === '[DONE]') return;

  let piece = '';
  try {
    const j = JSON.parse(data);
    piece = deltaFromSSEJson(j);
  } catch {
    piece = data; // plain text token
  }
  if (piece) {
    target.textContent += piece;
    scrollChat();
  }
}

/* ===========================================================================
 * VOICE — local STT/TTS via the Hub voice service. Graceful degradation:
 * if the service is down, or getUserMedia is unsupported/denied, the mic and
 * read-aloud controls stay hidden and the page keeps working as normal.
 * ========================================================================= */
function wireVoice() {
  const mic = $('#chat-mic');
  if (mic) mic.addEventListener('click', toggleRecording);
  // Probe availability without blocking boot.
  probeVoice();
}

async function probeVoice() {
  // Need both a reachable service AND a usable mic API. We only check support
  // here (not permission) — permission is requested lazily on first mic tap.
  const micSupported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia
    && typeof window.MediaRecorder !== 'undefined');
  let serverOk = false;
  try {
    const h = await api('/api/voice/health');
    serverOk = !!(h && h.available);
  } catch { serverOk = false; }

  const was = State.voiceAvailable;
  State.voiceAvailable = serverOk && micSupported;
  applyVoiceAvailability();

  // If availability flipped on after content already rendered (the health
  // probe can resolve after the first chat/notes render), refresh the active
  // view so existing assistant messages and notes get their read-aloud button.
  if (State.voiceAvailable && !was) {
    if (State.activeTab === 'chat' && State.activeConvId) openConversation(State.activeConvId);
    else if (State.activeTab === 'notes') renderNotes();
  }

  if (!serverOk) return; // stay quiet if the service simply isn't running
  if (!micSupported) {
    toast('Voice input needs a browser with microphone support.', 'info');
  }
}

function applyVoiceAvailability() {
  const on = State.voiceAvailable;
  const mic = $('#chat-mic');
  const bar = $('#chat-voice-bar');
  if (mic) mic.hidden = !on;
  if (bar) bar.hidden = !on;
  if (!on) {
    const t = $('#speak-replies-toggle');
    if (t) t.checked = false;
  }
}

function setVoiceStatus(text) {
  const node = $('#voice-status');
  if (!node) return;
  if (text) { node.textContent = text; node.hidden = false; }
  else { node.textContent = ''; node.hidden = true; }
}

async function toggleRecording() {
  if (State.transcribing) return;            // busy turning speech into text
  if (State.recording) { stopRecording(); return; }
  await startRecording();
}

async function startRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
  } catch (e) {
    // Denied or unavailable — degrade gracefully and don't break the page.
    State.voiceAvailable = false;
    applyVoiceAvailability();
    toast('Microphone unavailable. Voice input turned off.', 'error');
    return;
  }

  let rec;
  try {
    rec = new MediaRecorder(stream);
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    toast('Recording is not supported on this browser.', 'error');
    return;
  }

  State.recStream = stream;
  State.recorder = rec;
  State.recChunks = [];

  rec.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) State.recChunks.push(e.data);
  });
  rec.addEventListener('stop', onRecordingStop);

  rec.start();
  State.recording = true;
  const mic = $('#chat-mic');
  if (mic) { mic.classList.add('recording'); mic.title = 'Stop recording'; mic.setAttribute('aria-label', 'Stop recording'); }
  setVoiceStatus('Recording… tap the mic to stop');
}

function stopRecording() {
  if (State.recorder && State.recorder.state !== 'inactive') {
    try { State.recorder.stop(); } catch {}
  }
  State.recording = false;
  const mic = $('#chat-mic');
  if (mic) { mic.classList.remove('recording'); mic.title = 'Record a voice message'; mic.setAttribute('aria-label', 'Record a voice message'); }
}

function releaseRecStream() {
  if (State.recStream) {
    State.recStream.getTracks().forEach((t) => t.stop());
    State.recStream = null;
  }
  State.recorder = null;
}

async function onRecordingStop() {
  const chunks = State.recChunks;
  State.recChunks = [];
  const type = (State.recorder && State.recorder.mimeType) || 'audio/webm';
  releaseRecStream();

  if (!chunks.length) { setVoiceStatus(''); return; }
  const blob = new Blob(chunks, { type });

  State.transcribing = true;
  const mic = $('#chat-mic');
  if (mic) mic.disabled = true;
  setVoiceStatus('Transcribing…');

  try {
    const fd = new FormData();
    // Give it an extension matching the recorded container for the server.
    const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'mp4' : 'webm';
    fd.append('audio', blob, `recording.${ext}`);
    const data = await api('/api/voice/transcribe', { method: 'POST', body: fd });
    const text = (data && data.text ? String(data.text) : '').trim();
    if (!text) {
      toast('No speech detected. Try again.', 'info');
    } else {
      // Insert transcript into the input for review/edit — do NOT auto-send.
      const input = $('#chat-input');
      const existing = input.value.trim();
      input.value = existing ? `${existing} ${text}` : text;
      input.dispatchEvent(new Event('input'));   // trigger autosize
      input.focus();
      // Mark the next send as voice-originated for the talk loop.
      State.nextMsgFromVoice = true;
    }
  } catch (e) {
    toast(e.message || 'Transcription failed.', 'error');
  } finally {
    State.transcribing = false;
    if (mic) mic.disabled = false;
    setVoiceStatus('');
  }
}

/* ---- read-aloud (TTS) ---- */
function makeReadAloudBtn(getText) {
  return el('button', {
    type: 'button',
    class: 'icon-btn read-aloud',
    title: 'Read aloud',
    'aria-label': 'Read aloud',
    onclick: (e) => {
      e.stopPropagation();      // don't trigger card/message click handlers
      e.preventDefault();
      const text = (getText() || '').trim();
      if (text) speakText(text);
    },
  }, '\u{1F50A}');
}

async function speakText(text) {
  if (!State.voiceAvailable) return;
  const audio = $('#voice-audio');
  if (!audio) return;
  setVoiceStatus('Generating speech…');
  try {
    const res = await api('/api/voice/speak', { method: 'POST', body: { text }, raw: true });
    if (!res.ok) {
      let msg = `Speech failed (${res.status})`;
      try { const j = await res.json(); msg = (j.error && j.error.message) || msg; } catch {}
      throw new Error(msg);
    }
    const blob = await res.blob();
    // Revoke any previous object URL to avoid leaks.
    if (audio.dataset.objUrl) { try { URL.revokeObjectURL(audio.dataset.objUrl); } catch {} }
    const url = URL.createObjectURL(blob);
    audio.dataset.objUrl = url;
    audio.src = url;
    await audio.play();
  } catch (e) {
    toast(e.message || 'Could not play audio.', 'error');
  } finally {
    setVoiceStatus('');
  }
}

/* ===========================================================================
 * NOTES
 * ========================================================================= */
const NOTE_COLORS = ['default', 'yellow', 'green', 'blue', 'pink', 'purple'];

function wireNotes() {
  $('#note-new').addEventListener('click', () => openNoteEditor(null));
  $('#notes-grid').addEventListener('click', (e) => {
    const card = e.target.closest('.note-card');
    if (!card) return;
    const id = card.dataset.noteId;
    if (e.target.closest('[data-note-del]')) { deleteNote(id); return; }
    if (e.target.closest('[data-note-pin]')) { togglePin(id); return; }
    openNoteEditor(id);
  });
}

let NOTES = [];

async function loadNotes() {
  if (!can('notes')) return;
  try { NOTES = await api('/api/notes') || []; }
  catch (e) { toast(e.message, 'error'); NOTES = []; }
  renderNotes();
}

function renderNotes() {
  const grid = $('#notes-grid');
  grid.innerHTML = '';
  const sorted = [...NOTES].sort((a, b) =>
    (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) ||
    String(b.updated_at || '').localeCompare(String(a.updated_at || '')));
  $('#notes-empty').hidden = sorted.length > 0;
  for (const n of sorted) {
    grid.append(el('article', {
      class: `note-card note-${n.color || 'default'}`,
      dataset: { noteId: n.id },
    },
      el('div', { class: 'note-card-head' },
        el('h3', { class: 'note-title' }, n.title || 'Untitled'),
        el('button', {
          class: 'icon-btn note-pin' + (n.pinned ? ' pinned' : ''),
          title: n.pinned ? 'Unpin' : 'Pin',
          dataset: { notePin: '1' },
        }, n.pinned ? '★' : '☆'),
      ),
      el('p', { class: 'note-body' }, (n.body || '').slice(0, 280)),
      el('div', { class: 'note-card-foot' },
        State.voiceAvailable
          ? makeReadAloudBtn(() => [n.title, n.body].filter(Boolean).join('. '))
          : null,
        el('button', { class: 'btn-link danger', dataset: { noteDel: '1' } }, 'Delete'),
      ),
    ));
  }
}

function openNoteEditor(id) {
  const note = id ? NOTES.find((n) => String(n.id) === String(id)) : null;
  const form = el('form', { class: 'note-editor' });
  const titleInput = el('input', { type: 'text', placeholder: 'Title', maxlength: 200, value: note ? note.title || '' : '' });
  const bodyInput = el('textarea', { placeholder: 'Write your note…', rows: 8 }, note ? note.body || '' : '');
  const colorRow = el('div', { class: 'color-row' });
  let chosenColor = (note && note.color) || 'default';
  NOTE_COLORS.forEach((c) => {
    const swatch = el('button', {
      type: 'button',
      class: `swatch note-${c}` + (c === chosenColor ? ' active' : ''),
      title: c,
      dataset: { color: c },
      onclick: () => {
        chosenColor = c;
        $$('.swatch', colorRow).forEach((s) => s.classList.toggle('active', s.dataset.color === c));
      },
    });
    colorRow.append(swatch);
  });

  form.append(
    el('h3', {}, id ? 'Edit note' : 'New note'),
    titleInput,
    bodyInput,
    el('label', { class: 'field-label' }, 'Color'),
    colorRow,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, id ? 'Save' : 'Create'),
    ),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = { title: titleInput.value.trim(), body: bodyInput.value, color: chosenColor };
    try {
      if (id) await api(`/api/notes/${id}`, { method: 'PUT', body: payload });
      else await api('/api/notes', { method: 'POST', body: payload });
      closeModal();
      loadNotes();
    } catch (err) { toast(err.message, 'error'); }
  });

  openModal(form);
  titleInput.focus();
}

async function deleteNote(id) {
  if (!confirm('Delete this note?')) return;
  try { await api(`/api/notes/${id}`, { method: 'DELETE' }); loadNotes(); }
  catch (e) { toast(e.message, 'error'); }
}

async function togglePin(id) {
  const n = NOTES.find((x) => String(x.id) === String(id));
  if (!n) return;
  try {
    await api(`/api/notes/${id}`, { method: 'PUT', body: { title: n.title, body: n.body, color: n.color, pinned: n.pinned ? 0 : 1 } });
    loadNotes();
  } catch (e) { toast(e.message, 'error'); }
}

/* ===========================================================================
 * CHECKLISTS
 * ========================================================================= */
function wireChecklists() {
  $('#checklist-new-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const title = $('#checklist-new-title').value.trim();
    if (!title) return;
    try {
      await api('/api/checklists', { method: 'POST', body: { title } });
      $('#checklist-new-title').value = '';
      loadChecklists();
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#checklists-list').addEventListener('click', onChecklistClick);
  $('#checklists-list').addEventListener('submit', onChecklistSubmit);
  $('#checklists-list').addEventListener('change', onChecklistChange);
}

let CHECKLISTS = [];

async function loadChecklists() {
  if (!can('checklists')) return;
  try { CHECKLISTS = await api('/api/checklists') || []; }
  catch (e) { toast(e.message, 'error'); CHECKLISTS = []; }
  renderChecklists();
}

function renderChecklists() {
  const wrap = $('#checklists-list');
  wrap.innerHTML = '';
  $('#checklists-empty').hidden = CHECKLISTS.length > 0;
  for (const cl of CHECKLISTS) {
    const items = (cl.items || []).slice().sort((a, b) => (a.position || 0) - (b.position || 0));
    const card = el('section', { class: 'checklist-card', dataset: { clId: cl.id } });
    const head = el('div', { class: 'checklist-head' },
      el('h3', { class: 'checklist-title', dataset: { clTitle: '1' } }, cl.title || 'Untitled'),
      el('div', { class: 'checklist-head-actions' },
        el('button', { class: 'btn-link', dataset: { clRename: '1' } }, 'Rename'),
        el('button', { class: 'btn-link danger', dataset: { clDel: '1' } }, 'Delete'),
      ),
    );
    const ul = el('ul', { class: 'check-items' });
    for (const it of items) {
      ul.append(el('li', { class: 'check-item' + (it.done ? ' done' : ''), dataset: { itemId: it.id } },
        el('label', { class: 'check-label' },
          el('input', { type: 'checkbox', checked: !!it.done, dataset: { itemToggle: '1' } }),
          el('span', { class: 'check-text' }, it.text || ''),
        ),
        el('button', { class: 'icon-btn', title: 'Delete item', dataset: { itemDel: '1' } }, '✕'),
      ));
    }
    const addForm = el('form', { class: 'add-item-form', dataset: { addItem: '1' } },
      el('input', { type: 'text', placeholder: 'Add an item…', maxlength: 300 }),
      el('button', { type: 'submit', class: 'btn btn-primary btn-sm' }, 'Add'),
    );
    card.append(head, ul, addForm);
    wrap.append(card);
  }
}

function clIdOf(node) { return node.closest('.checklist-card').dataset.clId; }
function itemIdOf(node) { return node.closest('.check-item').dataset.itemId; }

async function onChecklistClick(e) {
  const card = e.target.closest('.checklist-card');
  if (!card) return;
  const clId = card.dataset.clId;

  if (e.target.closest('[data-cl-del]')) {
    if (!confirm('Delete this checklist?')) return;
    try { await api(`/api/checklists/${clId}`, { method: 'DELETE' }); loadChecklists(); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (e.target.closest('[data-cl-rename]')) {
    const cur = CHECKLISTS.find((c) => String(c.id) === String(clId));
    const title = prompt('Rename checklist', cur ? cur.title : '');
    if (title == null) return;
    try { await api(`/api/checklists/${clId}`, { method: 'PUT', body: { title: title.trim() } }); loadChecklists(); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (e.target.closest('[data-item-del]')) {
    const itemId = itemIdOf(e.target);
    try { await api(`/api/checklists/${clId}/items/${itemId}`, { method: 'DELETE' }); loadChecklists(); }
    catch (err) { toast(err.message, 'error'); }
  }
}

async function onChecklistSubmit(e) {
  const form = e.target.closest('[data-add-item]');
  if (!form) return;
  e.preventDefault();
  const clId = clIdOf(form);
  const input = form.querySelector('input');
  const text = input.value.trim();
  if (!text) return;
  try {
    await api(`/api/checklists/${clId}/items`, { method: 'POST', body: { text } });
    input.value = '';
    loadChecklists();
  } catch (err) { toast(err.message, 'error'); }
}

async function onChecklistChange(e) {
  const box = e.target.closest('[data-item-toggle]');
  if (!box) return;
  const clId = clIdOf(box);
  const itemId = itemIdOf(box);
  try {
    await api(`/api/checklists/${clId}/items/${itemId}`, { method: 'PUT', body: { done: box.checked ? 1 : 0 } });
    box.closest('.check-item').classList.toggle('done', box.checked);
  } catch (err) { toast(err.message, 'error'); loadChecklists(); }
}

/* ===========================================================================
 * CALENDAR & CHORES
 * ---------------------------------------------------------------------------
 * Reads are open to any approved device; writes need the 'checklists'
 * privilege (the server enforces this — the UI just hides write affordances).
 * Recurring events arrive pre-expanded from the server, so an occurrence's
 * `id` is its anchor event's id: editing/deleting touches the whole series.
 * ========================================================================= */
let CAL_EVENTS = [];    // occurrences in the visible month
let CAL_UPCOMING = [];  // occurrences in the next 14 days
let CHORES = [];
let CAL_YEAR = null, CAL_MONTH = null;  // visible month (1-12); set on first load

const CAL_WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const CAL_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];
const CAL_RECURRENCES = [['', 'Does not repeat'], ['daily', 'Daily'],
  ['weekly', 'Weekly'], ['monthly', 'Monthly'], ['yearly', 'Yearly']];

const pad2 = (n) => String(n).padStart(2, '0');
const ymd = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

/** 'YYYY-MM-DD' -> local Date (avoids the UTC shift of new Date(string)). */
function calDate(s) {
  const [y, m, d] = String(s).split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** Short human date for lists/toasts, e.g. "Fri, Jul 17". */
function fmtDay(s) {
  return calDate(s).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

/** Stable person -> chip color class (cal-p0..cal-p5). */
function personClass(name) {
  let h = 0;
  for (const c of String(name || '')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return `cal-p${h % 6}`;
}

function calCanWrite() { return can('checklists'); }

function wireCalendar() {
  $('#cal-add-event').addEventListener('click', () => openEventEditor(null));
  $('#chore-add').addEventListener('click', openChoreEditor);
  $('#cal-month').addEventListener('click', onCalMonthClick);
  $('#cal-upcoming').addEventListener('click', onCalUpcomingClick);
  $('#chores-list').addEventListener('click', onChoresClick);
}

async function loadCalendar() {
  if (!State.me || State.me.status !== 'approved') return;
  if (CAL_YEAR == null) {
    const now = new Date();
    CAL_YEAR = now.getFullYear(); CAL_MONTH = now.getMonth() + 1;
  }
  // show/hide write buttons depending on privilege (server still enforces)
  $('#cal-add-event').hidden = !calCanWrite();
  $('#chore-add').hidden = !calCanWrite();

  const first = `${CAL_YEAR}-${pad2(CAL_MONTH)}-01`;
  const last = `${CAL_YEAR}-${pad2(CAL_MONTH)}-${pad2(new Date(CAL_YEAR, CAL_MONTH, 0).getDate())}`;
  const today = new Date();
  const horizon = new Date(today); horizon.setDate(horizon.getDate() + 13);  // 14 days incl. today
  try {
    const [evs, up, chores] = await Promise.all([
      api(`/api/calendar/events?start=${first}&end=${last}`),
      api(`/api/calendar/events?start=${ymd(today)}&end=${ymd(horizon)}`),
      api('/api/calendar/chores'),
    ]);
    CAL_EVENTS = evs || []; CAL_UPCOMING = up || []; CHORES = chores || [];
  } catch (e) {
    toast(e.message, 'error');
    CAL_EVENTS = []; CAL_UPCOMING = []; CHORES = [];
  }
  renderCalMonth();
  renderCalUpcoming();
  renderChores();
}

/* ===========================================================================
 * SMART HOME — hybrid, LAN-first control (skeleton).
 * Reads: any approved device. Connect/sync/control: admin, or a per-entity
 * grant (all enforced server-side). Inert until an admin links a provider.
 * ========================================================================= */
const HOME = { status: null };

function homeIsAdmin() { return !!(State.me && State.me.role === 'admin'); }

async function loadHome() {
  if (!State.me || State.me.status !== 'approved') return;
  const body = $('#home-body');
  const sync = $('#home-sync');
  if (sync) sync.hidden = true;
  body.replaceChildren(el('div', { class: 'empty-hint' }, 'Loading…'));

  let st;
  try {
    st = await api('/api/home/status');
  } catch (e) {
    body.replaceChildren(el('div', { class: 'empty-hint' }, e.message));
    return;
  }
  HOME.status = st;

  if (!st.enabled) {
    body.replaceChildren(el('div', { class: 'empty-hint' }, 'Smart Home is turned off.'));
    return;
  }
  if (!st.configured) {
    body.replaceChildren(homeSetupCard(st));
    return;
  }
  if (sync && st.is_admin) { sync.hidden = false; sync.onclick = homeSync; }
  await renderHomeConnected(st);
}

function homeSetupCard(st) {
  if (!st.is_admin) {
    return el('div', { class: 'empty-hint' },
      'No smart home is connected yet. Ask an admin to link Home Assistant.');
  }
  const url = el('input', { class: 'field', type: 'text',
    placeholder: 'http://homeassistant.local:8123', autocapitalize: 'none' });
  const token = el('input', { class: 'field', type: 'password',
    placeholder: 'Home Assistant long-lived token', autocomplete: 'off' });
  const msg = el('p', { class: 'form-error', hidden: true });
  const btn = el('button', { class: 'btn btn-primary', onclick: async () => {
    msg.hidden = true; btn.disabled = true;
    try {
      await api('/api/home/connect', { method: 'POST', body: {
        provider: 'home_assistant', base_url: url.value.trim(), token: token.value } });
      toast('Smart home connected', 'success');
      loadHome();
    } catch (e) { msg.textContent = e.message; msg.hidden = false; }
    finally { btn.disabled = false; }
  } }, 'Connect');
  return el('div', { class: 'home-setup' },
    el('p', { class: 'home-lead' },
      'Link your Home Assistant to see and control lights, locks and sensors from '
      + 'the hub. It stays on your home network — nothing is sent to the cloud.'),
    el('label', { class: 'field-label' }, 'Home Assistant address (on your WiFi)'), url,
    el('label', { class: 'field-label' }, 'Long-lived access token'), token,
    msg, btn);
}

async function homeSync() {
  try {
    const r = await api('/api/home/sync', { method: 'POST' });
    toast(`Synced ${r.entity_count} devices`, 'success');
    loadHome();
  } catch (e) { toast(e.message, 'error'); }
}

async function renderHomeConnected(st) {
  const body = $('#home-body');
  const status = el('div', { class: 'home-status' },
    el('span', { class: `home-dot ${st.connected ? 'ok' : 'bad'}` }),
    el('span', {}, st.connected
      ? `Connected to ${st.provider} · ${st.entity_count} devices`
      : `Not reachable${st.last_error ? ': ' + st.last_error : ''}`));
  body.replaceChildren(status);
  if (homeIsAdmin()) {
    body.append(el('button', { class: 'btn-link', onclick: async () => {
      if (!window.confirm('Disconnect the smart home?')) return;
      try { await api('/api/home/disconnect', { method: 'POST' }); loadHome(); }
      catch (e) { toast(e.message, 'error'); }
    } }, 'Disconnect'));
  }

  let rooms = [];
  try { rooms = await api('/api/home/rooms'); } catch (e) { /* status card stands alone */ }
  if (!rooms.length) {
    body.append(el('div', { class: 'empty-hint' }, 'No devices cached yet — tap Sync.'));
    return;
  }
  for (const room of rooms) {
    body.append(el('h3', { class: 'home-room' }, room.area));
    const grid = el('div', { class: 'home-grid' });
    for (const ent of room.entities) grid.append(homeDeviceCard(ent));
    body.append(grid);
  }
}

function homeDeviceCard(ent) {
  const children = [
    el('div', { class: 'home-dev-name' }, ent.name || ent.entity_id),
    el('div', { class: 'home-dev-state' }, ent.state == null ? '—' : String(ent.state)),
  ];
  if (ent.controllable && ent.can_control) {
    const on = String(ent.state).toLowerCase() === 'on';
    children.push(el('button', { class: 'btn btn-sm', onclick: async (e) => {
      e.currentTarget.disabled = true;
      try {
        await api(`/api/home/entities/${encodeURIComponent(ent.entity_id)}/action`,
          { method: 'POST', body: { action: on ? 'turn_off' : 'turn_on' } });
        loadHome();
      } catch (err) { toast(err.message, 'error'); e.currentTarget.disabled = false; }
    } }, on ? 'Turn off' : 'Turn on'));
  }
  return el('div', { class: 'home-dev' }, ...children);
}

/* ---- month grid ---- */
function calChip(ev, { withTime = true } = {}) {
  const label = (withTime && ev.time ? `${ev.time} ` : '') + (ev.title || '');
  const tip = [ev.time, ev.title, ev.person && `— ${ev.person}`, ev.recurrence && `(${ev.recurrence})`,
    ev.notes].filter(Boolean).join(' ');
  return el('button', {
    type: 'button',
    class: `cal-chip ${personClass(ev.person)}`,
    title: tip,
    dataset: { evId: ev.id },
  }, label);
}

function renderCalMonth() {
  const wrap = $('#cal-month');
  wrap.innerHTML = '';

  const byDate = new Map();
  for (const ev of CAL_EVENTS) {
    if (!byDate.has(ev.date)) byDate.set(ev.date, []);
    byDate.get(ev.date).push(ev);
  }

  const nav = el('div', { class: 'cal-nav' },
    el('button', { type: 'button', class: 'btn btn-ghost btn-sm', title: 'Previous month', dataset: { calNav: '-1' } }, '←'),
    el('strong', { class: 'cal-nav-title' }, `${CAL_MONTHS[CAL_MONTH - 1]} ${CAL_YEAR}`),
    el('button', { type: 'button', class: 'btn btn-ghost btn-sm', title: 'Next month', dataset: { calNav: '1' } }, '→'),
    el('button', { type: 'button', class: 'btn btn-ghost btn-sm', dataset: { calToday: '1' } }, 'Today'),
  );

  const grid = el('div', { class: 'cal-grid' });
  for (const wd of CAL_WEEKDAYS) grid.append(el('div', { class: 'cal-wd' }, wd));
  const firstDow = new Date(CAL_YEAR, CAL_MONTH - 1, 1).getDay();
  const daysInMonth = new Date(CAL_YEAR, CAL_MONTH, 0).getDate();
  const todayStr = ymd(new Date());
  for (let i = 0; i < firstDow; i++) grid.append(el('div', { class: 'cal-day cal-pad' }));
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${CAL_YEAR}-${pad2(CAL_MONTH)}-${pad2(d)}`;
    const cell = el('div', {
      class: 'cal-day' + (dateStr === todayStr ? ' cal-today' : ''),
      dataset: { calDate: dateStr },
      title: calCanWrite() ? 'Click to add an event' : null,
    },
      el('span', { class: 'cal-daynum' }, d),
      el('span', { class: 'cal-dayname' }, CAL_WEEKDAYS[new Date(CAL_YEAR, CAL_MONTH - 1, d).getDay()]),
    );
    for (const ev of (byDate.get(dateStr) || [])) cell.append(calChip(ev));
    grid.append(cell);
  }
  wrap.append(nav, grid);
}

function onCalMonthClick(e) {
  const nav = e.target.closest('[data-cal-nav]');
  if (nav) {
    CAL_MONTH += Number(nav.dataset.calNav);
    if (CAL_MONTH < 1) { CAL_MONTH = 12; CAL_YEAR -= 1; }
    if (CAL_MONTH > 12) { CAL_MONTH = 1; CAL_YEAR += 1; }
    loadCalendar();
    return;
  }
  if (e.target.closest('[data-cal-today]')) {
    const now = new Date();
    CAL_YEAR = now.getFullYear(); CAL_MONTH = now.getMonth() + 1;
    loadCalendar();
    return;
  }
  if (!calCanWrite()) return;
  const chip = e.target.closest('.cal-chip');
  if (chip) { openEventEditor(chip.dataset.evId); return; }
  const day = e.target.closest('.cal-day[data-cal-date]');
  if (day) openEventEditor(null, day.dataset.calDate);
}

/* ---- upcoming (14 days) ---- */
function renderCalUpcoming() {
  const wrap = $('#cal-upcoming');
  wrap.innerHTML = '';
  if (!CAL_UPCOMING.length) {
    wrap.append(el('div', { class: 'empty-hint' }, 'Nothing on the calendar for the next two weeks.'));
    return;
  }
  for (const ev of CAL_UPCOMING) {
    wrap.append(el('button', { type: 'button', class: 'cal-up-row', title: ev.notes || null, dataset: { evId: ev.id } },
      el('span', { class: 'cal-up-date' }, fmtDay(ev.date)),
      el('span', { class: 'cal-up-time' }, ev.time || 'all day'),
      el('span', { class: 'cal-up-title' }, ev.title || ''),
      ev.person ? el('span', { class: `cal-chip cal-up-person ${personClass(ev.person)}` }, ev.person) : null,
      ev.recurrence ? el('span', { class: 'cal-up-recur muted' }, `↻ ${ev.recurrence}`) : null,
    ));
  }
}

function onCalUpcomingClick(e) {
  const row = e.target.closest('.cal-up-row');
  if (row && calCanWrite()) openEventEditor(row.dataset.evId);
}

/* ---- event editor modal (create + edit + delete) ---- */
function openEventEditor(id, presetDate) {
  const ev = id
    ? CAL_EVENTS.concat(CAL_UPCOMING).find((x) => String(x.id) === String(id)) || null
    : null;
  const titleInput = el('input', { class: 'field', type: 'text', placeholder: 'e.g. Dentist, football practice',
    maxlength: 200, required: true, value: ev ? ev.title || '' : '' });
  const dateInput = el('input', { class: 'field', type: 'date', required: true,
    value: ev ? ev.date : (presetDate || ymd(new Date())) });
  const timeInput = el('input', { class: 'field', type: 'time', value: (ev && ev.time) || '' });
  const personInput = el('input', { class: 'field', type: 'text', placeholder: 'Who is it for? (optional)',
    maxlength: 80, value: ev ? ev.person || '' : '' });
  const recurSelect = el('select', { class: 'field' },
    ...CAL_RECURRENCES.map(([v, label]) =>
      el('option', { value: v, selected: !!(ev && ev.recurrence === v) }, label)));
  const notesInput = el('textarea', { class: 'field', rows: 3, placeholder: 'Notes (optional)' },
    ev ? ev.notes || '' : '');

  const form = el('form', { class: 'cal-editor' },
    el('h3', {}, id ? 'Edit event' : 'New event'),
    ev && ev.recurrence
      ? el('p', { class: 'muted cal-series-note' }, 'This event repeats — changes apply to the whole series.')
      : null,
    el('label', { class: 'field-label' }, 'Title'), titleInput,
    el('label', { class: 'field-label' }, 'Date'), dateInput,
    el('label', { class: 'field-label' }, 'Time'), timeInput,
    el('label', { class: 'field-label' }, 'Person'), personInput,
    el('label', { class: 'field-label' }, 'Repeats'), recurSelect,
    el('label', { class: 'field-label' }, 'Notes'), notesInput,
    el('div', { class: 'modal-actions' },
      id ? el('button', { type: 'button', class: 'btn btn-ghost danger',
        onclick: () => deleteCalEvent(id, !!(ev && ev.recurrence)) }, 'Delete') : null,
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, id ? 'Save' : 'Create'),
    ),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      title: titleInput.value.trim(),
      date: dateInput.value,
      time: timeInput.value || null,
      person: personInput.value.trim() || null,
      recurrence: recurSelect.value || null,
      notes: notesInput.value.trim() || null,
    };
    // A recurring event's editor shows ONE occurrence's date; writing it back
    // unchanged would silently re-anchor the whole series to that occurrence
    // (dropping earlier repeats and any month-end anchor day). Only send the
    // date when the user actually changed it.
    if (ev && ev.recurrence && payload.date === ev.date) delete payload.date;
    try {
      if (id) await api(`/api/calendar/events/${id}`, { method: 'PUT', body: payload });
      else await api('/api/calendar/events', { method: 'POST', body: payload });
      closeModal();
      loadCalendar();
    } catch (err) { toast(err.message, 'error'); }
  });

  openModal(form);
  titleInput.focus();
}

async function deleteCalEvent(id, isSeries) {
  if (!confirm(isSeries ? 'Delete this event and all its repeats?' : 'Delete this event?')) return;
  try {
    await api(`/api/calendar/events/${id}`, { method: 'DELETE' });
    closeModal();
    loadCalendar();
  } catch (e) { toast(e.message, 'error'); }
}

/* ---- chores ---- */
function renderChores() {
  const wrap = $('#chores-list');
  wrap.innerHTML = '';
  $('#chores-empty').hidden = CHORES.length > 0;
  const todayStr = ymd(new Date());
  for (const ch of CHORES) {
    const overdue = !!(ch.due_date && ch.due_date < todayStr);
    const sub = [
      ch.assignee || null,
      ch.cadence !== 'once' ? ch.cadence : null,
      ch.due_date ? `due ${fmtDay(ch.due_date)}${overdue ? ' — overdue' : ''}` : 'no due date',
      ch.rotation ? `rotates ${ch.rotation.join(' → ')}` : null,
    ].filter(Boolean).join(' · ');
    wrap.append(el('div', { class: 'chore-row' + (overdue ? ' overdue' : ''), dataset: { choreId: ch.id } },
      el('div', { class: 'chore-main' },
        el('strong', { class: 'chore-title' }, ch.title || ''),
        el('span', { class: 'chore-sub' }, sub),
      ),
      calCanWrite() ? el('div', { class: 'chore-actions' },
        el('button', { type: 'button', class: 'btn btn-primary btn-sm', dataset: { choreDone: '1' } }, 'Complete'),
        el('button', { type: 'button', class: 'btn-link danger', dataset: { choreDel: '1' } }, 'Delete'),
      ) : null,
    ));
  }
}

async function onChoresClick(e) {
  const row = e.target.closest('.chore-row');
  if (!row) return;
  const id = row.dataset.choreId;

  if (e.target.closest('[data-chore-done]')) {
    const before = CHORES.find((c) => String(c.id) === String(id));
    try {
      const after = await api(`/api/calendar/chores/${id}/complete`, { method: 'POST' });
      if (after.done_at) {
        toast('Chore done — nice work!', 'success');
      } else if (before && after.assignee && after.assignee !== before.assignee) {
        // recurring chore handed off through the rotation
        toast(`Done! Next up: ${after.assignee}${after.due_date ? ` (due ${fmtDay(after.due_date)})` : ''}`, 'success');
      } else {
        toast(`Done!${after.due_date ? ` Due again ${fmtDay(after.due_date)}.` : ''}`, 'success');
      }
      loadCalendar();
    } catch (err) { toast(err.message, 'error'); }
    return;
  }

  if (e.target.closest('[data-chore-del]')) {
    if (!confirm('Delete this chore?')) return;
    try { await api(`/api/calendar/chores/${id}`, { method: 'DELETE' }); loadCalendar(); }
    catch (err) { toast(err.message, 'error'); }
  }
}

function openChoreEditor() {
  const titleInput = el('input', { class: 'field', type: 'text', placeholder: 'e.g. Take out the bins',
    maxlength: 200, required: true });
  const assigneeInput = el('input', { class: 'field', type: 'text',
    placeholder: 'e.g. Alex  —  or  Alex, Sam, Kai to rotate' });
  const cadenceSelect = el('select', { class: 'field' },
    el('option', { value: 'once' }, 'Once'),
    el('option', { value: 'daily' }, 'Daily'),
    el('option', { value: 'weekly' }, 'Weekly'));
  const dueInput = el('input', { class: 'field', type: 'date' });

  const form = el('form', { class: 'cal-editor' },
    el('h3', {}, 'New chore'),
    el('label', { class: 'field-label' }, 'Title'), titleInput,
    el('label', { class: 'field-label' }, 'Assignee(s) — comma-separated names take turns'), assigneeInput,
    el('label', { class: 'field-label' }, 'Repeats'), cadenceSelect,
    el('label', { class: 'field-label' }, 'Due date'), dueInput,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, 'Create'),
    ),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const names = assigneeInput.value.split(',').map((s) => s.trim()).filter(Boolean);
    const payload = {
      title: titleInput.value.trim(),
      assignee: names[0] || null,
      rotation: names.length > 1 ? names : null,   // >1 name -> rotation hand-off
      cadence: cadenceSelect.value,
      due_date: dueInput.value || null,
    };
    try {
      await api('/api/calendar/chores', { method: 'POST', body: payload });
      closeModal();
      loadCalendar();
    } catch (err) { toast(err.message, 'error'); }
  });

  openModal(form);
  titleInput.focus();
}

/* ===========================================================================
 * FILES & PHOTOS
 * ========================================================================= */
function wireFiles() {
  // kind filter chips
  $('.files-filter').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    State.filesKind = chip.dataset.kind;
    $$('.files-filter .chip').forEach((c) => c.classList.toggle('chip-active', c === chip));
    loadFiles();
  });

  // upload affordances
  const dz = $('#dropzone');
  const fileInput = $('#file-input');
  $('#file-browse').addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => { uploadFiles(fileInput.files); fileInput.value = ''; });

  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add('dragover');
  }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove('dragover');
  }));
  dz.addEventListener('drop', (e) => {
    if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
  });

  // file grid actions
  $('#files-grid').addEventListener('click', onFilesGridClick);
  $('#files-grid').addEventListener('change', onFilesGridChange);

  // search
  $('#search-form').addEventListener('submit', (e) => { e.preventDefault(); runSearch(); });
  $('#search-clear').addEventListener('click', clearSearch);
}

let FILES = [];

function canWriteForKind(kind) {
  return kind === 'photo' ? can('photos_write') : can('files_write');
}
function canReadForKind(kind) {
  return kind === 'photo' ? can('photos_read') : can('files_read');
}

async function loadFiles() {
  // show/hide dropzone depending on write privilege (server still enforces)
  const dz = $('#dropzone');
  dz.hidden = !(can('files_write') || can('photos_write'));

  const q = State.filesKind ? `?kind=${encodeURIComponent(State.filesKind)}` : '';
  try { FILES = await api(`/api/files${q}`) || []; }
  catch (e) { toast(e.message, 'error'); FILES = []; }
  renderFiles();
}

function renderFiles() {
  const grid = $('#files-grid');
  grid.innerHTML = '';
  $('#files-empty').hidden = FILES.length > 0;
  for (const f of FILES) {
    grid.append(fileCard(f));
  }
}

function fileCard(f) {
  const isPhoto = f.kind === 'photo';
  const mine = State.me && f.owner_username === State.me.username;
  const card = el('article', { class: 'file-card', dataset: { fileId: f.id, kind: f.kind } });

  if (isPhoto) {
    card.append(el('div', { class: 'file-thumb' },
      el('img', { src: `/api/files/${f.id}/content`, alt: f.caption || f.filename, loading: 'lazy' }),
    ));
  } else {
    card.append(el('div', { class: 'file-thumb file-icon' }, '📄'));
  }

  const meta = el('div', { class: 'file-meta' },
    el('div', { class: 'file-name', title: f.filename }, f.filename || 'file'),
    el('div', { class: 'file-sub' },
      humanSize(f.size),
      f.shared ? el('span', { class: 'tag tag-shared' }, 'shared') : null,
      f.owner_username ? el('span', { class: 'tag' }, `@${f.owner_username}`) : null,
      f.indexed ? null : el('span', { class: 'tag tag-pending' }, 'indexing…'),
    ),
  );
  if (f.caption) meta.append(el('div', { class: 'file-caption' }, f.caption));

  const actions = el('div', { class: 'file-actions' },
    el('a', { class: 'btn-link', href: `/api/files/${f.id}/content`, target: '_blank', rel: 'noopener' }, 'Open'),
  );
  // shared toggle — only owner can flip; show as a label toggle (PUT not in contract,
  // so this is informational unless owner; we re-upload semantics not supported).
  if (mine || isAdmin()) {
    actions.append(el('button', { class: 'btn-link danger', dataset: { fileDel: '1' } }, 'Delete'));
  }

  card.append(meta, actions);
  return card;
}

function humanSize(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}

async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const shared = $('#upload-shared').checked;
  const prog = $('#upload-progress');
  prog.hidden = false;
  prog.innerHTML = '';

  for (const file of files) {
    const row = el('div', { class: 'upload-row' },
      el('span', { class: 'upload-name' }, file.name),
      el('span', { class: 'upload-status' }, 'uploading…'),
    );
    prog.append(row);
    const status = row.querySelector('.upload-status');
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('shared', shared ? 'true' : 'false');
      await api('/api/files', { method: 'POST', body: fd });
      status.textContent = 'done';
      status.classList.add('ok');
    } catch (e) {
      status.textContent = e.message || 'failed';
      status.classList.add('err');
    }
  }
  setTimeout(() => { prog.hidden = true; prog.innerHTML = ''; }, 2500);
  loadFiles();
}

async function onFilesGridClick(e) {
  const del = e.target.closest('[data-file-del]');
  if (!del) return;
  const id = del.closest('.file-card').dataset.fileId;
  if (!confirm('Delete this item?')) return;
  try { await api(`/api/files/${id}`, { method: 'DELETE' }); loadFiles(); }
  catch (err) { toast(err.message, 'error'); }
}

function onFilesGridChange() { /* reserved for future shared-toggle if backend adds it */ }

/* ---- search ---- */
async function runSearch() {
  const q = $('#search-input').value.trim();
  const box = $('#search-results');
  if (!q) { clearSearch(); return; }
  box.hidden = false;
  box.innerHTML = '<div class="empty-hint">Searching…</div>';
  $('#search-clear').hidden = false;

  const body = { q };
  if (State.filesKind) body.kind = State.filesKind;
  try {
    const results = await api('/api/search', { method: 'POST', body }) || [];
    renderSearchResults(results, q);
  } catch (e) {
    box.innerHTML = '';
    box.append(el('div', { class: 'msg-error' }, e.message || 'Search failed.'));
  }
}

function renderSearchResults(results, q) {
  const box = $('#search-results');
  box.innerHTML = '';
  box.append(el('div', { class: 'search-results-head' }, `${results.length} result${results.length === 1 ? '' : 's'} for "${q}"`));
  if (!results.length) { box.append(el('div', { class: 'empty-hint' }, 'Nothing matched.')); return; }
  for (const r of results) {
    box.append(el('a', {
      class: 'search-hit', href: `/api/files/${r.file_id}/content`, target: '_blank', rel: 'noopener',
    },
      el('div', { class: 'search-hit-head' },
        el('span', { class: 'search-hit-name' }, r.filename || `file ${r.file_id}`),
        el('span', { class: 'tag' }, r.kind || 'file'),
        (r.score != null) ? el('span', { class: 'search-score' }, `score ${Number(r.score).toFixed(3)}`) : null,
      ),
      r.snippet ? el('div', { class: 'search-snippet' }, r.snippet) : null,
    ));
  }
}

function clearSearch() {
  $('#search-input').value = '';
  $('#search-results').hidden = true;
  $('#search-results').innerHTML = '';
  $('#search-clear').hidden = true;
}

/* ===========================================================================
 * KEYS
 * ========================================================================= */
function wireKeys() {
  $('#key-new-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = $('#key-new-name').value.trim();
    if (!name) return;
    try {
      const res = await api('/api/keys', { method: 'POST', body: { name } });
      $('#key-new-name').value = '';
      showKeyReveal(res, name);
      loadKeys();
    } catch (err) { toast(err.message, 'error'); }
  });

  $('#keys-list').addEventListener('click', async (e) => {
    const rev = e.target.closest('[data-key-revoke]');
    if (!rev) return;
    const id = rev.closest('.key-row').dataset.keyId;
    if (!confirm('Revoke this key? Apps using it will stop working.')) return;
    try { await api(`/api/keys/${id}/revoke`, { method: 'POST' }); loadKeys(); }
    catch (err) { toast(err.message, 'error'); }
  });
}

let KEYS = [];
let GW_KEY_CLOUD = null;   // gateway key id -> cloud_allowed (admins; null = unknown)

async function loadKeys() {
  if (!can('api_keys')) return;
  try { KEYS = await api('/api/keys') || []; }
  catch (e) { toast(e.message, 'error'); KEYS = []; }
  // Admins also see + toggle each key's cloud opt-in (state lives gateway-side).
  GW_KEY_CLOUD = null;
  if (isAdmin()) {
    try {
      const d = await api('/api/admin/gateway-keys');
      GW_KEY_CLOUD = {};
      for (const gk of (d && d.keys) || []) GW_KEY_CLOUD[String(gk.id)] = !!gk.cloud_allowed;
    } catch (e) { /* gateway down — hide the toggles */ }
  }
  renderKeys();
}

function cloudToggle(k) {
  // Admin-only switch: may this key use cloud models? (opt-in, per key)
  const gid = String(k.gateway_key_id);
  const cb = el('input', { type: 'checkbox', checked: !!GW_KEY_CLOUD[gid] });
  cb.addEventListener('change', async () => {
    cb.disabled = true;
    try {
      await api(`/api/admin/gateway-keys/${encodeURIComponent(gid)}/cloud`, {
        method: 'POST', body: { allowed: cb.checked },
      });
      GW_KEY_CLOUD[gid] = cb.checked;
      toast(cb.checked ? 'Cloud models allowed for this key' : 'Cloud models blocked for this key', 'success');
    } catch (e) { toast(e.message, 'error'); cb.checked = !cb.checked; }
    cb.disabled = false;
  });
  return el('label', { class: 'switch key-cloud-toggle', title: 'Allow this key to use cloud models (chats leave your home)' },
    cb, el('span', { class: 'track' }), el('span', {}, 'Cloud'));
}

function renderKeys() {
  const wrap = $('#keys-list');
  wrap.innerHTML = '';
  $('#keys-empty').hidden = KEYS.length > 0;
  for (const k of KEYS) {
    const revoked = !!k.revoked;
    const canCloud = !revoked && GW_KEY_CLOUD && String(k.gateway_key_id) in GW_KEY_CLOUD;
    wrap.append(el('div', { class: 'key-row' + (revoked ? ' revoked' : ''), dataset: { keyId: k.id } },
      el('div', { class: 'key-main' },
        el('div', { class: 'key-name' }, k.name || 'key'),
        el('div', { class: 'key-sub' },
          el('code', {}, (k.key_prefix || '') + '…'),
          k.created_at ? el('span', { class: 'muted' }, ` · ${fmtDate(k.created_at)}`) : null,
          revoked ? el('span', { class: 'tag tag-pending' }, 'revoked') : null,
        ),
      ),
      canCloud ? cloudToggle(k) : null,
      revoked ? null : el('button', { class: 'btn btn-ghost btn-sm', dataset: { keyRevoke: '1' } }, 'Revoke'),
    ));
  }
}

function showKeyReveal(res, name) {
  const plaintext = res.plaintext_key || '';
  const baseUrl = res.base_url || '';
  const model = res.model || '';

  const copyField = (label, value) => {
    const input = el('input', { type: 'text', readOnly: true, value, class: 'reveal-input' });
    return el('div', { class: 'reveal-field' },
      el('label', { class: 'field-label' }, label),
      el('div', { class: 'reveal-row' },
        input,
        el('button', {
          type: 'button', class: 'btn btn-ghost btn-sm',
          onclick: async () => {
            try { await navigator.clipboard.writeText(value); toast('Copied', 'success', 1500); }
            catch { input.select(); document.execCommand('copy'); toast('Copied', 'success', 1500); }
          },
        }, 'Copy'),
      ),
    );
  };

  const node = el('div', { class: 'key-reveal' },
    el('h3', {}, `Key “${name}” created`),
    el('p', { class: 'warn-text' }, 'Copy this key now — it will not be shown again.'),
    copyField('API key', plaintext),
    baseUrl ? copyField('Base URL', baseUrl) : null,
    model ? copyField('Model', model) : null,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-primary', onclick: closeModal }, 'Done'),
    ),
  );
  openModal(node);
}

function fmtDate(s) {
  // DB timestamps are Unix SECONDS; JS Date wants milliseconds. Numeric values
  // below 1e12 (any real date, as seconds) are scaled up; larger numbers are
  // already ms, and non-numeric strings (ISO, etc.) are parsed as-is.
  let v = Number(s);
  if (!isNaN(v)) { if (v < 1e12) v *= 1000; } else { v = s; }
  const d = new Date(v);
  if (isNaN(d.getTime())) return String(s);
  return d.toLocaleString();
}

/* ===========================================================================
 * ADMIN
 * ========================================================================= */
const ROLE_DEFAULTS = {
  guest:  ['chat'],
  member: ['chat', 'notes', 'checklists', 'files_read', 'files_write', 'photos_read', 'photos_write'],
  admin:  ['chat', 'notes', 'checklists', 'files_read', 'files_write', 'photos_read', 'photos_write', 'api_keys'],
};

function wireAdmin() {
  $('#admin-refresh').addEventListener('click', loadDevices);
  $('#devices-list').addEventListener('click', onDeviceClick);
}

let DEVICES = [];

async function loadDevices() {
  if (!isAdmin()) return;
  try { DEVICES = await api('/api/admin/devices') || []; }
  catch (e) { toast(e.message, 'error'); DEVICES = []; }
  renderDevices();
}

function renderDevices() {
  const wrap = $('#devices-list');
  wrap.innerHTML = '';
  $('#devices-empty').hidden = DEVICES.length > 0;
  for (const d of DEVICES) {
    const privs = parsePrivs(d.privileges_json || d.privileges);
    const card = el('div', { class: `device-card status-${d.status || 'unknown'}`, dataset: { devId: d.id } },
      el('div', { class: 'device-head' },
        el('div', {},
          el('strong', {}, d.username || '(no name)'),
          el('span', { class: `tag tag-role tag-${d.role}` }, d.role || 'guest'),
          el('span', { class: `tag tag-status tag-${d.status}` }, d.status || 'unknown'),
        ),
        el('div', { class: 'device-actions' },
          el('button', { class: 'btn btn-primary btn-sm', dataset: { devApprove: '1' } },
            d.status === 'approved' ? 'Edit access' : 'Approve'),
          d.status === 'revoked'
            ? null
            : el('button', { class: 'btn btn-ghost btn-sm danger', dataset: { devRevoke: '1' } }, 'Revoke'),
          el('button', { class: 'btn btn-ghost btn-sm danger', dataset: { devRemove: '1' } }, 'Remove'),
        ),
      ),
      el('div', { class: 'device-sub muted' },
        privs.length ? `privileges: ${privs.join(', ')}` : 'no privileges',
        d.last_seen ? ` · last seen ${fmtDate(d.last_seen)}` : '',
      ),
    );
    wrap.append(card);
  }
}

function parsePrivs(p) {
  if (Array.isArray(p)) return p;
  if (typeof p === 'string') { try { const a = JSON.parse(p); return Array.isArray(a) ? a : []; } catch { return []; } }
  return [];
}

async function onDeviceClick(e) {
  const card = e.target.closest('.device-card');
  if (!card) return;
  const id = card.dataset.devId;
  const dev = DEVICES.find((d) => String(d.id) === String(id));

  if (e.target.closest('[data-dev-revoke]')) {
    if (!confirm('Revoke this device? It will lose access immediately.')) return;
    try { await api(`/api/admin/devices/${id}/revoke`, { method: 'POST' }); loadDevices(); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (e.target.closest('[data-dev-remove]')) {
    const who = dev && dev.username ? ` "${dev.username}"` : '';
    if (!confirm(`Permanently remove this device${who}? This deletes the entry entirely.`)) return;
    try { await api(`/api/admin/devices/${id}`, { method: 'DELETE' }); loadDevices(); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (e.target.closest('[data-dev-approve]')) {
    openApproveDialog(dev);
  }
}

function openApproveDialog(dev) {
  const curRole = dev.role && dev.role !== 'guest' ? dev.role : 'member';
  const curPrivs = new Set(parsePrivs(dev.privileges_json || dev.privileges));
  // New/pending device (or one with no privileges yet): start from the selected
  // role's defaults (guest=chat, member=most, admin=all). Editing an already-
  // approved device keeps its current selection.
  const initialPrivs = (dev.status !== 'approved' || curPrivs.size === 0)
    ? new Set(ROLE_DEFAULTS[curRole] || [])
    : curPrivs;

  const roleSelect = el('select', { class: 'role-select' },
    ...['guest', 'member', 'admin'].map((r) =>
      el('option', { value: r, selected: r === curRole }, r)),
  );

  const privWrap = el('div', { class: 'priv-grid' });
  const tpl = $('#priv-template').content.cloneNode(true);
  privWrap.append(tpl);
  $$('.priv-check input', privWrap).forEach((cb) => { cb.checked = initialPrivs.has(cb.value); });

  // when role changes, prefill defaults
  roleSelect.addEventListener('change', () => {
    const def = new Set(ROLE_DEFAULTS[roleSelect.value] || []);
    $$('.priv-check input', privWrap).forEach((cb) => { cb.checked = def.has(cb.value); });
  });

  const form = el('form', { class: 'approve-form' },
    el('h3', {}, `Access for ${dev.username || 'device'}`),
    el('label', { class: 'field-label' }, 'Role'),
    roleSelect,
    el('label', { class: 'field-label' }, 'Privileges'),
    privWrap,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, 'Save'),
    ),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const role = roleSelect.value;
    const privileges = $$('.priv-check input', privWrap).filter((cb) => cb.checked).map((cb) => cb.value);
    try {
      await api(`/api/admin/devices/${dev.id}/approve`, { method: 'POST', body: { role, privileges } });
      closeModal();
      loadDevices();
      toast('Device updated', 'success');
    } catch (err) { toast(err.message, 'error'); }
  });

  openModal(form);
}

/* ===========================================================================
 * MODELS (admin) — control plane: lifecycle + metrics + resources
 * ========================================================================= */
let MODELS = [];
let MODELS_POLL = null;
let _modelsSig = '';
const PULLING = new Set();        // ollama tags currently downloading (via a catalog "Add"/"Download")
let INSTALLED_TAGS = new Set();   // ollama tags whose weights are actually on disk (i.e. ready to Start)
function tagVariants(t) { t = (t || '').trim(); return [t, t.replace(/:latest$/, ''), t.includes(':') ? t : `${t}:latest`]; }
function llmDownloaded(m) { return tagVariants(m.ollama_tag || m.alias).some((t) => INSTALLED_TAGS.has(t)); }
function llmPulling(m) { return tagVariants(m.ollama_tag).some((t) => PULLING.has(t)); }

function wireModels() {
  $('#models-refresh').addEventListener('click', () => { _modelsSig = ''; loadModels(); });
  $('#models-scan').addEventListener('click', scanModels);
  $('#models-add').addEventListener('click', openAddModelDialog);
  $('#models-list').addEventListener('click', onModelClick);
  $('#services-strip').addEventListener('click', onServiceClick);
}

async function scanModels() {
  const btn = $('#models-scan');
  if (btn) btn.disabled = true;
  try {
    const res = await api('/api/admin/models/scan', { method: 'POST' });
    const reg = (res.registered || []).length;
    const imp = (res.imports && res.imports.importing) || [];
    let msg = reg ? `Registered ${reg} new model${reg > 1 ? 's' : ''}` : 'No new Ollama models';
    if (imp.length) msg += ` · importing ${imp.length} file${imp.length > 1 ? 's' : ''}…`;
    else if (res.imports && res.imports.note) msg += ` · ${res.imports.note}`;
    toast(msg, 'success');
    imp.forEach(trackImport);
    _modelsSig = '';
    await refreshModels();
  } catch (e) { toast(e.message, 'error'); }
  finally { if (btn) btn.disabled = false; }
}

function trackImport(name) {
  const iv = setInterval(async () => {
    let s;
    try { s = await api(`/api/admin/models/import/status?name=${encodeURIComponent(name)}`); }
    catch { clearInterval(iv); return; }
    if (s.status === 'done') { clearInterval(iv); toast(`${name} imported`, 'success'); _modelsSig = ''; refreshModels(); }
    else if (s.status === 'error') { clearInterval(iv); toast(`Import failed (${name}): ${s.detail || 'error'}`, 'error'); }
  }, 4000);
  setTimeout(() => clearInterval(iv), 60 * 60 * 1000);
}

function stopModelsPoll() {
  if (MODELS_POLL) { clearInterval(MODELS_POLL); MODELS_POLL = null; }
}

async function loadModels() {
  if (!isAdmin()) return;
  await refreshModels();
  stopModelsPoll();
  MODELS_POLL = setInterval(() => {
    if (State.activeTab !== 'models') { stopModelsPoll(); return; }
    refreshModels();
  }, 5000);
}

async function refreshModels() {
  // models-catalog always returns the AI models (live when up, last-known when
  // stopped) so the page lists them even when the stack is off. All fetches are
  // fault-tolerant so nothing ever blanks the page.
  let svc = null, cat = null, img = null, res = null, prov = null;
  try { svc = await api('/api/admin/services'); } catch (e) { /* rare */ }
  try { cat = await api('/api/admin/models-catalog'); } catch (e) { cat = null; }
  try { img = await api('/api/admin/image-models'); } catch (e) { img = null; }
  try { res = await api('/api/admin/resources-overview'); } catch (e) { res = null; }
  try { prov = await api('/api/admin/providers'); } catch (e) { prov = null; }  // gateway down → hidden
  // Which ollama tags are actually downloaded — a model is only Start-able once its
  // weights are on disk. (Registered-but-not-downloaded shows as "downloading".)
  if (cat && cat.gw_up) {
    try { const inst = await api('/api/admin/ollama/installed');
      INSTALLED_TAGS = new Set((inst && inst.installed || []).flatMap((i) => tagVariants(i.tag))); }
    catch (e) { /* keep last-known */ }
  }

  // Live RAM/CPU + aggregate refresh every poll (small); the model grid
  // re-renders only when structure/disk changes so cards don't flicker.
  renderServices(svc, res);
  renderAggregate(res);

  const aiDisk = res && res.services ? res.services.ai.models : null;
  const imgDisk = res && res.services ? res.services.images.models : null;
  // Structural-only signature — exclude volatile per-model fields (resident_bytes,
  // requests_24h, tokens) so the grid re-renders on real state/disk changes, not
  // every 5s poll. Keep the full `cat` for renderModels().
  const catSig = cat ? {
    gw_up: cat.gw_up, voice_up: cat.voice_up,
    llm: (cat.llm || []).map((m) => ({ alias: m.alias, role: m.role, state: m.state, loaded: m.loaded, display_name: m.display_name, ollama_tag: m.ollama_tag, provider: m.provider, dl: llmDownloaded(m), pull: llmPulling(m) })),
    voice: (cat.voice || []).map((m) => ({ name: m.name, role: m.role, state: m.state, loaded: m.loaded, display_name: m.display_name })),
  } : null;
  const structSig = JSON.stringify({
    ai: svc && svc.ai ? svc.ai.running : null,
    images: svc && svc.images ? svc.images.running : null,
    cat: catSig,
    imgIds: img && img.models ? img.models.filter((m) => m.cached).map((m) => m.id) : [],
    disk: { ai: aiDisk, img: imgDisk },
    prov: prov && prov.providers ? prov.providers : null,
  });
  if (structSig !== _modelsSig) {
    _modelsSig = structSig;
    renderModels(cat, img, res);
    renderProviders(prov);
  }
}

function renderServices(s, res) {
  const strip = $('#services-strip');
  if (!strip) return;
  strip.innerHTML = '';
  strip.append(el('p', { class: 'services-warn' },
    '⚠️ 16 GB box — the AI models and the Image Studio can’t run together. Starting one stops the other.'));
  if (!s) { strip.append(el('p', { class: 'muted' }, 'Service status unavailable.')); return; }
  const R = res && res.services ? res.services : {};
  strip.append(el('div', { class: 'services-row' },
    serviceCard('ai', 'AI models — chat · vision · voice', !!(s.ai && s.ai.running), null, R.ai),
    serviceCard('images', 'Image Studio (separate)', !!(s.images && s.images.running), s.images && s.images.url, R.images),
  ));
}

function serviceCard(name, label, running, url, res) {
  const actions = el('div', { class: 'svc-actions' });
  actions.append(running
    ? el('button', { class: 'btn btn-sm btn-ghost', dataset: { svc: name, svcAction: 'stop' } }, 'Stop')
    : el('button', { class: 'btn btn-sm btn-primary', dataset: { svc: name, svcAction: 'start' } }, 'Start'));
  if (name === 'images' && running && url) {
    actions.append(el('a', { class: 'btn btn-sm btn-ghost', href: url, target: '_blank', rel: 'noopener' }, 'Open ↗'));
  }
  // Resources: disk always; RAM/CPU only when running (0 is hidden).
  const stats = el('div', { class: 'svc-stats' });
  if (res) {
    stats.append(svcStat('disk', fmtBytes(res.disk_bytes || 0)));
    if (running && res.rss_bytes != null) stats.append(svcStat('RAM', fmtBytes(res.rss_bytes)));
    if (running && res.cpu_percent) stats.append(svcStat('CPU', `${res.cpu_percent}%`));
  }
  return el('div', { class: `svc-card ${running ? 'on' : 'off'}` },
    el('div', { class: 'svc-head' },
      el('span', { class: `svc-dot ${running ? 'on' : 'off'}` }),
      el('strong', {}, label)),
    el('div', { class: 'svc-status muted' }, running ? 'running' : 'stopped'),
    stats,
    actions,
  );
}

function svcStat(label, value) {
  return el('span', { class: 'svc-stat' },
    el('span', { class: 'svc-stat-l muted' }, label), ' ', el('strong', {}, value));
}

function renderAggregate(res) {
  const strip = $('#resources-strip');
  if (!strip) return;
  strip.innerHTML = '';
  if (!res || !res.aggregate) return;
  const a = res.aggregate;
  strip.append(resCard('Total model disk', fmtBytes(a.disk_bytes || 0), null));
  if (a.rss_bytes != null) strip.append(resCard('RAM in use', fmtBytes(a.rss_bytes), null));
  if (a.cpu_percent) strip.append(resCard('CPU', `${a.cpu_percent}%`, null));
}

async function onServiceClick(e) {
  const btn = e.target.closest('button[data-svc]');
  if (!btn) return;
  const { svc, svcAction } = btn.dataset;
  const label = svc === 'ai' ? 'AI models' : 'Image Studio';
  btn.disabled = true;
  try {
    await api(`/api/admin/services/${svc}/${svcAction}`, { method: 'POST' });
    toast(`${label} ${svcAction === 'start' ? 'starting…' : 'stopping…'}`, 'success');
    _modelsSig = '';
    setTimeout(() => refreshModels(), 2500);
    setTimeout(() => { _modelsSig = ''; refreshModels(); }, 8000);
  } catch (err) { toast(err.message, 'error'); btn.disabled = false; }
}


function renderModels(cat, img, res) {
  const wrap = $('#models-list');
  wrap.innerHTML = '';
  const llm = (cat && cat.llm) || [];
  const voice = (cat && cat.voice) || [];
  const gwUp = !!(cat && cat.gw_up);
  const voiceUp = !!(cat && cat.voice_up);
  const aiDisk = (res && res.services) ? res.services.ai.models : {};
  const imgDisk = (res && res.services) ? res.services.images.models : {};
  const dk = (map, key) => (map && map[key] ? map[key].disk_bytes : null);

  // AI models (the 5) — always listed. `offline` cards show a Start button.
  // Cloud models (provider != local) have no local weights: no disk/RAM stats,
  // never "not downloaded", and a 'cloud' badge instead.
  const aiGrid = el('div', { class: 'models-grid' });
  llm.forEach((m) => { const cloud = isCloudModel(m); aiGrid.append(modelCard({
    ...m, source: 'gateway', key: m.alias, removable: true, cloud,
    subtitle: cloud ? `${m.provider} · ${m.upstream_model || m.ollama_tag}` : m.ollama_tag,
    tokens_24h: (m.prompt_tokens_24h || 0) + (m.completion_tokens_24h || 0),
    has_tokens: m.role !== 'embed', offline: !gwUp, disk_bytes: cloud ? null : dk(aiDisk, m.alias),
    downloaded: cloud || (gwUp ? llmDownloaded(m) : true), pulling: !cloud && llmPulling(m),
  })); });
  voice.forEach((m) => aiGrid.append(modelCard({
    ...m, source: 'voice', key: m.name, removable: false, subtitle: 'voice service',
    has_tokens: false, offline: !voiceUp, disk_bytes: dk(aiDisk, m.name),
  })));
  wrap.append(aiGrid);

  // Image models — kept on the page, but a separate count under their own label.
  const cached = (img && img.models) ? img.models.filter((m) => m.cached) : [];
  if (cached.length) {
    wrap.append(el('div', { class: 'model-subhead muted' }, 'Image models'));
    const imgGrid = el('div', { class: 'models-grid' });
    cached.forEach((m) => imgGrid.append(imageModelCard(m, img, dk(imgDisk, m.id))));
    wrap.append(imgGrid);
  }

  const total = llm.length + voice.length + cached.length;
  const empty = $('#models-empty');
  empty.hidden = total > 0;
  if (!total) empty.textContent = 'No models yet. Add one via “Add New models”.';
}

function imageModelCard(m, img, diskBytes) {
  const short = m.id.split('/').pop();
  const actions = el('div', { class: 'model-actions' });
  if (!m.cached) actions.append(el('button', { class: 'btn btn-sm btn-ghost', dataset: { imgDownload: m.id } }, 'Download'));
  if (img.running && img.url) actions.append(el('a', { class: 'btn btn-sm btn-ghost', href: img.url, target: '_blank', rel: 'noopener' }, 'Open Studio ↗'));

  const badges = el('div', { class: 'model-head-right' }, el('span', { class: 'role-badge role-vision' }, 'Image'));
  if (m.recommended) badges.append(el('span', { class: 'role-badge role-stt' }, 'Recommended'));
  if (m.license === 'noncommercial') badges.append(el('span', { class: 'role-badge' }, '⚠ non-commercial'));

  const stats = el('div', { class: 'model-stats' });
  if (diskBytes != null) stats.append(mstat(fmtBytes(diskBytes), 'disk'));
  stats.append(mstat(img.running ? 'up' : 'down', 'studio'));

  return el('div', { class: `model-card state-${m.cached ? 'running' : 'stopped'}` },
    el('div', { class: 'model-head' },
      el('div', { class: 'model-id' }, el('strong', {}, short), el('span', { class: 'model-alias mono' }, m.id)),
      badges),
    stats,
    actions,
  );
}

async function downloadImageModel(id, btn) {
  if (btn) btn.disabled = true;
  try {
    await api('/api/admin/image-models/download', { method: 'POST', body: { id } });
    toast('Downloading model… it will show as downloaded when done', 'success');
  } catch (e) { toast(e.message, 'error'); if (btn) btn.disabled = false; }
}

const ROLE_LABEL = { chat: 'Chat', vision: 'Vision', embed: 'Embed', stt: 'Voice→Text', tts: 'Text→Voice' };

function modelCard(m) {
  // Offline: the AI stack is stopped, so show the model as stopped with a Start
  // that brings the whole AI stack up (an LLM can't run without ollama+gateway).
  if (m.offline) {
    return el('div', { class: 'model-card state-stopped' },
      el('div', { class: 'model-head' },
        el('div', { class: 'model-id' },
          el('strong', {}, m.display_name || m.key),
          el('span', { class: 'model-alias mono' }, m.key)),
        el('div', { class: 'model-head-right' },
          m.cloud ? el('span', { class: 'role-badge role-cloud' }, 'cloud') : null,
          el('span', { class: `role-badge role-${m.role}` }, ROLE_LABEL[m.role] || m.role),
          el('span', { class: 'state-pill state-stopped' }, el('span', { class: 'state-dot' }), 'stopped')),
      ),
      el('div', { class: 'model-tag mono muted' }, m.subtitle || ''),
      m.cloud ? null : el('div', { class: 'model-stats' }, mstat(fmtBytes(m.disk_bytes || 0), 'disk')),
      el('div', { class: 'model-actions' },
        el('button', { class: 'btn btn-sm btn-primary', dataset: { startAi: '1' } }, 'Start'),
        el('span', { class: 'model-offline-hint muted' }, 'starts the AI models'),
      ),
    );
  }

  const ds = { modelAction: '', key: m.key, source: m.source };
  const act = (label, action, cls) => el('button',
    { class: `btn btn-sm ${cls || 'btn-ghost'}`, dataset: { ...ds, modelAction: action } }, label);

  // A gateway model whose weights aren't on disk yet is NOT startable — show its
  // download state (and a Download button if a pull isn't already running).
  const notReady = m.source === 'gateway' && m.downloaded === false;
  const dispState = notReady ? (m.pulling ? 'downloading' : 'missing') : m.state;
  const dispLabel = notReady ? (m.pulling ? 'downloading…' : 'not downloaded') : stateLabel(m.state);

  const actions = el('div', { class: 'model-actions' });
  if (notReady) {
    if (m.pulling) actions.append(el('span', { class: 'state-pill state-downloading' }, el('span', { class: 'state-dot' }), 'downloading…'));
    else actions.append(el('button', { class: 'btn btn-sm btn-primary', dataset: { modelPull: '1', key: m.key, tag: m.ollama_tag || m.subtitle || '', role: m.role || '' } }, 'Download'));
  } else {
    if (m.state === 'stopped')   actions.append(act('Start', 'start', 'btn-primary'));
    if (m.state === 'running')   actions.append(act('Suspend', 'suspend'), act('Shutdown', 'shutdown'));
    if (m.state === 'suspended') actions.append(act('Resume', 'resume', 'btn-primary'), act('Shutdown', 'shutdown'));
  }
  actions.append(el('button', { class: 'btn btn-sm btn-ghost', dataset: { modelMetrics: '1', key: m.key, source: m.source } }, 'Metrics'));
  if (m.removable) actions.append(el('button', { class: 'btn btn-sm btn-ghost danger', dataset: { modelRemove: '1', key: m.key } }, 'Remove'));

  // Disk always; RAM (in memory), requests, tokens only when non-zero.
  // Cloud models run elsewhere — no disk/RAM stats, usage counters only.
  const stats = el('div', { class: 'model-stats' });
  if (!m.cloud && m.disk_bytes != null) stats.append(mstat(fmtBytes(m.disk_bytes), 'disk'));
  if (!m.cloud && m.loaded) stats.append(mstat(m.resident_bytes ? fmtBytes(m.resident_bytes) : 'yes', 'in memory', 'ok'));
  if (m.requests_24h) stats.append(mstat(String(m.requests_24h), 'requests 24h'));
  if (m.has_tokens && m.tokens_24h) stats.append(mstat(fmtNum(m.tokens_24h), 'tokens 24h'));

  return el('div', { class: `model-card state-${dispState}`, dataset: { key: m.key } },
    el('div', { class: 'model-head' },
      el('div', { class: 'model-id' },
        el('strong', {}, m.display_name || m.key),
        el('span', { class: 'model-alias mono' }, m.key),
      ),
      el('div', { class: 'model-head-right' },
        m.cloud ? el('span', { class: 'role-badge role-cloud' }, 'cloud') : null,
        el('span', { class: `role-badge role-${m.role}` }, ROLE_LABEL[m.role] || m.role),
        el('span', { class: `state-pill state-${dispState}` }, el('span', { class: 'state-dot' }), dispLabel),
      ),
    ),
    el('div', { class: 'model-tag mono muted' }, m.subtitle || ''),
    stats,
    actions,
  );
}

function mstat(value, label, cls = '') {
  return el('div', { class: `mstat ${cls}` },
    el('span', { class: 'mstat-v' }, value),
    el('span', { class: 'mstat-l' }, label));
}

const modelBase = (source) => source === 'voice' ? '/api/admin/voice-models' : '/api/admin/models';

async function onModelClick(e) {
  const btn = e.target.closest('button');
  if (!btn) return;
  const { key, source } = btn.dataset;
  if (btn.dataset.startAi)      return startAiFromModel(btn);
  if (btn.dataset.imgDownload)  return downloadImageModel(btn.dataset.imgDownload, btn);
  if (btn.dataset.modelPull)    return pullModel(btn.dataset.tag, btn.dataset.role, btn);
  if (btn.dataset.modelAction)  return doModelAction(key, btn.dataset.modelAction, source, btn);
  if (btn.dataset.modelMetrics) return openMetricsDialog(key, source);
  if (btn.dataset.modelRemove)  return removeModel(key);
}

async function startAiFromModel(btn) {
  if (btn) btn.disabled = true;
  try {
    await api('/api/admin/services/ai/start', { method: 'POST' });
    toast('AI models starting… (Image Studio stopped to free memory)', 'success');
    _modelsSig = '';
    setTimeout(() => refreshModels(), 2500);
    setTimeout(() => { _modelsSig = ''; refreshModels(); }, 9000);
  } catch (e) { toast(e.message, 'error'); if (btn) btn.disabled = false; }
}

async function pullModel(tag, role, btn) {
  if (!tag) return toast('No model tag to download', 'error');
  if (btn) { btn.disabled = true; btn.textContent = 'downloading…'; }
  try {
    await api('/api/admin/models/pull', { method: 'POST', body: { tag } });
    toast('Downloading…', 'success');
    trackPull(tag, btn);
    _modelsSig = ''; await refreshModels();
  } catch (e) { toast(e.message, 'error'); if (btn) { btn.disabled = false; btn.textContent = 'Download'; } }
}

async function doModelAction(key, action, source, btn) {
  if (btn) btn.disabled = true;
  try {
    await api(`${modelBase(source)}/${encodeURIComponent(key)}/${action}`, { method: 'POST' });
    const verb = { start: 'started', suspend: 'suspended', resume: 'resumed', shutdown: 'shut down' }[action] || action;
    toast(`Model ${verb}`, 'success');
    _modelsSig = '';
    await refreshModels();
  } catch (e) { toast(e.message, 'error'); if (btn) btn.disabled = false; }
}

async function removeModel(alias) {
  if (!confirm(`Remove "${alias}" from the registry?\nThis unloads it and stops managing it, but does not delete the downloaded model files.`)) return;
  try {
    await api(`/api/admin/models/${encodeURIComponent(alias)}`, { method: 'DELETE' });
    toast('Model removed', 'success');
    _modelsSig = '';
    await refreshModels();
  } catch (e) { toast(e.message, 'error'); }
}

/* ===========================================================================
 * CLOUD PROVIDERS (admin) — BYO-key Anthropic/OpenAI via the gateway.
 * Keys are write-only: set here, never displayed back (masked hint only).
 * ========================================================================= */
const CLOUD_MODEL_PLACEHOLDER = { anthropic: 'claude-opus-4-8', openai: 'gpt-4o' };
const CLOUD_KEY_PLACEHOLDER = { anthropic: 'sk-ant-…', openai: 'sk-…' };

function renderProviders(prov) {
  const wrap = $('#providers-section');
  if (!wrap) return;
  wrap.innerHTML = '';
  if (!prov || !Array.isArray(prov.providers) || !prov.providers.length) return; // gateway down
  wrap.append(el('div', { class: 'model-subhead muted' }, 'Cloud providers'));
  wrap.append(el('p', { class: 'muted providers-hint' },
    'Bring your own API key. Cloud chats leave your home — models are visible only to admins, and API keys need a per-key opt-in on the Keys page.'));
  const grid = el('div', { class: 'providers-grid' });
  prov.providers.forEach((p) => grid.append(providerCard(p)));
  wrap.append(grid);
}

function providerCard(p) {
  const budgetInput = el('input', {
    class: 'field', type: 'number', min: '0', step: '1000',
    value: String(p.monthly_token_budget || 0), title: 'Monthly token budget (0 = unlimited)',
  });
  const usage = p.monthly_token_budget
    ? `${fmtNum(p.month_usage_tokens || 0)} of ${fmtNum(p.monthly_token_budget)} tokens this month`
    : `${fmtNum(p.month_usage_tokens || 0)} tokens this month · no budget cap`;

  return el('div', { class: `svc-card provider-card ${p.enabled ? 'on' : 'off'}` },
    el('div', { class: 'svc-head' },
      el('span', { class: `svc-dot ${p.enabled ? 'on' : 'off'}` }),
      el('strong', {}, PROVIDER_LABEL[p.name] || p.name),
      el('span', { class: 'role-badge role-cloud' }, 'cloud'),
    ),
    el('div', { class: 'svc-status muted' },
      p.has_key ? el('span', {}, 'key ', el('code', { class: 'mono' }, p.key_hint || '••••')) : 'no API key set',
      ` · ${p.enabled ? 'enabled' : 'disabled'}`,
    ),
    el('div', { class: 'provider-usage muted' }, usage),
    el('div', { class: 'provider-budget-row' },
      el('label', { class: 'field-label' }, 'Budget'),
      budgetInput,
      el('button', {
        class: 'btn btn-sm btn-ghost', type: 'button',
        onclick: () => saveProviderBudget(p.name, budgetInput.value),
      }, 'Save'),
    ),
    el('div', { class: 'svc-actions' },
      el('button', {
        class: 'btn btn-sm btn-ghost', type: 'button',
        onclick: () => openProviderKeyDialog(p),
      }, p.has_key ? 'Replace API key' : 'Set API key'),
      el('button', {
        class: `btn btn-sm ${p.enabled ? 'btn-ghost' : 'btn-primary'}`, type: 'button',
        onclick: () => toggleProvider(p),
      }, p.enabled ? 'Disable' : 'Enable'),
      p.enabled && p.has_key ? el('button', {
        class: 'btn btn-sm btn-ghost', type: 'button',
        onclick: () => openAddCloudModelDialog(p),
      }, 'Add cloud model') : null,
    ),
  );
}

function openProviderKeyDialog(p) {
  const input = el('input', {
    class: 'field', type: 'password', autocomplete: 'off',
    placeholder: CLOUD_KEY_PLACEHOLDER[p.name] || 'API key', required: true,
  });
  const form = el('form', { class: 'add-model-form' },
    el('h3', {}, `${PROVIDER_LABEL[p.name] || p.name} API key`),
    el('p', { class: 'muted' },
      'Stored encrypted on the gateway and never shown again — only a masked hint.'),
    el('label', { class: 'field-label' }, 'API key'),
    input,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, 'Save key'),
    ),
  );
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const api_key = input.value.trim();
    if (!api_key) return;
    try {
      await api(`/api/admin/providers/${encodeURIComponent(p.name)}/key`, { method: 'PUT', body: { api_key } });
      closeModal();
      toast('API key saved', 'success');
      _modelsSig = ''; refreshModels();
    } catch (err) { toast(err.message, 'error'); }
  });
  openModal(form);
  input.focus();
}

async function toggleProvider(p) {
  const enabled = !p.enabled;
  if (enabled && !p.has_key) return toast('Set an API key first', 'error');
  try {
    await api(`/api/admin/providers/${encodeURIComponent(p.name)}/enable`, { method: 'POST', body: { enabled } });
    toast(`${PROVIDER_LABEL[p.name] || p.name} ${enabled ? 'enabled' : 'disabled'}`, 'success');
    _modelsSig = ''; refreshModels();
  } catch (e) { toast(e.message, 'error'); }
}

async function saveProviderBudget(name, value) {
  const monthly_token_budget = Math.max(0, Math.floor(Number(value) || 0));
  try {
    await api(`/api/admin/providers/${encodeURIComponent(name)}/budget`, { method: 'PUT', body: { monthly_token_budget } });
    toast(monthly_token_budget ? `Budget set: ${fmtNum(monthly_token_budget)} tokens/month` : 'Budget removed (unlimited)', 'success');
    _modelsSig = ''; refreshModels();
  } catch (e) { toast(e.message, 'error'); }
}

function openAddCloudModelDialog(p) {
  const example = CLOUD_MODEL_PLACEHOLDER[p.name] || 'model-id';
  const aliasInput = el('input', { class: 'field', type: 'text', placeholder: `client alias, e.g. ${example}`, required: true });
  const upstreamInput = el('input', { class: 'field', type: 'text', placeholder: `provider model id, e.g. ${example}`, required: true });
  const nameInput = el('input', { class: 'field', type: 'text', placeholder: 'display name (optional)' });
  upstreamInput.addEventListener('input', () => {
    if (!aliasInput.value) aliasInput.value = upstreamInput.value.replace(/[^A-Za-z0-9._-]/g, '-');
  });
  const form = el('form', { class: 'add-model-form' },
    el('h3', {}, `Add ${PROVIDER_LABEL[p.name] || p.name} model`),
    el('p', { class: 'muted' }, 'Chats with this model are sent to the provider, outside your home.'),
    el('label', { class: 'field-label' }, 'Provider model id'),
    upstreamInput,
    el('label', { class: 'field-label' }, 'Alias (client-facing name)'),
    aliasInput,
    el('label', { class: 'field-label' }, 'Display name'),
    nameInput,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'button', class: 'btn btn-ghost', onclick: closeModal }, 'Cancel'),
      el('button', { type: 'submit', class: 'btn btn-primary' }, 'Add cloud model'),
    ),
  );
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const alias = aliasInput.value.trim();
    const upstream_model = upstreamInput.value.trim();
    const display_name = nameInput.value.trim();
    if (!alias || !upstream_model) return;
    try {
      await api(`/api/admin/providers/${encodeURIComponent(p.name)}/models`, {
        method: 'POST',
        body: display_name ? { alias, upstream_model, display_name } : { alias, upstream_model },
      });
      closeModal();
      toast('Cloud model added', 'success');
      _modelsSig = ''; refreshModels();
    } catch (err) { toast(err.message, 'error'); }
  });
  openModal(form);
  upstreamInput.focus();
}


function resCard(label, value, pct) {
  const kids = [el('span', { class: 'res-label' }, label), el('span', { class: 'res-value' }, value)];
  if (pct != null) kids.push(el('div', { class: 'res-meter' },
    el('div', { class: 'res-meter-fill', style: `width:${Math.max(2, Math.min(100, pct))}%` })));
  return el('div', { class: 'res-card' }, ...kids);
}

async function openMetricsDialog(key, source) {
  let data;
  try { data = await api(`${modelBase(source)}/${encodeURIComponent(key)}/metrics?hours=24&bucket=hour`); }
  catch (e) { toast(e.message, 'error'); return; }
  const series = data.series || [];
  const totals = data.totals || {};
  const hasTokens = 'prompt_tokens' in totals || series.some((s) => 'prompt_tokens' in s);
  const hour = (ts) => (ts || '').slice(11) + ':00';

  const maxReq = Math.max(1, ...series.map((s) => s.requests || 0));
  const reqBars = el('div', { class: 'hist' },
    ...series.map((s) => bar(s.requests || 0, maxReq, `${hour(s.ts)} · ${s.requests || 0} req`)));

  const totalsRow = el('div', { class: 'metrics-totals' }, mstat(fmtNum(totals.requests || 0), 'requests'));
  const blocks = [el('div', { class: 'hist-block' }, el('span', { class: 'hist-title muted' }, 'Requests / hour'), reqBars)];

  if (hasTokens) {
    const maxTok = Math.max(1, ...series.map((s) => (s.prompt_tokens || 0) + (s.completion_tokens || 0)));
    const tokBars = el('div', { class: 'hist' },
      ...series.map((s) => { const t = (s.prompt_tokens || 0) + (s.completion_tokens || 0); return bar(t, maxTok, `${hour(s.ts)} · ${fmtNum(t)} tokens`); }));
    totalsRow.append(
      mstat(fmtNum(totals.prompt_tokens || 0), 'prompt tok'),
      mstat(fmtNum(totals.completion_tokens || 0), 'completion tok'),
      mstat(fmtNum((totals.prompt_tokens || 0) + (totals.completion_tokens || 0)), 'total tok'),
    );
    blocks.push(el('div', { class: 'hist-block' }, el('span', { class: 'hist-title muted' }, 'Tokens / hour'), tokBars));
  }

  const body = el('div', { class: 'metrics-dialog' },
    el('h3', {}, `${key} — last 24 hours`),
    totalsRow,
    ...blocks,
    el('div', { class: 'modal-actions' }, el('button', { class: 'btn btn-primary', onclick: closeModal }, 'Close')),
  );
  openModal(body);
}

function bar(v, max, title) {
  const h = Math.round(3 + (v > 0 ? (v / max) * 54 : 0));
  return el('div', { class: `bar${v > 0 ? '' : ' bar-zero'}`, title, style: `height:${h}px` });
}

// Curated, CPU-friendly language models with purpose + minimum RAM.
const LLM_CATALOG = [
  { name: 'Qwen2.5 7B Instruct', tag: 'qwen2.5:7b-instruct-q4_K_M', role: 'chat', purpose: 'General chat — balanced default', ram: '~5 GB', license: 'Apache-2.0' },
  { name: 'Qwen2.5 14B Instruct', tag: 'qwen2.5:14b-instruct-q4_K_M', role: 'chat', purpose: 'Smarter chat (best with 32 GB)', ram: '~9 GB', license: 'Apache-2.0' },
  { name: 'Phi-4 (14B)', tag: 'phi4', role: 'chat', purpose: 'Strong reasoning, cleanest license', ram: '~9 GB', license: 'MIT' },
  { name: 'Llama 3.2 3B', tag: 'llama3.2:3b', role: 'chat', purpose: 'Fast & light chat / tools', ram: '~2.5 GB', license: 'Llama' },
  { name: 'Qwen2.5-Coder 7B', tag: 'qwen2.5-coder:7b', role: 'chat', purpose: 'Coding assistant', ram: '~5 GB', license: 'Apache-2.0' },
  { name: 'moondream (vision)', tag: 'moondream', role: 'vision', purpose: 'Image understanding / captions', ram: '~2 GB', license: 'Apache-2.0' },
  { name: 'nomic-embed-text', tag: 'nomic-embed-text', role: 'embed', purpose: 'Embeddings for search / RAG', ram: '~0.3 GB', license: 'Apache-2.0' },
];

// One row in the "Add new models" screen: name + badges + id + purpose·min-req + action.
function catalogRow(o) {
  const badges = [];
  if (o.recommended) badges.push(el('span', { class: 'role-badge role-stt' }, 'Recommended'));
  if (o.license) badges.push(el('span', { class: `role-badge${o.license === 'non-commercial' ? '' : ' role-embed'}` }, o.license));
  const right = o.cached
    ? el('span', { class: 'addm-done' }, o.cachedLabel || '✓ downloaded')
    : (o.action ? el('button', { class: 'btn btn-sm btn-primary', type: 'button', dataset: o.action.ds }, o.action.label) : null);
  return el('div', { class: 'addm-row' },
    el('div', { class: 'addm-row-id' },
      el('div', { class: 'addm-row-title' }, el('strong', {}, o.title), ...badges),
      el('span', { class: 'model-alias mono' }, o.sub),
      el('span', { class: 'addm-purpose muted' }, `${o.purpose} · min ${o.ram}`),
    ),
    right,
  );
}

async function catalogAddClick(e) {
  const b = e.target.closest('button[data-add-img], button[data-add-llm]');
  if (!b) return;
  const orig = b.textContent;
  b.disabled = true; b.textContent = '…';
  try {
    if (b.dataset.addImg) {
      await api('/api/admin/image-models/download', { method: 'POST', body: { id: b.dataset.addImg } });
      toast('Downloading… it appears on the Models page when done', 'success'); b.textContent = 'downloading…';
    } else {
      const tag = b.dataset.addLlm, role = b.dataset.addLlmRole;
      const alias = tag.replace(/:/g, '-').replace(/[^A-Za-z0-9._-]/g, '-');
      const res = await api('/api/admin/models', { method: 'POST', body: { alias, ollama_tag: tag, role, pull: true } });
      if (res.pulling) {
        b.textContent = 'downloading…';       // stays disabled; trackPull flips it to "✓ installed"
        toast('Added — downloading…', 'success');
        trackPull(tag, b);
      } else {
        b.textContent = '✓ installed'; toast('Added', 'success');
      }
      _modelsSig = ''; refreshModels();
    }
  } catch (err) { toast(err.message, 'error'); b.disabled = false; b.textContent = orig; }
}

async function openAddModelDialog() {
  let installed = [], imgCat = { models: [] };
  try { const d = await api('/api/admin/ollama/installed'); installed = (d && d.installed) || []; }
  catch (e) { /* still allow manual entry */ }
  try { imgCat = await api('/api/admin/image-models'); } catch (e) { /* image studio optional */ }
  const available = installed.filter((i) => !i.registered);
  const registered = new Set(installed.map((i) => i.tag));

  // Image models (purpose + min RAM from the backend).
  const imgList = el('div', { class: 'addm-imglist' });
  (imgCat.models || []).forEach((m) => imgList.append(catalogRow({
    title: m.id.split('/').pop(), sub: m.id, purpose: m.purpose, ram: `${m.min_ram_gb} GB RAM`,
    license: m.license === 'non-commercial' ? 'non-commercial' : m.license,
    recommended: m.recommended, cached: m.cached,
    action: m.cached ? null : { label: 'Download', ds: { addImg: m.id } },
  })));
  imgList.addEventListener('click', catalogAddClick);

  // Curated language models.
  const llmList = el('div', { class: 'addm-imglist' });
  LLM_CATALOG.forEach((m) => {
    const inst = registered.has(m.tag) || registered.has(m.tag + ':latest');
    llmList.append(catalogRow({
      title: m.name, sub: m.tag, purpose: m.purpose, ram: m.ram, license: m.license,
      cached: inst, cachedLabel: '✓ installed',
      action: inst ? null : { label: 'Add', ds: { addLlm: m.tag, addLlmRole: m.role } },
    }));
  });
  llmList.addEventListener('click', catalogAddClick);

  const tagSelect = el('select', { class: 'field' },
    el('option', { value: '' }, available.length ? '— pick an installed model —' : '(no unregistered models installed)'),
    ...available.map((i) => el('option', { value: i.tag }, `${i.tag}  (${fmtBytes(i.size_bytes)})`)),
  );
  const tagInput = el('input', { class: 'field', type: 'text', placeholder: 'or type a tag, e.g. llama3.2:3b' });
  const pullChk = el('input', { type: 'checkbox' });
  const aliasInput = el('input', { class: 'field', type: 'text', placeholder: 'client alias, e.g. llama3.2-3b' });
  const nameInput = el('input', { class: 'field', type: 'text', placeholder: 'display name (optional)' });
  const roleSelect = el('select', { class: 'field' },
    ...[['auto', 'Auto-detect'], ['chat', 'Chat'], ['vision', 'Vision'], ['embed', 'Embed']]
      .map(([v, l]) => el('option', { value: v }, l)));

  const deriveAlias = (tag) => tag.replace(/:/g, '-').replace(/[^A-Za-z0-9._-]/g, '-');
  tagSelect.addEventListener('change', () => {
    if (tagSelect.value) { tagInput.value = ''; if (!aliasInput.value) aliasInput.value = deriveAlias(tagSelect.value); }
  });
  tagInput.addEventListener('input', () => {
    if (tagInput.value && !aliasInput.value) aliasInput.value = deriveAlias(tagInput.value);
  });

  const form = el('form', { class: 'add-model-form' },
    el('label', { class: 'field-label' }, 'Installed model'),
    tagSelect,
    el('label', { class: 'field-label' }, 'Or Ollama tag'),
    tagInput,
    el('label', { class: 'priv-check pull-check' }, pullChk, el('span', {}, ' Download now if not installed (can take several minutes)')),
    el('label', { class: 'field-label' }, 'Alias (client-facing name)'),
    aliasInput,
    el('label', { class: 'field-label' }, 'Display name'),
    nameInput,
    el('label', { class: 'field-label' }, 'Role'),
    roleSelect,
    el('div', { class: 'modal-actions' },
      el('button', { type: 'submit', class: 'btn btn-primary' }, 'Add language model'),
    ),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const ollama_tag = (tagSelect.value || tagInput.value || '').trim();
    const alias = aliasInput.value.trim();
    const display_name = nameInput.value.trim();
    const pull = pullChk.checked;
    const role = roleSelect.value === 'auto' ? undefined : roleSelect.value;
    if (!ollama_tag) return toast('Pick or type a model tag', 'error');
    if (!alias) return toast('Enter an alias', 'error');
    try {
      const res = await api('/api/admin/models', { method: 'POST', body: { alias, ollama_tag, display_name, role, pull } });
      closeModal();
      toast(res.pulling ? 'Model registered — downloading…' : 'Model added', 'success');
      if (res.pulling) trackPull(ollama_tag);
      _modelsSig = '';
      await refreshModels();
    } catch (err) { toast(err.message, 'error'); }
  });

  openModal(el('div', { class: 'addm-dialog' },
    el('h3', {}, 'Add new models'),
    el('p', { class: 'muted' }, 'Requirements are for this CPU box — RAM is the limit. Image models run in the Image Studio; language models run in the AI stack.'),
    el('div', { class: 'addm-section-title' }, 'Image models — Image Studio'),
    imgList,
    el('hr', { class: 'addm-sep' }),
    el('div', { class: 'addm-section-title' }, 'Language models — AI stack'),
    el('p', { class: 'muted addm-hint' }, 'The AI models must be running to add these.'),
    llmList,
    el('hr', { class: 'addm-sep' }),
    el('div', { class: 'addm-section-title' }, 'Custom / advanced'),
    form,
    el('div', { class: 'modal-actions' }, el('button', { class: 'btn btn-ghost', onclick: closeModal }, 'Close')),
  ));
}

function trackPull(tag, btn) {
  PULLING.add(tag);                     // marks the model card as "downloading…"
  const finish = (ok, msg) => {
    PULLING.delete(tag);
    if (btn) { btn.disabled = ok; btn.textContent = ok ? '✓ installed' : 'Retry'; }
    if (msg) toast(msg, ok ? 'success' : 'error');
    _modelsSig = ''; refreshModels();   // re-render: Start now appears (or the Download button returns)
  };
  const iv = setInterval(async () => {
    let s;
    try { s = await api(`/api/admin/models/pull/status?tag=${encodeURIComponent(tag)}`); }
    catch { clearInterval(iv); finish(false); return; }
    if (btn && btn.textContent.startsWith('downloading') && s.percent != null) btn.textContent = `downloading ${Math.round(s.percent)}%`;
    if (s.status === 'done') { clearInterval(iv); finish(true, `${tag} downloaded`); }
    else if (s.status === 'error') { clearInterval(iv); finish(false, `Download failed: ${s.detail || 'error'}`); }
  }, 3000);
  setTimeout(() => clearInterval(iv), 30 * 60 * 1000);
}

/* ---- small formatters shared by the models dashboard ---- */
function stateLabel(s) { return { running: 'Running', suspended: 'Suspended', stopped: 'Stopped' }[s] || s; }
function fmtNum(n) { return (Number(n) || 0).toLocaleString(); }
function fmtBytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB']; let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`;
}

/* ===========================================================================
 * STUDIO — art/animation asset pipeline (manifest-backed)
 * ========================================================================= */
let STUDIO = [];
let _riveWasmSet = false;

function wireStudio() {
  $('#studio-import').addEventListener('click', importStudio);
  $('#studio-upload-input').addEventListener('change', uploadStudioImage);
  $('#studio-list').addEventListener('click', onStudioClick);
  $('#studio-list').addEventListener('change', onStudioChange);
}

async function loadStudio() {
  if (!can('files_write')) return;
  try { const d = await api('/api/studio/assets'); STUDIO = (d && d.assets) || []; }
  catch (e) { toast(e.message, 'error'); STUDIO = []; }
  renderStudio();
}

function renderStudio() {
  const wrap = $('#studio-list');
  wrap.innerHTML = '';
  $('#studio-empty').hidden = STUDIO.length > 0;
  for (const a of STUDIO) wrap.append(studioCard(a));
}

function studioCard(a) {
  const catalog = el('input', { class: 'field studio-field', type: 'text', value: a.catalogId || '',
    placeholder: 'catalog id (e.g. anml-owl)', dataset: { metaField: 'catalogId', id: a.id } });
  const games = el('input', { class: 'field studio-field', type: 'text', value: (a.games || []).join(', '),
    placeholder: 'games (comma-separated)', dataset: { metaField: 'games', id: a.id } });

  const actions = el('div', { class: 'studio-actions' },
    el('button', { class: 'btn btn-sm btn-ghost', dataset: { studioAnimate: '1', id: a.id } }, 'Auto-animate'),
    el('label', { class: 'btn btn-sm btn-ghost', for: `riv-${a.id}` }, 'Upload .riv'),
    el('input', { id: `riv-${a.id}`, type: 'file', accept: '.riv', hidden: true, dataset: { studioRiv: '1', id: a.id } }),
    el('a', { class: 'btn btn-sm btn-ghost', href: 'https://editor.rive.app', target: '_blank', rel: 'noopener' }, 'Open in Rive ↗'),
    a.status === 'ready'
      ? el('button', { class: 'btn btn-sm btn-ghost', dataset: { studioStatus: 'draft', id: a.id } }, 'Unapprove')
      : el('button', { class: 'btn btn-sm btn-primary', dataset: { studioStatus: 'ready', id: a.id } }, 'Approve'),
    a.animationType
      ? el('button', { class: 'btn btn-sm btn-ghost', dataset: { studioRemoveAnim: '1', id: a.id } }, 'Remove animation')
      : null,
    el('button', { class: 'btn btn-sm btn-ghost danger', dataset: { studioDel: '1', id: a.id } }, 'Delete asset'),
  );

  return el('div', { class: `studio-card status-${a.status}`, dataset: { id: a.id } },
    el('div', { class: 'studio-preview' }, studioPreview(a)),
    el('div', { class: 'studio-meta' },
      el('div', { class: 'studio-head' },
        el('strong', {}, a.name || a.id),
        el('span', { class: `state-pill studio-${a.status}` }, el('span', { class: 'state-dot' }), a.status),
      ),
      el('div', { class: 'studio-tag muted' }, a.animationType ? `animation: ${a.animationType}` : 'no animation yet'),
      catalog,
      games,
    ),
    actions,
  );
}

function studioPreview(a) {
  if (a.animationType === 'rive' && a.animation_url && window.rive) {
    const canvas = el('canvas', { class: 'studio-canvas', width: 320, height: 240 });
    setTimeout(() => mountRive(canvas, a.animation_url), 0);
    return canvas;
  }
  const url = a.animation_url || a.source_url || '';
  return el('img', { class: 'studio-img', src: url, alt: a.name || '' });
}

function mountRive(canvas, src) {
  if (!window.rive) return;
  try {
    if (!_riveWasmSet) { window.rive.RuntimeLoader.setWasmUrl('/static/vendor/rive/rive.wasm'); _riveWasmSet = true; }
    new window.rive.Rive({ src, canvas, autoplay: true, layout: new window.rive.Layout({ fit: window.rive.Fit.contain }) });
  } catch (e) { /* leave the canvas blank; a .riv download still works via the file URL */ }
}

async function importStudio() {
  try {
    const r = await api('/api/studio/import-generated', { method: 'POST' });
    toast(r.count ? `Imported ${r.count} image${r.count > 1 ? 's' : ''}` : 'No new images in the Image Studio output', r.count ? 'success' : 'info');
    loadStudio();
  } catch (e) { toast(e.message, 'error'); }
}

async function uploadStudioImage(e) {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const fd = new FormData(); fd.append('file', f);
  try { await api('/api/studio/upload', { method: 'POST', body: fd }); toast('Image added', 'success'); }
  catch (err) { toast(err.message, 'error'); }
  e.target.value = '';
  loadStudio();
}

async function onStudioClick(e) {
  const btn = e.target.closest('button');
  if (!btn) return;
  const id = btn.dataset.id;
  if (btn.dataset.studioAnimate) {
    btn.disabled = true;
    try { await api(`/api/studio/${id}/animate`, { method: 'POST' }); toast('Motion generated', 'success'); loadStudio(); }
    catch (err) { toast(err.message, 'error'); btn.disabled = false; }
    return;
  }
  if (btn.dataset.studioStatus) {
    try { await api(`/api/studio/${id}/meta`, { method: 'POST', body: { status: btn.dataset.studioStatus } }); loadStudio(); }
    catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (btn.dataset.studioRemoveAnim) {
    try {
      await api(`/api/studio/${id}/remove-animation`, { method: 'POST' });
      toast('Animation removed — the static image is back', 'success');
      loadStudio();
    } catch (err) { toast(err.message, 'error'); }
    return;
  }
  if (btn.dataset.studioDel) {
    if (!confirm('Delete this whole asset from the Studio (its copy + any animation)?\nYour original image in the Image Studio is kept.')) return;
    try { await api(`/api/studio/${id}/delete`, { method: 'POST' }); toast('Asset deleted', 'success'); loadStudio(); }
    catch (err) { toast(err.message, 'error'); }
  }
}

async function onStudioChange(e) {
  const inp = e.target;
  if (inp.dataset && inp.dataset.studioRiv) {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    try { await api(`/api/studio/${inp.dataset.id}/rive`, { method: 'POST', body: fd }); toast('.riv uploaded', 'success'); loadStudio(); }
    catch (err) { toast(err.message, 'error'); }
    inp.value = '';
    return;
  }
  if (inp.dataset && inp.dataset.metaField) {
    const { id, metaField } = inp.dataset;
    const body = {};
    if (metaField === 'games') body.games = inp.value.split(',').map((s) => s.trim()).filter(Boolean);
    else body[metaField] = inp.value;
    try { await api(`/api/studio/${id}/meta`, { method: 'POST', body }); toast('Saved', 'success', 1200); }
    catch (err) { toast(err.message, 'error'); }
  }
}
