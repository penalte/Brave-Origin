let csrf = '', opened = false;
let isAdmin = false, forceWarp = false, warpBusy = false;
let picture = '';
let network = {mode:'unknown', state:'unavailable'};
let sessionName = 'Private browser', ending = false, sessionError = '';
let preparing = false, streamReady = false, preparationTimer, preparationCloseTimer;
let preparationRequest = false;
let startupCompleted = false;
let checkingConnection = false;
async function checkConnection() {
  if (checkingConnection) return true;
  checkingConnection = true;
  try {
    const response = await fetch('/session/connection', {method:'POST',headers:{'X-CSRF-Token':csrf}});
    if (!response.ok) throw Error('Could not check the desktop connection. Please reload.');
    const data = await response.json();
    if (data.location) { location.replace(data.location); return true; }
    return false;
  } finally { checkingConnection = false; }
}
function needsPreparation() {
  try {
    const previous = sessionStorage.getItem('brave-prepared-session');
    sessionStorage.setItem('brave-prepared-session', csrf);
    return previous !== csrf;
  } catch { return performance.getEntriesByType('navigation')[0]?.type !== 'reload'; }
}
function finishPreparation() {
  preparing = false;
  clearInterval(preparationTimer);
  clearTimeout(preparationCloseTimer);
  document.body.classList.remove('preparing', 'preparation-closing');
  document.body.classList.remove('preparation-approved');
  document.getElementById('prepare-task').hidden = true;
  document.getElementById('welcome').removeAttribute('role');
  document.getElementById('welcome').removeAttribute('aria-modal');
  document.getElementById('welcome').hidden = opened;
  document.getElementById('prepare-countdown').hidden = true;
  document.getElementById('show-connection').hidden = true;
  document.querySelector('#welcome h1').textContent = 'Welcome back.';
}
function closePreparation() {
  if (document.body.classList.contains('preparation-closing')) return;
  clearInterval(preparationTimer);
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return finishPreparation();
  document.body.classList.add('preparation-closing');
  const card = document.getElementById('welcome');
  card.addEventListener('animationend', event => {
    if (event.animationName === 'tv-close') finishPreparation();
  }, {once:true});
  preparationCloseTimer = setTimeout(finishPreparation, 1100);
}
function beginPreparation(waitForStartup = false) {
  clearInterval(preparationTimer);
  preparationTimer = null;
  preparing = true; streamReady = false;
  const deadline = performance.now() + 3000;
  document.body.classList.add('preparing');
  document.getElementById('welcome').hidden = false;
  document.querySelector('#welcome h1').textContent = 'Preparing your desktop…';
  document.getElementById('prepare-countdown').hidden = waitForStartup;
  document.getElementById('prepare-task').hidden = false;
  document.getElementById('task-label').textContent = waitForStartup ? 'Preparing your connection and desktop' : 'Connection and desktop prepared';
  document.body.classList.toggle('preparation-approved', !waitForStartup);
  document.getElementById('prepare-seconds').textContent = '3';
  document.getElementById('prepare-ring').style.strokeDashoffset = '0';
  document.getElementById('message').textContent = 'Connecting and fitting your desktop to this screen.';
  if (waitForStartup) {
    document.getElementById('prepare-seconds').textContent = '…';
    document.getElementById('message').textContent = 'Finishing your previous session and preparing your connection…';
    return;
  }
  document.querySelector('#welcome h1').textContent = 'Your session is approved!';
  document.getElementById('message').textContent = 'All set. Your private space is about to open.';
  preparationTimer = setInterval(() => {
    const remaining = Math.max(0, deadline - performance.now());
    const left = Math.ceil(remaining / 1000);
    document.getElementById('prepare-ring').style.strokeDashoffset = String(100 * (1 - remaining / 3000));
    document.getElementById('prepare-seconds').textContent = left || '…';
    if (!left && !opened) {
      document.getElementById('desktop').src = '/desktop/'; opened = true;
    }
    if (!left && streamReady) closePreparation();
    else if (!left) document.getElementById('message').textContent = 'Opening your desktop…';
    if (preparing && performance.now() > deadline + 17000) document.getElementById('show-connection').hidden = false;
  }, 100);
}
document.getElementById('show-connection').onclick = closePreparation;
function notifyDesktop() {
  document.getElementById('desktop').contentWindow.postMessage({type:'brave-session-state',
    active:opened, name:sessionName, admin:isAdmin, picture, network:{...network, switching:warpBusy}, ending, error:sessionError}, location.origin);
}
async function update() {
  if (preparationRequest || checkingConnection || new URLSearchParams(location.search).has('session_cancelled')) return;
  try {
    const response = await fetch('/session/status', {cache:'no-store'});
    const state = await response.json();
    csrf = state.csrf || '';
    isAdmin = state.admin === true;
    picture = state.picture || '';
    network = state.network || {mode:'unknown', state:'unavailable'};
    if (!isAdmin) document.getElementById('admin-panel').close();
    const active = state.owner && state.state === 'RUNNING';
    document.getElementById('welcome').hidden = active && !preparing;
    document.getElementById('session').hidden = !active;
    document.getElementById('login').hidden = state.state !== 'IDLE';
    if (!preparing) document.getElementById('message').textContent = state.state === 'IDLE'
      ? 'Sign in to open your private browser and desktop.'
      : state.owner ? 'Preparing your browser…'
      : state.state === 'ERROR' ? 'The service needs administrator attention.' : 'All desktop slots are occupied or maintenance is in progress. Please try again later.';
    sessionName = state.name || 'Private browser';
    if (active && !opened) {
      if (preparing && preparationTimer) return;
      if (await checkConnection()) return;
      const fresh = needsPreparation();
      if (startupCompleted) {
        needsPreparation();
        document.getElementById('desktop').src = '/desktop/'; opened = true;
        preparationTimer = setInterval(() => { if (streamReady) closePreparation(); }, 100);
      } else if ((fresh && !preparing) || (preparing && !preparationTimer)) beginPreparation();
      if (!preparing) { document.getElementById('desktop').src = '/desktop/'; opened = true; }
    }
    if (!active && opened) { document.getElementById('desktop').src = 'about:blank'; opened = false; finishPreparation(); }
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
  if (event.data?.type === 'brave-desktop-stream-ready') streamReady = true;
  if (event.data?.type === 'brave-session-ready') notifyDesktop();
  if (event.data?.type === 'brave-session-admin' && isAdmin) openAdmin();
  if (event.data?.type === 'brave-session-end') endSession();
  if (event.data?.type === 'brave-session-share') openShare();
  if (event.data?.type === 'brave-session-warp') toggleWarp();
});
function renderStartup(progress) {
  const row = document.getElementById('prepare-task');
  row.replaceChildren();
  for (const task of progress.tasks || []) {
    const item = document.createElement('div');
    item.className = 'startup-item';
    const mark = document.createElement('span');
    mark.textContent = task.state === 'ready' ? '\u2713' : task.state === 'pending' ? '?' : '\u2022\u2022\u2022';
    mark.className = task.state === 'ready' ? 'ready-mark' : task.state === 'running' ? 'running-mark' : '';
    const label = document.createElement('span'); label.textContent = task.label;
    item.append(mark, label); row.append(item);
  }
  const countdown = progress.phase === 'countdown';
  const complete = countdown || progress.phase === 'opening';
  document.body.classList.toggle('preparation-approved', complete);
  document.getElementById('prepare-countdown').hidden = !countdown;
  document.querySelector('#welcome h1').textContent = complete ? 'Your session is approved!' : 'Preparing your desktop…';
  document.getElementById('message').textContent = countdown ? 'Everything is ready. Opening your desktop in…'
    : progress.phase === 'opening' ? 'Opening your desktop…'
    : progress.phase === 'desktop' ? 'Getting your browser and desktop ready.' : 'Checking your private connection…';
  if (countdown) {
    document.getElementById('prepare-seconds').textContent = Math.max(1, Math.ceil(progress.remaining));
    document.getElementById('prepare-ring').style.strokeDashoffset = String(100 * (1 - Math.min(3, progress.remaining) / 3));
  }
}
async function initializePortal() {
  if (new URLSearchParams(location.search).has('session_cancelled')) {
    document.querySelector('#welcome h1').textContent = 'Your desktop stays open.';
    document.getElementById('message').textContent = 'Continue in the original tab. You can close this one.';
    return;
  }
  if (location.pathname === '/session/prepare') {
    preparationRequest = true;
    document.body.classList.add('preparing'); preparing = true;
    document.getElementById('welcome').hidden = false;
    document.getElementById('prepare-task').hidden = false;
    document.getElementById('login').hidden = true;
    document.getElementById('retry-startup').hidden = true;
    let latest = {phase:'connection', tasks:[{label:'Connection',state:'running'},{label:'Preparing desktop',state:'pending'}]};
    renderStartup(latest);
    let polling = true;
    const poll = async () => {
      while (polling) {
        try {
          const response = await fetch('/session/preparation-status', {cache:'no-store'});
          if (response.ok && polling) renderStartup(latest = await response.json());
        } catch { /* The launch request reports failures; polling may recover. */ }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    };
    const pollingTask = poll();
    try {
      const response = await fetch('/session/prepare', {method:'POST', headers:{Accept:'application/json'}});
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Unable to prepare your desktop.');
      polling = false; await pollingTask;
      if (result.location !== '/') { location.replace(result.location); return; }
      startupCompleted = true;
      renderStartup({phase:'opening', tasks:(latest.tasks || []).map(task => ({...task, state:'ready'}))});
      history.replaceState(null, '', '/');
    } catch (error) {
      polling = false; await pollingTask;
      document.body.classList.remove('preparation-approved');
      for (const mark of document.querySelectorAll('.running-mark')) {
        mark.className = ''; mark.textContent = '!';
      }
      document.querySelector('#welcome h1').textContent = 'Let’s try that again.';
      document.getElementById('message').textContent = error.message;
      document.getElementById('prepare-countdown').hidden = true;
      document.getElementById('retry-startup').hidden = false;
      return;
    }
    preparationRequest = false;
  }
  await update();
}
document.getElementById('retry-startup').onclick = initializePortal;
initializePortal(); setInterval(update, 3000);

async function openAdmin() {
  const panel = document.getElementById('admin-panel');
  if (!panel.open) panel.showModal();
  document.getElementById('admin-error').textContent = '';
  try {
    const response = await fetch('/admin/status', {cache:'no-store'});
    if (!response.ok) throw new Error('Administrator access unavailable.');
    const state = await response.json();
    forceWarp = state.force_warp;
    document.getElementById('admin-network').textContent = forceWarp ? 'WARP is required for everyone.' : 'Each user has their own network route.';
    const toggle = document.getElementById('admin-warp');
    toggle.textContent = forceWarp ? 'Stop forcing WARP' : 'Force WARP for everyone';
    toggle.disabled = !forceWarp && (!state.warp_available || !state.tos_accepted);
    if (toggle.disabled) document.getElementById('admin-error').textContent = 'Enable WARP support in the image and set WARP_ACCEPT_TOS=true first.';
    const users = document.getElementById('admin-users');
    users.replaceChildren();
    for (const user of state.users) {
      const row = document.createElement('li');
      row.className = 'person-row';
      const info = personHeading(user.name, user.admin);
      const presence = document.createElement('span');
      presence.className = 'badge' + (user.state === 'RUNNING' ? ' online' : '');
      presence.textContent = user.state === 'RUNNING' ? 'Online' : user.state === 'IDLE' ? 'Offline' : user.state.toLowerCase();
      info.append(presence); row.append(info);
      const actions = document.createElement('div'); actions.className = 'person-actions';
      const permission = document.createElement('button');
      permission.textContent = user.allow_direct ? 'Revoke direct access' : 'Allow disabling WARP';
      permission.title = user.allow_direct ? 'Revoke permission and return this user to WARP' : 'Let this user toggle their own WARP connection';
      permission.onclick = async () => {
        permission.disabled = true;
        try { await sharingPost('/admin/users/warp-permission', {id:user.id, allowed:!user.allow_direct}); await openAdmin(); await update(); }
        catch(error) { document.getElementById('admin-error').textContent = error.message; permission.disabled = false; }
      };
      actions.append(permission);
      const view = document.createElement('button');
      view.textContent = 'View session'; view.disabled = user.state !== 'RUNNING';
      view.onclick = async () => {
        const popup = window.open('about:blank', '_blank');
        try { const data = await sharingPost('/admin/view', {id:user.id});
          if (popup) { popup.opener = null; popup.location = data.url; }
        } catch(error) { if (popup) popup.close(); document.getElementById('admin-error').textContent = error.message; }
      };
      view.title = user.state === 'RUNNING' ? 'Open this desktop in a new tab' : 'This user has no active desktop';
      actions.append(view); row.append(actions); users.append(row);
    }
    document.getElementById('admin-summary').textContent = `${state.users.filter(u=>u.state==='RUNNING').length} online · ${state.users.length} registered`;
    if (!state.users.length) users.append(emptyState('No registered users yet.'));
  } catch (error) { document.getElementById('admin-error').textContent = error.message; }
}
document.getElementById('admin-close').onclick = () => document.getElementById('admin-panel').close();
document.getElementById('admin-warp').onclick = async () => {
  if (!confirm('Change the WARP requirement for everyone? Desktops stay open; affected network connections may interrupt.')) return;
  document.getElementById('admin-warp').disabled = true;
  try {
    const response = await fetch('/admin/warp', {method:'POST', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({enabled:!forceWarp})});
    if (!response.ok) throw new Error(await response.text());
    await openAdmin();
  } catch (error) {
    document.getElementById('admin-error').textContent = error.message;
    document.getElementById('admin-warp').disabled = false;
  }
};

async function toggleWarp() {
  if (warpBusy || !network.can_toggle) return;
  warpBusy = true; sessionError = ''; notifyDesktop();
  const start = performance.now();
  try {
    const result = await sharingPost('/session/warp', {enabled:network.mode !== 'warp'});
    network = result.network;
  } catch(error) { sessionError = error.message; }
  finally {
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 650-(performance.now()-start))));
    warpBusy = false; notifyDesktop(); await update();
  }
}

