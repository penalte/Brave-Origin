"""Real owner media uplinks, private device discovery and signal isolation."""
import asyncio
import io
import os
import pwd
import struct
from fractions import Fraction
import math
from array import array
import av
from PIL import Image
from gamepad_session import open_browser_page

PRELOAD = '/usr/local/lib/brave-gamepad/selkies_v4l2_interposer.so:/usr/local/lib/brave-gamepad/selkies_joystick_interposer.so:/usr/local/lib/brave-gamepad/libudev.so.1.0.0-fake'

async def command(desktop, *args):
    proc = await asyncio.create_subprocess_exec(
        'runuser', '-u', pwd.getpwuid(desktop.uid).pw_name, '--',
        'env', f'HOME={desktop.home}', f'XDG_RUNTIME_DIR={desktop.directory}',
        f'PULSE_SERVER=unix:{desktop.directory}/pulse/native',
        f'SELKIES_WEBCAM_SOCKET_PATH={desktop.directory}', f'LD_PRELOAD={PRELOAD}',
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 20)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise
    assert proc.returncode == 0, (args, out, err)
    return out

async def exercise_media(desktops):
    feeders = []
    try:
        for name, color in (('alice','red'), ('bob','blue')):
            session = desktops[name]
            data = io.BytesIO()
            Image.new('RGB', (320,240), color).save(data, 'JPEG')
            async def feed(ws=session.test_socket, frame=data.getvalue()):
                while True:
                    await ws.send_bytes(b'\x06\x00\x01'+frame)
                    await asyncio.sleep(.05)
            feeders.append(asyncio.create_task(feed()))
        for name in ('alice','bob'):
            session = desktops[name]
            desktop = session.browser
            socket = desktop.directory/'selkies_webcam0.sock'
            for _ in range(100):
                if socket.is_socket(): break
                await asyncio.sleep(.1)
            assert socket.is_socket(), (name, 'missing webcam socket')
            other = desktops['bob' if name=='alice' else 'alice'].browser
            await command(other, 'python3', '-c',
                'import socket,sys; s=socket.socket(socket.AF_UNIX)\n'
                'try: s.connect(sys.argv[1])\n'
                'except PermissionError: pass\n'
                'else: raise AssertionError("Cross-user webcam accessible")', str(socket))
            # Actual V4L2 read through the same preload chain as Brave, no host device.
            raw = await command(desktop, 'python3', '-c',
                'import os,sys; assert "video0" in os.listdir("/dev"); '
                'fd=os.open("/dev/video0",os.O_RDWR); sys.stdout.buffer.write(os.read(fd,8000000))')
            picture = Image.open(io.BytesIO(raw)).convert('RGB')
            pixel = picture.getpixel((picture.width//2,picture.height//2))
            channel = 0 if name=='alice' else 2
            assert pixel[channel] > 180 and pixel[2-channel] < 80, (name,pixel)
            page = desktop.home/'Downloads/webcam.html'
            page.write_text('''<html><body style="margin:0;background:red"><script>
(async()=>{
const d=await navigator.mediaDevices.enumerateDevices();
if(!d.some(x=>x.kind==='videoinput'))return;
const stream=await navigator.mediaDevices.getUserMedia({video:true});
const v=document.createElement('video');v.srcObject=stream;v.muted=true;await v.play();
const c=document.createElement('canvas');c.width=320;c.height=240;const ctx=c.getContext('2d');
const timer=setInterval(()=>{ctx.drawImage(v,0,0,320,240);
const p=ctx.getImageData(160,120,1,1).data;
if(p[CHANNEL]>180 && p[OTHER]<80){document.body.style.background='#00ff00';
clearInterval(timer);stream.getTracks().forEach(t=>t.stop());}},100);
})().catch(e=>document.body.textContent=e.name+': '+e.message);
</script></body></html>'''.replace('CHANNEL',str(channel)).replace('OTHER',str(2-channel)), encoding='utf-8')
            os.chown(page,desktop.uid,desktop.uid)
            await open_browser_page(desktop, page.as_uri())
            await session.test_socket.send_str('REQUEST_KEYFRAME')
            captured = False
            async with asyncio.timeout(20):
                async for msg in session.test_socket:
                    raw = msg.data
                    if not isinstance(raw,bytes) or len(raw)<11 or raw[0]!=4: continue
                    fid,y,_,_=struct.unpack('!4H',raw[2:10])
                    await session.test_socket.send_str(f'CLIENT_FRAME_ACK {fid}')
                    if y: continue
                    try: frames = session.test_decoder.decode(av.Packet(raw[10:]))
                    except av.error.InvalidDataError: continue
                    if any((lambda p:p[1]>180 and p[0]<80)(f.to_image().getpixel((400,300))) for f in frames):
                        captured = True
                        break
            assert captured, (name, 'stream closed before camera capture succeeded')
            print(f'PASS: {name} webcam reaches V4L2 and Brave getUserMedia with own pixels; other UID denied',flush=True)
    finally:
        for task in feeders: task.cancel()
        await asyncio.gather(*feeders,return_exceptions=True)

async def exercise_microphones(desktops):
    feeders = []
    readers = []
    try:
        for name, session in desktops.items():
            # The microphone must exist even before the first uplink packet.
            assert (await command(session.browser,'pactl','get-default-source')).strip()==b'SelkiesVirtualMic'
            other = desktops['bob' if name=='alice' else 'alice'].browser
            await command(other,'python3','-c',
                'import socket,sys; s=socket.socket(socket.AF_UNIX)\n'
                'try: s.connect(sys.argv[1])\n'
                'except PermissionError: pass\n'
                'else: raise AssertionError("Cross-user audio accessible")',str(session.browser.directory/'pulse/native'))
        for name, frequency in (('alice',300),('bob',900)):
            session = desktops[name]
            async def feed(ws=session.test_socket, hz=frequency):
                encoder = av.CodecContext.create('libopus','w')
                encoder.sample_rate = 24000
                encoder.layout = 'mono'
                encoder.format = 's16'
                encoder.time_base = Fraction(1,24000)
                encoder.options = {'application':'lowdelay'}
                encoder.open()
                offset = 0
                while True:
                    frame = av.AudioFrame(format='s16',layout='mono',samples=480)
                    frame.sample_rate = 24000
                    frame.pts = offset
                    frame.time_base = Fraction(1,24000)
                    frame.planes[0].update(struct.pack('<480h',*[round(6553*math.sin(2*math.pi*hz*(offset+i)/24000)) for i in range(480)]))
                    for packet in encoder.encode(frame):
                        await ws.send_bytes(b'\x02'+bytes(packet))
                    offset += 480
                    await asyncio.sleep(.02)
            feeders.append(asyncio.create_task(feed()))
        await asyncio.sleep(3)
        for name, frequency in (('alice',300),('bob',900)):
            desktop = desktops[name].browser
            info = await command(desktop,'pactl','get-default-source')
            assert info.strip()==b'SelkiesVirtualMic', (name,info)
            proc = await asyncio.create_subprocess_exec('runuser','-u',pwd.getpwuid(desktop.uid).pw_name,'--',
                'env',f'PULSE_SERVER=unix:{desktop.directory}/pulse/native',
                'parecord','--raw','--format=float32le','--channels=1','--rate=24000','--latency-msec=50',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            readers.append(proc)
            data = await asyncio.wait_for(proc.stdout.readexactly(24000*4*2),15)
            signal = array('f',data)[12000:]
            rms = math.sqrt(sum(x*x for x in signal)/len(signal))
            powers = {hz: abs(sum(x*complex(math.cos(2*math.pi*hz*i/24000),math.sin(2*math.pi*hz*i/24000)) for i,x in enumerate(signal))) for hz in (300,900)}
            dominant = max(powers,key=powers.get)
            print(f'Microphone {name}: RMS={rms:.4f}, frequency={dominant}Hz',flush=True)
            assert .10 < rms < .18, (name,'microphone attenuated or amplified',rms)
            assert abs(dominant-frequency)<5, (name,'wrong user microphone',dominant)
            assert powers[1200-frequency] < powers[frequency]*.05, (name,'other microphone signal detected',powers)
            proc.terminate()
            await proc.communicate()
            session = desktops[name]
            page = desktop.home/'Downloads/microphone.html'
            page.write_text('''<html><body style="margin:0;background:red"><script>
(async()=>{const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false}});
const ctx=new AudioContext();await ctx.resume();const source=ctx.createMediaStreamSource(stream);
const analyser=ctx.createAnalyser();analyser.fftSize=2048;source.connect(analyser);
const samples=new Float32Array(2048);let good=0;
const timer=setInterval(()=>{analyser.getFloatTimeDomainData(samples);
const rms=Math.sqrt(samples.reduce((s,x)=>s+x*x,0)/samples.length);
document.body.textContent='RMS: '+rms;
if(rms>.10&&rms<.18&&++good>=5){document.body.style.background='#0000ff';clearInterval(timer);
stream.getTracks().forEach(t=>t.stop());ctx.close();}},100);
})().catch(e=>document.body.textContent=e.name+': '+e.message);
</script></body></html>''',encoding='utf-8')
            os.chown(page,desktop.uid,desktop.uid)
            await open_browser_page(desktop,page.as_uri())
            await session.test_socket.send_str('REQUEST_KEYFRAME')
            captured = False
            async with asyncio.timeout(20):
                async for msg in session.test_socket:
                    raw = msg.data
                    if not isinstance(raw,bytes) or len(raw)<11 or raw[0]!=4: continue
                    fid,y,_,_=struct.unpack('!4H',raw[2:10])
                    await session.test_socket.send_str(f'CLIENT_FRAME_ACK {fid}')
                    if y: continue
                    try: frames=session.test_decoder.decode(av.Packet(raw[10:]))
                    except av.error.InvalidDataError: continue
                    if any((lambda p:p[2]>180 and p[0]<80)(f.to_image().getpixel((400,300))) for f in frames):
                        captured = True
                        break
            assert captured, (name, 'stream closed before microphone capture succeeded')
            print(f'PASS: {name} Brave getUserMedia preserves microphone level',flush=True)
        print('PASS: both private microphone uplinks preserve signal level and separate user audio',flush=True)
    finally:
        for task in feeders: task.cancel()
        await asyncio.gather(*feeders,return_exceptions=True)
        for proc in readers:
            if proc.returncode is None:
                proc.terminate()
                await proc.communicate()
