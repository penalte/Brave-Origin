'use strict';
// My files: the portal's file manager. It talks only to /files/* on this origin
// and builds every element from text, never from markup in a file name.
(() => {
const root = document.getElementById('files-app');
if (!root) return;

const SHAPES = {
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  image: 'M4 5h16v14H4Zm0 11 5-5 4 4 3-3 4 4M9 9.5a1 1 0 1 0 0-.01',
  video: 'M4 5h16v14H4Zm6 4v6l5-3Z',
  audio: 'M9 18V6l10-2v12M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm10-2a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z',
  pdf: 'M6 3h8l4 4v14H6Zm8 0v4h4M9 13h6M9 16h6M9 10h2',
  text: 'M6 3h8l4 4v14H6Zm8 0v4h4M9 12h6M9 15h6M9 18h4',
  document: 'M6 3h8l4 4v14H6Zm8 0v4h4M9 11h6v7H9Zm0 3.5h6M12 11v7',
  other: 'M6 3h8l4 4v14H6Zm8 0v4h4',
  upload: 'M12 16V5m0 0-4 4m4-4 4 4M5 19h14',
  plus: 'M12 5v14M5 12h14',
  grid: 'M4 4h7v7H4Zm9 0h7v7h-7ZM4 13h7v7H4Zm9 0h7v7h-7Z',
  list: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6 6 18',
  left: 'm14 6-6 6 6 6',
  right: 'm10 6 6 6-6 6',
};
const THUMBNAILS = new Set(['image', 'video', 'audio', 'pdf']);
const PIECE = 4 * 1024 * 1024;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else if (value !== false && value != null) node.setAttribute(key, value);
  }
  node.append(...children.filter(child => child != null));
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', SHAPES[name] || SHAPES.other);
  svg.append(path);
  return svg;
}
const button = (label, onclick, props = {}, shape) => el('button', {type: 'button', onclick, ...props}, shape ? icon(shape) : null, label);
function readableSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit++; }
  return value.toFixed(value < 10 ? 1 : 0) + ' ' + units[unit];
}
const readableDate = seconds => new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
const address = (route, params) => route + '?' + new URLSearchParams(params);
const shapeOf = entry => entry.folder ? 'folder' : entry.kind !== 'other' ? entry.kind : entry.document ? 'document' : 'other';
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const saved = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch { return fallback; } };
const save = (key, value) => { try { localStorage.setItem(key, value); } catch { /* Private mode keeps nothing. */ } };

let token = () => '';
let path = [], entries = [], shown = [], office = false, limit = 0, loadRun = 0;
let selected = new Set(), anchor = null, focused = null;
let view = saved('files-view', 'grid'), order = saved('files-sort', 'name');
let viewing = [], viewingAt = 0, viewRun = 0, toastTimer = 0;
const jobs = [];
let sending = 0;

// ---- Layout -----------------------------------------------------------------
const crumbs = el('nav', {class: 'fa-crumbs', 'aria-label': 'Folder'});
const search = el('input', {type: 'search', placeholder: 'Search this folder', 'aria-label': 'Search this folder', oninput: () => draw()});
const sorter = el('select', {'aria-label': 'Sort by', onchange: () => { order = sorter.value; save('files-sort', order); draw(); }},
  ...[['name', 'Name'], ['newest', 'Newest first'], ['largest', 'Largest first'], ['type', 'Type']].map(([value, label]) => el('option', {value, text: label})));
