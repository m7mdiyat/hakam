// Broadcast leg (spectator live listening) via the Cloudflare Realtime SFU.
// Debaters PUBLISH their mic to Cloudflare; spectators LISTEN from
// Cloudflare — no device ever connects to another device (the debaters'
// private P2P link in rtc.js is untouched), and all Cloudflare auth lives
// server-side behind /sfu/* proxies. Strictly additive like rtc.js: any
// failure here leaves recording/turn playback exactly as before.
//
// Cloudflare's SFU is ICE-lite: the client always initiates connectivity
// checks against Cloudflare's candidates, so we never wait for local ICE
// gathering — offers/answers post immediately (faster connect).
import { api } from './api.js';
import { unlockAudioEl, isUnlocked, setUnlocked } from './audiounlock.js';

async function iceFor(code, token) {
  try { return (await api.getIce(code, token)).iceServers; }
  catch {
    return [{ urls: ['stun:stun.cloudflare.com:3478', 'stun:stun.cloudflare.com:53'] }];
  }
}

// --- publisher (debater) -----------------------------------------------------
// One sendonly transceiver; the mic track is attached per take (same object
// the recorder enables), so listeners hear exactly what is being recorded.
export function createSfuPublisher({ code, token }) {
  let pc = null;
  let sender = null;
  let micTrack = null;
  let muted = false;
  let started = false;
  let destroyed = false;
  let retryTimer = null;

  async function start() {
    if (started || destroyed) return;
    started = true;
    try {
      pc = new RTCPeerConnection({ iceServers: await iceFor(code, token) });
      const tr = pc.addTransceiver('audio', { direction: 'sendonly' });
      sender = tr.sender;
      if (micTrack && !muted) sender.replaceTrack(micTrack).catch(() => {});
      pc.onconnectionstatechange = () => {
        if (destroyed || !pc) return;
        // 'disconnected' can linger forever without ever reaching 'failed';
        // both mean spectators hear dead air — republish (fresh session +
        // gen bump makes every listener re-pull).
        if (pc.connectionState === 'failed') restartSoon(2000);
        else if (pc.connectionState === 'disconnected') restartSoon(5000);
      };
      await pc.setLocalDescription(await pc.createOffer());
      const res = await api.sfuPublish(code, token, {
        sdp: { type: 'offer', sdp: pc.localDescription.sdp }, mid: tr.mid,
      });
      await pc.setRemoteDescription(res.sdp);
    } catch {
      started = false;
      restartSoon(6000);
    }
  }

  function restartSoon(delayMs) {
    if (retryTimer || destroyed) return;
    retryTimer = setTimeout(() => {
      retryTimer = null;
      if (destroyed) return;
      if (pc && pc.connectionState === 'connected') return;  // healed itself
      if (pc) { try { pc.close(); } catch { /* closing */ } pc = null; }
      sender = null;
      started = false;
      start();
    }, delayMs);
  }

  // A backgrounded phone can freeze the connection without any state event:
  // on return, restart unless provably healthy.
  const onVisible = () => {
    if (destroyed || document.visibilityState !== 'visible') return;
    if (!pc || pc.connectionState !== 'connected') restartSoon(500);
  };
  document.addEventListener('visibilitychange', onVisible);

  return {
    start,
    attachTrack(track) {
      micTrack = track;
      if (!started) start();
      else if (sender && !muted) sender.replaceTrack(track).catch(() => {});
    },
    setMuted(m) {  // «صوتي مكتوم» covers ALL live listeners, never the recording
      muted = m;
      if (sender) sender.replaceTrack(m ? null : micTrack).catch(() => {});
    },
    destroy() {
      destroyed = true;
      document.removeEventListener('visibilitychange', onVisible);
      if (retryTimer) clearTimeout(retryTimer);
      if (pc) { try { pc.close(); } catch { /* closing */ } }
    },
  };
}

