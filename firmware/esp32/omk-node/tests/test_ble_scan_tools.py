import base64
import importlib.util
import json
from pathlib import Path
import subprocess
import pytest
from test_ble_scan_ab import ab_host
SPEC=importlib.util.spec_from_file_location('ble_scan_tools',Path(__file__).resolve().parents[1]/'tools/ble_scan_ab/ab.py')
ab=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(ab)
def record(**fields):
    raw=bytearray(256)
    def put(at,n,value):raw[at:at+n]=int(value).to_bytes(n,'little')
    put(0,8,fields.get('uptime',100000));put(40,4,1|(fields.get('role',0)<<10)|(4<<12)|(4<<18))
    put(44,4,fields.get('flags',0));put(28,8,123);put(24,4,fields.get('epoch',0))
    for i in range(4):
        for j in range(14):put(52+42*i+3*j,3,fields.get('count',0))
    return {'protocol_version':2,'node_id':fields.get('node','020000000001'),'boot_id':fields.get('boot','0123456789abcdef'),'seq':fields.get('seq',1),'revision':0,'m':base64.b64encode(raw).decode()}
def observation(**kw):
    return {'time':kw.pop('time',1000),'retained':kw.pop('retained',False),'report':ab.decode(record(**kw))}
def root():return observation(node='020000000004',role=1,flags=1<<13)
@pytest.mark.parametrize('case',range(30,50))
def test_production_pack_decodes(ab_host,case):
    payload=json.loads(subprocess.check_output([str(ab_host),str(case)],text=True))
    assert len(json.dumps(payload,separators=(',',':')))==466
    r=ab.decode(payload)
    assert r['heap']['free']==12345 and r['heap']['min']==10000 and r['root_nonce']=='0123456789abcdef'
    if case==37:assert r['banks']['A']['lt20ms']==2 and r['banks']['B40']['TIMEOUT']==1
    if case==38:assert r['banks']['A']['TIMEOUT']==200 and r['episode']['count']==1
    if case==39:assert r['banks']['A']['TIMEOUT']==200 and not r['flags']&((1<<14)|(1<<15))
    if case==40:assert r['banks']['B25']['NO_MEMORY']==0xffffff and r['self']['sdk_ok']==65535
    if case==48:assert r['mixed_invalid']==0xffffffff and r['flags']&(1<<7)
def test_all_offsets():
    r=record();raw=bytearray(base64.b64decode(r['m']))
    for i in range(4):
        for j in range(14):raw[52+42*i+3*j:55+42*i+3*j]=(i*100+j+1).to_bytes(3,'little')
    r['m']=base64.b64encode(raw).decode();d=ab.decode(r)
    for i,p in enumerate(ab.PRESETS):assert list(d['banks'][p].values())==list(range(i*100+1,i*100+15))
@pytest.mark.parametrize('fault',range(9))
def test_decoder_rejects(fault):
    r=record()
    if fault==0:r['m']=r['m'][:-4]
    if fault==1:r['m']='!'+r['m'][1:]
    if fault==2:r['node_id']='*'
    if fault==3:r['boot_id']='0'*15
    if fault==4:r['protocol_version']=1
    if fault==5:r['seq']=-1
    if fault==6:r['revision']=True
    if fault==7:r['m']=base64.b64encode(bytes(256)).decode()
    if fault==8:r['m']=base64.b64encode(bytes(257)).decode()
    with pytest.raises((ValueError,KeyError)):ab.decode(r)
def test_plan_and_cancel():
    c=ab.make_command(observation(),root(),1,['A','B40','A','B15'],[3600]*4,now=1000)
    assert c['segments'][0][0]>c['accept_before_ms'] and c['plan_end_ms']==c['segments'][-1][1]
    assert len(json.dumps(c,separators=(',',':')).encode())<=512
    assert len(ab.make_command(observation(),root(),2,[],[],now=1000,cancel=True))==8
