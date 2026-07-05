import './styles/tokens.css';
import './styles/app.css';
import './audiounlock.js'; // arm the autoplay unlock on the user's FIRST tap
import { creds } from './store.js';
import { startPolling } from './poll.js';
import { mountLanding } from './screens/landing.js';
import { mountJoin } from './screens/join.js';
import { mountSpectate } from './screens/spectate.js';
import { mountShared } from './screens/shared.js';
import { createRoomView } from './screens/room.js';

const root = document.getElementById('app');
let teardown = null;

export function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState({}, '', path);
  else history.pushState({}, '', path);
  route();
}

function shell() {
  root.innerHTML = '<div class="app" id="screen"></div>';
  return root.querySelector('#screen');
}

function codeFrom(path, prefix) {
  return decodeURIComponent(path.slice(prefix.length)).split('/')[0].toUpperCase();
}

function mountRoom(screen, ctx) {
  const c = creds.get(ctx.code);
  if (!c) { navigate(`/j/${ctx.code}`, { replace: true }); return null; }
  const view = createRoomView(screen, { ...ctx, creds: c });
  const stop = startPolling(ctx.code, c.token, (s) => view.update(s), (e) => view.onError(e));
  return { unmount() { stop(); view.unmount(); } };
}

function route() {
  if (teardown) { teardown(); teardown = null; }
  const screen = shell();
  const path = location.pathname;
  const ctx = { navigate };
  let mounted = null;

  if (path === '/' || path === '') {
    mounted = mountLanding(screen, ctx);
  } else if (path.startsWith('/j/')) {
    mounted = mountJoin(screen, { ...ctx, code: codeFrom(path, '/j/') });
  } else if (path.startsWith('/s/')) {
    mounted = mountSpectate(screen, { ...ctx, code: codeFrom(path, '/s/') });
  } else if (path.startsWith('/v/')) {
    mounted = mountShared(screen, { ...ctx, id: codeFrom(path, '/v/') });
  } else if (path.startsWith('/r/')) {
    mounted = mountRoom(screen, { ...ctx, code: codeFrom(path, '/r/') });
  } else {
    navigate('/', { replace: true });
    return;
  }
  teardown = mounted && mounted.unmount ? mounted.unmount : null;
}

// Intercept internal links marked with [data-nav].
document.addEventListener('click', (e) => {
  const a = e.target.closest('a[data-nav]');
  if (a && a.getAttribute('href')) {
    e.preventDefault();
    navigate(a.getAttribute('href'));
  }
});

window.addEventListener('popstate', route);
route();