// --- listener (spectator) ----------------------------------------------------
// Pulls every published mic into one stream. The room view's sfu_published
// generations drive (re)connection: a bump means a debater (re)published
// under a NEW Cloudflare session, so the old pull would be dead air.
export function createSfuListener({ code, token, onStatus }) {
  let pc = null;
  let audioEl = null;
  let remote = null;
  let lastKey = null;
  let connecting = false;
  let destroyed = false;
  let needsGesture = false;
  let muted = false;

  function state() {
    if (!pc) return 'idle';
    if (pc.connectionState === 'connected') return 'connected';
    if (pc.connectionState === 'failed') return 'failed';
    return 'connecting';
  }
  const emit = () => { if (onStatus) onStatus({ state: state(), needsGesture, muted }); };

  async function tryPlay() {
    if (destroyed || !remote) return;
    audioEl = audioEl || unlockAudioEl();
    audioEl.muted = muted;
    if (audioEl.srcObject !== remote) audioEl.srcObject = remote;
    try {
      await audioEl.play();
      setUnlocked();
      needsGesture = false;
    } catch {
      needsGesture = !isUnlocked();
    }
    emit();
  }

  function unlock() {
    if (!destroyed && remote && needsGesture) tryPlay();
  }
  document.addEventListener('click', unlock, true);
  document.addEventListener('touchend', unlock, true);
  document.addEventListener('keydown', unlock, true);

  // A locked phone / app switch pauses the element and can freeze the
  // connection with no state event — heal both on return.
  const onVisible = () => {
    if (destroyed || document.visibilityState !== 'visible') return;
    if (pc && pc.connectionState === 'connected') tryPlay();
    else lastKey = null;                             // next poll reconnects
  };
  document.addEventListener('visibilitychange', onVisible);

  async function connect() {
    if (connecting || destroyed) return;
    connecting = true;
    emit();
    try {
      if (pc) { try { pc.close(); } catch { /* closing */ } }
      remote = new MediaStream();
      pc = new RTCPeerConnection({ iceServers: await iceFor(code, token) });
      pc.ontrack = (e) => {
        remote.addTrack(e.track);
        tryPlay();
        e.track.addEventListener('unmute', tryPlay);
      };
      pc.onconnectionstatechange = () => {
        if (destroyed || !pc) return;
        if (pc.connectionState === 'connected') tryPlay();
        // failed OR lingering disconnected: let the next poll rebuild.
        if (pc.connectionState === 'failed') lastKey = null;
        else if (pc.connectionState === 'disconnected') {
          const dead = pc;
          setTimeout(() => {
            if (!destroyed && pc === dead && dead.connectionState === 'disconnected') {
              lastKey = null;
            }
          }, 5000);
        }
        emit();
      };
      const res = await api.sfuListen(code, token);      // Cloudflare's offer
      await pc.setRemoteDescription(res.sdp);
      await pc.setLocalDescription(await pc.createAnswer());
      await api.sfuRenegotiate(code, token, {
        session_id: res.session_id,
        sdp: { type: 'answer', sdp: pc.localDescription.sdp },
      });
    } catch {
      lastKey = null;                                    // retry on a later poll
    } finally {
      connecting = false;
      emit();
    }
  }

  return {
    // Fed from the poll: reconnect whenever the publish generations change.
    onView(view) {
      if (destroyed) return;
      const gens = view.sfu_published || {};
      if (((gens.a || 0) + (gens.b || 0)) === 0) return;   // nothing to hear yet
      const key = `${gens.a || 0}:${gens.b || 0}`;
      if (key !== lastKey) { lastKey = key; connect(); }
    },
    setMuted(m) {
      muted = m;
      if (audioEl) audioEl.muted = m;
      if (!m) tryPlay();
      emit();
    },
    resume() { if (remote) tryPlay(); },
    get muted() { return muted; },
    destroy() {
      destroyed = true;
      document.removeEventListener('click', unlock, true);
      document.removeEventListener('touchend', unlock, true);
      document.removeEventListener('keydown', unlock, true);
      document.removeEventListener('visibilitychange', onVisible);
      if (pc) { try { pc.close(); } catch { /* closing */ } }
      if (audioEl) { try { audioEl.srcObject = null; audioEl.muted = false; } catch { /* gone */ } }
    },
  };
}
