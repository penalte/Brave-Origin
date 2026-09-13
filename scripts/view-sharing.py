"""Per-desktop sharing grants with independent input permissions enforced by the broker."""
import asyncio
import json
import math
import base64
import hashlib
import re
import secrets
import time
from pathlib import Path

from aiohttp import WSMsgType, web

VIEW_COOKIE = '__Secure-brave-view'


class ViewSharing:
    def sharing_routes(self):
        self.shares = {}
        self.view_lock = asyncio.Lock()
        self.token_lock = asyncio.Lock()
        return [web.get('/shares/status', self.share_status),
                web.post('/shares/create', self.share_create),
                web.post('/shares/revoke', self.share_revoke),
                web.post('/shares/open', self.share_open),
                web.post('/shares/control', self.share_control),
                web.post('/shares/gamepad', self.share_gamepad),
                web.post('/shares/disconnect', self.share_disconnect),
                web.post('/admin/view', self.admin_view),
                web.get('/view/', self.view_page), web.get('/view.js', self.view_script),
                web.get('/view/ended', self.view_ended),
                web.get('/view-lifecycle.js', self.view_lifecycle),
                web.route('*', '/watch/{path:.*}', self.view_proxy)]

    def share_owner(self, request, mutate=False):
        session = self.selected(request)
        if not session or session.state != 'RUNNING':
            raise web.HTTPForbidden(text='Active desktop required')
        if mutate and (request.headers.get('Origin') != request['app_origin'] or
                not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), session.owner['csrf'])):
            raise web.HTTPForbidden(text='Invalid sharing request')
        return session

    def grant_valid(self, grant, request=None):
        session = grant['session']
        owner = session.owner
        valid = (not grant.get('parent') or self.shares.get(grant['parent']) is grant.get('invitation'))
        valid = valid and (grant['expires'] > time.time() and session in self.sessions.values()
                 and session.state == 'RUNNING' and owner and owner['expires'] > time.time()
                 and owner['cookie'] == grant['generation'])
        if grant['admin']:
            admin = grant['admin']
            valid = valid and admin in self.sessions.values() and admin.state == 'RUNNING' and admin.owner
            valid = valid and admin.owner.get('admin') and admin.owner['expires'] > time.time()
            valid = valid and admin.owner['cookie'] == grant['admin_generation']
            if request is not None:
                valid = valid and self.selected(request) is admin
        return bool(valid)

    async def drop_grant(self, token):
        grant = self.shares.pop(token, None)
        if grant:
            for child_token, child in list(self.shares.items()):
                if child.get('parent') == token:
                    await self.drop_grant(child_token)
            grant['control'] = False
            grant['gamepad'] = False
            grant['slot'] = None
            await asyncio.gather(*(ws.close(code=4001, message=b'Viewing ended')
                                   for ws in list(grant['connections'])), return_exceptions=True)
            if grant['session'].state == 'RUNNING':
                await self.provision_view_tokens(grant['session'])

    async def prune_shares(self):
        for token, grant in list(self.shares.items()):
            if not self.grant_valid(grant):
                await self.drop_grant(token)

    async def revoke_session_views(self, session):
        for token, grant in list(self.shares.items()):
            if grant['session'] is session or grant['admin'] is session:
                await self.drop_grant(token)

    async def new_grant(self, session, minutes, admin=None):
        await self.prune_shares()
        if len(self.shares) >= 128:
            raise web.HTTPTooManyRequests(text='Too many active share links')
        token = secrets.token_urlsafe(32)
        self.shares[token] = dict(session=session, generation=session.owner['cookie'],
            expires=min(time.time() + minutes * 60, session.owner['expires']),
            admin=admin, admin_generation=admin.owner['cookie'] if admin else None, connections=set())
        return token

    async def share_status(self, request):
        session = self.share_owner(request)
        await self.prune_shares()
        grants = [g for g in self.shares.values() if g['session'] is session]
        return web.json_response({'links': sum(not g['admin'] and not g.get('participant') for g in grants),
            'viewers': sum(len(g['connections']) for g in grants),
            'admin_viewers': sum(len(g['connections']) for g in grants if g['admin']),
            'participants': [{'id': key, 'name': g.get('name', 'Administrator'), 'control': g.get('control', False), 'admin': bool(g['admin']),
                'gamepad':g.get('gamepad',False), 'slot':g.get('slot'), 'waiting':g.get('waiting',False)}
                for key, g in self.shares.items() if g['session'] is session and g.get('participant') and g['connections']]})

    async def share_create(self, request):
        session = self.share_owner(request, True)
        data = await request.json()
        minutes = data.get('minutes') if isinstance(data, dict) else None
        if type(minutes) is not int or minutes not in (15, 60, 240):
            raise web.HTTPBadRequest(text='Choose 15, 60, or 240 minutes')
        control = data.get('control', False)
        if type(control) is not bool:
            raise web.HTTPBadRequest(text='control must be a boolean')
        gamepad = data.get('gamepad', False)
        if type(gamepad) is not bool:
            raise web.HTTPBadRequest(text='gamepad must be a boolean')
        token = await self.new_grant(session, minutes)
        self.shares[token]['allow_control'] = control
        self.shares[token]['allow_gamepad'] = gamepad
        return web.json_response({'url': request['app_origin'] + '/view/#' + token})

    async def share_revoke(self, request):
        session = self.share_owner(request, True)
        for token, grant in list(self.shares.items()):
            if grant['session'] is session:
                await self.drop_grant(token)
        return web.json_response({'revoked': True})

    async def admin_view(self, request):
        admin = self.require_admin(request, mutate=True)
        data = await request.json()
        session = self.sessions.get(data.get('id')) if isinstance(data, dict) and isinstance(data.get('id'), str) else None
        if not session or session.state != 'RUNNING' or not session.owner:
            raise web.HTTPNotFound(text='Desktop unavailable')
        token = await self.new_grant(session, 60, admin)
        return web.json_response({'url': request['app_origin'] + '/view/#' + token})

    async def view_page(self, request):
        return web.Response(content_type='text/html', text='<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><title>View desktop</title><body style="background:#11151d;color:#edf1fa;font:16px system-ui;padding:24px"><p id="message">Opening view-only desktop…</p><script src="/view.js"></script></body></html>')

    async def view_script(self, request):
        return web.Response(content_type='application/javascript', text="""(async()=>{
const token=location.hash.slice(1); history.replaceState(null,'','/view/');
try { const r=await fetch('/shares/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});
if(!r.ok) throw Error('This viewing link has expired, was revoked, or requires an active administrator login.');
const data=await r.json();location.replace(data.login || '/watch/#shared'); } catch(e) {document.getElementById('message').textContent=e.message;}
})();""")

    async def share_open(self, request):
        if request.headers.get('Origin') != request['app_origin']:
            raise web.HTTPForbidden()
        data = await request.json()
        token = data.get('token') if isinstance(data, dict) else None
        grant = self.shares.get(token) if isinstance(token, str) else None
        if not grant or not self.grant_valid(grant, request):
            raise web.HTTPForbidden()
        if grant['admin']:
            admin = grant['admin']
            return await self.admit_viewer(token, {'iss': 'admin-session', 'sub': grant['admin_generation'], 'name': admin.owner['name'], 'exp': admin.owner['expires']})
        return await self.admit_viewer(token, {'iss': 'guest-link', 'sub': secrets.token_urlsafe(16),
            'name': 'Guest ' + secrets.token_hex(2), 'exp': grant['expires']})

    async def view_ended(self, request):
        return web.FileResponse('/usr/local/share/brave-origin/view-ended.html')

    async def view_lifecycle(self, request):
        return web.Response(content_type='application/javascript', text="""(() => {
let checking = false;
async function check() {
  if (checking) return;
  checking = true;
  try {
    const response = await fetch('/watch/session-status', {cache:'no-store'});
    if (response.status === 403 || response.status === 410) location.replace('/view/ended');
    if (response.ok) {
      const state = await response.json();
      let badge = document.getElementById('guest-controller-status');
      if (!badge) {
        badge = document.createElement('div'); badge.id='guest-controller-status';
        badge.setAttribute('role','status');
        badge.style.cssText='position:fixed;right:12px;bottom:12px;z-index:1100;padding:8px 12px;border-radius:9px;background:#1c2330e8;color:#edf1fa;font:12px system-ui;pointer-events:none';
        document.body.append(badge);
      }
      badge.hidden=!state.gamepad;
      badge.textContent=state.slot?`Controller: Player ${state.slot}`:state.waiting?'All controller slots are in use — viewing remains available':'Gamepad allowed — connect a controller and press a button';
    }
  } catch {} // A temporary network outage is not a revoked invitation.
  finally { checking = false; }
}
check(); setInterval(check, 1500);
document.addEventListener('visibilitychange', () => { if (!document.hidden) check(); });
})();""")

    async def view_proxy(self, request):
        token = request.cookies.get(VIEW_COOKIE, '')
        grant = self.shares.get(token)
        if not grant or not grant.get('participant') or not self.grant_valid(grant, request):
            if request.method == 'GET' and request.match_info['path'] == '':
                raise web.HTTPFound('/view/ended')
            raise web.HTTPForbidden(text='Viewing ended. Ask the owner for a new link.')
        if request.method != 'GET':
            raise web.HTTPForbidden()
        path = request.match_info['path']
        if path == 'session-status':
            return web.json_response({'active': True, 'gamepad':grant.get('gamepad',False),
                'slot':grant.get('slot'), 'waiting':grant.get('waiting',False)})
        if path == 'api/status':
            return web.json_response({'current_mode':'websockets', 'available_modes':['websockets'], 'enable_dual_mode':False})
        if path == 'api/websockets':
            if request.headers.get('Origin') != request['app_origin']:
                raise web.HTTPForbidden()
            return await self.viewer_socket(request, token, grant)
        # Static dashboard only. No arbitrary upstream APIs, transfers, or control routes.
        if path and not path.startswith('assets/') and path not in ('icon.png', 'icon-512.png', 'manifest.json'):
            raise web.HTTPForbidden()
        root = Path('/usr/share/selkies/web').resolve()
        target = (root / (path or 'index.html')).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise web.HTTPNotFound()
        if not path:
            html = target.read_text(encoding='utf-8').replace('</head>', '<script src="/view-lifecycle.js" defer></script></head>', 1)
            return web.Response(text=html, content_type='text/html')
        return web.FileResponse(target)

    async def viewer_socket(self, request, token, grant):
        if grant['connections']:
            raise web.HTTPConflict(text='This guest is already connected. Open the invitation again for another guest.')
        if sum(len(g['connections']) for g in self.shares.values() if g['session'] is grant['session']) >= 8:
            raise web.HTTPTooManyRequests(text='Viewer limit reached')
        client = web.WebSocketResponse(heartbeat=20, max_msg_size=4096)
        grant['connections'].add(client)
        try:
            # Ignore client query parameters: role and slot are broker-owned.
            async with grant['session'].http.ws_connect('http://localhost/api/websockets?token=' + grant['upstream_token'],
                    heartbeat=20, max_msg_size=32*1024*1024,
                    headers={'Origin': request['app_origin'], 'Host': request.host}) as upstream:
                if self.shares.get(token) is not grant or not self.grant_valid(grant, request):
                    raise web.HTTPForbidden()
                await client.prepare(request)
                grant['upstream'] = upstream
                # The browser page remains in shared-viewer mode; the broker owns the secure token.
                async def inbound():
                    async for msg in client:
                        if not self.grant_valid(grant, request):
                            break
                        if msg.type == WSMsgType.TEXT and msg.data.startswith('js,'):
                            async with self.view_lock:
                                if self.shares.get(token) is grant and self.grant_valid(grant,request):
                                    await self.guest_gamepad(grant, msg.data, upstream)
                            continue
                        # No compressed/binary input, settings or clipboard commands.
                        if msg.type == WSMsgType.TEXT and (msg.data in ('START_VIDEO', 'STOP_VIDEO', 'REQUEST_KEYFRAME', 'START_AUDIO', 'STOP_AUDIO') or grant.get('control') and re.fullmatch(r'(?:kd|ku|kh|kr|m|m2)(?:,[-0-9.]+){0,8}', msg.data)):
                            await upstream.send_str(msg.data)
                async def outbound():
                    async for msg in upstream:
                        if self.shares.get(token) is not grant or not self.grant_valid(grant, request):
                            break
                        if msg.type == WSMsgType.BINARY:
                            # Only Opus (1), JPEG (3) and H.264 (4). Never gzip control text.
                            if msg.data and msg.data[0] in (1, 3, 4):
                                await client.send_bytes(msg.data)
                        elif msg.type == WSMsgType.TEXT:
                            # Selkies excludes viewer clipboard delivery; also enforce it here.
                            if msg.data.startswith(('clipboard', 'cb,', 'cw,', 'cbs,', 'cws,', 'cbd,', 'cwd,', 'cbe', 'cwe')):
                                continue
                            await client.send_str(msg.data)
                tasks = [asyncio.create_task(inbound()), asyncio.create_task(outbound())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            grant['connections'].discard(client)
            grant.pop('upstream',None)
            async with self.view_lock:
                grant['slot'] = None
                grant['waiting'] = False
                if grant['session'].state == 'RUNNING':
                    await self.provision_view_tokens(grant['session'])
            if grant.get('control') and not grant['connections']:
                grant['control'] = False
                if grant['session'].state == 'RUNNING':
                    await self.provision_view_tokens(grant['session'])
            await client.close()
        return client

    async def admit_viewer(self, invitation_token, claims, redirect=False):
        async with self.view_lock:
            invite = self.shares.get(invitation_token)
            if not invite or not self.grant_valid(invite):
                raise web.HTTPForbidden()
            token = await self.new_grant(invite['session'], 60, invite['admin'])
            record = self.shares[token]
            record.update(participant=True, parent=invitation_token, invitation=invite,
                name=str(claims.get('name') or 'Guest')[:120],
                principal=hashlib.sha256(json.dumps([claims['iss'], claims['sub']]).encode()).hexdigest(),
                upstream_token=secrets.token_urlsafe(32), control=False,
                gamepad=invite.get('allow_gamepad',False), slot=None, waiting=False,
                expires=min(invite['expires'], float(claims['exp'])))
            if invite.get('allow_control'):
                for other in self.shares.values():
                    if other['session'] is invite['session']:
                        other['control'] = False
                record['control'] = True
            await self.provision_view_tokens(invite['session'])
        response = web.HTTPFound('/watch/#shared') if redirect else web.json_response({'view_only': True})
        response.set_cookie(VIEW_COOKIE, token, secure=True, httponly=True, samesite='Strict',
            path='/watch/', max_age=max(1, int(record['expires']-time.time())))
        return response

    async def provision_view_tokens(self, session):
        async with self.token_lock:
            await self._provision_view_tokens(session)

    async def _provision_view_tokens(self, session):
        browser = session.browser
        if not getattr(browser, 'master_token', None):
            return
        participants = [g for g in self.shares.values() if g['session'] is session and g.get('participant') and self.grant_valid(g)]
        used = {g.get('slot') for g in participants if g.get('slot')}
        assigned = []
        for g in participants:
            if g.get('gamepad') and g.get('waiting') and g['connections'] and not g.get('slot'):
                slot = next((s for s in (2,3,4) if s not in used),None)
                if slot:
                    g['slot'] = slot
                    g['waiting'] = False
                    used.add(slot)
                    assigned.append(g)
        controller = next((g for g in participants if g.get('control')), None)
        payload = {browser.owner_token: {'role': 'controller', 'slot': 1, 'mk_control': controller is None}}
        payload.update({g['upstream_token']: {'role':'viewer', 'slot':g.get('slot'), 'mk_control': g is controller} for g in participants})
        try:
            async with session.http.post('http://localhost/api/tokens', json=payload,
                    headers={'Authorization':'Bearer '+browser.master_token}) as response:
                response.raise_for_status()
        except Exception:
            for g in assigned:
                g['slot'] = None
                g['waiting'] = True
            raise
        for g in assigned:
            if g.get('gamepad_connect') and g.get('upstream'):
                fields = g['gamepad_connect'].split(',')
                fields[2] = str(g['slot']-1)
                await g['upstream'].send_str(','.join(fields))

    async def guest_gamepad(self, grant, message, upstream):
        """Accept one strictly validated controller, never a client-selected slot."""
        fields = message.split(',')
        try:
            cmd, index = fields[1], int(fields[2])
            if not 0 <= index <= 3: return
            if cmd == 'c' and len(fields)==6:
                name = base64.b64decode(fields[3],validate=True)
                if len(name)>256 or not 0<=int(fields[4])<=16 or not 0<=int(fields[5])<=64: return
            elif cmd in ('b','a') and len(fields)==5:
                number, value = int(fields[3]), float(fields[4])
                if not math.isfinite(value) or not 0<=number<(64 if cmd=='b' else 16): return
                if not (0<=value<=1 if cmd=='b' else -1<=value<=1): return
            elif cmd in ('d','h') and len(fields)==3:
                pass
            else: return
        except (ValueError,IndexError):
            return
        if cmd=='c':
            grant['gamepad_connect'] = message
        if not grant.get('gamepad'):
            if cmd=='d': grant.pop('gamepad_connect',None)
            return
        if not grant.get('slot'):
            if cmd=='d':
                grant['waiting'] = False
                grant.pop('gamepad_connect',None)
            if cmd=='c':
                grant['gamepad_connect'] = message
                grant['waiting'] = True
                await self.provision_view_tokens(grant['session'])
            # ROLE_UPDATE rebinds the client's controller and sends a new connect.
            return
        if index != grant['slot']-1:
            return
        await upstream.send_str(message)
        if cmd=='d':
            grant.pop('gamepad_connect',None)
            grant['slot'] = None
            grant['waiting'] = False
            await self.provision_view_tokens(grant['session'])

    async def share_gamepad(self, request):
        owner = self.share_owner(request, True)
        data = await request.json()
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool or not isinstance(data.get('id'),str):
            raise web.HTTPBadRequest()
        async with self.view_lock:
            grant = self.shares.get(data['id'])
            if not grant or grant['session'] is not owner or not grant.get('participant') or not self.grant_valid(grant):
                raise web.HTTPNotFound()
            grant['gamepad'] = data['enabled']
            grant['slot'] = None
            grant['waiting'] = data['enabled'] and bool(grant['connections']) and bool(grant.get('gamepad_connect'))
            await self.provision_view_tokens(owner)
        return web.json_response({'updated':True})

    async def share_disconnect(self, request):
        owner = self.share_owner(request, True)
        data = await request.json()
        token = data.get('id') if isinstance(data,dict) else None
        grant = self.shares.get(token) if isinstance(token,str) else None
        if not grant or grant['session'] is not owner or not grant.get('participant'):
            raise web.HTTPNotFound()
        await self.drop_grant(token)
        return web.json_response({'disconnected':True})

    async def share_control(self, request):
        owner = self.share_owner(request, True)
        data = await request.json()
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool or not isinstance(data.get('id'), str):
            raise web.HTTPBadRequest()
        async with self.view_lock:
            grant = self.shares.get(data['id'])
            if not grant or grant['session'] is not owner or not grant.get('participant') or not self.grant_valid(grant):
                raise web.HTTPNotFound()
            previous = [(g, g.get('control', False)) for g in self.shares.values() if g['session'] is owner]
            for g, _ in previous:
                g['control'] = False
            grant['control'] = data['enabled']
            try:
                await self.provision_view_tokens(owner)
            except Exception:
                for g, control in previous:
                    g['control'] = control
                raise
        return web.json_response({'updated': True})
