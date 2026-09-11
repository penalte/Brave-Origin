let csrf = '', opened = false;
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
    document.getElementById('name').textContent = state.name || 'Private browser';
    if (active && !opened) { document.getElementById('desktop').src = '/desktop/'; opened = true; }
    if (!active && opened) { document.getElementById('desktop').src = 'about:blank'; opened = false; }
  } catch { document.getElementById('message').textContent = 'Reconnecting to the service…'; }
}
document.getElementById('logout').onclick = async () => {
  const response = await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':csrf}});
  if (response.ok) { await update(); } else { alert('Could not end the session. Please retry.'); }
};
update(); setInterval(update, 3000);
