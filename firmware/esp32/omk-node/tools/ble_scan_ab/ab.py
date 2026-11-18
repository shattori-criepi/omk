#!/usr/bin/env python3
"""Offline-first BLE scan trial tooling. Only `send` accesses MQTT via mosquitto_pub.
No service, adapter, Mesh, Flash or registration settings are modified.
"""
from __future__ import annotations
import argparse
import base64
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
import tempfile
from pathlib import Path
import re
import subprocess
import sys
import time
PRESETS=('A','B40','B25','B15')
COUNTERS=('lt20ms','20to200ms','200to2000ms','2000to5000ms','ge5000ms',
          'TIMEOUT','NO_MEMORY','QUEUE_FULL','XMIT','DISCONNECTED','NO_ROUTE_FOUND','other',
          'outside_completions','outside_TIMEOUT')
HEX12=re.compile(r'^[0-9a-f]{12}$')
HEX16=re.compile(r'^[0-9a-f]{16}$')
MAX_TIME=2**53-1

def utc_seconds(value):
    if isinstance(value,(float,int)) and not isinstance(value,bool):
        try:
            stamp=float(value)
            if not math.isfinite(stamp):raise ValueError('non-finite timestamp')
            datetime.fromtimestamp(stamp,timezone.utc)
        except (OverflowError,OSError) as exc:raise ValueError('timestamp outside datetime range') from exc
        return stamp
    # Mosquitto 2.0.x can append a numeric zone after a literal Z. That Z
    # is a format marker, not a second UTC offset; use the explicit suffix.
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z[+-]\d{4}|Z|[+-]\d{2}:?\d{2})',value):
        raise ValueError('invalid timezone-qualified timestamp')
    value=re.sub(r'Z(?=[+-]\d{4}$)','',value)
    if value.endswith('Z'):value=value[:-1]+'+00:00'
    zone=re.search(r'([+-])(\d{2}):?(\d{2})$',value)
    if not zone or int(zone[2])>23 or int(zone[3])>59:raise ValueError('invalid timezone offset')
    # Python 3.10 requires the colon in ISO8601 numeric offsets.
    value=value[:zone.start()]+zone[1]+zone[2]+':'+zone[3]
    return datetime.fromisoformat(value).timestamp()

def ack_state(cmd,r):
    if r['role'] or r['gap']!=4 or r['effective']!=r['desired']:return False
    if cmd['command']=='ble_scan_cancel':
        return r['plan_state']==3 and r['effective']==0 and r['result'] in (2,3,5)
    if r['plan_state']!=1 or r['result'] not in (1,2,3):return False
    desired=0
    for start,end,preset in cmd['segments']:
        if start<=r['uptime_ms']<end:desired=preset
    return r['uptime_ms']<cmd['plan_end_ms'] and r['effective']==desired


def decode(payload):
    if isinstance(payload,str): payload=json.loads(payload)
    if payload.get('protocol_version')!=2 or not HEX12.fullmatch(payload.get('node_id','')) or not HEX16.fullmatch(payload.get('boot_id','')):
        raise ValueError('invalid measurement envelope')
    for name in ('seq','revision'):
        if type(payload.get(name)) is not int or not 0<=payload[name]<=0xffffffff: raise ValueError(name)
    raw=base64.b64decode(payload['m'],validate=True)
    if len(raw)!=256 or len(payload['m'])!=344 or base64.b64encode(raw).decode()!=payload['m']: raise ValueError('noncanonical 256B record')
    def u(offset,n=4): return int.from_bytes(raw[offset:offset+n],'little')
    state,flags=u(40),u(44)
    if state&15!=1 or flags>>17: raise ValueError('unsupported measurement version/flags')
    r={k:payload[k] for k in ('node_id','boot_id','seq','revision')}
    r.update(uptime_ms=u(0,8),applied_ms=u(8,8),plan_end_ms=u(16,8),epoch=u(24),root_nonce=f'{u(28,8):016x}',
             inflight_age_ms=u(36),flags=flags,mixed_invalid=u(48),
             desired=(state>>4)&7,effective=(state>>7)&7,role=(state>>10)&3,gap=(state>>12)&7,
             plan_state=(state>>15)&7,segment=(state>>18)&7,inflight_kind=(state>>21)&15,result=(state>>25)&127)
    if r['desired']>3 or r['effective']>4 or r['role']>1 or r['gap']>6 or r['plan_state']>3 or r['segment']>4: raise ValueError('invalid state bits')
    r['banks']={p:dict(zip(COUNTERS,(u(52+i*42+j*3,3) for j in range(14)))) for i,p in enumerate(PRESETS)}
    r['episode']={'count':u(220,2),'onset_s':u(222),'end_s':u(226),'cumulative_s':u(230)}
    r['self']={'sdk_ok':u(234,2),'sdk_error':u(236,2),'pre_sdk_drop':u(238,2)}
    r['heap']={'free':u(240),'min':u(244),'stale':bool(flags&(1<<11))}
    masks=u(252,1)
    r['flash']={'arm_generation':u(248),'ready':bool(masks&1),'occupied':(masks>>1)&3,'valid':(masks>>3)&3,'attempt':(masks>>5)&3,
                'phase':u(253,1)&15,'block':u(253,1)>>4,'error':u(254,1),'age_s':u(255,1),'stale':bool(flags&(1<<12))}
    return r