sorter.value = order;
const viewButton = button('', () => { view = view === 'grid' ? 'list' : 'grid'; save('files-view', view); draw(); }, {class: 'plain'});
const picker = el('input', {type: 'file', multiple: true, hidden: true, onchange: () => { upload(picker.files, path); picker.value = ''; }});
const actions = el('div', {class: 'fa-actions'});
const list = el('div', {class: 'fa-list', role: 'listbox', 'aria-multiselectable': 'true', 'aria-label': 'Files in this folder', tabindex: '0'});
const dropNote = el('div', {class: 'fa-drop', hidden: true});
const uploads = el('aside', {class: 'fa-uploads', hidden: true, 'aria-label': 'Uploads'});
const toastBox = el('div', {class: 'fa-toast', role: 'status', 'aria-live': 'polite'});
const menu = el('menu', {class: 'fa-menu', hidden: true});
const stage = el('div', {class: 'fa-stage'});
const viewName = el('span', {class: 'fa-name'}), viewCount = el('span', {class: 'fa-meta'});
const viewPrevious = button('', () => step(-1), {class: 'plain', 'aria-label': 'Previous file', title: 'Previous (Left arrow)'}, 'left');
const viewNext = button('', () => step(1), {class: 'plain', 'aria-label': 'Next file', title: 'Next (Right arrow)'}, 'right');
const viewOpen = button('Open in Brave', () => openInBrave(viewing[viewingAt]));
const viewer = el('div', {class: 'fa-preview', hidden: true},
  el('header', {}, viewName, viewCount, viewPrevious, viewNext, viewOpen,
    button('Download', () => download([viewing[viewingAt]])),
    button('', () => closeViewer(), {class: 'plain', 'aria-label': 'Close preview', title: 'Close (Esc or Space)'}, 'close')),
  stage);
root.append(
  el('header', {class: 'fa-bar'},
    el('span', {class: 'fa-title'}, icon('folder'), 'My files'), crumbs,
    el('div', {class: 'fa-tools'}, search, sorter, viewButton,
      button('New folder', () => newFolder(), {}, 'plus'),
      button('Upload', () => picker.click(), {class: 'primary'}, 'upload'), picker,
      button('', () => shut(), {class: 'plain', 'aria-label': 'Close My files', title: 'Close My files'}, 'close'))),
  actions, list, dropNote, uploads, toastBox, menu, viewer);

function toast(text, failed) {
  toastBox.textContent = text;
  toastBox.classList.toggle('error', !!failed);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastBox.textContent = ''; }, failed ? 7000 : 3500);
}

// ---- Server -----------------------------------------------------------------
const joined = parts => parts.join('/');
const contentAddress = (entry, download) => address('/files/content',
  download ? {path: joined([...path, entry.name]), download: '1'} : {path: joined([...path, entry.name])});
async function failure(response) {
  const text = (await response.text()).trim();
  return new Error(text && !/^\d{3}: /.test(text) ? text : 'That did not work. Please try again.');
}
async function act(body) {
  const response = await fetch('/files/action', {method: 'POST', body: JSON.stringify(body),
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token()}});
  if (!response.ok) throw await failure(response);
  return response.json();
}
async function load(keep) {
  const run = ++loadRun;
  try {
    const response = await fetch(address('/files/list', {path: joined(path)}), {cache: 'no-store'});
    if (run !== loadRun) return;
    if (response.status === 404 && path.length) { path = []; return load(); }
    if (!response.ok) throw await failure(response);
    const data = await response.json();
    if (run !== loadRun) return;
    entries = data.entries; office = data.office; limit = data.upload_limit;
    const names = new Set(entries.map(entry => entry.name));
    selected = keep ? new Set([...selected].filter(name => names.has(name))) : new Set();
    if (!names.has(focused)) focused = null;
    draw();
  } catch (error) {
    if (run === loadRun) toast(error.message, true);
  }
}
function go(parts) {
  path = parts; search.value = ''; entries = []; selected = new Set(); focused = anchor = null;
  draw(true);
  return load();
}

