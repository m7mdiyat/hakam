// Live walkie-talkie link between the two debaters. Strictly additive: if
// anything here fails (no STUN, hostile NAT, autoplay refused), the debate
// records/uploads/judges exactly as before — this layer only carries sound.
//
// One RTCPeerConnection per debate-screen mount, kept for the whole debate.
// Debater A is ALWAYS the offerer, B always answers — deterministic, no
// glare; when B's side breaks it posts a restart request and A re-offers
// under a bumped generation. Vanilla ICE bundles all candidates into one SDP
// blob, so the whole handshake is one offer + one answer riding the room
// doc via the 2s poll (server strips it for non-debaters).
//
// The mic is attached via replaceTrack on a pre-allocated transceiver (no
// renegotiation), and it is the SAME track object the recorder enables only
// while a take is running — so the opponent hears exactly what is being
// recorded, and silence otherwise. Walkie-talkie semantics for free.
import { api } from './api.js';

export function createLiveLink({ code, token, side, onStatus }) {
  let pc = null;
  let sender = null;         // pre-allocated audio slot (replaceTrack target)
  let audioEl = null;
  let micTrack = null;
  let gen = 0;               // A: generation of my current offer
  let answeredGen = 0;       // B: latest offer generation I answered
  let restartAskedGen = -1;  // B: dedupe restart requests
  let starting = false;
  let destroyed = false;
  let iceServers = null;
  let restartTimer = null;
  let muted = false;
  let needsGesture = false;

  const emit = () => {
    if (onStatus) {
      onStatus({
        connected: !!pc && pc.connectionState === 'connected',
        muted,
        needsGesture,
      });
    }
  };

  function ensureAudioEl() {
    if (!audioEl) {
      audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.setAttribute('playsinline', '');
      audioEl.muted = muted;
      document.body.appendChild(audioEl);
    }
    return audioEl;
  }

  async function tryPlay() {
    try {
      await audioEl.play();
      needsGesture = false;
    } catch {
      needsGesture = true;   // iOS autoplay policy: one tap fixes it for good
    }
    emit();
  }

  async function newPc() {
    if (!iceServers) {
      try { iceServers = (await api.getIce(code, token)).iceServers; }
      catch { iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }]; }
    }
    if (destroyed) return null;
    const p = new RTCPeerConnection({ iceServers });
    const tr = p.addTransceiver('audio', { direction: 'sendrecv' });
    sender = tr.sender;
    if (micTrack) sender.replaceTrack(micTrack).catch(() => {});
    p.ontrack = (e) => {
      ensureAudioEl().srcObject = e.streams[0] || new MediaStream([e.track]);
      tryPlay();
    };
    p.onconnectionstatechange = () => {
      emit();
      if (p.connectionState === 'failed') scheduleRestart();
    };
    return p;
  }

  // Vanilla ICE: wait for gathering to finish (or 2.5s — send what we have)
  // so a single blob carries the SDP plus every candidate.
  function gathered(p) {
    if (p.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      const t = setTimeout(resolve, 2500);
      p.addEventListener('icegatheringstatechange', () => {
        if (p.iceGatheringState === 'complete') { clearTimeout(t); resolve(); }
      });
    });
  }

  const sdpJson = (d) => ({ type: d.type, sdp: d.sdp });

  async function makeOffer(nextGen) {
    if (starting || destroyed) return;
    starting = true;
    try {
      if (pc) pc.close();
      pc = await newPc();
      if (!pc) return;
      gen = nextGen;
      await pc.setLocalDescription(await pc.createOffer());
      await gathered(pc);
      await api.postRtc(code, token, { gen, sdp: sdpJson(pc.localDescription) });
    } catch { /* the next poll's onSignal retries */ }
    finally { starting = false; }
  }

  async function makeAnswer(offer, offerGen) {
    if (starting || destroyed) return;
    starting = true;
    try {
      if (pc) pc.close();
      pc = await newPc();
      if (!pc) return;
      await pc.setRemoteDescription(offer);
      await pc.setLocalDescription(await pc.createAnswer());
      await gathered(pc);
      await api.postRtc(code, token, { gen: offerGen, sdp: sdpJson(pc.localDescription) });
      answeredGen = offerGen;
      restartAskedGen = -1;
    } catch { /* retried when the offer reappears in the poll */ }
    finally { starting = false; }
  }

  function requestRestart(atGen) {  // B can't offer — ask A to re-offer
    if (restartAskedGen >= atGen) return;
    restartAskedGen = atGen;
    api.postRtc(code, token, { gen: atGen, restart: true })
      .catch(() => { restartAskedGen = -1; });
  }

  function scheduleRestart() {
    if (restartTimer || destroyed) return;
    restartTimer = setTimeout(() => {
      restartTimer = null;
      if (destroyed || !pc || pc.connectionState === 'connected') return;
      if (side === 'a') makeOffer(gen + 1);
      else requestRestart(answeredGen);
    }, 2000);
  }

  // Fed from the debate screen's poll: view.rtc = {a: blob|stub|null, b: ...}.
  function onSignal(rtc) {
    if (destroyed || !rtc) return;
    const theirs = rtc[side === 'a' ? 'b' : 'a'];
    if (side === 'a') {
      const myPrevGen = rtc.a ? rtc.a.gen : 0;
      if (!pc && !starting) { makeOffer(myPrevGen + 1); return; }
      if (theirs && theirs.restart && theirs.gen >= gen && !starting) {
        makeOffer(gen + 1);
        return;
      }
      if (theirs && theirs.sdp && theirs.sdp.type === 'answer' && theirs.gen === gen
          && pc && pc.signalingState === 'have-local-offer') {
        pc.setRemoteDescription(theirs.sdp).catch(() => {});
      }
    } else if (theirs) {
      if (theirs.sdp && theirs.sdp.type === 'offer' && theirs.gen > answeredGen) {
        makeAnswer(theirs.sdp, theirs.gen);
      } else if (!theirs.sdp && theirs.gen > answeredGen) {
        // A's offer went stale before we could answer (e.g. we refreshed):
        // ask for a fresh one.
        requestRestart(theirs.gen);
      }
    }
  }

  function attachMic(track) {
    micTrack = track;
    if (sender) sender.replaceTrack(track).catch(() => {});
  }

  function setMuted(m) {
    muted = m;
    if (audioEl) audioEl.muted = m;
    emit();
  }

  function resumeAudio() {  // wired to the one-tap iOS «تفعيل الصوت» button
    if (audioEl) tryPlay();
  }

  function destroy() {
    destroyed = true;
    if (restartTimer) clearTimeout(restartTimer);
    if (pc) { try { pc.close(); } catch { /* closing */ } }
    if (audioEl) {
      try { audioEl.srcObject = null; audioEl.remove(); } catch { /* gone */ }
    }
  }

  return {
    onSignal, attachMic, setMuted, resumeAudio, destroy,
    get muted() { return muted; },
  };
}