def records(path):
    """mosquitto_sub -F '%j': preserve malformed payloads; diagnose bad lines."""
    with Path(path).open() as stream:
        for number,line in enumerate(stream,1):
            try:
                item=json.loads(line)
                stamp=utc_seconds(item.get('received_at',item.get('tst')))
                retained=item.get('retained',item.get('retain',False))
                if retained not in (0,1,False,True): raise ValueError('retain')
                payload=item['payload']
                if isinstance(payload,str): payload=json.loads(payload)
                if not isinstance(payload,dict): raise ValueError('payload is not object')
                yield {'time':stamp,'retained':bool(retained),'topic':item['topic'],'payload':payload,'line':number}
            except (ValueError,KeyError,TypeError) as exc:
                yield {'invalid':str(exc),'line':number}

def reports(path):
    for r in records(path):
        if 'invalid' in r or not r['topic'].endswith('/measurement'): continue
        try: r['report']=decode(r['payload'])
        except (ValueError,KeyError,TypeError): continue
        if r['topic']!=f"omk/node/{r['report']['node_id']}/measurement": continue
        yield r

def latest(path,node):
    values=[r for r in reports(path) if not r['retained'] and r['report']['node_id']==node]
    if not values: raise ValueError(f'no non-retained measurement for {node}')
    # Never use an old queued record to extrapolate a newer node uptime.
    return max(values,key=lambda r:(r['time'],r['report']['uptime_ms']))

def make_command(node,root,epoch,pattern,durations,now=None,cancel=False,lead_s=150):
    now=time.time() if now is None else now
    for observation in (node,root):
        age=now-observation['time']
        if not 0<=age<=75 or observation['retained']: raise ValueError('measurement stale, retained or future')
    n,r=node['report'],root['report']
    if n['role'] or r['role']!=1 or not r['flags']&(1<<13) or r['root_nonce']=='0000000000000000': raise ValueError('fresh non-Root and Root nonce required')
    if not n['epoch']<epoch<=0xffffffff: raise ValueError('epoch must increase within current boot')
    elapsed=int((now-node['time'])*1000)
    current=n['uptime_ms']+elapsed
    # Delivery is still bounded by Root queue TTL; Node checks its own uptime.
    accept=current+90000
    cmd={'protocol_version':2,'command':'ble_scan_cancel' if cancel else 'ble_scan_plan','node_id':n['node_id'],
         'target_boot_id':n['boot_id'],'root_id':r['node_id'],'root_nonce':r['root_nonce'],
         'control_epoch':epoch,'accept_before_ms':accept}
    if not cancel:
        if not 1<=len(pattern)<=4 or len(pattern)!=len(durations) or lead_s<121: raise ValueError('1..4 segments and lead >=121s required')
        start=current+lead_s*1000; segments=[]
        for p,seconds in zip(pattern,durations):
            if p not in PRESETS or type(seconds) is not int or seconds<=0: raise ValueError('preset/duration')
            end=start+seconds*1000;segments.append([start,end,PRESETS.index(p)]);start=end
        if start-current>36000000: raise ValueError('plan exceeds 10 hours')
        cmd.update(segments=segments,plan_end_ms=start)
    if max(accept,cmd.get('plan_end_ms',0))>MAX_TIME: raise ValueError('uptime outside exact JSON integer range')
    if len(json.dumps(cmd,separators=(',',':')).encode())>512: raise ValueError('command exceeds 512B')
    return cmd

