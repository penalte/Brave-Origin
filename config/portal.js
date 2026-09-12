let csrf = '', opened = false;
let isAdmin = false, warpEnabled = false;
let picture = '';
let network = {mode:'unknown', state:'unavailable'};
let sessionName = 'Private browser', ending = false, sessionError = '';
function notifyDesktop() {
  document.getElementById('desktop').contentWindow.postMessage({type:'brave-session-state',
    active:opened, name:sessionName, admin:isAdmin, picture, network, ending, error:sessionError}, location.origin);
}
async function update() {
  try {
    const response = await fetch('/session/status', {cache:'no-store'});
    const state = await response.json();
    csrf = state.csrf || '';
    isAdmin = state.admin === true;
    picture = state.picture || '';
    network = state.network || {mode:'unknown', state:'unavailable'};
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
  } catch {
    network = {mode:'unknown', state:'unavailable'}; notifyDesktop();
    document.getElementById('message').textContent = 'Reconnecting to the service…';
  }
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
  if (event.data?.type === 'brave-session-share') openShare();
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
      const view = document.createElement('button');
      view.textContent = 'View session'; view.disabled = user.state !== 'RUNNING';
      view.onclick = async () => {
        const popup = window.open('about:blank', '_blank');
        try { const data = await sharingPost('/admin/view', {id:user.id});
          if (popup) { popup.opener = null; popup.location = data.url; }
        } catch(error) { if (popup) popup.close(); document.getElementById('admin-error').textContent = error.message; }
      };
      row.append(view); users.append(row);
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

async function sharingPost(path, data={}) {
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
  if(!r.ok) throw Error(await r.text());
  return r.json();
}
async function openShare() {
  const panel=document.getElementById('share-panel'); if(!panel.open) panel.showModal();
  await refreshShare();
}
async function refreshShare() {
  try {
    const r=await fetch('/shares/status',{cache:'no-store'}); if(!r.ok) throw Error('Sharing unavailable');
    const state=await r.json();
    document.getElementById('share-status').textContent=`${state.viewers} viewers (${state.admin_viewers} administrators), ${state.links} invitation links`;
    const list=document.getElementById('share-participants'); list.replaceChildren();
    for(const user of state.participants) {
      const li=document.createElement('li'); li.textContent=user.name+(user.admin?' (administrator)':'');
      const button=document.createElement('button');button.textContent=user.control?'Revoke control':'Grant mouse and keyboard';
      button.onclick=async()=>{try{await sharingPost('/shares/control',{id:user.id,enabled:!user.control});await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
      li.append(button);list.append(li);
    }
  } catch(e) {document.getElementById('share-error').textContent=e.message;}
}
document.getElementById('share-create').onclick=async()=>{try{
  const data=await sharingPost('/shares/create',{minutes:Number(document.getElementById('share-expiry').value),control:document.getElementById('share-control').checked});
  document.getElementById('share-link').value=data.url;await refreshShare();
}catch(e){document.getElementById('share-error').textContent=e.message;}};
document.getElementById('share-copy').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('share-link').value);}catch{document.getElementById('share-link').select();}};
document.getElementById('share-revoke').onclick=async()=>{try{await sharingPost('/shares/revoke');document.getElementById('share-link').value='';await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
document.getElementById('share-close').onclick=()=>document.getElementById('share-panel').close();
setInterval(()=>{if(document.getElementById('share-panel').open) refreshShare();},3000);
