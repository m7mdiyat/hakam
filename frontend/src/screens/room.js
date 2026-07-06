import { mountMessage } from '../components.js';
import { creds, specCreds } from '../store.js';
import { mountLobby, mountSpectatorLobby } from './lobby.js';
import { mountDebate } from './debate.js';
import { mountSijal } from './sijal.js';
import { mountVerdict } from './verdict.js';

function category(state) {
  if (state === 'lobby' || state === 'claims') return 'lobby';
  if (state && state.startsWith('turn_')) return 'debate';
  if (state === 'sijal_offer' || state === 'sijal') return 'sijal';
  if (state === 'deliberating') return 'verdict';
  if (state === 'abandoned') return 'abandoned';
  return 'lobby';
}

// Orchestrates the room: swaps the sub-screen when the state category changes and
// forwards every poll to the active sub-screen's update().
export function createRoomView(root, ctx) {
  let kind = null;
  let sub = null;

  const clearMyCreds = () =>
    (ctx.role === 'spectator' ? specCreds : creds).clear(ctx.code);

  function mountKind(k) {
    if (sub && sub.unmount) sub.unmount();
    root.innerHTML = '';
    // Smooth cross-fade between phases (last speech -> سجال -> deliberation ->
    // verdict): a subtle fade+lift on the new screen reads far more polished
    // than an instant swap. Cosmetic only; the sub-screen mounts as usual.
    if (kind !== null) {
      root.classList.remove('screen-enter');
      void root.offsetWidth;
      root.classList.add('screen-enter');
    }
    kind = k;
    if (k === 'lobby') {
      sub = ctx.role === 'spectator' ? mountSpectatorLobby(root, ctx) : mountLobby(root, ctx);
    } else if (k === 'debate') sub = mountDebate(root, ctx);
    else if (k === 'sijal') {
      // Debaters run the open-mic round; spectators just wait it out.
      sub = ctx.role === 'spectator'
        ? mountMessage(root, {
          label: 'سجال', title: 'جولة السِّجال',
          body: 'يتبادل المتناظران مداخلة ختامية حرة، يظهر الحُكم بعد قليل.',
        })
        : mountSijal(root, ctx);
    } else if (k === 'verdict') sub = mountVerdict(root, ctx);
    else if (k === 'abandoned') {
      sub = mountMessage(root, {
        label: 'الجلسة', title: 'انتهت الجلسة',
        body: 'انتهت المناظرة بسبب عدم النشاط.',
        cta: 'مناظرة جديدة', onCta: () => { clearMyCreds(); ctx.navigate('/'); },
      });
    } else if (k === 'gone') {
      sub = mountMessage(root, {
        label: 'الجلسة', title: 'انتهت صلاحية الجلسة',
        body: 'لم تعد هذه الجلسة متاحة.',
        cta: 'مناظرة جديدة', onCta: () => { clearMyCreds(); ctx.navigate('/'); },
      });
    }
  }

  return {
    update(state) {
      const k = category(state.state);
      if (k !== kind) mountKind(k);
      if (sub && sub.update) sub.update(state);
    },
    onError(err) {
      if (err && (err.status === 404 || err.status === 410) && kind !== 'gone') {
        mountKind('gone');
      }
      // transient errors: ignore; next poll retries.
    },
    unmount() { if (sub && sub.unmount) sub.unmount(); },
  };
}
