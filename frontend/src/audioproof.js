// Range playback for audio proofs: play [start_s, end_s] of a turn recording.
// One shared <audio> element; turn blobs are fetched once (auth header) and
// cached as object URLs — blob URLs load metadata instantly, so seeking before
// play is reliable on every platform.
import { api } from './api.js';

export function createProofPlayer(code, token, onChange) {
  const audio = new Audio();
  const urls = {};          // turn -> object URL
  let stopAt = null;
  let active = null;        // key of the playing proof button

  const emit = () => { if (onChange) onChange(active); };

  audio.addEventListener('timeupdate', () => {
    if (stopAt != null && audio.currentTime >= stopAt) audio.pause();
  });
  audio.onpause = audio.onended = () => { active = null; stopAt = null; emit(); };

  async function urlFor(turn) {
    if (!urls[turn]) urls[turn] = await api.fetchAudioUrl(code, token, turn);
    return urls[turn];
  }

  return {
    active: () => active,
    // key: unique id for the button; turn: turn key; start/end: seconds.
    async toggle(key, turn, start, end) {
      if (active === key) { audio.pause(); return; }
      const url = await urlFor(turn);
      if (audio.src !== url) {
        audio.src = url;
        await new Promise((res, rej) => {
          audio.addEventListener('loadedmetadata', res, { once: true });
          audio.addEventListener('error', rej, { once: true });
        });
      }
      audio.currentTime = Math.max(0, start || 0);
      stopAt = end != null ? end : null;
      await audio.play();
      active = key;
      emit();
    },
    destroy() {
      try { audio.pause(); } catch { /* ignore */ }
      Object.values(urls).forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* ignore */ } });
    },
  };
}
