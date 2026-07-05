// Range playback for audio proofs: play [start_s, end_s] of a turn recording.
// One shared <audio> element; turn blobs are fetched once (auth header) and
// cached as object URLs — blob URLs load metadata instantly, so seeking before
// play is reliable on every platform.
//
// Precision rules (the server snaps bounds to measured speech boundaries —
// the player must not squander that):
// - never play before the seek has LANDED ('seeked' event) — calling play()
//   right after setting currentTime can emit frames from the old position;
// - cut the clip end with a requestAnimationFrame watcher (~16ms) rather than
//   'timeupdate' alone (~250ms overshoot); timeupdate stays as the backstop
//   for hidden tabs, where rAF doesn't run.
import { api } from './api.js';

export function createProofPlayer(code, token, onChange) {
  const audio = new Audio();
  const urls = {};          // turn -> object URL
  let stopAt = null;
  let active = null;        // key of the playing proof button
  let seq = 0;              // toggle generation — rapid clicks: last one wins
  let rafId = null;

  const emit = () => { if (onChange) onChange(active); };

  function watchEnd() {
    rafId = null;
    if (stopAt == null || audio.paused) return;
    if (audio.currentTime >= stopAt) { audio.pause(); return; }
    rafId = requestAnimationFrame(watchEnd);
  }
  audio.addEventListener('timeupdate', () => {
    if (stopAt != null && audio.currentTime >= stopAt) audio.pause();
  });
  audio.onpause = audio.onended = () => {
    if (rafId != null) { cancelAnimationFrame(rafId); rafId = null; }
    active = null; stopAt = null; emit();
  };

  // One-shot event wait with full listener cleanup (a leaked {once} 'error'
  // listener per play would pile up on the shared element). The optional
  // timeout resolves — used for 'seeked', where a missing event on some
  // platform must degrade to "play from roughly the right place", not hang.
  function waitFor(ev, timeoutMs) {
    return new Promise((resolve, reject) => {
      let tid = null;
      const ok = () => { cleanup(); resolve(); };
      const fail = () => { cleanup(); reject(new Error('audio_error')); };
      function cleanup() {
        audio.removeEventListener(ev, ok);
        audio.removeEventListener('error', fail);
        if (tid != null) clearTimeout(tid);
      }
      audio.addEventListener(ev, ok);
      audio.addEventListener('error', fail);
      if (timeoutMs) tid = setTimeout(ok, timeoutMs);
    });
  }

  async function urlFor(turn) {
    if (!urls[turn]) urls[turn] = await api.fetchAudioUrl(code, token, turn);
    return urls[turn];
  }

  return {
    active: () => active,
    // key: unique id for the button; turn: turn key; start/end: seconds.
    async toggle(key, turn, start, end) {
      if (active === key) { audio.pause(); return; }
      const my = ++seq;
      if (!audio.paused) audio.pause();   // never seek a playing element — it
                                          // would emit audio from the old spot
      const url = await urlFor(turn);
      if (my !== seq) return;                    // a newer click took over
      if (audio.src !== url) {
        audio.src = url;
        await waitFor('loadedmetadata');
        if (my !== seq) return;
      }
      stopAt = null;                             // old bound must not cut the new clip
      const target = Math.max(0, start || 0);
      audio.currentTime = target;
      await waitFor('seeked', 400);
      if (my !== seq) return;
      if (Math.abs(audio.currentTime - target) > 0.75) {
        // The 400ms wait resolved by timeout with the seek still in flight —
        // playing now would emit the wrong words. One more grace period.
        await waitFor('seeked', 600);
        if (my !== seq) return;
      }
      stopAt = end != null ? end : null;
      await audio.play();
      if (my !== seq) { audio.pause(); return; }
      active = key;
      emit();
      watchEnd();
    },
    destroy() {
      try { audio.pause(); } catch { /* ignore */ }
      Object.values(urls).forEach((u) => { try { URL.revokeObjectURL(u); } catch { /* ignore */ } });
    },
  };
}