// ---- Drawing ----------------------------------------------------------------
function sortedEntries() {
  const needle = search.value.trim().toLowerCase();
  const result = entries.filter(entry => !needle || entry.name.toLowerCase().includes(needle));
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, {numeric: true, sensitivity: 'base'});
  const rule = {newest: (a, b) => b.modified - a.modified || byName(a, b), largest: (a, b) => b.size - a.size || byName(a, b),
    type: (a, b) => shapeOf(a).localeCompare(shapeOf(b)) || byName(a, b)}[order] || byName;
  return result.sort((a, b) => (b.folder - a.folder) || rule(a, b));
}
function picture(entry) {
  const shape = shapeOf(entry);
  const box = el('span', {class: 'fa-pic' + (entry.folder ? ' folder' : '')});
  if (THUMBNAILS.has(entry.kind)) {
    const image = el('img', {alt: '', loading: 'lazy', draggable: 'false',
      src: address('/files/thumb', {path: joined([...path, entry.name]), size: '256', v: entry.modified + '-' + entry.size})});
    image.addEventListener('error', () => box.replaceChildren(icon(shape)));
    box.append(image);
  } else box.append(icon(shape));
  return box;
}
function drawCrumbs(target, parts, choose) {
  target.replaceChildren();
  [[], ...parts.map((_, index) => parts.slice(0, index + 1))].forEach((place, index) => {
    if (index) target.append(el('span', {text: '›', 'aria-hidden': 'true'}));
    const crumb = el('button', {type: 'button', text: index ? place[place.length - 1] : 'My files', onclick: () => choose(place)});
    crumb.place = place;
    target.append(crumb);
  });
}
function draw(waiting) {
  drawCrumbs(crumbs, path, go);
  viewButton.replaceChildren(icon(view === 'grid' ? 'list' : 'grid'));
  viewButton.title = view === 'grid' ? 'Show as a list' : 'Show as a grid';
  viewButton.setAttribute('aria-label', viewButton.title);
  shown = sortedEntries();
  list.className = 'fa-list ' + view;
  if (!shown.length) {
    list.className = 'fa-list';
    list.style.display = 'flex';
    list.replaceChildren(waiting ? el('div', {class: 'fa-empty', text: 'Loading…'}) : el('div', {class: 'fa-empty'}, icon(search.value ? 'other' : 'upload'),
      el('strong', {text: search.value ? 'Nothing here matches your search' : 'This folder is empty'}),
      search.value ? 'Try a different name.' : 'Drop files anywhere on this page, or use Upload.'));
  } else {
    list.style.display = '';
    if (!shown.some(entry => entry.name === focused)) focused = shown[0].name;
    list.replaceChildren(...shown.map(entry => {
      const item = el('div', {class: 'fa-item', role: 'option', 'aria-selected': String(selected.has(entry.name)),
        tabindex: entry.name === focused ? '0' : '-1', draggable: 'true', title: entry.name},
        picture(entry), el('span', {class: 'fa-name', text: entry.name}),
        el('span', {class: 'fa-meta', text: entry.folder ? 'Folder' : readableSize(entry.size)}),
        el('span', {class: 'fa-date', text: readableDate(entry.modified)}));
      item.entry = entry;
      return item;
    }));
  }
  drawActions();
}
function chosen() { return shown.filter(entry => selected.has(entry.name)); }
function drawActions() {
  const picked = chosen(), one = picked.length === 1 ? picked[0] : null;
  const files = picked.length > 0 && picked.every(entry => !entry.folder);
  for (const item of list.querySelectorAll('.fa-item')) item.setAttribute('aria-selected', String(selected.has(item.entry.name)));
  actions.classList.toggle('idle', !picked.length);
  if (!picked.length) {
    actions.replaceChildren(el('span', {class: 'fa-hint',
      text: entries.length ? `${entries.length} item${entries.length === 1 ? '' : 's'} · Double-click to open, Space to preview, drag onto a folder to move` : ''}));
    return;
  }
  actions.replaceChildren(
    el('span', {class: 'fa-count', text: one ? one.name : picked.length + ' selected'}),
    button('Preview', () => preview(one), {disabled: !one || one.folder}),
    button('Open in Brave', () => openInBrave(one), {disabled: !one || !one.opens, title: one && !one.folder && !one.opens ? "Brave can't display this type of file" : null}),
    button('Download', () => download(picked), {disabled: !files, title: files ? null : 'Folders cannot be downloaded; open the folder and download its files'}),
    button('Rename', () => rename(one), {disabled: !one}),
    button('Move to…', () => relocate('move', picked)),
    button('Copy to…', () => relocate('copy', picked)),
    button('Delete', () => remove(picked), {class: 'danger'}),
    el('span', {class: 'fa-space'}),
    button('Clear selection', () => { selected.clear(); drawActions(); }, {class: 'plain'}));
}
function focusItem(name) {
  focused = name;
  for (const item of list.querySelectorAll('.fa-item')) {
    const own = item.entry.name === name;
    item.tabIndex = own ? 0 : -1;
    if (own) { item.focus(); item.scrollIntoView({block: 'nearest'}); }
  }
}

