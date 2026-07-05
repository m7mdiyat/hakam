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
import { unlockAudioEl, isUnlocked, setUnlocked } from './audiounlock.js';

// A link that never connected across this many handshake generations is
// reported 'unreachable' — an honest label instead of eternal «جاري
// الاتصال» (a production room posted offers with ZERO ICE candidates for
// six minutes: UDP-blocked network, nothing to connect to). Retries keep
// running quietly; a mid-debate network change can still heal it.
const UNREACHABLE_AFTER = 2;
const CONNECT_WATCHDOG_MS = 15000;

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
  let watchdog = null;       // this generation never connected -> retry
  let failedGens = 0;
  let everConnected = false;
  let peerMuted = false;     // their voice, on my speaker
  let selfMuted = false;     // my voice, on their speaker (never the recording)
  let needsGesture = false;

  function linkState() {
    if (!began) return 'idle';
    if (pc && pc.connectionState === 'connected') return 'connected';
    if (!everConnected && failedGens >= UNREACHABLE_AFTER) return 'unreachable';
    if (pc && pc.connectionState === 'failed') return 'failed';
    return 'connecting';     // new / connecting / disconnected-recovering
  }

  const emit = () => {
    if (onStatus) onStatus({ state: linkState(), peerMuted, selfMuted, needsGesture });
  };

  // --- audio output: ONE unmuted <audio> element, unlocked once by a tap ----
  // WebAudio is deliberately NOT used: Safari (iOS/macOS) has a long-standing
  // WebKit bug where a remote WebRTC stream routed through an AudioContext
  // renders SILENCE — that broke everything, everywhere, at once. A plain
  // element plays on every browser; the only catch is the autoplay gate, so
  // we disarm it with the classic unlock: play a tiny silent clip inside any
  // real activation gesture (click/touchend/keydown — debaters must tap «أنا
  // جاهز» and the mic orb anyway). A media element that has once played from
  // a gesture stays user-activated, so live audio then starts by itself.
  let remoteStream = null;

  function ensureAudioEl() {
    // The app-global singleton, usually pre-armed by a tap long before the
    // debate screen existed (create/join/«أنا جاهز») — see audiounlock.js.
    if (!audioEl) {
      audioEl = unlockAudioEl();
      audioEl.muted = peerMuted;
    }
    return audioEl;
  }

  async function tryPlay() {
    if (destroyed || !remoteStream) return;
    const el = ensureAudioEl();
    if (el.srcObject !== remoteStream) el.srcObject = remoteStream;
    try {
      await el.play();
      setUnlocked();
      needsGesture = false;
    } catch {
      needsGesture = !isUnlocked();  // pill only when no gesture ever landed
    }
    emit();
  }

  function attachRemote(stream) {
    remoteStream = stream;
    tryPlay();
  }

  // Gesture hook while the debate screen is up: if remote audio is waiting
  // behind the gate, any tap plays it (audiounlock.js handles pre-arming).
  function unlock() {
    if (!destroyed && remoteStream && needsGesture) tryPlay();
  }
  document.addEventListener('click', unlock, true);
  document.addEventListener('touchend', unlock, true);
  document.addEventListener('keydown', unlock, true);

  async function newPc(withSendSlot) {
    if (!iceServers) {
      try { iceServers = (await api.getIce(code, token)).iceServers; }
      catch { iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }]; }
    }
    if (destroyed) return null;
    const p = new RTCPeerConnection({ iceServers });
    if (withSendSlot) {
      // Offerer only: create the audio m-line up front.
      const tr = p.addTransceiver('audio', { direction: 'sendrecv' });
      sender = tr.sender;
      if (micTrack && !selfMuted) sender.replaceTrack(micTrack).catch(() => {});
    } else {
      // Answerer: the offer's own m-line is adopted after setRemoteDescription
      // (see makeAnswer). Pre-adding a transceiver here is a trap: JSEP only
      // matches incoming m-lines against addTrack()-created transceivers, so
      // a pre-added slot stays orphaned and the answer negotiates a=recvonly
      // — the answerer's voice silently never sends (the one-way-audio bug).
      sender = null;
    }
    p.ontrack = (e) => {
      attachRemote(e.streams[0] || new MediaStream([e.track]));
      // Re-kick when RTP actually starts flowing (dead air until then).
      e.track.addEventListener('unmute', tryPlay);
    };
    p.onconnectionstatechange = () => {
      if (p.connectionState === 'connected') {
        everConnected = true;
        failedGens = 0;
        if (watchdog) { clearTimeout(watchdog); watchdog = null; }
        tryPlay();
      } else if (p.connectionState === 'failed') scheduleRestart(2000);
      else if (p.connectionState === 'disconnected') scheduleRestart(5000);
      emit();
    };
    return p;
  }

  // Vanilla ICE: wait for gathering to finish so a single blob carries the
  // SDP plus every candidate. Soft cap 2.5s — but ONLY if something was
  // gathered: an offer with zero candidates gives the peer nothing to
  // connect to (observed in production on a UDP-blocked network), so an
  // empty blob waits up to the hard cap before being sent regardless (the
  // watchdog + restart loop then owns the failure).
  function gathered(p) {
    if (p.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve) => {
      let hard = null;
      const done = () => { clearTimeout(soft); clearTimeout(hard); resolve(); };
      const hasCands = () => /\r?\na=candidate:/.test(
        (p.localDescription && p.localDescription.sdp) || '');
      const soft = setTimeout(() => { if (hasCands()) done(); }, 2500);
      hard = setTimeout(done, 8000);
      p.addEventListener('icegatheringstatechange', () => {
        if (p.iceGatheringState === 'complete') done();
      });
    });
  }

  // This generation never reached 'connected' in time: count it and retry
  // (A re-offers, B asks A to). Catches ICE stuck in 'new'/'checking' — with
  // an empty remote candidate list it never even reaches 'failed'.
  function armWatchdog() {
    if (watchdog) clearTimeout(watchdog);
    watchdog = setTimeout(() => {
      watchdog = null;
      if (destroyed || !pc || pc.connectionState === 'connected') return;
      failedGens += 1;
      emit();
      if (side === 'a') makeOffer(gen + 1);
      else requestRestart(answeredGen);
    }, CONNECT_WATCHDOG_MS);
  }

  const sdpJson = (d) => ({ type: d.type, sdp: d.sdp });

  async function makeOffer(nextGen) {
    if (starting || destroyed) return;
    starting = true;
    began = true;
    emit();
    try {
      if (pc) pc.close();
      pc = await newPc(true);
      if (!pc) return;
      gen = nextGen;
      await pc.setLocalDescription(await pc.createOffer());
      await gathered(pc);
      await api.postRtc(code, token, { gen, sdp: sdpJson(pc.localDescription) });
      armWatchdog();
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
      pc = await newPc(false);
      if (!pc) return;
      await pc.setRemoteDescription(offer);
      // Adopt the offer's transceiver and flip it to sendrecv so the answer
      // actually offers our audio back (see the trap note in newPc).
      const tr = pc.getTransceivers()[0];
      if (tr) {
        tr.direction = 'sendrecv';
        sender = tr.sender;
        if (micTrack && !selfMuted) sender.replaceTrack(micTrack).catch(() => {});
      }
      await pc.setLocalDescription(await pc.createAnswer());
      await gathered(pc);
      await api.postRtc(code, token, { gen: offerGen, sdp: sdpJson(pc.localDescription) });
      answeredGen = offerGen;
      restartAskedGen = -1;
      armWatchdog();
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
      failedGens += 1;
      emit();
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

  // Their voice on MY speaker (pure local playback mute).
  function setPeerMuted(m) {
    peerMuted = m;
    if (audioEl) audioEl.muted = m;
    if (!m) tryPlay();
    emit();
  }

  function resumeAudio() {  // the explicit «تفعيل الصوت» fallback pill
    if (remoteStream) tryPlay();
  }

  function destroy() {
    destroyed = true;
    document.removeEventListener('click', unlock, true);
    document.removeEventListener('touchend', unlock, true);
    document.removeEventListener('keydown', unlock, true);
    if (restartTimer) clearTimeout(restartTimer);
    if (watchdog) clearTimeout(watchdog);
    if (pc) { try { pc.close(); } catch { /* closing */ } }
    if (audioEl) {
      // The element is the app-global unlock singleton: detach the stream
      // but keep it alive (and armed) for the next mount (e.g. a rematch).
      try { audioEl.srcObject = null; audioEl.muted = false; } catch { /* gone */ }
    }
  }

  return {
    onSignal, attachMic, setSelfMuted, setPeerMuted, resumeAudio, destroy,
    get selfMuted() { return selfMuted; },
    get peerMuted() { return peerMuted; },
  };
}
