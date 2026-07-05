// API client. The backend base URL is configurable so the GitHub Pages frontend
// and local dev can target different backends:
//   frontend/.env.production  -> Cloud Run URL   (cross-origin, CORS)
//   frontend/.env.development -> http://localhost:8080
// Empty/unset falls back to a relative /api (same-origin, e.g. Cloud Run serving
// the SPA itself).
const API = `${(import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')}/api`;

async function handle(res) {
  if (res.ok) return res.status === 204 ? null : res.json();
  let payload;
  try { payload = await res.json(); } catch { payload = {}; }
  const err = new Error(payload.message || payload.error || 'خطأ في الاتصال');
  err.code = payload.error || `http_${res.status}`;
  err.status = res.status;
  throw err;
}

const jsonHeaders = (token) => {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-Debater-Token'] = token;
  return h;
};

export const api = {
  createRoom: (topic) =>
    fetch(`${API}/rooms`, { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ topic }) }).then(handle),

  // Token identifies the poller so the server can track presence («غير متصل»).
  getRoom: (code, token) =>
    fetch(`${API}/rooms/${code}`, token ? { headers: { 'X-Debater-Token': token } } : undefined).then(handle),

  joinRoom: (code, { name, claim, consent }) =>
    fetch(`${API}/rooms/${code}/join`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ name, claim, consent }),
    }).then(handle),

  setClaim: (code, token, { name, claim }) =>
    fetch(`${API}/rooms/${code}/claim`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify({ name, claim }),
    }).then(handle),

  setFormat: (code, token, roundsPerSide) =>
    fetch(`${API}/rooms/${code}/format`, {
      method: 'POST', headers: jsonHeaders(token),
      body: JSON.stringify({ rounds_per_side: roundsPerSide }),
    }).then(handle),

  // Join as a named spectator (read-only; listed in the spectator strip).
  spectate: (code, name) =>
    fetch(`${API}/rooms/${code}/spectate`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ name }),
    }).then(handle),

  // Creator rewords the topic (pre-debate only; resets both ready flags).
  setTopic: (code, token, topic) =>
    fetch(`${API}/rooms/${code}/topic`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify({ topic }),
    }).then(handle),

  // Start the server-stamped speaking clock (fired when the mic is tapped).
  startTurn: (code, token) =>
    fetch(`${API}/rooms/${code}/turns/start`, { method: 'POST', headers: jsonHeaders(token) }).then(handle),

  ready: (code, token, want = true) =>
    fetch(`${API}/rooms/${code}/ready`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify({ ready: want }),
    }).then(handle),

  finish: (code, token) =>
    fetch(`${API}/rooms/${code}/finish`, { method: 'POST', headers: jsonHeaders(token) }).then(handle),

  // Retrigger judging (idempotent; server lease decides whether to run).
  judge: (code, token) =>
    fetch(`${API}/rooms/${code}/judge`, { method: 'POST', headers: jsonHeaders(token) }).then(handle),

  // Creator restarts the debate with the same opponent (verdict screen).
  // The opponent's client follows the old room's rematch_code from the poll.
  rematch: (code, token) =>
    fetch(`${API}/rooms/${code}/rematch`, { method: 'POST', headers: jsonHeaders(token) }).then(handle),

  // Live-audio (P2P) signaling + ICE servers — debaters only.
  postRtc: (code, token, payload) =>
    fetch(`${API}/rooms/${code}/rtc`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify(payload),
    }).then(handle),
  getIce: (code, token) =>
    fetch(`${API}/rooms/${code}/ice`, { headers: { 'X-Debater-Token': token } }).then(handle),

  submitTurn: (code, token, blob, durationMs) => {
    const fd = new FormData();
    const ext = (blob.type.split('/')[1] || 'webm').split(';')[0];
    fd.append('audio', blob, `turn.${ext}`);
    fd.append('duration_ms', String(durationMs));
    fd.append('content_type', blob.type);
    return fetch(`${API}/rooms/${code}/turns`, {
      method: 'POST', headers: { 'X-Debater-Token': token }, body: fd,
    }).then(handle);
  },

  // Audio needs an auth header, so fetch as a blob and hand back an object URL.
  fetchAudioUrl: async (code, token, turn) => {
    const res = await fetch(`${API}/rooms/${code}/turns/${turn}/audio`, {
      headers: { 'X-Debater-Token': token },
    });
    if (!res.ok) throw new Error('audio_fetch_failed');
    return URL.createObjectURL(await res.blob());
  },
};