// ---- Selection, keyboard and pointer ---------------------------------------
function pick(entry, event) {
  const names = shown.map(item => item.name), at = names.indexOf(entry.name);
  if (event.shiftKey && anchor !== null && names.includes(anchor)) {
    const from = names.indexOf(anchor);
    selected = new Set(names.slice(Math.min(from, at), Math.max(from, at) + 1));
  } else if (event.ctrlKey || event.metaKey) {
    selected.has(entry.name) ? selected.delete(entry.name) : selected.add(entry.name);
    anchor = entry.name;
  } else { selected = new Set([entry.name]); anchor = entry.name; }
  focusItem(entry.name);
  drawActions();
}
function activate(entry) {
  if (entry.folder) go([...path, entry.name]); else preview(entry);
}
list.addEventListener('click', event => {
  const item = event.target.closest('.fa-item');
  if (!item) { selected.clear(); drawActions(); return; }
  // A finger has no double click: a tap opens, a long press selects.
  if (matchMedia('(pointer: coarse)').matches && !selected.size && !event.shiftKey && !event.ctrlKey) return activate(item.entry);
  pick(item.entry, event);
});
list.addEventListener('dblclick', event => { const item = event.target.closest('.fa-item'); if (item) activate(item.entry); });
list.addEventListener('keydown', event => {
  if (!shown.length || event.altKey) return;
  const names = shown.map(entry => entry.name), at = Math.max(0, names.indexOf(focused)), entry = shown[at];
  const items = [...list.querySelectorAll('.fa-item')];
  const columns = view === 'grid' ? Math.max(1, items.filter(item => item.offsetTop === items[0].offsetTop).length) : 1;
  const move = {ArrowRight: view === 'grid' ? 1 : 0, ArrowLeft: view === 'grid' ? -1 : 0, ArrowDown: columns, ArrowUp: -columns,
    Home: -at, End: names.length - 1 - at}[event.key];
  if (move !== undefined) {
    event.preventDefault();
    const next = shown[Math.min(names.length - 1, Math.max(0, at + move))];
    if (event.shiftKey) pick(next, event);
    else { selected = new Set([next.name]); anchor = next.name; focusItem(next.name); drawActions(); }
  } else if (event.key === 'Enter') { event.preventDefault(); activate(entry); }
  else if (event.key === ' ') { event.preventDefault(); if (entry.folder) pick(entry, event); else preview(entry); }
  else if (event.key === 'Delete' && selected.size) { event.preventDefault(); remove(chosen()); }
  else if (event.key === 'F2' && selected.size === 1) { event.preventDefault(); rename(chosen()[0]); }
  else if (event.key === 'Backspace' && path.length) { event.preventDefault(); go(path.slice(0, -1)); }
  else if (event.key.toLowerCase() === 'a' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); selected = new Set(names); drawActions(); }
});
function closeMenu() { menu.hidden = true; menu.replaceChildren(); }
list.addEventListener('contextmenu', event => {
  const item = event.target.closest('.fa-item');
  event.preventDefault();
  if (!item) { selected.clear(); drawActions(); }
  else if (!selected.has(item.entry.name)) pick(item.entry, {});
  const picked = chosen(), one = picked.length === 1 ? picked[0] : null;
  const choices = picked.length ? [
    one && !one.folder && ['Preview', () => preview(one)], one && one.folder && ['Open', () => activate(one)],
    one && one.opens && ['Open in Brave', () => openInBrave(one)],
    picked.every(entry => !entry.folder) && ['Download', () => download(picked)],
    one && ['Rename', () => rename(one)], ['Move to…', () => relocate('move', picked)], ['Copy to…', () => relocate('copy', picked)],
    ['Delete', () => remove(picked)],
  ] : [['New folder', () => newFolder()], ['Upload files', () => picker.click()], ['Refresh', () => load(true)]];
  menu.replaceChildren(...choices.filter(Boolean).map(([label, run]) => el('li', {}, button(label, () => { closeMenu(); run(); }))));
  menu.hidden = false;
  menu.style.left = Math.min(event.clientX, innerWidth - menu.offsetWidth - 8) + 'px';
  menu.style.top = Math.min(event.clientY, innerHeight - menu.offsetHeight - 8) + 'px';
  menu.querySelector('button').focus();
});
root.addEventListener('pointerdown', event => { if (!menu.hidden && !menu.contains(event.target)) closeMenu(); }, true);

