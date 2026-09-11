"""Exercise the website upload button via the real private desktop."""
import asyncio
import struct
import time
import av

async def exercise_picker(socket, desktop, decoder, user):
    for event in ('m,70,155,0,0','m,70,155,1,0','m,70,155,0,0'):
        await socket.send_str(event)
    for _ in range(100):
        if 'picker opened: OpenFile' in (desktop.directory/'picker.log').read_text():
            break
        await asyncio.sleep(.1)
    else:
        raise AssertionError('Brave did not open the private portal: '+(desktop.directory/'picker.log').read_text())
    until = time.monotonic() + 1
    while time.monotonic() < until:
        try:
            message = await asyncio.wait_for(socket.receive(), .3)
        except asyncio.TimeoutError:
            continue
        raw = message.data
        if not isinstance(raw, bytes) or len(raw) < 11 or raw[0] != 4:
            continue
        fid,y,_,_ = struct.unpack('!4H',raw[2:10])
        await socket.send_str(f'CLIENT_FRAME_ACK {fid}')
        if not y:
            for frame in decoder.decode(av.Packet(raw[10:])):
                frame.to_image().save('/tmp/'+user+'-picker-open.png')
    # Tree starts focused; same.txt is the last file in this fresh test home.
    await socket.send_str('kd,65367'); await socket.send_str('ku,65367')
    await socket.send_str('kd,65293'); await socket.send_str('ku,65293')
    async with asyncio.timeout(30):
        async for message in socket:
            raw=message.data
            if not isinstance(raw,bytes) or len(raw)<11 or raw[0]!=4: continue
            fid,y,w,h=struct.unpack('!4H',raw[2:10])
            await socket.send_str(f'CLIENT_FRAME_ACK {fid}')
            if y: continue
            for frame in decoder.decode(av.Packet(raw[10:])):
                frame.to_image().save('/tmp/'+user+'-picker.png')
                plane=frame.reformat(format='rgb24').planes[0]
                offset=(h//2)*plane.line_size+(w//2)*3
                r,g,b=bytes(plane)[offset:offset+3]
                if g>180 and r<80 and b<80:
                    print('PASS: '+user+' website received its own file through My files',flush=True)
                    await exercise_save(socket, desktop, user)
                    log = desktop.directory/'picker.log'
                    opened = log.read_text().count('picker opened: OpenFile')
                    closed = log.read_text().count('picker closed: 1')
                    for event in ('m,70,155,0,0','m,70,155,1,0','m,70,155,0,0'):
                        await socket.send_str(event)
                    for _ in range(100):
                        if log.read_text().count('picker opened: OpenFile') > opened:
                            break
                        await asyncio.sleep(.1)
                    else:
                        raise AssertionError('Could not reopen upload picker')
                    await socket.send_str('kd,65307'); await socket.send_str('ku,65307')
                    for _ in range(100):
                        if log.read_text().count('picker closed: 1') > closed:
                            print('PASS: '+user+' cancelled website picker with Escape', flush=True)
                            return
                        await asyncio.sleep(.1)
                    raise AssertionError('Escape did not cancel the picker')
    raise AssertionError('Website did not receive the selected private file')


async def exercise_save(socket, desktop, user):
    for event in ('kd,65507','kd,115','ku,115','ku,65507'):
        await socket.send_str(event)
    for _ in range(100):
        if 'picker opened: SaveFile' in (desktop.directory/'picker.log').read_text():
            break
        await asyncio.sleep(.1)
    else:
        raise AssertionError('Brave did not open the private save picker')
    async def name(value):
        for event in ('kd,65507','kd,97','ku,97','ku,65507','co,end,'+value,'kd,65293','ku,65293'):
            await socket.send_str(event)
    await name('../outside.html')
    await asyncio.sleep(.5)
    assert not (desktop.home/'outside.html').exists()
    await name('saved-'+user+'.html')
    for _ in range(100):
        if (desktop.home/'Downloads'/('saved-'+user+'.html')).exists():
            print('PASS: '+user+' saved inside My files; outside save name rejected',flush=True)
            return
        await asyncio.sleep(.1)
    raise AssertionError('Private save dialog did not save the page')
