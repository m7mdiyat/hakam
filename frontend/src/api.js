// API client. Same-origin in production; Vite proxies /api to Flask in dev.

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
    fetch('/api/rooms', { method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ topic }) }).then(handle),

  getRoom: (code) => fetch(`/api/rooms/${code}`).then(handle),

  joinRoom: (code, { name, claim, consent }) =>
    fetch(`/api/rooms/${code}/join`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ name, claim, consent }),
    }).then(handle),

  setClaim: (code, token, { name, claim }) =>
    fetch(`/api/rooms/${code}/claim`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify({ name, claim }),
    }).then(handle),

  ready: (code, token, want = true) =>
    fetch(`/api/rooms/${code}/ready`, {
      method: 'POST', headers: jsonHeaders(token), body: JSON.stringify({ ready: want }),
    }).then(handle),

  finish: (code, token) =>
    fetch(`/api/rooms/${code}/finish`, { method: 'POST', headers: jsonHeaders(token) }).then(handle),

  submitTurn: (code, token, blob, durationMs) => {
    const fd = new FormData();
    const ext = (blob.type.split('/')[1] || 'webm').split(';')[0];
    fd.append('audio', blob, `turn.${ext}`);
    fd.append('duration_ms', String(durationMs));
    fd.append('content_type', blob.type);
    return fetch(`/api/rooms/${code}/turns`, {
      method: 'POST', headers: { 'X-Debater-Token': token }, body: fd,
    }).then(handle);
  },

  // Audio needs an auth header, so fetch as a blob and hand back an object URL.
  fetchAudioUrl: async (code, token, turn) => {
    const res = await fetch(`/api/rooms/${code}/turns/${turn}/audio`, {
      headers: { 'X-Debater-Token': token },
    });
    if (!res.ok) throw new Error('audio_fetch_failed');
    return URL.createObjectURL(await res.blob());
  },
};