@pytest.mark.parametrize('fault',range(8))
def test_unsafe_plan_rejected(fault):
    n=observation();r=root();epoch=1;patterns=['B15'];durations=[3600]
    if fault==0:n['time']=900
    if fault==1:n['retained']=True
    if fault==2:r['report']['role']=0
    if fault==3:r['report']['flags']=0
    if fault==4:epoch=0
    if fault==5:patterns*=5;durations*=5
    if fault==6:durations=[36000]
    if fault==7:n['report']['role']=1
    with pytest.raises(ValueError):ab.make_command(n,r,epoch,patterns,durations,now=1000)
def logline(payload,t,topic=None,retain=False):
    return json.dumps({'tst':t,'retain':retain,'topic':topic or f"omk/node/{payload['node_id']}/measurement",'payload':json.dumps(payload)})
def test_gap_boot_dropout_retained_and_duplicate(tmp_path):
    p=tmp_path/'mqtt.jsonl';lines=[]
    for t,u,seq,boot,count in [(1000,100000,1,'0123456789abcdef',1),(1060,160000,2,'0123456789abcdef',2),(1400,500000,3,'0123456789abcdef',30),(1460,1000,1,'0000000000000001',0)]:
        lines.append(logline(record(uptime=u,seq=seq,boot=boot,count=count),t))
    lines += [lines[1],logline(record(),900,retain=True),'broken']
    p.write_text('\n'.join(lines)+'\n');out=ab.compare(p,['020000000001','020000000002','020000000003','020000000004'])
    assert out['duplicate_reports']==1 and out['retained_reports_excluded']==1 and out['invalid_lines']==1
    assert len(out['missing_nodes'])==3 and out['unknown_intervals']==2 and not out['groups']
    assert out['boot_cumulative_exploratory']['020000000001']['0123456789abcdef']['banks']['A']['TIMEOUT']==30
def test_send_never_auto_renews_or_changes_bytes(tmp_path,monkeypatch):
    p=tmp_path/'cmd.json';cmd=ab.make_command(observation(),root(),1,['B25'],[1000],now=1000)
    p.write_text(json.dumps(cmd,separators=(',',':')));sent=[]
    monkeypatch.setattr(ab.time,'time',lambda:1000);monkeypatch.setattr(ab.time,'sleep',lambda _:None)
    monkeypatch.setattr(ab,'latest',lambda log,node:root() if node=='020000000004' else observation())
    class Result:returncode=0
    def run(args,**kw):sent.append((args,kw['input']));return Result()
    monkeypatch.setattr(ab.subprocess,'run',run)
    assert ab.send_command(p,'unused','unused')==2 and len(sent)==6
    assert all(data==p.read_bytes() and '-r' not in args for args,data in sent)
def test_prepared_wrong_boot_never_sent(tmp_path,monkeypatch):
    p=tmp_path/'cmd.json';cmd=ab.make_command(observation(),root(),1,[],[],now=1000,cancel=True);p.write_text(json.dumps(cmd))
    monkeypatch.setattr(ab.time,'time',lambda:1000);monkeypatch.setattr(ab,'latest',lambda *args:observation(boot='0'*16))
    monkeypatch.setattr(ab.subprocess,'run',lambda *a,**k:pytest.fail('must not transmit'))
    with pytest.raises(ValueError):ab.send_command(p,'unused','unused')
@pytest.mark.parametrize('fault',range(15))
def test_production_command_parser(ab_host,fault):
    cmd=ab.make_command(observation(),root(),1,['A','B40','B25','B15'],[1000]*4,now=1000)
    expected='invalid'
    if fault==0:expected='valid'
    if fault==1:cmd['extra']=0
    if fault==2:cmd['control_epoch']=0
    if fault==3:cmd['control_epoch']=2**32
    if fault==4:cmd['control_epoch']=1.5
    if fault==5:cmd['target_boot_id']='0'*15
    if fault==6:cmd['root_nonce']='0000000000000000'
    if fault==7:cmd['accept_before_ms']=2**53
    if fault==8:cmd['segments'][0][2]=4
    if fault==9:cmd['segments']=[]
    if fault==10:cmd['segments']*=2
    if fault==11:cmd['node_id']='02000000000Z'
    text=json.dumps(cmd,separators=(',',':'))
    if fault==12:text=text[:-1]+',"control_epoch":2}'
    if fault==13:text+='null'
    if fault==14:text+='\0'
    result=subprocess.check_output([str(ab_host),'60'],input=text,text=True).strip()
    assert result==expected