def send_command(path,log,broker):
    """Explicit future operation only. Six identical deliveries, never renew TTL."""
    raw=Path(path).read_bytes();cmd=json.loads(raw)
    if cmd.get('command') not in ('ble_scan_plan','ble_scan_cancel') or not HEX12.fullmatch(cmd.get('node_id','')) or len(raw)>512:
        raise ValueError('invalid prepared command')
    accepted={1,2,3,4,5} # admission/result, never a proof of sustained BLE quality
    first_send=None
    for attempt in range(6):
        n=latest(log,cmd['node_id']);now=time.time()
        r=n['report'];age=now-n['time']
        if not 0<=age<=75 or r['boot_id']!=cmd['target_boot_id'] or r['role']:
            raise ValueError('Node freshness/boot/role changed; no send')
        if r['epoch']>=cmd['control_epoch']:
            # An already-used epoch could belong to a different prepared file.
            # Never infer acknowledgement from a report predating this send.
            ok=(first_send is not None and n['time']>=first_send and r['epoch']==cmd['control_epoch'] and
                r['result'] in accepted and ack_state(cmd,r) and r['root_nonce']==cmd['root_nonce'] and
                r['plan_end_ms']==cmd.get('plan_end_ms',0))
            print(json.dumps({'acknowledged':ok,'attempts':attempt,'report':r}))
            return 0 if ok else 2
        if r['uptime_ms']+int(age*1000)>=cmd['accept_before_ms']:
            raise ValueError('absolute acceptance deadline passed; no retry')
        root_observation=latest(log,cmd['root_id'])
        if not 0<=now-root_observation['time']<=75: raise ValueError('Root measurement stale')
        root=root_observation['report']
        if root['role']!=1 or not root['flags']&(1<<13) or root['root_nonce']!=cmd['root_nonce']:
            raise ValueError('Root tenure changed; do not rewrap old plan with a new nonce')
        if first_send is None:first_send=now
        # Ignore ambient client config: it could silently enable retained publish.
        with tempfile.TemporaryDirectory(prefix='omk-ab-mqtt-') as config:
            result=subprocess.run(['mosquitto_pub','-h',broker,'-q','1','-t',f"omk/node/{cmd['node_id']}/ble_scan/config",'-s'],input=raw,timeout=4,check=False,env=dict(os.environ,XDG_CONFIG_HOME=config))
        print(json.dumps({'attempt':attempt+1,'exit_code':result.returncode,'epoch':cmd['control_epoch']}),flush=True)
        if attempt<5: time.sleep(5)
    print('Receipt not yet confirmed: check the next report; no automatic new epoch.',file=sys.stderr)
    return 2

def classify_interval(start,end,onsets,unknown,stopped):
    # Half-open intervals. Never split cumulative counters proportionally.
    if any(start<b and end>a for a,b in unknown):return 'unknown'
    if any(start<c and end>max(0,c-180) for c in onsets):return 'precursor_or_boundary'
    if stopped:return 'stopped_or_recovering'
    return 'stable_observed'

