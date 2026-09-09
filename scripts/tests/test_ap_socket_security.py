import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='omk-socket-test-') as tmp:
    d=Path(tmp); units=d/'units'; units.mkdir(); (d/'bin').mkdir()
    shutil.copy2(ROOT/'scripts/tests/lib/proxy-systemctl',d/'bin/systemctl')
    shutil.copy2(ROOT/'scripts/tests/lib/proxy-ss',d/'bin/ss')
    env=dict(os.environ,PATH=str(d/'bin')+':'+os.environ['PATH'],OMK_SYSTEMD_UNIT_DIR=str(units))
    def render():
        shutil.rmtree(units); units.mkdir()
        for f in (ROOT/'systemd').glob('omk-*-ap-proxy.*.in'):
            (units/f.name.removesuffix('.in')).write_text(f.read_text().replace('@SYSTEMD_SOCKET_PROXYD@','/usr/lib/systemd/systemd-socket-proxyd'))
    def validate(mode, accepted, environ=env):
        proc=subprocess.run([str(ROOT/'scripts/lib/validate-ap-socket-units.sh'),str(units),mode],env=environ,capture_output=True,text=True)
        assert (proc.returncode==0)==accepted, proc.stdout+proc.stderr
    render()
    for mode in ('--source','--effective','--runtime'): validate(mode,True)
    mutations=['FreeBind=no','BindToDevice=','Accept=yes','ListenStream=8000',
               'ListenStream=0.0.0.0:8000','ListenStream=[::]:8000',
               'ListenStream=192.168.50.1:8001','ListenDatagram=8000',
               'FreeBind=yes']  # Even an identical duplicate must be rejected.
    for directive in mutations:
        for dropin in (False,True):
            render()
            f=units/'omk-dashboard-ap-proxy.socket'
            if dropin:
                target=units/(f.name+'.d'); target.mkdir()
                (target/'override.conf').write_text('[Socket]\n'+directive+'\n')
            else: f.write_text(f.read_text().replace('[Install]','[Socket]\n'+directive+'\n[Install]'))
            for mode in ('--source','--effective','--runtime'): validate(mode,False)
    for key in ('BindToDevice','FreeBind','Accept','ListenStream'):
        render(); f=units/'omk-mqtt-ap-proxy.socket'
        f.write_text('\n'.join(line for line in f.read_text().splitlines() if not line.startswith(key+'=')))
        validate('--source',False)
    render()
    validate('--runtime',False,dict(env,TEST_EFFECTIVE_FREEBIND='no'))
    # Correct files/properties do not excuse a missing or unbound actual listener.
    (d/'bin/ss').write_text('#!/bin/bash\nprintf "LISTEN 0 4096 192.168.50.1:8000 0.0.0.0:*\\n"\n')
    validate('--runtime',False)
print('PASS: source, effective settings, drop-ins and actual-listener failure injection.')