// ---- Moving by dragging, uploading by dropping ------------------------------
const MOVING = 'application/x-my-files';
let dragged = [];
list.addEventListener('dragstart', event => {
  const item = event.target.closest('.fa-item');
  if (!item) return;
  if (!selected.has(item.entry.name)) pick(item.entry, {});
  dragged = chosen().map(entry => entry.name);
  event.dataTransfer.setData(MOVING, 'names');
  event.dataTransfer.effectAllowed = 'move';
});
function dropPlace(event) {
  const crumb = event.target.closest('.fa-crumbs button'), item = event.target.closest('.fa-item');
  if (crumb && crumbs.contains(crumb)) return {node: crumb, parts: crumb.place};
  if (item && item.entry.folder && !dragged.includes(item.entry.name)) return {node: item, parts: [...path, item.entry.name]};
  return null;
}
function clearTargets() { for (const node of root.querySelectorAll('.target')) node.classList.remove('target'); }
root.addEventListener('dragover', event => {
  const types = [...event.dataTransfer.types];
  if (types.includes(MOVING)) {
    clearTargets();
    const place = dropPlace(event);
    if (place && joined(place.parts) !== joined(path)) { event.preventDefault(); place.node.classList.add('target'); }
  } else if (types.includes('Files')) {
    event.preventDefault();
    const place = dropPlace(event);
    dropNote.hidden = false;
    dropNote.textContent = 'Drop to upload to ' + (place ? place.parts[place.parts.length - 1] || 'My files' : path[path.length - 1] || 'My files');
  }
});
root.addEventListener('dragleave', event => { if (!root.contains(event.relatedTarget)) { dropNote.hidden = true; clearTargets(); } });
root.addEventListener('dragend', () => { clearTargets(); dragged = []; });
root.addEventListener('drop', async event => {
  const types = [...event.dataTransfer.types], place = dropPlace(event);
  dropNote.hidden = true; clearTargets();
  if (types.includes(MOVING)) {
    event.preventDefault();
    if (place) await transfer('move', dragged.map(name => ({name})), place.parts);
    dragged = [];
  } else if (types.includes('Files')) {
    event.preventDefault();
    const folders = [...event.dataTransfer.items].filter(item => item.webkitGetAsEntry && item.webkitGetAsEntry()?.isDirectory).length;
    const files = [...event.dataTransfer.files].filter(file => file.size || file.type);
    if (folders) toast('Folders cannot be uploaded whole. Create the folder here, then drop its files into it.', true);
    upload(folders ? files.filter(file => file.type) : files, place ? place.parts : path);
  }
});

// ---- Small dialogs ----------------------------------------------------------
function box(title, text, ...content) {
  const dialog = el('dialog', {class: 'fa-box'}, el('h2', {text: title}), text ? el('p', {text}) : null, ...content);
  root.append(dialog);
  return dialog;
}
function settle(dialog) {
  // Each button settles the answer itself; the close event only covers Esc.
  return new Promise(resolve => {
    let settled = false;
    dialog.finish = value => {
      if (settled) return;
      settled = true;
      if (dialog.open) dialog.close();
      dialog.remove();
      resolve(value ?? null);
    };
    dialog.addEventListener('close', () => dialog.finish(null));
    dialog.showModal();
  });
}
function ask(title, text, value, label) {
  const input = el('input', {type: 'text', value, maxlength: '200', 'aria-label': title, autocomplete: 'off', spellcheck: 'false'});
  const dialog = box(title, text, el('form', {method: 'dialog', onsubmit: event => {
    event.preventDefault();
    if (input.value.trim()) dialog.finish(input.value.trim());
  }}, input, el('div', {class: 'fa-buttons'}, button('Cancel', () => dialog.finish(null)), el('button', {class: 'primary', text: label}))));
  const done = settle(dialog);
  input.focus();
  const dot = value.lastIndexOf('.');
  input.setSelectionRange(0, dot > 0 ? dot : value.length);
  return done;
}
function confirmBox(title, text, label) {
  const dialog = box(title, text, el('div', {class: 'fa-buttons'}, button('Cancel', () => dialog.finish(null)),
    button(label, () => dialog.finish(true), {class: 'danger'})));
  return settle(dialog);
}
function chooseFolder(title, label) {
  let place = [...path], run = 0;
  const trail = el('nav', {class: 'fa-crumbs', 'aria-label': 'Destination'}), folders = el('div', {class: 'fa-folders'});
  const dialog = box(title, '', trail, folders, el('div', {class: 'fa-buttons'}, button('Cancel', () => dialog.finish(null)),
    button(label, () => dialog.finish(place), {class: 'primary'})));
  async function show(parts) {
    place = parts;
    const current = ++run;
    drawCrumbs(trail, place, show);
    folders.replaceChildren(el('p', {text: 'Loading…'}));
    try {
      const response = await fetch(address('/files/list', {path: joined(place)}), {cache: 'no-store'});
      if (!response.ok) throw await failure(response);
      const inside = (await response.json()).entries.filter(entry => entry.folder);
      if (current !== run) return;
      folders.replaceChildren(...(inside.length ? inside.map(entry => button(entry.name, () => show([...place, entry.name]), {}, 'folder'))
        : [el('p', {text: 'No folders in here.'})]));
    } catch (error) { if (current === run) folders.replaceChildren(el('p', {text: error.message})); }
  }
  show(place);
  return settle(dialog);
}

