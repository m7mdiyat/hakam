// Per-room debater credentials, persisted so a refresh keeps your seat.

const key = (code) => `hakam:room:${String(code || '').toUpperCase()}`;

export const creds = {
  get(code) {
    try { return JSON.parse(localStorage.getItem(key(code))); }
    catch { return null; }
  },
  set(code, token, side) {
    localStorage.setItem(key(code), JSON.stringify({ token, side }));
  },
  clear(code) {
    localStorage.removeItem(key(code));
  },
};

// Per-room spectator credentials — separate key: the same device can debate
// one room and spectate another. `name` is kept to auto-follow rematches.
const skey = (code) => `hakam:spec:${String(code || '').toUpperCase()}`;

export const specCreds = {
  get(code) {
    try { return JSON.parse(localStorage.getItem(skey(code))); }
    catch { return null; }
  },
  set(code, token, name) {
    localStorage.setItem(skey(code), JSON.stringify({ token, name }));
  },
  clear(code) {
    localStorage.removeItem(skey(code));
  },
};
