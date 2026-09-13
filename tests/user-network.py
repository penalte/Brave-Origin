"""Disposable root container: per-UID ports, persistence and isolated switching."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location('users', '/usr/local/bin/user-network.py')
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


async def main():
    os.environ.update(OIDC_ENABLED='true', WARP_ENABLED='false', WARP_ACCEPT_TOS='true')
    u.network.setup()
    users = []
    server = None
    try:
        for index in range(2):
            key = str(index+1)*64
            uid = 200001+index
            process = await asyncio.create_subprocess_exec('useradd','-u',str(uid),'test'+str(index))
            assert await process.wait() == 0
            directory = u.USERS/key
            directory.mkdir(parents=True)
            u.save(directory/'identity.json', {'key':key,'uid':uid})
            u.register(key,'Test '+str(index))
            proxy = u.UserProxy(key,uid)
            await proxy.start()
            users.append(proxy)
        a,b = users
        assert a.port != b.port
        async def check(uid, port, allowed):
            code = f"import socket; s=socket.create_connection(('127.0.0.1',{port}),1); s.sendall(bytes([5,1,0])); assert s.recv(2)==bytes([5,0])"
            process = await asyncio.create_subprocess_exec('setpriv','--reuid',str(uid),'--regid',str(uid),'--clear-groups',
                'python3','-c',code,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            assert (await process.wait() == 0) == allowed
        for current, other in ((a,b),(b,a)):
            await check(current.uid,current.port,True)
            await check(current.uid,other.port,False)
            await check(current.uid,40000,False)
            reader,writer = await asyncio.open_connection('127.0.0.1',current.port)
            writer.write(b'\x05\x01\x00')
            assert await reader.readexactly(2) == b'\x05\x00'
            writer.write(b'\x05\x01\x00\x01\x7f\x00\x00\x01'+other.port.to_bytes(2,'big'))
            assert (await reader.readexactly(10))[1] != 0, 'Another user proxy reachable by chaining'
            writer.close()
        async def echo(reader,writer):
            try:
                while data := await reader.read(1024):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
        server = await asyncio.start_server(echo,'127.0.0.1',18081)
        async def connect(proxy):
            reader,writer = await asyncio.open_connection('127.0.0.1',proxy.port)
            writer.write(b'\x05\x01\x00')
            assert await reader.readexactly(2) == b'\x05\x00'
            writer.write(b'\x05\x01\x00\x01\x7f\x00\x00\x01'+(18081).to_bytes(2,'big'))
            assert (await reader.readexactly(10))[1] == 0
            return reader,writer
        ar,aw = await connect(a)
        br,bw = await connect(b)
        port = a.port
        await a.switch(True)
        assert a.port == port and await asyncio.wait_for(ar.read(),2) == b''
        bw.write(b'unaffected')
        assert await br.readexactly(10) == b'unaffected'
        assert b.manager.mode == 'direct' and a.manager.mode == 'warp'
        # Saved preference survives worker teardown and a new session.
        path,record = u.metadata(a.key)
        record['warp_enabled'] = True
        u.save(path,record)
        await a.stop()
        await a.start()
        assert a.manager.mode == 'warp'
        u.save(u.FORCE,{'force_warp':True})
        assert not b.status(admin=True)['can_toggle']
        await b.stop()
        await b.start()
        assert b.manager.mode == 'warp'
        aw.close(); bw.close()
        for proxy in users:
            await proxy.stop()
        stale = Path('/run/brave-network/users')/('c'*64)
        stale.mkdir()
        (stale/'config.json').write_text('{"mode":"warp"}')
        await u.nft('add','element','inet','brave_egress','clients','{',str(a.uid),'.','40002','}')
        await u.recover()
        assert not stale.exists()
        query = await asyncio.create_subprocess_exec('nft','get','element','inet','brave_egress','clients','{',str(a.uid),'.','40002','}',stderr=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL)
        assert await query.wait() != 0
        print('PASS: own-port access, cross-UID denial, independent switching, fixed active port, saved preference, force enforcement and logout cleanup')
    finally:
        for proxy in users:
            await proxy.stop()
        if server:
            server.close()
            await server.wait_closed()


asyncio.run(main())
