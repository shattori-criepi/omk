"""Collector lifecycle tests. Doubles + local dummy executables; no broker/HCI."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

TOOL=Path(__file__).resolve().parents[1]/'tools/ble_scan_ab/collect.py'

@pytest.fixture
def collector():
    spec=importlib.util.spec_from_file_location('collector_under_test',TOOL)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

@pytest.fixture
def fake_run(collector,tmp_path,monkeypatch):
    c=collector
    state=SimpleNamespace(children=[],handlers={},signals=[],startup_error=None,
                          signal_on_spawn=None,ignore=(),cleanup_signal=None,clock=0,
                          initial_free=10**10,monitor_free=10**10,monitor_error=None,
                          fill=False,exited=False,forever=False,signal_error=False)
    out=tmp_path/'capture'
    monkeypatch.setattr(sys,'argv',['collect.py','--out',str(out),'--adapter','0','--seconds','1','--max-bytes','1048576'])
    monkeypatch.setattr(c.shutil,'which',lambda name:name)
    previous={sig:signal.getsignal(sig) for sig in c.STOP_SIGNALS}
    state.handlers.update(previous)
    def register(sig,handler):
        old=state.handlers[sig];state.handlers[sig]=handler;return old
    monkeypatch.setattr(c.signal,'signal',register)
    checks=0
    def usage(path):
        nonlocal checks
        checks+=1
        return SimpleNamespace(free=state.initial_free if checks==1 else state.monitor_free)
    monkeypatch.setattr(c.shutil,'disk_usage',usage)
    monkeypatch.setattr(c.time,'monotonic',lambda:state.clock)
    def sleep(seconds):
        if state.monitor_error:raise state.monitor_error
        state.clock+=seconds
    monkeypatch.setattr(c.time,'sleep',sleep)
    class Child:
        def __init__(self,number):
            self.pid=1000+number;self.returncode=7 if state.exited and number==0 else None
            self.sent=[];self.waits=[]
        def poll(self):return self.returncode
        def send_signal(self,sig):
            self.sent.append(sig)
            if state.cleanup_signal:state.handlers[state.cleanup_signal](state.cleanup_signal,None)
            if state.signal_error and self.pid==1000:raise PermissionError('test denied')
            if sig not in state.ignore and not state.forever:self.returncode=-sig
        def wait(self,timeout):
            self.waits.append(timeout)
            if self.returncode is None:raise subprocess.TimeoutExpired('dummy',timeout)
            return self.returncode
    def spawn(cmd,**kw):
        if state.startup_error and state.children:raise state.startup_error
        child=Child(len(state.children));state.children.append(child)
        if state.fill:
            kw['stdout'].write(b'x'*1048576);kw['stdout'].flush()
        # Reproduce signal delivery before the Popen caller appends its handle.
        if state.signal_on_spawn:state.handlers[state.signal_on_spawn](state.signal_on_spawn,None)
        return child
    monkeypatch.setattr(c.subprocess,'Popen',spawn)
    def run():
        code=c.main();metadata=json.loads((out/'capture.json').read_text())
        assert state.handlers==previous
        return code,metadata
    state.run=run;state.out=out
    return state

@pytest.mark.parametrize('signum',[signal.SIGHUP,signal.SIGTERM,signal.SIGINT])
def test_signal_during_spawn_and_cleanup(fake_run,signum):
    s=fake_run;s.signal_on_spawn=signum;s.cleanup_signal=signum
    code,meta=s.run()
    assert code==0 and meta['reason']=='operator_stop' and meta['signal']==signal.Signals(signum).name
    assert len(s.children)==1 and meta['cleanup_ok'] and meta['children'][0]['reaped']
    assert s.children[0].sent==[signal.SIGINT] and s.children[0].waits==[5]

@pytest.mark.parametrize('case,reason,code',[
    ('normal','duration',0),('size','size_limit',2),('disk','disk_reserve',2),
    ('exited','collector_exit',2),('startup','error: OSError: spawn failed',2),
    ('exception','error: RuntimeError: monitor failed',2),
    ('no_space','error: OSError: insufficient disk reserve',2)])
def test_all_exit_paths_reap_children(fake_run,case,reason,code):
    s=fake_run
    if case=='size':s.fill=True
    if case=='disk':s.monitor_free=0
    if case=='exited':s.exited=True
    if case=='startup':s.startup_error=OSError('spawn failed')
    if case=='exception':s.monitor_error=RuntimeError('monitor failed')
    if case=='no_space':s.initial_free=0
    result,meta=s.run()
    assert result==code and meta['reason']==reason and meta['cleanup_ok']
    assert all(x['reaped'] for x in meta['children'])
    assert all(c.returncode is not None and c.waits for c in s.children)

@pytest.mark.parametrize('ignored,forced,code',[((),False,0),((signal.SIGINT,),False,0),((signal.SIGINT,signal.SIGTERM),True,2)])
def test_escalation_is_bounded_and_recorded(fake_run,ignored,forced,code):
    s=fake_run;s.ignore=ignored
    result,meta=s.run()
    assert result==code and meta['cleanup_ok']
    for item,child in zip(meta['children'],s.children):
        assert item['reaped'] and item['forced']==forced
        assert item['wait_timeouts']==[signal.Signals(x).name for x in ignored]
        assert len(child.waits)==len(ignored)+1 and max(child.waits)==5

@pytest.mark.parametrize('failure',['unkillable','permission'])
def test_failed_stop_does_not_skip_other_children(fake_run,failure):
    s=fake_run
    if failure=='unkillable':s.forever=True
    else:s.signal_error=True
    result,meta=s.run()
    assert result==2 and not meta['cleanup_ok'] and len(meta['children'])==2
    assert not meta['children'][0]['reaped'] and s.children[0].waits==[5,2,2]
    if failure=='permission':
        assert len(meta['children'][0]['errors'])==3
        assert meta['children'][1]['reaped']


def test_metadata_write_failure_still_reaps_and_reports(collector,fake_run,monkeypatch,capsys):
    original=Path.write_text
    def write(path,*args,**kwargs):
        if path.name=='capture.json':raise OSError('disk full')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',write)
    assert collector.main()==2
    meta=json.loads(capsys.readouterr().err)
    assert not meta['cleanup_ok'] and 'capture.json: disk full' in meta['cleanup_errors']
    assert all(c['reaped'] for c in meta['children'])
    assert all(c.returncode is not None for c in fake_run.children)


@pytest.mark.skipif(sys.platform!='linux',reason='isolated lifecycle test uses POSIX signals and Linux /proc')
@pytest.mark.parametrize('signum',[None,signal.SIGHUP,signal.SIGTERM,signal.SIGINT])
def test_local_dummy_processes_reaped(tmp_path,signum):
    """Real child processes that never open a socket or touch Bluetooth."""
    executable_dir=tmp_path/'bin';executable_dir.mkdir()
    controls=tmp_path/'controls';controls.mkdir()
    script=r'''import json,os,signal,sys,time
from pathlib import Path
name=Path(sys.argv[0]).name
folder=Path(os.environ['DUMMY_CAPTURE_CONTROL'])
stop=False
def handle(sig,frame):
    global stop
    stop=True
    (folder/(name+'-signal')).write_text(str(sig))
for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,handle)
(folder/(name+'.json')).write_text(json.dumps({'pid':os.getpid(),'parent':os.getppid()}))
while not stop:time.sleep(.02)
time.sleep(.2)
'''
    for name in ('btmon','mosquitto_sub'):
        executable=executable_dir/name
        executable.write_text('#!'+sys.executable+'\n'+script)
        executable.chmod(0o700)
    out=tmp_path/'capture'
    env=dict(os.environ,PATH=str(executable_dir)+os.pathsep+os.environ.get('PATH',''),
             DUMMY_CAPTURE_CONTROL=str(controls),PYTHONDONTWRITEBYTECODE='1')
    parent=subprocess.Popen([sys.executable,str(TOOL),'--out',str(out),'--adapter','0',
                             '--seconds','2' if signum is None else '60','--max-bytes','1048576'],
                            env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    pids=[]
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            ready=list(controls.glob('*.json'))
            try:data=[json.loads(f.read_text()) for f in ready]
            except json.JSONDecodeError:data=[]
            if len(data)==2:
                pids=[v['pid'] for v in data]
                assert all(v['parent']==parent.pid for v in data)
                break
            assert parent.poll() is None,'collector exited before dummy readiness'
            time.sleep(.02)
        assert len(pids)==2,'dummy startup deadline'
        if signum is not None:
            parent.send_signal(signum)
            time.sleep(.05)
            # These arrive while the first child is flushing/shutting down.
            parent.send_signal(signal.SIGINT);parent.send_signal(signal.SIGTERM)
        stdout,stderr=parent.communicate(timeout=12)
        assert parent.returncode==0,(stdout,stderr)
        metadata=json.loads((out/'capture.json').read_text())
        assert metadata['signal']==(None if signum is None else signal.Signals(signum).name)
        assert metadata['reason']==('duration' if signum is None else 'operator_stop')
        assert metadata['cleanup_ok'] and len(metadata['children'])==2
        assert all(v['reaped'] and v['exit_code']==0 for v in metadata['children'])
        assert all(not Path('/proc',str(pid)).exists() for pid in pids)
        assert all((controls/(name+'-signal')).read_text()==str(int(signal.SIGINT)) for name in ('btmon','mosquitto_sub'))
    finally:
        if parent.poll() is None:
            parent.send_signal(signal.SIGTERM)
            try:parent.communicate(timeout=12)
            except subprocess.TimeoutExpired:parent.kill();parent.communicate(timeout=3)
        for pid in pids:
            try:os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:pass


def test_partial_file_open_failure_closes_handles_and_reaps(collector,fake_run,monkeypatch):
    original=Path.open;opened=[]
    def open_file(path,*args,**kwargs):
        if path.name=='btmon.stderr':raise OSError('cannot open second stderr')
        handle=original(path,*args,**kwargs);opened.append(handle);return handle
    monkeypatch.setattr(Path,'open',open_file)
    code,meta=fake_run.run()
    assert code==2 and meta['reason']=='error: OSError: cannot open second stderr'
    assert len(meta['children'])==1 and meta['children'][0]['reaped']
    assert all(handle.closed for handle in opened)


def test_temporary_config_cleanup_failure_is_recorded(collector,fake_run,monkeypatch,tmp_path):
    def cleanup():raise OSError('cleanup denied')
    monkeypatch.setattr(collector.tempfile,'TemporaryDirectory',lambda **kw:SimpleNamespace(name=str(tmp_path/'fake-config'),cleanup=cleanup))
    code,meta=fake_run.run()
    assert code==2 and not meta['cleanup_ok']
    assert meta['cleanup_errors']==['temporary config: OSError: cleanup denied']
    assert all(c['reaped'] for c in meta['children'])
