let csrf = '', opened = false;
let isAdmin = false, forceWarp = false, warpBusy = false;
let picture = '';
let network = {mode:'unknown', state:'unavailable'};
let sessionName = 'Private browser', ending = false, sessionError = '';
let preparing = false, streamReady = false, preparationTimer, preparationCloseTimer;
let preparationRequest = false;
let startupCompleted = false;
let checkingConnection = false;
// Why the visitor was returned here, shown above the sign-in button.
const notice = {
  choice_expired: 'That choice timed out. Sign in again to continue.',
  signin_expired: 'That sign-in timed out. Sign in again to continue.',
  signin_cancelled: 'Sign-in was cancelled.',
  signin_failed: 'Sign-in did not complete. Please try again.',
}[new URLSearchParams(location.search).get('notice')] || '';
// Sessions end at a fixed time. Say so five minutes ahead instead of closing a
// video or a half-written form without notice.
let expiresAt = 0, expiryDismissed = false;
function renderExpiry() {
  const left = Math.ceil((expiresAt - Date.now()) / 1000);
  const show = opened && expiresAt && left > 0 && left <= 300 && !expiryDismissed;
  document.getElementById('expiry-warning').hidden = !show;
  if (show) document.getElementById('expiry-text').textContent = left > 60
    ? `This session ends in ${Math.ceil(left / 60)} minutes. Finish up, then sign in again to continue.`
    : `This session ends in ${left} seconds. Sign in again to continue.`;
}
document.getElementById('expiry-dismiss').onclick = () => { expiryDismissed = true; renderExpiry(); };
setInterval(renderExpiry, 1000);
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
    const ends = typeof state.expires_in === 'number' ? Date.now() + state.expires_in * 1000 : 0;
    // A later end time means a new sign-in; show its warning again when due.
    if (ends > expiresAt + 5000) expiryDismissed = false;
    expiresAt = ends;
    if (!isAdmin) document.getElementById('admin-panel').close();
    const active = state.owner && state.state === 'RUNNING';
    document.getElementById('welcome').hidden = active && !preparing;
    document.getElementById('session').hidden = !active;
    document.getElementById('login').hidden = state.state !== 'IDLE';
    if (!preparing) document.getElementById('message').textContent = state.state === 'IDLE'
      ? notice || 'Sign in to open your private browser and desktop.'
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
    if (!active && opened) { document.getElementById('desktop').src = 'about:blank'; opened = false; finishPreparation(); closePreview(); }
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
// Quick preview for My files. The file list runs in a frame inside the desktop
// page and asks for it here, where a preview can use the whole window. Files
// are fetched with the owner's own session, straight from their private folder.
const previewDialog = document.getElementById('file-preview');
const previewBody = document.getElementById('preview-body');
let previewItems = [], previewIndex = 0, previewRun = 0, previewObject = '', previewAbort = null;
function clearPreview() {
  previewRun++;
  if (previewAbort) previewAbort.abort();
  previewAbort = null;
  for (const media of previewBody.querySelectorAll('video,audio')) { media.pause(); media.removeAttribute('src'); media.load(); }
  previewBody.replaceChildren();
  if (previewObject) URL.revokeObjectURL(previewObject);
  previewObject = '';
}
function previewNote(text) {
  const note = document.createElement('p'); note.textContent = text; previewBody.replaceChildren(note);
}
async function renderPreview() {
  clearPreview();
  const run = previewRun, item = previewItems[previewIndex];
  document.getElementById('preview-name').textContent = item.name;
  document.getElementById('preview-count').textContent = previewItems.length > 1 ? `${previewIndex + 1} of ${previewItems.length}` : '';
  document.getElementById('preview-prev').disabled = previewIndex === 0;
  document.getElementById('preview-next').disabled = previewIndex === previewItems.length - 1;
  if (item.kind === 'image' || item.kind === 'video' || item.kind === 'audio') {
    const element = document.createElement(item.kind === 'image' ? 'img' : item.kind);
    if (item.kind === 'image') element.alt = item.name; else { element.controls = true; element.autoplay = true; }
    element.addEventListener('error', () => { if (run === previewRun) previewNote('This file could not be shown here. Use Open or download it.'); });
    element.src = item.url;
    previewBody.append(element);
    return;
  }
  if (item.kind === 'pdf' && item.bytes > 64 * 1024 * 1024) return previewNote('This PDF is too large to preview. Use Open instead.');
  previewNote('Loading…');
  try {
    previewAbort = new AbortController();
    const response = await fetch(item.url, {signal: previewAbort.signal, cache: 'no-store'});
    if (!response.ok) throw Error('unavailable');
    if (item.kind === 'pdf') {
      const data = await response.blob();
      if (run !== previewRun) return;
      previewObject = URL.createObjectURL(new Blob([data], {type: 'application/pdf'}));
      const frame = document.createElement('iframe'); frame.title = item.name; frame.src = previewObject;
      previewBody.replaceChildren(frame);
      return;
    }
    // Text: the first part is enough to recognise a file.
    const reader = response.body.getReader(), parts = [];
    let size = 0;
    while (size < 262144) {
      const {done, value} = await reader.read();
      if (done) break;
      parts.push(value); size += value.length;
    }
    reader.cancel().catch(() => {});
    if (run !== previewRun) return;
    const text = document.createElement('pre');
    text.textContent = await new Blob(parts).slice(0, 262144).text() + (size >= 262144 ? '\n…' : '');
    previewBody.replaceChildren(text);
  } catch (error) {
    if (run === previewRun && error.name !== 'AbortError') previewNote('This file could not be shown here. Use Open or download it.');
  }
}
function stepPreview(by) {
  const next = previewIndex + by;
  if (next < 0 || next >= previewItems.length) return;
  previewIndex = next; renderPreview();
}
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.data?.type !== 'brave-files-preview' || !opened) return;
  const items = [];
  for (const item of Array.isArray(event.data.items) ? event.data.items.slice(0, 2000) : []) {
    let address;
    try { address = new URL(String(item.url), location.origin); } catch { continue; }
    // Only this application's own file route, and only kinds drawn safely here.
    if (address.origin !== location.origin || !/^\/(desktop\/)?api\/files\/./.test(address.pathname)) continue;
    if (!['image', 'video', 'audio', 'pdf', 'text'].includes(item.kind)) continue;
    items.push({url: address.href, name: String(item.name).slice(0, 300), kind: item.kind, bytes: Number(item.bytes) || 0});
  }
  if (!items.length) return;
  previewItems = items;
  previewIndex = Math.min(Math.max(0, Number(event.data.index) || 0), items.length - 1);
  if (!previewDialog.open) previewDialog.showModal();
  renderPreview();
});
document.getElementById('preview-prev').onclick = () => stepPreview(-1);
document.getElementById('preview-next').onclick = () => stepPreview(1);
function closePreview() { if (previewDialog.open) previewDialog.close(); clearPreview(); }
document.getElementById('preview-close').onclick = closePreview;
previewDialog.addEventListener('close', clearPreview);
previewDialog.addEventListener('keydown', event => {
  if (event.key === 'ArrowLeft') stepPreview(-1);
  else if (event.key === 'ArrowRight') stepPreview(1);
  // Space closes, as in a quick look, unless a player is using it to pause.
  else if (event.key === ' ' && !event.target.closest('video,audio,button')) { event.preventDefault(); closePreview(); }
});
function renderStartup(progress) {
  const row = document.getElementById('prepare-task');
  row.replaceChildren();
  for (const task of progress.tasks || []) {
    const item = document.createElement('div');
    item.className = 'startup-item';
    const mark = document.createElement('span');
    if (task.state === 'ready' || task.state === 'error') {
      mark.className = task.state === 'ready' ? 'ready-mark' : 'error-mark';
      mark.textContent = task.state === 'ready' ? '\u2713' : '!';
    } else {
      // Waiting steps show still white dots; only the working step's dots jump, in green.
      mark.className = task.state === 'running' ? 'step-dots running-mark' : 'step-dots';
      mark.append(document.createElement('i'), document.createElement('i'), document.createElement('i'));
    }
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
    : progress.phase === 'desktop' ? 'Getting your browser and desktop ready.'
    : progress.phase === 'connection' ? 'Checking your private connection…' : 'Opening your profile…';
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
    let latest = {phase:'profile', tasks:[{label:'Your profile',state:'running'},{label:'Connection',state:'pending'},
      {label:'Desktop',state:'pending'},{label:'My files',state:'pending'}]};
    renderStartup(latest);
    let polling = true;
    const poll = async () => {
      while (polling) {
        try {
          const response = await fetch('/session/preparation-status', {cache:'no-store'});
          const next = response.ok && polling ? await response.json() : null;
          // Until the launch request registers its steps the server has none to report.
          if (next && polling && next.tasks?.length) renderStartup(latest = next);
        } catch { /* The launch request reports failures; polling may recover. */ }
        await new Promise(resolve => setTimeout(resolve, 200));
      }
    };
    const pollingTask = poll();
    try {
      const response = await fetch('/session/prepare', {method:'POST', headers:{Accept:'application/json'}});
      const result = await response.json().catch(() => ({}));
      // A sign-in that is no longer current cannot be retried from here.
      if (!response.ok && result.location) { polling = false; location.replace(result.location); return; }
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
        mark.className = 'error-mark'; mark.textContent = '!';
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