async function sharingPost(path, data={}) {
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});
  if(!r.ok) throw Error(await r.text());
  return r.json();
}
function personHeading(name, admin) {
  const info=document.createElement('div'); info.className='person-info';
  const label=document.createElement('span'); label.className='person-name'; label.textContent=name;
  info.append(label);
  if(admin){const badge=document.createElement('span');badge.className='badge admin';badge.textContent='Admin';info.append(badge);}
  return info;
}
function emptyState(message) {
  const item=document.createElement('li');item.className='empty-state';item.textContent=message;return item;
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
      const li=document.createElement('li'); li.className='person-row';li.append(personHeading(user.name,user.admin));
      const button=document.createElement('button');button.textContent=user.control?'Revoke control':'Grant mouse and keyboard';
      button.onclick=async()=>{try{await sharingPost('/shares/control',{id:user.id,enabled:!user.control});await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
      const status=document.createElement('div');status.className='person-detail';status.textContent=(user.control?'Mouse & keyboard enabled':'View only')+' · '+(user.slot?`Player ${user.slot}`:user.waiting?'Waiting for a controller slot':user.gamepad?'Gamepad allowed — connect a controller':'No gamepad access');
      const gamepad=document.createElement('button');gamepad.textContent=user.gamepad?'Revoke gamepad':'Allow gamepad';
      gamepad.onclick=async()=>{try{await sharingPost('/shares/gamepad',{id:user.id,enabled:!user.gamepad});await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
      const disconnect=document.createElement('button');disconnect.textContent='Disconnect';
      disconnect.className='danger';
      disconnect.onclick=async()=>{try{await sharingPost('/shares/disconnect',{id:user.id});await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
      const actions=document.createElement('div');actions.className='person-actions';actions.append(button,gamepad,disconnect);
      li.append(status,actions);list.append(li);
    }
    if(!state.participants.length)list.append(emptyState('No guests connected. Create a link and invite someone in.'));
    document.getElementById('share-revoke').disabled=!state.links&&!state.viewers;
  } catch(e) {document.getElementById('share-error').textContent=e.message;}
}
document.getElementById('share-create').onclick=async()=>{try{
  const data=await sharingPost('/shares/create',{minutes:Number(document.getElementById('share-expiry').value),control:document.getElementById('share-control').checked,gamepad:document.getElementById('share-gamepad').checked});
  document.getElementById('share-link').value=data.url;document.getElementById('share-copy').disabled=false;await refreshShare();
}catch(e){document.getElementById('share-error').textContent=e.message;}};
document.getElementById('share-copy').onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('share-link').value);const button=document.getElementById('share-copy');button.textContent='Copied!';setTimeout(()=>button.textContent='Copy link',1800);}catch{document.getElementById('share-link').select();}};
document.getElementById('share-revoke').onclick=async()=>{try{await sharingPost('/shares/revoke');document.getElementById('share-link').value='';document.getElementById('share-copy').disabled=true;await refreshShare();}catch(e){document.getElementById('share-error').textContent=e.message;}};
document.getElementById('share-close').onclick=()=>document.getElementById('share-panel').close();
setInterval(()=>{if(document.getElementById('share-panel').open) refreshShare();},3000);