def test_quality_union_is_not_truth_and_short_round_trip(tmp_path):
    import sys
    folder=Path(__file__).resolve().parents[1]/'tools/ble_scan_ab'
    sys.path.insert(0,str(folder))
    import quality
    observations=[]
    for path,offset in [('node',1),('gateway_hci',0)]:
        for stamp,value in [(100,0),(110,1),(112,0),(130,1)]:
            observations.append({'path':path,'time':stamp+offset,'address':'aabbccddeeff','field':'motion_state','value':value})
    out=quality.evaluate(observations,['node','gateway_hci','missing'])
    assert out['initial_states_excluded']==2 and out['ambiguous_clusters']==2
    assert out['observed_union']['missing']['captured']==0
    assert out['observed_union']['node']['observed_reference_events']==1
    manual=tmp_path/'manual.csv';manual.write_text('timestamp,address,field,value,trial\n1970-01-01T00:02:10+00:00,aabbccddeeff,motion_state,1,1\n')
    result=quality.evaluate(observations,['node','missing'],manual)['manual'][0]
    assert result['delay_s']=={'node':1,'missing':None}
def test_legacy_schema2_decoder_preserves_scan_event():
    import ctypes
    import sys
    folder=Path(__file__).resolve().parents[1]/'tools/legacy_diag'
    sys.path.insert(0,str(folder))
    import diag_payload
    core=diag_payload.CoreV2();core.schema=2;core.bytes=ctypes.sizeof(core)
    core.event_count=1;core.event_head=1
    core.events[0].type=8;core.events[0].code=123;core.events[0].detail=0x301
    result=diag_payload.decode_payload(bytes(core))
    assert result['events'][0]['type']==8 and result['events'][0]['code']==123
def test_consumed_epoch_cannot_ack_different_prepared_file(tmp_path,monkeypatch):
    cmd=ab.make_command(observation(),root(),1,['B15'],[1000],now=1000)
    p=tmp_path/'cmd.json';p.write_text(json.dumps(cmd,separators=(',',':')))
    monkeypatch.setattr(ab.time,'time',lambda:1000)
    r=observation(epoch=1);r['report']['result']=1
    monkeypatch.setattr(ab,'latest',lambda *args:r)
    monkeypatch.setattr(ab.subprocess,'run',lambda *a,**k:pytest.fail('consumed epoch must not transmit'))
    assert ab.send_command(p,'unused','unused')==2
@pytest.mark.parametrize('root_dropped',[False,True])
def test_network_scope_requires_both_root_endpoints(tmp_path,root_dropped):
    ids=[f'02000000000{i}' for i in range(1,5)];rows=[]
    for node in ids:
        is_root=node==ids[-1]
        for i,t in enumerate((1000,1060)):
            if is_root and root_dropped and i:continue
            rows.append(logline(record(node=node,role=int(is_root),flags=(1<<13) if is_root else 0,seq=i+1,uptime=100000+i*60000,count=i+1),t))
            state={'node_id':node,'boot_id':'0123456789abcdef','root_mac':'02:00:00:00:00:04','is_root':is_root,'rootless':False,'root_change_count':0,'parent_disconnect_count':0,'mesh_layer':1 if is_root else 2}
            rows.append(logline(state,t,f'omk/node/{node}/status'))
    p=tmp_path/'mqtt.jsonl';p.write_text('\n'.join(rows)+'\n');out=ab.compare(p,ids)
    assert len(out['intervals'])==3
    assert all(x['scope']==('partial' if root_dropped else 'network') for x in out['intervals'])
@pytest.mark.parametrize('suffix',['Z+0900','+0900','+09:00'])
def test_mosquitto_offset(suffix):
    assert ab.utc_seconds('2026-11-17T09:00:00.123456'+suffix)==ab.utc_seconds('2026-11-17T00:00:00.123456Z')
@pytest.mark.parametrize('bad',['2026-11-17T09:00:00','2026-11-17T09:00:00Z+0960','2026-11-17T09:00:00+24:00','2026-02-30T00:00:00Z',float('nan'),1e308,10**400,True,None])
def test_timestamp_rejects(bad):
    with pytest.raises(ValueError):ab.utc_seconds(bad)