def compare(path,expected):
    """Conservative interval deltas; missing conditions never become healthy time."""
    if len(expected)!=4 or len(set(expected))!=4 or any(not HEX12.fullmatch(n) for n in expected):
        raise ValueError('four distinct physical Node IDs required')
    # Streaming pass discards high-volume BLE/SEN66 payloads immediately.
    raw=[];invalid_count=0
    for row in records(path):
        if 'invalid' in row:invalid_count+=1
        elif row['topic'].endswith('/status') and '/registration/' not in row['topic']:raw.append(row)
    rows=reports(path); unique={};duplicates=retained=late=0
    for row in rows:
        r=row['report'];key=(r['node_id'],r['boot_id'],r['seq'])
        if row['retained']: retained+=1;continue
        if key in unique: duplicates+=1;continue
        unique[key]=row
    bynode=defaultdict(list)
    for row in unique.values(): bynode[row['report']['node_id']].append(row)
    statuses=defaultdict(list)
    for row in raw:
        if 'invalid' not in row and not row['retained'] and row['topic'].endswith('/status') and '/registration/' not in row['topic']:
            statuses[row['payload'].get('node_id')].append(row)
    for values in statuses.values(): values.sort(key=lambda x:x['time'])
    def topology(node,start,end):
        candidates=[x for x in statuses[node] if start-75<=x['time']<=end+1]
        before=[x for x in candidates if x['time']<=start]
        after=[x for x in candidates if x['time']<=end]
        if not before or not after or end-after[-1]['time']>75:return None
        relevant=[before[-1],*[x for x in candidates if start<x['time']<=end]]
        keys={(x['payload'].get('boot_id'),x['payload'].get('root_mac'),x['payload'].get('root_change_count'),
               x['payload'].get('parent_disconnect_count'),x['payload'].get('mesh_layer')) for x in relevant}
        p=relevant[-1]['payload']
        return p.get('root_mac') if len(keys)==1 and p.get('root_mac') and p.get('is_root') is False and not p.get('rootless') else None
    intervals=[]; totals={};unknown=0
    episode_evidence={}
    for node,values in bynode.items():
        for boot in {v['report']['boot_id'] for v in values}:
            ordered=sorted((v['report'] for v in values if v['report']['boot_id']==boot),key=lambda r:r['uptime_ms'])
            onsets=sorted({r['episode']['onset_s'] for r in ordered if r['episode']['count'] and r['episode']['onset_s']})
            uncertain=[]
            for u,v in zip(ordered,ordered[1:]):
                if v['uptime_ms']-u['uptime_ms']>90000 or v['episode']['count']-u['episode']['count']>1:
                    uncertain.append((max(0,u['uptime_ms']/1000-180),v['uptime_ms']/1000))
            if ordered:uncertain.append((max([0,ordered[-1]['uptime_ms']/1000-180]+onsets),float('inf')))
            episode_evidence[(node,boot)]=(onsets,uncertain)

    for node,values in bynode.items():
        if node not in expected:continue
        values.sort(key=lambda x:x['time']);previous=None;node_totals={}
        for row in values:
            r=row['report'];boot=r['boot_id']
            if previous and previous['report']['boot_id']==boot and r['uptime_ms']<=previous['report']['uptime_ms']:
                late+=1;continue
            node_totals[boot]=r # Final cumulative value preserves recovered stop-period history.
            if previous and previous['report']['boot_id']==boot:
                old=previous['report'];seconds=(r['uptime_ms']-old['uptime_ms'])/1000
                root=topology(node,previous['time'],row['time'])
                known=0<seconds<=150 and row['time']-previous['time']<=150 and root is not None and r['role']==old['role']==0 and r['gap']==old['gap']==4 and r['effective']==old['effective']<4 and r['epoch']==old['epoch'] and r['plan_state']==old['plan_state'] and r['segment']==old['segment'] and r['applied_ms']==old['applied_ms']
                p=r['effective'];banks={}
                for i,preset in enumerate(PRESETS):
                    delta={k:r['banks'][preset][k]-old['banks'][preset][k] for k in COUNTERS}
                    if any(v<0 for v in delta.values()) or (r['flags']|old['flags'])&(1<<i): known=False
                    banks[preset]=delta
                if known:
                    intervals.append({'node':node,'boot':boot,'start':previous['time'],'end':row['time'],'seconds':seconds,'root':root,'preset':PRESETS[p],'counts':banks[PRESETS[p]],'mixed_delta':r['mixed_invalid']-old['mixed_invalid'], 'phase':classify_interval(old['uptime_ms']/1000,r['uptime_ms']/1000,*episode_evidence[(node,boot)],bool((old['flags']|r['flags'])&((1<<14)|(1<<15))))})
                else: unknown+=1
            previous=row
        totals[node]=node_totals
    # Network-wide requires simultaneous evidence from ALL expected nodes,
    # including fresh Root role/nonce. Partial observations remain labelled.
    def full(x):
        for node in expected:
            if node==x['node']:continue
            peer=any(y['node']==node and y['root']==x['root'] and y['preset']==x['preset'] and y['start']<=x['start']+5 and y['end']>=x['end']-5 for y in intervals)
            rootrows=bynode.get(node,[])
            before=[z for z in rootrows if x['start']-75<=z['time']<=x['start']+5]
            after=[z for z in rootrows if x['end']-5<=z['time']<=x['end']+75]
            rootok=any(u['time']<v['time'] and u['report']['role']==v['report']['role']==1 and
                u['report']['boot_id']==v['report']['boot_id'] and u['report']['root_nonce']==v['report']['root_nonce'] and
                u['report']['flags']&(1<<13) and v['report']['flags']&(1<<13) for u in before for v in after)
            # Status binds the physical Root identity to the Mesh MAC at both ends.
            roots=[z for z in statuses[node] if x['start']-75<=z['time']<=x['end']+75]
            roots_before=[z for z in roots if z['time']<=x['start']+5]
            roots_after=[z for z in roots if z['time']>=x['end']-5]
            rootok=rootok and any(u['time']<v['time'] and all(z['payload'].get('is_root') and
                z['payload'].get('root_mac')==x['root'] and z['payload'].get('boot_id')==u['payload'].get('boot_id') and
                z['payload'].get('root_change_count')==u['payload'].get('root_change_count') for z in (u,v))
                for u in roots_before for v in roots_after)
            if not(peer or rootok):return False
        return len(expected)==4
    for interval in intervals:interval['scope']='network' if full(interval) else 'partial'
    groups={}
    for x in intervals:
        # Avoid treating individual calls as independent samples; retain node/date/root strata.
        night=datetime.fromtimestamp(x['start'],timezone.utc).date().isoformat()
        key='|'.join((x['node'],night,x['root'],x['preset'],x['scope'],x['phase']))
        g=groups.setdefault(key,{'seconds':0,'counts':dict.fromkeys(COUNTERS,0),'mixed':0})
        g['seconds']+=x['seconds'];g['mixed']+=x['mixed_delta']
        for k in COUNTERS:g['counts'][k]+=x['counts'][k]
    for g in groups.values():
        c=g['counts'];g['outside_timeout_rate']=c['outside_TIMEOUT']/c['outside_completions'] if c['outside_completions'] else None
        n=sum(c[k] for k in COUNTERS[:5]);acc=0;g['p95_bin']=None
        for k in COUNTERS[:5]:
            acc+=c[k]
            if n and acc>=n*.95:g['p95_bin']=k;break
    timeout_comparisons={}
    for key,g in groups.items():
        stratum,phase=key.rsplit('|',1)
        if phase not in ('stable_observed','precursor_or_boundary'):continue
        summary=timeout_comparisons.setdefault(stratum,{
            name:{'TIMEOUT':0,'completions':0} for name in ('stable','including_precursor')})
        for name in ('stable','including_precursor') if phase=='stable_observed' else ('including_precursor',):
            summary[name]['TIMEOUT']+=g['counts']['outside_TIMEOUT']
            summary[name]['completions']+=g['counts']['outside_completions']
    for summary in timeout_comparisons.values():
        for value in summary.values():value['rate']=value['TIMEOUT']/value['completions'] if value['completions'] else None
    missing=sorted(set(expected)-set(bynode))
    return {'expected_nodes':expected,'missing_nodes':missing,'invalid_lines':invalid_count,'duplicate_reports':duplicates,'retained_reports_excluded':retained,'late_reports_excluded':late,'unknown_intervals':unknown,'groups':groups,'timeout_comparisons':timeout_comparisons,'intervals':intervals,'boot_cumulative_exploratory':totals,'incidence_rate':None,'incidence_note':'Episode counts are cumulative evidence only; risk-time denominator requires adjudicated demand, downtime and censoring. No automatic incidence rate.',
            'limits':['No RF or true event detection guarantee.','Durations include stopped periods; outside_TIMEOUT uses its own denominator.','Final boot totals across missing reports cannot reconstruct topology or timing.','No significance or improvement decision; dropout and partial samples must be reviewed.']}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    d=sub.add_parser('decode');d.add_argument('payload',type=Path)
    for name in ('plan','cancel'):
        c=sub.add_parser(name);c.add_argument('--log',type=Path,required=True);c.add_argument('--node',required=True);c.add_argument('--root',required=True);c.add_argument('--epoch',type=int,required=True);c.add_argument('--out',type=Path,required=True)
        if name=='plan':c.add_argument('--presets',nargs='+',choices=PRESETS,required=True);c.add_argument('--seconds',nargs='+',type=int,required=True);c.add_argument('--lead',type=int,default=150)
    s=sub.add_parser('send');s.add_argument('command',type=Path);s.add_argument('--log',type=Path,required=True);s.add_argument('--broker',default='localhost')
    c=sub.add_parser('compare');c.add_argument('log',type=Path);c.add_argument('--nodes',nargs=4,required=True)
    a=p.parse_args(argv)
    try:
        if a.action=='decode':out=decode(a.payload.read_text())
        elif a.action in ('plan','cancel'):
            out=make_command(latest(a.log,a.node),latest(a.log,a.root),a.epoch,getattr(a,'presets',[]),getattr(a,'seconds',[]),cancel=a.action=='cancel',lead_s=getattr(a,'lead',150))
            with a.out.open('x') as f:f.write(json.dumps(out,separators=(',',':')))
        elif a.action=='send':return send_command(a.command,a.log,a.broker)
        else:out=compare(a.log,a.nodes)
        print(json.dumps(out,ensure_ascii=False,indent=2));return 0
    except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as exc:
        p.exit(2,str(exc)+'\n')
if __name__=='__main__':raise SystemExit(main())