// ---- Actions ----------------------------------------------------------------
async function newFolder() {
  const name = await ask('New folder', 'Name the folder to create here.', 'New folder', 'Create');
  if (!name) return;
  try { await act({action: 'mkdir', path: joined([...path, name])}); await load(); selected = new Set([name]); focused = name; draw(); }
  catch (error) { toast(error.message, true); }
}
async function rename(entry) {
  const name = await ask('Rename', `New name for “${entry.name}”.`, entry.name, 'Rename');
  if (!name || name === entry.name) return;
  try { await act({action: 'rename', path: joined([...path, entry.name]), name}); await load(); selected = new Set([name]); focused = name; draw(); }
  catch (error) { toast(error.message, true); }
}
async function remove(picked) {
  const label = picked.length === 1 ? `“${picked[0].name}”` : picked.length + ' items';
  const folders = picked.some(entry => entry.folder) ? ' Folders are deleted with everything in them.' : '';
  if (!await confirmBox('Delete ' + label + '?', 'This cannot be undone.' + folders, 'Delete')) return;
  let failed = '';
  for (const entry of picked) {
    try { await act({action: 'delete', path: joined([...path, entry.name])}); } catch (error) { failed = error.message; }
  }
  await load();
  if (failed) toast(failed, true); else toast(picked.length === 1 ? 'Deleted.' : picked.length + ' items deleted.');
}
async function transfer(action, picked, target) {
  let failed = '', done = 0;
  for (const entry of picked) {
    try { await act({action, path: joined([...path, entry.name]), to: joined(target)}); done++; } catch (error) { failed = error.message; }
  }
  await load();
  const where = target[target.length - 1] || 'My files';
  if (failed) toast(failed, true);
  else toast(`${done} item${done === 1 ? '' : 's'} ${action === 'move' ? 'moved' : 'copied'} to ${where}.`);
}
async function relocate(action, picked) {
  const target = await chooseFolder(action === 'move' ? 'Move to…' : 'Copy to…', action === 'move' ? 'Move here' : 'Copy here');
  if (!target || action === 'move' && joined(target) === joined(path)) return;
  await transfer(action, picked, target);
}
function download(picked) {
  picked.forEach((entry, index) => setTimeout(() => {
    const link = el('a', {href: contentAddress(entry, true), download: entry.name, hidden: true});
    root.append(link); link.click(); link.remove();
  }, index * 250));
}
async function openInBrave(entry) {
  try {
    const result = await act({action: 'open', path: joined([...path, entry.name])});
    toast(result.viewer === 'office' ? 'Opened in the document viewer, in a new Brave tab.' : 'Opened in a new Brave tab.');
  } catch (error) { toast(error.message, true); }
}

