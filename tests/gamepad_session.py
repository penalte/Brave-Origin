"""Exercise real owner WebSockets and native adapters under two private UIDs."""
import asyncio
import os
from pathlib import Path
import pwd
import struct
import av

PRELOAD = '/usr/local/lib/brave-gamepad/selkies_joystick_interposer.so:/usr/local/lib/brave-gamepad/libudev.so.1.0.0-fake'
READER = r'''
import os, select, struct, time, ctypes
assert 'event1000' in os.listdir('/dev/input'), 'virtual evdev enumeration missing'
u=ctypes.CDLL('libudev.so.1')
u.udev_new.restype=ctypes.c_void_p
u.udev_enumerate_new.argtypes=[ctypes.c_void_p]; u.udev_enumerate_new.restype=ctypes.c_void_p
e=u.udev_enumerate_new(u.udev_new())
u.udev_enumerate_add_match_subsystem.argtypes=[ctypes.c_void_p,ctypes.c_char_p]
u.udev_enumerate_add_match_subsystem(e,b'input')
u.udev_enumerate_scan_devices.argtypes=[ctypes.c_void_p]; assert u.udev_enumerate_scan_devices(e)==0
u.udev_enumerate_get_list_entry.argtypes=[ctypes.c_void_p]; u.udev_enumerate_get_list_entry.restype=ctypes.c_void_p
u.udev_list_entry_get_name.argtypes=[ctypes.c_void_p]; u.udev_list_entry_get_name.restype=ctypes.c_char_p
u.udev_list_entry_get_next.argtypes=[ctypes.c_void_p]; u.udev_list_entry_get_next.restype=ctypes.c_void_p
n=u.udev_enumerate_get_list_entry(e); names=[]
while n:
 names.append(u.udev_list_entry_get_name(n).decode()); n=u.udev_list_entry_get_next(n)
assert any(x.endswith('/event1000') for x in names), names
fd=os.open('/dev/input/js0',os.O_RDONLY|os.O_NONBLOCK)
print('READY',flush=True)
until=time.monotonic()+10
while time.monotonic()<until:
 if not select.select([fd],[],[],.1)[0]: continue
 data=os.read(fd,8)
 if not data: break
 _,value,kind,number=struct.unpack('IhBB',data)
 if kind==1: print(f'{number}:{value}',flush=True)
'''

async def exercise_gamepads(desktops):
    readers=[]
    try:
        for name in ('alice','bob'):
            desktop=desktops[name].browser
            directory=str(desktop.directory)
            assert len(directory.encode()) <= 80
            await desktops[name].test_socket.send_str('js,c,0,VGVzdCBQYWQ=,4,17')
            # runuser must not preload the adapter itself.
            proc=await asyncio.create_subprocess_exec('runuser','-u',pwd.getpwuid(desktop.uid).pw_name,'--',
                'env',f'LD_PRELOAD={PRELOAD}',f'SELKIES_JS_SOCKET_PATH={directory}',
                'python3','-u','-c',READER,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            readers.append(proc)
            line=await asyncio.wait_for(proc.stdout.readline(),15)
            assert line.strip()==b'READY', (name,line,await proc.stderr.read())
            # Point the other UID at this user's sockets: filesystem permissions deny access.
            other=desktops['bob' if name=='alice' else 'alice'].browser
            denied=await asyncio.create_subprocess_exec('runuser','-u',pwd.getpwuid(other.uid).pw_name,'--',
                'python3','-c','import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1])',
                directory+'/selkies_js0.sock',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE)
            _,err=await denied.communicate()
            assert denied.returncode and b'PermissionError' in err,err
        async def event(index,expected):
            line=await asyncio.wait_for(readers[index].stdout.readline(),3)
            assert line.strip()==expected,(index,line,expected)
        await desktops['alice'].test_socket.send_str('js,b,0,0,1')
        await event(0,b'0:1')
        await desktops['bob'].test_socket.send_str('js,b,0,1,1')
        await event(1,b'1:1')
        # Disconnect releases held state instead of leaving buttons stuck.
        for index,name in enumerate(('alice','bob')):
            await desktops[name].test_socket.send_str('js,d,0')
            await event(index,f'{index}:0'.encode())
        print('PASS: private gamepad discovery, owner input, cross-UID denial and disconnect release',flush=True)
        for name in ('alice','bob'):
            desktop=desktops[name].browser
            ws=desktops[name].test_socket
            page=desktop.home/'Downloads/gamepad.html'
            page.write_text('''<html><body style="margin:0;background:blue"><script>
function tick(){const p=Array.from(navigator.getGamepads()).find(Boolean);
document.body.style.background=p?(p.buttons[0].pressed?'#00ff00':'#ff0000'):'#0000ff';
requestAnimationFrame(tick)}tick();</script></body></html>''')
            os.chown(page,desktop.uid,desktop.uid)
            for msg in ('kd,65507','kd,108','ku,108','ku,65507','co,end,file://'+str(page),'kd,65293','ku,65293'):
                await ws.send_str(msg)
            await asyncio.sleep(2)
            await ws.send_str('js,c,0,VGVzdCBQYWQ=,4,17')
            decoder=av.CodecContext.create('h264','r')
            async def color(channel):
                async with asyncio.timeout(15):
                    async for message in ws:
                        raw=message.data
                        if not isinstance(raw,bytes) or len(raw)<11 or raw[0]!=4: continue
                        fid,y,_,_=struct.unpack('!4H',raw[2:10])
                        await ws.send_str(f'CLIENT_FRAME_ACK {fid}')
                        if y: continue
                        try: frames=decoder.decode(av.Packet(raw[10:]))
                        except av.error.InvalidDataError: continue
                        for frame in frames:
                            im=frame.to_image(); rgb=im.getpixel((im.width//2,im.height//2))
                            if rgb[channel]>180 and all(rgb[i]<80 for i in range(3) if i!=channel): return
            await ws.send_str('js,b,0,0,1')
            await color(1)
            await ws.send_str('js,b,0,0,0')
            await color(0)
            await ws.send_str('js,d,0')
            print(f'PASS: {name} Brave Gamepad API receives press and release',flush=True)
    finally:
        for proc in readers:
            if proc.returncode is None: proc.terminate()
            await proc.communicate()
