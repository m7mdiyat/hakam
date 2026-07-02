import { api } from './api.js';

// Poll a room every `interval` ms. Uses a setTimeout chain (not setInterval) so a
// slow response never overlaps the next request. Returns a stop() function.
export function startPolling(code, onUpdate, onError, interval = 2000) {
  let stopped = false;
  let timer = null;

  async function tick() {
    if (stopped) return;
    try {
      const state = await api.getRoom(code);
      if (!stopped) onUpdate(state);
    } catch (e) {
      if (!stopped && onError) onError(e);
    } finally {
      if (!stopped) timer = setTimeout(tick, interval);
    }
  }

  tick();
  return () => { stopped = true; if (timer) clearTimeout(timer); };
}
