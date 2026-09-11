"""Check cursor sprites and encoded pixels through a real private desktop."""
import asyncio
import json
import struct
import time
import av


async def exercise_cursor(socket, decoder, user):
    async def sample(x, native):
        await socket.send_str('SET_NATIVE_CURSOR_RENDERING,' + str(native).lower())
        await socket.send_str(f'm,{x},400,0,0')
        cursor = None
        pixels = None
        until = time.monotonic() + 3
        while time.monotonic() < until:
            try:
                message = await asyncio.wait_for(socket.receive(), .5)
            except asyncio.TimeoutError:
                continue
            raw = message.data
            if isinstance(raw, str) and raw.startswith('cursor,'):
                cursor = json.loads(raw[7:])
            if not isinstance(raw, bytes) or len(raw) < 11 or raw[0] != 4:
                continue
            fid,y,_,_ = struct.unpack('!4H',raw[2:10])
            await socket.send_str(f'CLIENT_FRAME_ACK {fid}')
            if not y:
                for frame in decoder.decode(av.Packet(raw[10:])):
                    im = frame.to_image()
                    im.save('/tmp/'+user+'-cursor-'+str(native)+'.png')
                    pixels = list(im.crop((x-32,368,x+64,464)).getdata())
        assert pixels, 'No cursor test video frame'
        painted = sum(not (g > 180 and r < 80 and b < 80) for r,g,b in pixels)
        assert (painted > 10) if native else (painted == 0), (user,native,painted)
        return cursor

    text = await sample(400, False)
    hand = await sample(600, False)
    assert text and hand and text['handle'] != hand['handle'], ('Missing cursor shape updates',text,hand)
    await sample(400, True)
    await sample(600, False)
    # Resize the private stack, then return to the size used by the UI tests.
    for width,height in ((1280,720),(900,1200),(800,600)):
        await socket.send_str(f'r,{width}x{height},primary')
        async with asyncio.timeout(20):
            while True:
                raw = (await socket.receive()).data
                if not isinstance(raw, bytes) or len(raw)<11 or raw[0]!=4:
                    continue
                fid,y,w,h=struct.unpack('!4H',raw[2:10])
                await socket.send_str(f'CLIENT_FRAME_ACK {fid}')
                if y:
                    continue
                frames = decoder.decode(av.Packet(raw[10:]))
                if frames and (w,h)==(width,height):
                    break
    print('PASS: '+user+' separate cursor shapes, no duplicate pixels, native toggle and private resize', flush=True)