// ---- Preview ----------------------------------------------------------------
function emptyStage() {
  viewRun++;
  for (const media of stage.querySelectorAll('video,audio')) { media.pause(); media.removeAttribute('src'); media.load(); }
  stage.replaceChildren();
}
function note(entry, text) {
  stage.replaceChildren(el('div', {class: 'fa-note'}, icon(shapeOf(entry)), el('span', {text}),
    button('Download', () => download([entry]), {class: 'primary'})));
}
async function showViewer() {
  emptyStage();
  const run = viewRun, entry = viewing[viewingAt], source = contentAddress(entry);
  viewName.textContent = entry.name;
  viewCount.textContent = `${readableSize(entry.size)} · ${viewingAt + 1} of ${viewing.length}`;
  viewPrevious.disabled = viewingAt === 0; viewNext.disabled = viewingAt === viewing.length - 1;
  viewOpen.hidden = !entry.opens;
  const failed = () => { if (run === viewRun) note(entry, 'This file could not be shown here. Download it to open it on your device.'); };
  if (entry.kind === 'image') stage.append(el('img', {src: source, alt: entry.name, onerror: failed}));
  else if (entry.kind === 'video') stage.append(el('video', {src: source, controls: true, autoplay: true, playsinline: true, onerror: failed}));
  else if (entry.kind === 'audio') stage.append(el('div', {class: 'fa-sound'}, icon('audio'), el('audio', {src: source, controls: true, autoplay: true, onerror: failed})));
  else if (entry.kind === 'pdf') stage.append(el('iframe', {src: source, title: entry.name}));
  else if (entry.kind === 'text') {
    stage.append(el('div', {class: 'fa-note', text: 'Loading…'}));
    try {
      const response = await fetch(source, {headers: {Range: 'bytes=0-524287'}});
      if (!response.ok) throw new Error('Unavailable');
      const text = await response.text();
      if (run !== viewRun) return;
      stage.replaceChildren(el('pre', {text: text + (entry.size > 524288 ? '\n\n… The rest is not shown. Download the file to read all of it.' : '')}));
    } catch { failed(); }
  } else if (entry.document && office) {
    stage.append(el('div', {class: 'fa-note', text: 'Opening the document…'}));
    try {
      const {url} = await act({action: 'link', path: joined([...path, entry.name])});
      if (run === viewRun) stage.replaceChildren(el('iframe', {src: url, title: entry.name}));
    } catch (error) { if (run === viewRun) note(entry, error.message); }
  } else note(entry, entry.document ? 'Office documents need a document server, and none is configured. Download the file to open it on your device.'
    : 'There is no preview for this type of file.');
}
function preview(entry) {
  viewing = shown.filter(item => !item.folder);
  viewingAt = Math.max(0, viewing.findIndex(item => item.name === entry.name));
  viewer.hidden = false;
  showViewer();
  viewer.querySelector('header button:last-child').focus();
}
function step(by) {
  const next = viewingAt + by;
  if (next < 0 || next >= viewing.length) return;
  viewingAt = next;
  selected = new Set([viewing[next].name]); focused = viewing[next].name;
  drawActions();
  showViewer();
}
function closeViewer() {
  if (viewer.hidden) return;
  emptyStage();
  viewer.hidden = true;
  if (focused) focusItem(focused);
}
viewer.addEventListener('keydown', event => {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    if (event.target.matches('video,audio')) return;
    event.preventDefault(); step(event.key === 'ArrowLeft' ? -1 : 1);
  } else if (event.key === ' ' && !event.target.matches('video,audio,button')) { event.preventDefault(); closeViewer(); }
});