@pytest.mark.parametrize('start,end,expected',[(0,120,'stable_observed'),(120,121,'precursor_or_boundary'),(119,121,'precursor_or_boundary'),(299,300,'precursor_or_boundary'),(300,301,'stable_observed')])
def test_precursor_boundary(start,end,expected):
    assert ab.classify_interval(start,end,[300],[],False)==expected

def test_precursor_missing_and_stop():
    assert ab.classify_interval(0,60,[],[(0,100)],False)=='unknown'
    assert ab.classify_interval(300,360,[300],[],True)=='stopped_or_recovering'
@pytest.mark.parametrize('gap,plan,ok',[(4,1,True),(5,1,False),(6,1,False),(4,2,False),(4,3,False)])
def test_ack_actual_state(gap,plan,ok):
    cmd=ab.make_command(observation(),root(),1,['B25'],[1000],now=1000)
    r=observation()['report'];r.update(gap=gap,plan_state=plan,result=1)
    assert ab.ack_state(cmd,r)==ok

def test_received_at_format_flows(tmp_path):
    p=tmp_path/'sensor.jsonl';p.write_text(json.dumps({'received_at':'2026-11-17T09:00:00.123456Z+0900','topic':'omk/node/020000000001/measurement','payload':record()})+'\n')
    row=ab.latest(p,'020000000001');assert row['time']==ab.utc_seconds('2026-11-17T00:00:00.123456Z')
    assert list(ab.records(p))[0]['retained'] is False
    assert ab.compare(p,[f'02000000000{i}' for i in range(1,5)])['invalid_lines']==0

@pytest.mark.parametrize('version',['2.0.11','2.0.18'])
def test_mosquitto_release_fixtures(version):
    row=list(ab.records(Path(__file__).parent/f'fixtures/ble_scan_ab/mosquitto-{version}.jsonl'))[0]
    assert row['time']==ab.utc_seconds('2026-11-17T00:00:00.123456Z')
    assert row['payload']=={} and not row['retained']

