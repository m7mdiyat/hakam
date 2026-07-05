// App-global autoplay disarm. Browsers allow programmatic audio only on an
// element that has once played during a real user gesture — and the debate
// screen can mount with NO gesture opportunity for the listener (when the
// opponent speaks first there is nothing to tap), so waiting for a gesture
// AFTER mount is a structural hole. Instead: one singleton <audio> element,
// armed by capture listeners installed at app boot, so the taps every user
// necessarily makes earlier (create / join / «أنا جاهز») pre-unlock it. The
// live link adopts this element for remote playback.
const SILENT_WAV = 'data:audio/wav;base64,UklGRsQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YaAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

let el = null;
let unlocked = false;

export function unlockAudioEl() {
  if (!el) {
    el = document.createElement('audio');
    el.autoplay = true;
    el.setAttribute('playsinline', '');
    document.body.appendChild(el);
  }
  return el;
}

export const isUnlocked = () => unlocked;
export const setUnlocked = () => { unlocked = true; };

function arm() {
  if (unlocked) return;
  const a = unlockAudioEl();
  if (a.srcObject) {
    // Remote audio is already waiting: playing it IS the unlock.
    a.play().then(setUnlocked).catch(() => { /* next gesture retries */ });
    return;
  }
  a.src = SILENT_WAV; // srcObject, once set later, takes precedence
  a.play().then(setUnlocked).catch(() => { /* not a real activation */ });
}

document.addEventListener('click', arm, true);
document.addEventListener('touchend', arm, true);
document.addEventListener('keydown', arm, true);
