let csrf = '', opened = false;
let isAdmin = false, warpEnabled = false;
let sessionName = 'Private browser', ending = false, sessionError = '';
function notifyDesktop() {
  document.getElementById('desktop').contentWindow.postMessage({type:'brave-session-state',
    active:opened, name:sessionName, admin:isAdmin, ending, error:sessionError}, location.origin);
}
async function update() {
  try {
    const response = await fetch('/session/status', {cache:'no-store'});
    const state = await response.json();
    csrf = state.csrf || '';
    isAdmin = state.admin === true;
    if (!isAdmin) document.getElementById('admin-panel').close();
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
  if (event.data?.type === 'brave-session-admin' && isAdmin) openAdmin();
  if (event.data?.type === 'brave-session-end') endSession();
});
update(); setInterval(update, 3000);

async function openAdmin() {
  const panel = document.getElementById('admin-panel');
  if (!panel.open) panel.showModal();
  document.getElementById('admin-error').textContent = '';
  try {
    const response = await fetch('/admin/status', {cache:'no-store'});
    if (!response.ok) throw new Error('Administrator access unavailable.');
    const state = await response.json();
    warpEnabled = state.network.mode === 'warp';
    document.getElementById('admin-network').textContent = `Browser network: ${state.network.mode} — ${state.network.state}`;
    const toggle = document.getElementById('admin-warp');
    toggle.textContent = warpEnabled ? 'Disable WARP' : 'Enable WARP';
    toggle.disabled = !warpEnabled && (!state.warp_available || !state.tos_accepted);
    if (toggle.disabled) document.getElementById('admin-error').textContent = 'Enable WARP support in the image and set WARP_ACCEPT_TOS=true first.';
    const users = document.getElementById('admin-users');
    users.replaceChildren();
    for (const user of state.users) {
      const row = document.createElement('li');
      row.textContent = `${user.name}${user.admin ? ' (admin)' : ''} — ${user.state.toLowerCase()}`;
      users.append(row);
    }
  } catch (error) { document.getElementById('admin-error').textContent = error.message; }
}
document.getElementById('admin-close').onclick = () => document.getElementById('admin-panel').close();
document.getElementById('admin-warp').onclick = async () => {
  if (!confirm('Change WARP for everyone? All active desktops will close. Users must sign in again.')) return;
  document.getElementById('admin-warp').disabled = true;
  try {
    const response = await fetch('/admin/warp', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({enabled:!warpEnabled})});
    if (!response.ok) throw new Error(await response.text());
    location.reload();
  } catch (error) {
    document.getElementById('admin-error').textContent = error.message;
    document.getElementById('admin-warp').disabled = false;
  }
};
