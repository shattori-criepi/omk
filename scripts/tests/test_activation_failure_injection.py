"""Exercise the real helper with stateful NM at the process boundary."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

ROOT = Path(os.environ.get('OMK_REVIEW_TEST_ROOT', Path(__file__).resolve().parents[2]))
AP = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
OLD = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
OTHER = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
NM = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
p=Path(os.environ['NM_STATE']); s=json.loads(p.read_text()); args=sys.argv[1:]
with open(os.environ['NM_CALLS'],'a') as f: f.write(' '.join(args)+'\n')
ap='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'; old='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
other='cccccccc-cccc-cccc-cccc-cccccccccccc'
if args[:2]==['--wait','0']: args=args[2:]
if args[:2]==['-g','connection.uuid']: print(ap)
elif args[:2]==['-g','connection.interface-name']: print('wlan0')
elif args[:2]==['-g','connection.autoconnect']: print(s.get('auto','no'))
elif args[:2]==['-g','GENERAL.CON-UUID']:
    if s.get('stage')=='deactivating':
        s['down_polls']=s.get('down_polls',0)+1
        if s['down_polls']>=4: s['current']=other if s['mode']=='delayed_external' else ''
    if s.get('stage')=='restore':
        s['polls']=s.get('polls',0)+1
        if s['mode']=='user' and s['polls']>=2: s['current']=other
    print(s['current'] or '--')
elif args[:2]==['-g','GENERAL.STATE']:
    ready=s['mode'] in ('active','success','late')
    if s.get('stage')=='restore':
        ready=s['mode']!='user' and time.monotonic()-s['restore_time'] >= (35 if s['mode']=='slow' else 3 if s['mode']=='term_restore' else 0)
    print('100 (connected)' if ready else '50 (configuring)')
elif args[:2]==['connection','modify']: s['auto']=args[-1]
elif args[:2]==['connection','up']:
    s['current']=args[3]
    if args[3]==ap:
        s['stage']='activate'
        if s['mode']=='external_before': s['current']=other
        if s['mode'] in ('failure','user','external_before','late','term_restore','delayed','delayed_external'):
            temporary=p.with_suffix('.tmp'); temporary.write_text(json.dumps(s)); temporary.replace(p); sys.exit(42)
    elif args[3]==old:
        s['stage']='restore'; s['restore_time']=time.monotonic()
    else: sys.exit('forbidden activation of another UUID')
elif args[:2]==['connection','down']:
    if s['current']!=ap or args[3]!=ap: sys.exit('forbidden down of another connection')
    if s['mode'] in ('delayed','delayed_external'): s['stage']='deactivating'
    else: s['current']=''
else: sys.exit('unexpected NM call: '+str(args))
temporary=p.with_suffix('.tmp'); temporary.write_text(json.dumps(s)); temporary.replace(p)
'''
unit = (ROOT/'systemd/omk-ap-activation.service.in').read_text()
unit_timeout = int(re.search(r'^TimeoutStartSec=(\d+)', unit, re.M)[1])
worker = (ROOT/'scripts/omk-activate-access-point').read_text()
budgets = dict((k, int(v)) for k, v in re.findall(r'(PREFLIGHT|REQUEST|ACTIVATION|VERIFY|ROLLBACK)_SECONDS=(\d+)', worker))
assert unit_timeout >= sum(budgets.values()) + 30
with tempfile.TemporaryDirectory(prefix='omk-worker-test-') as tmp:
    d=Path(tmp); (d/'bin').mkdir()
    (d/'bin/nmcli').write_text(NM); (d/'bin/nmcli').chmod(0o755)
    (d/'bin/ip').write_text('#!/bin/bash\nprintf "2: wlan0 inet 192.168.50.1/24 scope global wlan0\\n"\n')
    (d/'bin/ip').chmod(0o755)
    for mode in os.environ.get('OMK_WORKER_TEST_MODES','active success failure user external_before late term term_restore slow delayed delayed_external').split():
        state=d/(mode+'.json'); calls=d/(mode+'.calls')
        state.write_text(json.dumps({'mode':mode,'current':AP if mode=='active' else OLD}))
        env=dict(os.environ,PATH=str(d/'bin')+':'+os.environ['PATH'],NM_STATE=str(state),NM_CALLS=str(calls),OMK_AP_ACTIVATION_LOCK_FILE=str(d/'lock'))
        started=time.monotonic()
        proc=subprocess.Popen([str(ROOT/'scripts/omk-activate-access-point')],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        if mode in ('term','term_restore'):
            for _ in range(100):
                if json.loads(state.read_text()).get('stage')==('activate' if mode=='term' else 'restore'): break
                time.sleep(.05)
            else: raise AssertionError('activation never started')
            second=subprocess.run([str(ROOT/'scripts/omk-activate-access-point')],env=env,capture_output=True,text=True)
            assert second.returncode != 0, 'concurrent worker accepted'
            proc.terminate()
        try:
            output=proc.communicate(timeout=unit_timeout)[0]
        finally:
            if proc.poll() is None:
                proc.kill(); proc.wait()
        after=json.loads(state.read_text()); log=calls.read_text()
        if mode in ('active','success'):
            assert proc.returncode==0, output
            if mode=='active': assert 'connection up' not in log and 'connection down' not in log
        elif mode=='late':
            assert proc.returncode!=0 and after['current']==AP and after['auto']=='yes', output
            assert 'connection down' not in log and ('connection up uuid '+OLD) not in log
        else:
            assert proc.returncode!=0, output
            assert after['auto']=='no', output
            assert after['current']==(OTHER if mode in ('user','external_before','delayed_external') else OLD), output
            assert log.count('connection up uuid '+OLD)==(0 if mode in ('external_before','delayed_external') else 1), output
        assert ('connection down uuid '+OTHER) not in log, 'third-party connection was disconnected'
        if mode in ('delayed','delayed_external'):
            assert after.get('down_polls',0)>=4, output
        if mode=='slow':
            assert time.monotonic()-started >= 90, 'slow failure was not exercised'
            assert 'Previous Wi-Fi restored.' in output, output
        print('PASS worker '+mode, flush=True)
