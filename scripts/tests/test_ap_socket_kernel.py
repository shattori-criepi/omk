"""Optional integration test: run ONLY in a disposable user/network namespace.

OMK_TEST_PARENT_NETNS=$(readlink /proc/self/ns/net) unshare --user --map-root-user --net python3 scripts/tests/test_ap_socket_kernel.py
systemctl responses are fixtures; ss and the sockets below use the real kernel.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

assert os.environ.get('OMK_TEST_PARENT_NETNS') and os.readlink('/proc/self/ns/net') != os.environ['OMK_TEST_PARENT_NETNS'], 'isolated network namespace required'
ROOT=Path(__file__).resolve().parents[2]
subprocess.run(['ip','link','add','wlan0','type','dummy'],check=True)
subprocess.run(['ip','link','set','wlan0','up'],check=True)
assert '192.168.50.1' not in subprocess.check_output(['ip','-4','address'],text=True)
with tempfile.TemporaryDirectory(prefix='omk-kernel-socket-') as tmp:
    d=Path(tmp);(d/'bin').mkdir();(d/'units').mkdir()
    shutil.copy2(ROOT/'scripts/tests/lib/proxy-systemctl',d/'bin/systemctl')
    for f in (ROOT/'systemd').glob('omk-*-ap-proxy.*.in'):
        (d/'units'/f.name.removesuffix('.in')).write_text(f.read_text().replace('@SYSTEMD_SOCKET_PROXYD@','/usr/lib/systemd/systemd-socket-proxyd'))
    env=dict(os.environ,PATH=str(d/'bin')+':'+os.environ['PATH'],OMK_SYSTEMD_UNIT_DIR=str(d/'units'))
    for interface in ('wlan0','lo'):
        sockets=[]
        try:
            for port in (1883,8000):
                stream=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
                stream.setsockopt(socket.SOL_IP,15,1)  # Linux IP_FREEBIND
                stream.setsockopt(socket.SOL_SOCKET,socket.SO_BINDTODEVICE,interface.encode()+b'\0')
                stream.bind(('192.168.50.1',port));stream.listen(10);sockets.append(stream)
            proc=subprocess.run([str(ROOT/'scripts/lib/validate-ap-socket-units.sh'),str(d/'units'),'--runtime'],env=env,capture_output=True,text=True)
            assert (proc.returncode==0)==(interface=='wlan0'),proc.stdout+proc.stderr
        finally:
            for stream in sockets:stream.close()
print('PASS: real kernel FreeBind sockets without AP IP; runtime rejects wrong device.')
