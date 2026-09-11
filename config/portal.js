let csrf = '', opened = false;
let sessionName = 'Private browser', ending = false, sessionError = '';
function notifyDesktop() {
  document.getElementById('desktop').contentWindow.postMessage({type:'brave-session-state',
    active:opened, name:sessionName, ending, error:sessionError}, location.origin);
}
async function update() {
  try {
    const response = await fetch('/session/status', {cache:'no-store'});
    const state = await response.json();
    csrf = state.csrf || '';
    const active = state.owner && state.state === 'RUNNING';
    document.getElementById('welcome').hidden = active;
    document.getElementById('session').hidden = !active;
    document.getElementById('login').hidden = state.state !== 'IDLE';
    document.getElementById('message').textContent = state.state === 'IDLE'
      ? 'Sign in to open your private browser and desktop.'
      : state.owner ? 'Preparing your browser…'
      : state.state === 'ERROR' ? 'The service needs administrator attention.' : 'All desktop slots are occupied or maintenance is in progress. Please try again later.';
    sessionName = state.name || 'Private browser';
    if (active && !opened) { document.getElementById('desktop').src = '/desktop/'; opened = true; }
    if (!active && opened) { document.getElementById('desktop').src = 'about:blank'; opened = false; }
    notifyDesktop();
  } catch { document.getElementById('message').textContent = 'Reconnecting to the service…'; }
}
async function endSession() {
  if (!opened || ending) return;
  ending = true; sessionError = ''; notifyDesktop();
  try {
    const response = await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':csrf}});
    if (!response.ok) throw new Error('Logout failed');
    await update();
  } catch { sessionError = 'Could not end the session. Please retry.'; }
  finally { ending = false; notifyDesktop(); }
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== document.getElementById('desktop').contentWindow) return;
  if (event.data?.type === 'brave-session-ready') notifyDesktop();
  if (event.data?.type === 'brave-session-end') endSession();
});
update(); setInterval(update, 3000);