// ---- Uploads ----------------------------------------------------------------
function drawUploads() {
  uploads.hidden = !jobs.length;
  if (!jobs.length) return;
  const active = jobs.filter(job => job.state === 'waiting' || job.state === 'sending').length;
  uploads.replaceChildren(
    el('header', {}, el('span', {text: active ? `Uploading ${active} file${active === 1 ? '' : 's'}` : 'Uploads finished'}),
      button(active ? 'Hide finished' : 'Close', () => {
        for (let index = jobs.length - 1; index >= 0; index--) if (!['waiting', 'sending'].includes(jobs[index].state)) jobs.splice(index, 1);
        drawUploads();
      }, {class: 'plain'})),
    ...jobs.map(job => {
      const text = {waiting: 'Waiting', sending: `${readableSize(job.sent)} of ${readableSize(job.file.size)}`,
        done: job.name === job.file.name ? 'Uploaded' : `Uploaded as ${job.name}`, failed: job.error, cancelled: 'Cancelled'}[job.state];
      const control = job.state === 'failed' ? button('Retry', () => { job.state = 'waiting'; drawUploads(); pump(); })
        : ['waiting', 'sending'].includes(job.state) ? button('Cancel', () => { job.cancelled = true; job.abort?.abort(); if (job.state === 'waiting') job.state = 'cancelled'; drawUploads(); }) : null;
      return el('div', {class: 'fa-job ' + job.state}, el('span', {class: 'fa-name', text: job.file.name, title: job.file.name}), control,
        el('small', {text}), job.state === 'sending' ? el('progress', {max: String(job.file.size || 1), value: String(job.file.size ? job.sent : 1)}) : null);
    }));
}
function upload(files, folder) {
  for (const file of files) {
    if (limit && file.size > limit) { toast(`“${file.name}” is larger than the ${readableSize(limit)} upload limit.`, true); continue; }
    if (!file.name || file.name.startsWith('.')) { toast(`“${file.name}” cannot be uploaded: names starting with a dot are not allowed.`, true); continue; }
    const id = [...crypto.getRandomValues(new Uint8Array(16))].map(value => (value % 36).toString(36)).join('');
    jobs.push({file, folder: [...folder], sent: 0, state: 'waiting', id});
  }
  drawUploads();
  pump();
}
function pump() {
  while (sending < 2) {
    const job = jobs.find(item => item.state === 'waiting');
    if (!job) break;
    sending++;
    send(job).finally(() => { sending--; drawUploads(); pump(); });
  }
}
async function send(job) {
  job.state = 'sending'; job.cancelled = false;
  let lost = 0, restarted = false;
  try {
    for (;;) {
      if (job.cancelled) throw new Error('Cancelled');
      drawUploads();
      job.abort = new AbortController();
      let response;
      try {
        response = await fetch(address('/files/upload', {path: joined(job.folder), name: job.file.name}), {
          method: 'POST', body: job.file.slice(job.sent, job.sent + PIECE), signal: job.abort.signal,
          headers: {'Content-Type': 'application/octet-stream', 'X-CSRF-Token': token(), 'X-Upload-Id': job.id,
            'X-Upload-Offset': String(job.sent), 'X-Upload-Total': String(job.file.size)}});
      } catch (error) {
        if (job.cancelled) throw error;
        // A dropped connection: wait and continue from what the server has.
        if (++lost > 5) throw new Error('The connection was lost. Retry to continue.');
        await wait(1000 * lost);
        continue;
      }
      lost = 0;
      if (response.status === 409) { job.sent = (await response.json()).offset; continue; }
      if (response.status === 404 && job.sent && !restarted) { job.sent = 0; restarted = true; continue; }
      if (!response.ok) throw await failure(response);
      const result = await response.json();
      if (result.done) { job.state = 'done'; job.name = result.name; break; }
      job.sent = result.offset;
    }
    if (joined(job.folder) === joined(path) && root.open) load(true);
  } catch (error) {
    job.state = job.cancelled ? 'cancelled' : 'failed';
    job.error = error.message;
  }
}
addEventListener('beforeunload', event => { if (jobs.some(job => job.state === 'sending' || job.state === 'waiting')) event.preventDefault(); });

// ---- Opening and closing ----------------------------------------------------
// Esc closes the innermost thing first: menu, preview, selection, then the page.
root.addEventListener('cancel', event => {
  if (event.target !== root) return;
  if (!menu.hidden) { event.preventDefault(); closeMenu(); }
  else if (!viewer.hidden) { event.preventDefault(); closeViewer(); }
  else if (selected.size) { event.preventDefault(); selected.clear(); drawActions(); }
});
function shut() {
  closeMenu(); closeViewer();
  if (root.open) root.close();
}
root.addEventListener('close', event => { if (event.target === root) shut(); });
window.MyFiles = {
  open(csrf) {
    token = csrf;
    if (!root.open) root.showModal();
    draw(true);
    load(true).then(() => list.focus());
  },
  close() { shut(); },
};
})();
