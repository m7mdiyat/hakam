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
