"""Drive a real guest slot and verify native isolation and release semantics."""
import asyncio
import pwd
from gamepad_session import PRELOAD, READER

async def exercise_guest_gamepad(client, server, desktops, viewer, token, auth):
    readers=[]
    try:
        for name in ('alice','bob'):
            desktop=desktops[name].browser
            proc=await asyncio.create_subprocess_exec('runuser','-u',pwd.getpwuid(desktop.uid).pw_name,'--',
                'env',f'LD_PRELOAD={PRELOAD}',f'SELKIES_JS_SOCKET_PATH={desktop.directory}',
                'python3','-u','-c',READER.replace("'/dev/input/js0'","'/dev/input/js1'").replace('+10','+60'),
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            readers.append(proc)
            assert (await asyncio.wait_for(proc.stdout.readline(),10)).strip()==b'READY'
        async def permission(enabled):
            response=await client.post(server.make_url('/shares/gamepad'),headers=auth,json={'id':token,'enabled':enabled})
            assert response.status==200,await response.text()
        async def event(expected):
            line=await asyncio.wait_for(readers[0].stdout.readline(),5)
            assert line.strip()==expected,(line,expected)
        await viewer.send_str('js,c,0,VGVzdA==,4,17')
        await asyncio.sleep(.1)
        await permission(True)
        async with asyncio.timeout(10):
            async for message in viewer:
                if isinstance(message.data,str) and message.data.startswith('ROLE_UPDATE,') and '"slot": 2' in message.data: break
            else: raise AssertionError('Missing guest slot assignment')
        await viewer.send_str('js,b,1,0,1')
        await event(b'0:1')
        try:
            await asyncio.wait_for(readers[1].stdout.readline(),.3)
            raise AssertionError('Guest input leaked into Bob desktop')
        except TimeoutError: pass
        await permission(False)
        await event(b'0:0')
        await viewer.send_str('js,b,1,0,1')
        await asyncio.sleep(.1)
        await permission(True)
        await viewer.send_str('js,b,1,0,1')
        await event(b'0:1')
        await viewer.close()
        await event(b'0:0')
        print('PASS: real guest Player 2 input, other desktop isolation, revoke and disconnect release held buttons',flush=True)
    finally:
        for proc in readers:
            if proc.returncode is None: proc.terminate()
            await proc.communicate()
