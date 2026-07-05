// Live walkie-talkie link between the two debaters. Strictly additive: if
// anything here fails (no STUN path, hostile NAT, autoplay refused), the
// debate records/uploads/judges exactly as before — this layer only carries
// sound.
//
// One RTCPeerConnection per debate-screen mount, kept for the whole debate.
// Debater A is ALWAYS the offerer, B always answers — deterministic, no
// glare; when B's side breaks it posts a restart request and A re-offers
// under a bumped generation. Vanilla ICE bundles all candidates into a
// single SDP blob, so the whole handshake is one offer + one answer riding
// the room doc via the 2s poll (the server strips it for non-debaters).
//
// The mic is attached via replaceTrack on a pre-allocated transceiver (no
// renegotiation), and it is the SAME track object the recorder enables only
// while a take is running — the opponent hears exactly what is being
// recorded. Self-mute swaps the sender's track for null: the RECORDING
// track is never touched, so muting yourself never mutes your turn.
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
  let began = false;         // any handshake attempt yet (drives 'idle')
  let destroyed = false;
  let iceServers = null;
  let restartTimer = null;
  let peerMuted = false;     // their voice, on my speaker
  let selfMuted = false;     // my voice, on their speaker (never the recording)
  let needsGesture = false;

  function linkState() {
    if (!began) return 'idle';
    if (!pc) return 'connecting';
    if (pc.connectionState === 'connected') return 'connected';
    if (pc.connectionState === 'failed') return 'failed';
    return 'connecting';     // new / connecting / disconnected-recovering
  }

  const emit = () => {
    if (onStatus) onStatus({ state: linkState(), peerMuted, selfMuted, needsGesture });
  };

  // --- audio output: WebAudio graph, not a plain <audio> element ------------
  // Autoplay policy blocks an UNMUTED element's play() outside a gesture (the
  // yellow-button problem), but a MUTED element always plays — and browsers
  // only pull WebRTC audio for a stream some media element is consuming. So:
  // a muted sink element keeps the RTP flowing, while an AudioContext
  // (resumable by ANY prior tap/keypress — debaters must tap «أنا جاهز» and
  // the mic orb anyway) produces the actual sound. In practice the explicit
  // enable pill never needs to appear.
  let audioCtx = null;
  let gainNode = null;
  let srcNode = null;
  let remoteStream = null;

  function ensureGraph() {
    if (audioCtx) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return false;
    audioCtx = new AC();
    gainNode = audioCtx.createGain();
    gainNode.connect(audioCtx.destination);
    return true;
  }
  ensureGraph();   // created now: any later gesture can resume it

  async function kickAudio() {
    if (destroyed) return;
    if (audioEl && audioEl.paused) audioEl.play().catch(() => { /* muted sink */ });
    if (audioCtx && audioCtx.state === 'suspended') {
      try { await audioCtx.resume(); } catch { /* needs a gesture */ }
    }
    // Only ask for a tap when there is actually something to hear.
    needsGesture = !!remoteStream && !!audioCtx && audioCtx.state !== 'running';
    emit();
  }

  function attachRemote(stream) {
    remoteStream = stream;
    if (!audioEl) {
      audioEl = document.createElement('audio');
      audioEl.autoplay = true;
      audioEl.setAttribute('playsinline', '');
      audioEl.muted = !!audioCtx;   // sink-only when the graph does the sound
      document.body.appendChild(audioEl);
    }
    audioEl.srcObject = stream;
    audioEl.play().catch(() => { /* retried by kickAudio */ });
    if (audioCtx) {
      if (srcNode) { try { srcNode.disconnect(); } catch { /* re-attach */ } }
      srcNode = audioCtx.createMediaStreamSource(stream);
      srcNode.connect(gainNode);
      gainNode.gain.value = peerMuted ? 0 : 1;
    } else {
      audioEl.muted = peerMuted;    // ancient-browser fallback: element audio
    }
    kickAudio();
  }

  // Any interaction resumes blocked audio — mouse, touch, or keyboard (the
  // desktop listener may never click but will type/press keys).
  const gestureKick = () => { kickAudio(); };
  document.addEventListener('pointerdown', gestureKick, true);
  document.addEventListener('keydown', gestureKick, true);

  async function newPc() {
    if (!iceServers) {
      try { iceServers = (await api.getIce(code, token)).iceServers; }
      catch { iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }]; }
    }
    if (destroyed) return null;
    const p = new RTCPeerConnection({ iceServers });
    const tr = p.addTransceiver('audio', { direction: 'sendrecv' });
    sender = tr.sender;
    if (micTrack && !selfMuted) sender.replaceTrack(micTrack).catch(() => {});
    p.ontrack = (e) => {
      attachRemote(e.streams[0] || new MediaStream([e.track]));
      // Re-kick when RTP actually starts flowing (dead air until then).
      e.track.addEventListener('unmute', kickAudio);
    };
    p.onconnectionstatechange = () => {
      emit();
      if (p.connectionState === 'connected') kickAudio();
      else if (p.connectionState === 'failed') scheduleRestart(2000);
      else if (p.connectionState === 'disconnected') scheduleRestart(5000);
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
    began = true;
    emit();
    try {
      if (pc) pc.close();
      pc = await newPc();
      if (!pc) return;
      gen = nextGen;
      await pc.setLocalDescription(await pc.createOffer());
      await gathered(pc);
      await api.postRtc(code, token, { gen, sdp: sdpJson(pc.localDescription) });
    } catch { /* the next poll's onSignal retries */ }
    finally { starting = false; emit(); }
  }

  async function makeAnswer(offer, offerGen) {
    if (starting || destroyed) return;
    starting = true;
    began = true;
    emit();
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
    finally { starting = false; emit(); }
  }

  function requestRestart(atGen) {  // B can't offer — ask A to re-offer
    if (restartAskedGen >= atGen) return;
    restartAskedGen = atGen;
    api.postRtc(code, token, { gen: atGen, restart: true })
      .catch(() => { restartAskedGen = -1; });
  }

  function scheduleRestart(delayMs) {
    if (restartTimer || destroyed) return;
    restartTimer = setTimeout(() => {
      restartTimer = null;
      if (destroyed || !pc || pc.connectionState === 'connected') return;
      if (side === 'a') makeOffer(gen + 1);
      else requestRestart(answeredGen);
    }, delayMs);
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
    if (sender && !selfMuted) sender.replaceTrack(track).catch(() => {});
  }

  // My voice on THEIR speaker. Swaps the sender's track only — the recording
  // track keeps running untouched.
  function setSelfMuted(m) {
    selfMuted = m;
    if (sender) sender.replaceTrack(m ? null : micTrack).catch(() => {});
    emit();
  }

  // Their voice on MY speaker (pure local playback mute — a gain of zero).
  function setPeerMuted(m) {
    peerMuted = m;
    if (audioCtx && gainNode) gainNode.gain.value = m ? 0 : 1;
    else if (audioEl) audioEl.muted = m;
    if (!m) kickAudio();
    emit();
  }

  function resumeAudio() {  // the explicit «تفعيل الصوت» fallback pill
    kickAudio();
  }

  function destroy() {
    destroyed = true;
    document.removeEventListener('pointerdown', gestureKick, true);
    document.removeEventListener('keydown', gestureKick, true);
    if (restartTimer) clearTimeout(restartTimer);
    if (pc) { try { pc.close(); } catch { /* closing */ } }
    if (audioCtx) { try { audioCtx.close(); } catch { /* closing */ } }
    if (audioEl) {
      try { audioEl.srcObject = null; audioEl.remove(); } catch { /* gone */ }
    }
  }

  return {
    onSignal, attachMic, setSelfMuted, setPeerMuted, resumeAudio, destroy,
    get selfMuted() { return selfMuted; },
    get peerMuted() { return peerMuted; },
  };
}
