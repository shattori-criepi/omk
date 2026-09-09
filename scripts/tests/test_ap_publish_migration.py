"""Run production startup and socket installer with stateful external boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT=Path(os.environ.get('OMK_REVIEW_TEST_ROOT',Path(__file__).resolve().parents[2]))
STUB=r'''#!PYTHON
import json, os, sys, re
from pathlib import Path
root=Path(os.environ['FIXTURE']); name=Path(sys.argv[0]).name; args=sys.argv[1:]; mode=os.environ['MODE']
s=json.loads((root/'state').read_text())
original_state=json.dumps(s,sort_keys=True)
with (root/'calls').open('a') as f: f.write(name+' '+' '.join(args)+'\n')
if name=='sudo':
    if args and args[0]=='-n': args=args[1:]
    os.execvp(args[0],args)
elif name=='python3':
    if args and args[0].endswith('check-mqtt-backend.py'):
        expected='192.168.50.1' if s.get('restored') else '127.0.0.1'
        assert (args[1] if len(args)>1 else '127.0.0.1')==expected, args
        sys.exit(1 if mode=='mqtt_fail' and not s.get('restored') else 0)
    os.execv('PYTHON',['PYTHON',*args])
elif name in ('nmcli','ip'):
    (root/'forbidden').write_text(name+' '+' '.join(args)); sys.exit(99)
elif name=='docker':
    if args[:2]==['ps','-a']:
        if s['ports']!='absent': print('\n'.join('omk-'+service for service in s['images']))
    elif args[0]=='inspect' and '-f' not in args:
        print(json.dumps([{'Name':'/omk-'+service,'Image':image,'State':{'Running':True},'HostConfig':{'PortBindings':({str(port)+'/tcp':[{'HostIp':'192.168.50.1' if s['ports']=='legacy' else '127.0.0.1','HostPort':str(port)}]} if port else {})}} for service,image in s['images'].items() for port in [{'mosquitto':1883,'dashboard':8000}.get(service)]]))
    elif args[0]=='inspect':
        spec=args[args.index('-f')+1]
        print(s['images'][args[-1].removeprefix('omk-')] if spec=='{{.Image}}' else 'unless-stopped' if 'RestartPolicy' in spec else 'false' if '.Mounts' in spec else 'true')
    elif '--format' in args and 'json' in args:
        print(json.dumps({'services':{name:{'ports':[{'target':port,'published':str(port),'host_ip':'127.0.0.1','protocol':'tcp'}]} for name,port in [('mosquitto',1883),('dashboard',8000)]}}))
    elif 'prune' in args:
        (root/'forbidden').write_text('image prune');sys.exit(99)
    elif 'build' in args: s['tag']='sha256:'+'f'*64
    elif 'stop' in args: s['stopped']=True
    elif 'up' in args:
        restoring=args.count('-f')==2
        if restoring:
            override=Path(args[args.index('-f',args.index('-f')+1)+1]).read_text()
            assert '192.168.50.1:1883:1883' in override and '!override' in override
            assert s.get('stopped'), 'new containers not stopped before rollback'
            assert '--no-build' in args and '--no-deps' in args and '--pull' in args
            pins=dict(re.findall(r'^  ([\w-]+):\n    image: (sha256:[0-9a-f]{64})$',override,re.M))
            s['images']={service:pins.get(service,s['tag']) for service in s['images']}
            if mode=='rollback_wrong_image': s['images']['dashboard']=s['tag']
            s['ports']='legacy';s['restored']=True
        else:
            s['ports']='loopback';s['images']={service:s['tag'] for service in s['images']}

    elif 'ps' in args and '-q' in args: print('omk-'+args[-1])
elif name=='curl':
    expected='192.168.50.1' if s.get('restored') else '127.0.0.1'
    assert any('http://'+expected+':8000/' in a for a in args),args
    if mode=='rollback_health_fail' or (mode in ('health_fail','rollback_wrong_image') and not s.get('restored')): sys.exit(22)
    s['health']=True
elif name=='systemctl':
    if args[0]=='cat': print((root/'units'/args[1]).read_text())
    elif args[0]=='show':
        port=1883 if 'mqtt' in args[1] else 8000
        print('FreeBind=yes\nBindToDevice=wlan0\nAccept=no\nListen=192.168.50.1:'+str(port)+' (Stream)\nActiveState='+('active' if s.get('socket') else 'inactive'))
    elif args[0]=='restart':
        assert s['ports']=='loopback' and s.get('health'), 'socket started before port release / health'
        if mode=='socket_fail': sys.exit(98)
        s['socket']=True
    elif args[0]=='stop': s['socket']=False
    elif args[0]=='reload-or-restart':
        assert s.get('socket'), 'legacy isolation removed before new AP path works'
        if mode=='firewall_fail': sys.exit(98)
        s['isolation']='new'
    elif any('activation' in a for a in args):
        (root/'forbidden').write_text('AP cycle');sys.exit(99)
elif name=='ss':
    if '-ltn6' not in args and s.get('socket'):
        print('LISTEN 0 4096 192.168.50.1%wlan0:1883 0.0.0.0:* inet-sockopt: ( freebind )\nLISTEN 0 4096 192.168.50.1%wlan0:8000 0.0.0.0:* inet-sockopt: ( freebind )')
elif name=='nft':
    if args==['list','table','inet','omk_ap_isolation']:
        if mode=='after_firewall_fail' and s['isolation']=='new': sys.exit(97)
        print('table inet omk_ap_isolation { # legacy fixture\n}')
    elif args[0]=='-f': s['isolation']='legacy'
elif name in ('tee','chmod'):
    if any(a.startswith('/etc/') for a in args):
        if name=='tee': sys.stdin.read()
    else: os.execv('/usr/bin/'+name,[name,*args])
elif name not in ('nft','install','sleep'): sys.exit('unexpected '+name)
if json.dumps(s,sort_keys=True)!=original_state:
    temporary=root/('state.'+str(os.getpid()));temporary.write_text(json.dumps(s));temporary.replace(root/'state')
'''.replace('PYTHON',sys.executable)
for mode in os.environ.get('OMK_MIGRATION_TEST_MODES','fresh legacy health_fail mqtt_fail socket_fail firewall_fail after_firewall_fail rollback_health_fail rollback_wrong_image').split():
    with tempfile.TemporaryDirectory(prefix='omk-migration-test-') as tmp:
        d=Path(tmp); (d/'bin').mkdir(); (d/'units').mkdir()
        shutil.copytree(ROOT/'scripts/lib',d/'scripts/lib')
        for name in ('setup-data-collection.sh','setup-wifi-access-point.sh'):
            shutil.copy2(ROOT/'scripts'/name,d/'scripts'/name)
        shutil.copy2(ROOT/'compose.yaml',d/'compose.yaml')
        for file in ('services/mosquitto/config/mosquitto.conf','services/sensor-collector/Dockerfile','services/dashboard/Dockerfile','services/harvest-uploader/Dockerfile','services/harvest-uploader/requirements.txt','services/harvest-uploader/src/harvest_uploader/__main__.py'):
            p=d/file;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
        for f in (ROOT/'systemd').glob('omk-*-ap-proxy.*.in'):
            (d/'units'/f.name.removesuffix('.in')).write_text(f.read_text().replace('@SYSTEMD_SOCKET_PROXYD@','/usr/lib/systemd/systemd-socket-proxyd'))
        for name in ('sudo','python3','nmcli','ip','docker','curl','systemctl','ss','nft','install','sleep','tee','chmod'):
            p=d/'bin'/name;p.write_text(STUB);p.chmod(0o755)
        old_images={service:'sha256:'+str(i)*64 for i,service in enumerate(('mosquitto','sensor-collector','dashboard','harvest-uploader'),1)}
        (d/'state').write_text(json.dumps({'images':old_images,'tag':'sha256:'+'f'*64,'ports':'absent' if mode=='fresh' else 'legacy','isolation':'legacy'}))
        env=dict(os.environ,PATH=str(d/'bin')+':'+os.environ['PATH'],MODE=mode,FIXTURE=str(d),TMPDIR=str(d),OMK_SYSTEMD_UNIT_DIR=str(d/'units'))
        prep=subprocess.run(['bash',str(d/'scripts/setup-data-collection.sh'),'--prepare'],env=env,capture_output=True,text=True,timeout=30)
        assert prep.returncode==0,prep.stdout+prep.stderr
        assert json.loads((d/'state').read_text())['images']==old_images, 'build must not change running containers'
        p=subprocess.run(['bash',str(d/'scripts/setup-data-collection.sh'),'--with-ap-proxies'],env=env,capture_output=True,text=True,timeout=30)
        assert not (d/'forbidden').exists(), 'AP/NM operation was called'
        state=json.loads((d/'state').read_text())
        if mode in ('fresh','legacy'):
            assert p.returncode==0,p.stdout+p.stderr
            assert state['ports']=='loopback' and state['socket'] and state['isolation']=='new',state
        else:
            assert p.returncode!=0,p.stdout+p.stderr
            assert state['ports']=='legacy' and not state.get('socket') and state['isolation']=='legacy',(state,p.stdout,p.stderr)
            output=p.stdout+p.stderr
            if mode in ('rollback_health_fail','rollback_wrong_image'):
                assert 'ERROR: rollback failed' in output, output
                assert 'Rollback verified:' not in output, output
            else:
                assert state['images']==old_images, 'ports restored but immutable old images were NOT restored'
                assert 'Rollback verified:' in output, output
                for service in old_images: assert 'rollback restored '+service in output, output
        print('PASS publish migration '+mode)