def test_late_stop_confirmation_reclassifies_prior_intervals(tmp_path):
    node='020000000001';ids=[f'02000000000{i}' for i in range(1,5)];rows=[]
    for seconds in range(60,601,60):
        payload=record(uptime=seconds*1000,seq=seconds,count=seconds//60,flags=(1<<14) if seconds>=360 else 0)
        if seconds>=360:
            raw=bytearray(base64.b64decode(payload['m']));raw[220:222]=(1).to_bytes(2,'little');raw[222:226]=(300).to_bytes(4,'little');payload['m']=base64.b64encode(raw).decode()
        rows.append(logline(payload,1000+seconds))
        status={'node_id':node,'boot_id':payload['boot_id'],'root_mac':'02:00:00:00:00:04','is_root':False,'rootless':False,'root_change_count':0,'parent_disconnect_count':0,'mesh_layer':2}
        rows.append(logline(status,1000+seconds,f'omk/node/{node}/status'))
    p=tmp_path/'mqtt.jsonl';p.write_text('\n'.join(rows)+'\n');out=ab.compare(p,ids)
    phases={x['start']-1000:x['phase'] for x in out['intervals']}
    assert phases[60]=='stable_observed'
    assert phases[120]==phases[180]==phases[240]=='precursor_or_boundary'
    assert phases[300]=='stopped_or_recovering' and phases[540]=='unknown'
    rates=list(out['timeout_comparisons'].values())[0]
    assert rates['stable']['completions']==1 and rates['including_precursor']['completions']==4
    assert out['incidence_rate'] is None
    # Without the later episode evidence the trailing 180 seconds are censored.
    p.write_text('\n'.join(rows[:10])+'\n');out=ab.compare(p,ids)
    assert all(x['phase']=='unknown' for x in out['intervals'] if x['start']>=1120)

def test_cli_plan_send_compare_quality(tmp_path,monkeypatch,capsys):
    import sys
    import time
    stamp=ab.utc_seconds('2026-11-17T09:00:00.123456Z+0900')
    monkeypatch.setattr(ab.time,'time',lambda:stamp)
    n=record();r=record(node='020000000004',role=1,flags=1<<13)
    log=tmp_path/'mqtt.jsonl';log.write_text(logline(n,'2026-11-17T09:00:00.123456Z+0900')+'\n'+logline(r,'2026-11-17T09:00:00.123456+0900')+'\n')
    cmd=tmp_path/'plan.json'
    assert ab.main(['plan','--log',str(log),'--node',n['node_id'],'--root',r['node_id'],'--epoch','1','--presets','B25','--seconds','600','--out',str(cmd)])==0
    c=json.loads(cmd.read_text());sent=[]
    original_run=subprocess.run
    def publish(args,**kw):
        sent.append(kw['input']);raw=bytearray(base64.b64decode(n['m']))
        raw[0:8]=(100001).to_bytes(8,'little');raw[16:24]=c['plan_end_ms'].to_bytes(8,'little');raw[24:28]=(1).to_bytes(4,'little')
        state=int.from_bytes(raw[40:44],'little')|(1<<15)|(1<<25);raw[40:44]=state.to_bytes(4,'little')
        new={**n,'seq':2,'m':base64.b64encode(raw).decode()}
        with log.open('a') as f:f.write(logline(new,stamp)+'\n')
        return subprocess.CompletedProcess(args,0)
    monkeypatch.setattr(ab.subprocess,'run',publish);monkeypatch.setattr(time,'sleep',lambda _:None)
    assert ab.main(['send',str(cmd),'--log',str(log)])==0 and sent==[cmd.read_bytes()]
    assert ab.main(['compare',str(log),'--nodes',*[f'02000000000{i}' for i in range(1,5)]])==0
    monkeypatch.setattr(ab.subprocess,'run',original_run)
    models=tmp_path/'models.json';models.write_text('{}')
    quality=Path(ab.__file__).with_name('quality.py')
    result=original_run([sys.executable,str(quality),str(log),'--models',str(models),'--paths',n['node_id']],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['input']['malformed']==0

def test_collect_ten_hours_explicit_budget_no_real_process(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('ab_collect',Path(ab.__file__).with_name('collect.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(sys,'argv',['collect.py','--out',str(tmp_path/'capture'),'--adapter','0','--seconds','36000','--max-bytes','1048576'])
    monkeypatch.setattr(module.shutil,'which',lambda name:name)
    monkeypatch.setattr(module.shutil,'disk_usage',lambda path:SimpleNamespace(free=10**10))
    monkeypatch.setattr(module.signal,'signal',lambda *args:None)
    clock=iter([0,36000]);monkeypatch.setattr(module.time,'monotonic',lambda:next(clock))
    class Child:
        pid=123
        returncode=None
        def poll(self):return self.returncode
        def send_signal(self,_):self.returncode=0
        def wait(self,timeout=None):return self.returncode
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kw:Child())
    assert module.main()==0
    metadata=json.loads((tmp_path/'capture/capture.json').read_text())
    assert metadata['limit_seconds']==36000 and metadata['reason']=='duration' and metadata['exit_codes']==[0,0]
    monkeypatch.setattr(sys,'argv',['collect.py','--out',str(tmp_path/'bad'),'--adapter','0','--seconds','36001','--max-bytes','1048576'])
    with pytest.raises(SystemExit):module.main()
    assert not (tmp_path/'bad').exists()

@pytest.mark.parametrize('suffix,expected',[
    ('+0900','+09:00'),('Z+0900','+09:00'),('+09:00','+09:00'),
    ('-0330','-03:30'),('Z-0330','-03:30'),('Z','+00:00'),('+0000','+00:00')])
def test_iso_input_normalized_for_python310(monkeypatch,suffix,expected):
    original=ab.datetime;seen=[]
    class StrictISO:
        @staticmethod
        def fromisoformat(value):
            seen.append(value)
            assert value.endswith(expected) and value[-3]==':'
            return original.fromisoformat(value)
    monkeypatch.setattr(ab,'datetime',StrictISO)
    value='2026-11-17T09:00:00.123456'
    assert ab.utc_seconds(value+suffix)==original.fromisoformat(value+expected).timestamp()
    assert seen==[value+expected]
