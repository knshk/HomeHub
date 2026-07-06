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
  try {
    const me = await api('/api/me');
    onMe(me);
  } catch (e) {
    if (e.status === 401) showGate();
    else { showGate(); }
  }
}

/* ---- theme ---- */
function initTheme() {
  const saved = localStorage.getItem('hub-theme');
  if (saved) document.documentElement.dataset.theme = saved;
  $('#theme-toggle')?.addEventListener('click', () => {
    const cur = document.documentElement.dataset.theme;
    const next = cur === 'dark' ? 'light' : cur === 'light' ? 'auto' : 'dark';
    document.documentElement.dataset.theme = next;
    localStorage.setItem('hub-theme', next);
  });
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

  // Gate: admin claim
  $('#gate-admin-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = $('#gate-admin-username').value.trim();
    const admin_token = $('#gate-admin-token').value;
    if (!username || !admin_token) return;
    await gateAction(() => api('/api/session/claim', { method: 'POST', body: { username, admin_token } }));
  });

  // Tabs
  $('#tabs').addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (btn && !btn.disabled) selectTab(btn.dataset.tab);
  });

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
  wireFiles();
  wireKeys();
  wireAdmin();
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
}

/* ===========================================================================
 * Apply /api/me -> render tabs/privileges
 * ========================================================================= */
function onMe(me) {
  State.me = me;
  State.privileges = new Set(me.privileges || []);

  $('#gate').hidden = true;
  $('#app').hidden = false;

  // user badge
  $('#me-badge').textContent = `${me.username} · ${me.role}`;
  $('#me-badge').dataset.role = me.role;

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

  // Pick first visible tab
  const firstVisible = $$('#tabs .tab').find((t) => !t.hidden);
  if (firstVisible) selectTab(firstVisible.dataset.tab);
  else {
    // No privileges yet (pending guest with nothing) — show banner only
    $$('.panel').forEach((p) => (p.hidden = true));
  }
}

function tabAllowed(tab) {
  const btn = $(`#tabs .tab[data-tab="${tab}"]`);
  if (!btn) return false;
  if (btn.dataset.role === 'admin') return isAdmin();
  // files tab needs files_read OR photos_read
  if (tab === 'files') return can('files_read') || can('photos_read');
  return can(btn.dataset.priv);
}

function renderTabs() {
  $$('#tabs .tab').forEach((btn) => {
    const tab = btn.dataset.tab;
    btn.hidden = !tabAllowed(tab);
  });
}

function selectTab(tab) {
  if (!tabAllowed(tab)) return;
  State.activeTab = tab;
  $$('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.panel').forEach((p) => (p.hidden = p.dataset.tab !== tab));

  switch (tab) {
    case 'chat':       loadConversations(); break;
    case 'notes':      loadNotes(); break;
    case 'checklists': loadChecklists(); break;
    case 'files':      loadFiles(); break;
    case 'keys':       loadKeys(); break;
    case 'admin':      loadDevices(); break;
  }
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
  const autosize = () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 180) + 'px'; };
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#chat-form').requestSubmit(); }
  });

  $('#chat-form').addEventListener('submit', (e) => { e.preventDefault(); sendMessage(); });
  $('#chat-stop').addEventListener('click', () => { if (State.chatAbort) State.chatAbort.abort(); });
}

async function loadConversations() {
  if (!can('chat')) return;
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
  const input = $('#chat-input');
  const content = input.value.trim();
  if (!content) return;

  if (!State.activeConvId) {
    // auto-create a conversation
    try {
      const conv = await api('/api/conversations', { method: 'POST', body: { title: content.slice(0, 40) } });
      State.conversations.unshift(conv);
      State.activeConvId = conv.id;
      $('#chat-messages').innerHTML = '';
      renderConversationList();
    } catch (e) { toast(e.message, 'error'); return; }
  }

  // Did this turn originate from a voice message? (consume the flag)
  const fromVoice = State.nextMsgFromVoice;
  State.nextMsgFromVoice = false;
  let replyOk = false;

  input.value = '';
  input.style.height = 'auto';
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
      body: { content, stream: true },
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

async function loadKeys() {
  if (!can('api_keys')) return;
  try { KEYS = await api('/api/keys') || []; }
  catch (e) { toast(e.message, 'error'); KEYS = []; }
  renderKeys();
}

function renderKeys() {
  const wrap = $('#keys-list');
  wrap.innerHTML = '';
  $('#keys-empty').hidden = KEYS.length > 0;
  for (const k of KEYS) {
    const revoked = !!k.revoked;
    wrap.append(el('div', { class: 'key-row' + (revoked ? ' revoked' : ''), dataset: { keyId: k.id } },
      el('div', { class: 'key-main' },
        el('div', { class: 'key-name' }, k.name || 'key'),
        el('div', { class: 'key-sub' },
          el('code', {}, (k.key_prefix || '') + '…'),
          k.created_at ? el('span', { class: 'muted' }, ` · ${fmtDate(k.created_at)}`) : null,
          revoked ? el('span', { class: 'tag tag-pending' }, 'revoked') : null,
        ),
      ),
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
  const d = new Date(s);
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
          el('button', { class: 'btn btn-ghost btn-sm danger', dataset: { devRevoke: '1' } }, 'Revoke'),
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
