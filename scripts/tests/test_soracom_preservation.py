import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(os.environ.get('OMK_REVIEW_TEST_ROOT', Path(__file__).resolve().parents[2]))
STUB = r'''#!/usr/bin/env python3
import json, os, sys, shutil
from pathlib import Path
name=Path(sys.argv[0]).name; args=sys.argv[1:]; mode=os.environ['MODE']; root=Path(os.environ['FIXTURE'])
with (root/'calls').open('a') as f: f.write(name+' '+' '.join(args)+'\n')
if name in ('apt-get','systemctl','curl','mmcli','udevadm'):
    with (root/'forbidden').open('a') as f: f.write(name+'\n')
    sys.exit(99)
elif name=='uname': print('Linux' if args==['-s'] else 'aarch64')
elif name=='getent': print('tester:x:1000:1000:test:'+str(root/'home')+':/bin/bash')
elif name=='sudo':
    if args==['-v']: sys.exit(0)
    os.execvp(args[0],args)
elif name=='nmcli':
    mapping={'-g NAME connection show --active':'soracom',
        '-g GENERAL.DEVICES connection show soracom':'cdc-wdm0',
        '-g GENERAL.IP-IFACE device show cdc-wdm0':'wwan0',
        '-g connection.uuid connection show soracom':'cellular-uuid',
        '-g GENERAL.CON-UUID device show cdc-wdm0':'cellular-uuid'}
    if ' '.join(args) not in mapping:
        (root/'forbidden').write_text('NM mutation'); sys.exit(99)
    print(mapping[' '.join(args)])
elif name=='install':
    if mode=='install_fail': sys.exit(73)
    dest=Path(args[-1]); assert str(dest).startswith(str(root)),dest
    if '-d' in args: dest.mkdir(parents=True,exist_ok=True)
    else: shutil.copyfile(args[-2],dest); dest.chmod(0o755)
elif name=='stat': print('1000:1000:755' if mode=='bad_metadata' else '0:0:755')
elif name=='ip':
    if args[:3]==['-j','-4','route']:
        if 'default' in args:
            if mode=='gateway_unknown': print('[]')
            elif mode=='gateway_missing': print(json.dumps([{'dst':'default','dev':'wwan0'}]))
            elif mode=='multiple_gateways': print(json.dumps([
                {'dst':'default','dev':'wwan0','gateway':'10.0.0.1'},
                {'dst':'default','dev':'wwan0','gateway':'10.0.0.2'}]))
            elif mode=='other_default': print(json.dumps([
                {'dst':'default','dev':'wwan0','gateway':'10.0.0.1'},
                {'dst':'default','dev':'eth0','gateway':'192.0.2.1'}]))
            elif mode=='filtered_json_omits_dev':
                assert 'dev' not in args, args
                print(json.dumps([{'dst':'default','dev':'wwan0','gateway':'10.0.0.1'}]))
            else: print(json.dumps([{'dst':'default','dev':'wwan0','gateway':'10.0.0.1'}]))
        elif mode=='missing': print('[]')
        elif mode=='partial_missing' and args[-1] != '100.127.0.0/16': print('[]')
        elif mode=='missing_then_conflict' and args[-1] != '54.250.252.99/32': print('[]')
        else:
            print(json.dumps([{'dst':args[-1], 'dev':'wwan0','gateway':'10.9.9.9' if mode in ('conflict', 'missing_then_conflict') and args[-1].endswith('99/32') else '10.0.0.1', 'metric':100}]))
    elif args[:3]==['-4','route','add']:
        with (root/'mutations').open('a') as f: f.write(' '.join(args)+'\n')
    else:
        (root/'forbidden').write_text('route replace/flush'); sys.exit(99)
else: sys.exit('unexpected command '+name)
'''
for mode in os.environ.get('OMK_SORACOM_TEST_MODES','matching missing partial_missing conflict missing_then_conflict gateway_unknown gateway_missing multiple_gateways other_default filtered_json_omits_dev install_fail bad_metadata rerun unmanaged').split():
    with tempfile.TemporaryDirectory(prefix='omk-soracom-test-') as tmp:
        d=Path(tmp); (d/'home').mkdir(); (d/'bin').mkdir(); (d/'scripts/lib').mkdir(parents=True)
        for name in ('setup-soracom-onyx.sh','soracom-route-dispatcher'):
            shutil.copy2(ROOT/'scripts'/name,d/'scripts'/name)
        for name in ('apt-helpers.sh','soracom-preservation-policy.sh'):
            shutil.copy2(ROOT/'scripts/lib'/name,d/'scripts/lib'/name)
        for name in ('uname','getent','sudo','nmcli','install','stat','ip','apt-get','systemctl','curl','mmcli','udevadm'):
            f=d/'bin'/name; f.write_text(STUB); f.chmod(0o755)
        env=dict(os.environ,PATH=str(d/'bin')+':'+os.environ['PATH'],MODE=mode,FIXTURE=str(d),SORACOM_DISPATCHER_PATH=str(d/'dispatcher/90.soracom_route'))
        if mode=='unmanaged':
            hook=d/'dispatcher/90.soracom_route';hook.parent.mkdir();hook.write_text('#!/bin/bash\necho unsafe\n');hook.chmod(0o755)
        if mode=='rerun': env['MODE']='gateway_unknown'
        proc=subprocess.run(['bash',str(d/'scripts/setup-soracom-onyx.sh')],env=env,capture_output=True,text=True,timeout=15)
        if mode=='rerun':
            assert proc.returncode!=0, proc.stdout+proc.stderr
            assert (d/'dispatcher/90.soracom_route').is_file()
            env['MODE']='missing'
            proc=subprocess.run(['bash',str(d/'scripts/setup-soracom-onyx.sh')],env=env,capture_output=True,text=True,timeout=15)
        assert not (d/'forbidden').exists(), 'forbidden operation: '+(d/'calls').read_text()
        assert (proc.returncode==0)==(mode in ('matching','missing','partial_missing','other_default','filtered_json_omits_dev','rerun')), proc.stdout+proc.stderr
        if mode in ('missing','rerun'):
            assert (d/'mutations').exists(), 'route repair was skipped on rerun'
            assert len((d/'mutations').read_text().splitlines())==3
        elif mode=='partial_missing':
            assert (d/'mutations').exists(), 'missing routes were not added'
            assert len((d/'mutations').read_text().splitlines())==2
        else: assert not (d/'mutations').exists(), 'existing routes changed'
        if mode == 'install_fail':
            assert 'Existing dispatcher is not verified OMK-managed code' in proc.stdout, proc.stdout+proc.stderr
        if mode == 'bad_metadata':
            assert 'Existing dispatcher is not verified OMK-managed code' in proc.stdout, proc.stdout+proc.stderr
        print('PASS SORACOM '+mode)
