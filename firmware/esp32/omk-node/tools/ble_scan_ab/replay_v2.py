#!/usr/bin/env python3
"""Read-only replay of cumulative observations through the production C policy.
No MQTT, serial, or SDK operations. Placement scenarios are not event traces.
Requires a C compiler and Python 3.10+. Missing data are never labelled healthy.
"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import ab

BASE=Path(__file__).resolve().parents[2]
ERRORS=ab.COUNTERS[5:12]

def observations(path,since):
    streams=defaultdict(list)
    for row in ab.records(path):
        if 'invalid' in row or row['retained'] or row['time']<since:continue
        p=row['payload'];parts=row['topic'].split('/')
        if len(parts)!=4 or parts[:2]!=['omk','node']:continue
        if parts[3]=='status' and 'mesh_tx_ok' in p:
            r=dict(ms=p['uptime_s']*1000,ok=p['mesh_tx_ok'],err=p['mesh_tx_errors'],timeout=0,nomem=0,
                   eligible=not p['is_root'] and not p['rootless'],root=p['is_root'],
                   legacy_req=p.get('recovery',{}).get('req'),seq=p['seq'])
            mode='status-total-only'
        elif parts[3]=='measurement':
            d=ab.decode(p);banks=d['banks'].values()
            count=lambda keys:sum(b[k] for b in banks for k in keys)
            errors=count(ERRORS)
            r=dict(ms=d['uptime_ms'],ok=count(ab.COUNTERS[:5])-errors,err=errors,
                   timeout=count(['TIMEOUT']),nomem=count(['NO_MEMORY']),
                   eligible=d['role']==0,root=d['role']==1,legacy_req=None,seq=p['seq'])
            mode='measurement-errors'
        else:continue
        r['received_at']=row['time'];r['line']=row['line']
        streams[(parts[2],p['boot_id'],mode)].append(r)
    return streams

def replay(exe,rows,placement):
    rows=sorted(rows,key=lambda r:r['ms']);batches=defaultdict(lambda:[0,0,0,0])
    unknown=[]; spans=[]
    for a,b in zip(rows,rows[1:]):
        duration=b['ms']-a['ms'];d={k:b[k]-a[k] for k in ('ok','err','timeout','nomem')}
        if duration<=0:continue
        if duration>180000 or any(x<0 for x in d.values()) or a['root']!=b['root']:
            unknown.append([a['ms'],b['ms']]);continue
        spans.append((a['ms'],b['ms'],a['eligible'] and b['eligible']))
        classes=[(0,d['ok']),(2,d['timeout']),(3,d['nomem']),(1,d['err']-d['timeout']-d['nomem'])]
        for kind,n in classes:
            if n<0:raise ValueError('inconsistent cumulative counters')
            for i in range(n):
                t=(a['ms']+1 if placement=='start' else b['ms'] if placement=='end'
                   else a['ms']+duration*(i+1)/(n+1))
                stamp=int(t//1000)*1000
                if stamp<=a['ms']:stamp=(a['ms']//1000+1)*1000
                item=batches[stamp]
                if kind==0:item[0]+=1
                else:
                    item[1]+=1
                    if kind>1:item[kind]+=1
    data=[];index=0;span_index=0
    for t in range(rows[0]['ms']//1000*1000,rows[-1]['ms']//1000*1000+2000,1000):
        while span_index<len(spans) and spans[span_index][1]<t:span_index+=1
        active=span_index<len(spans) and spans[span_index][0]<t<=spans[span_index][1]+999 and spans[span_index][2]
        # The rounded final sample has at most 999 ms resolution error.
        data.append(f"{t} {' '.join(map(str,batches[t]))} {int(active)}")
    run=subprocess.run([str(exe),'replay'],input='\n'.join(data)+'\n',text=True,capture_output=True,check=True)
    hits=[];previous=False
    for line in run.stdout.splitlines():
        t,bad,reasons,n,ok,healthy=map(int,line.split())
        if bad and not previous:
            nearest=min(rows,key=lambda r:abs(r['ms']-t))
            estimate=nearest['received_at']+(t-nearest['ms'])/1000
            hits.append(dict(uptime_ms=t,estimated_local=datetime.fromtimestamp(estimate,timezone(timedelta(hours=9))).isoformat(),
                             attempts=n,ok=ok,reasons=reasons,
                             nearest_observed_line=nearest['line'],legacy_req_at_nearest_observation=nearest['legacy_req']))
        previous=bool(bad)
    return dict(placement=placement,first_detection=hits[0] if hits else None,
                open_loop_detections=hits,unknown_intervals_ms=unknown,
                prospective_action='REC_CHILD_REPAIR: SDK quiesce <=30s; bounded disconnect/self-organization restore/reconnect; quality verification')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path);parser.add_argument('--since',required=True)
    args=parser.parse_args();streams=observations(args.log,ab.utc_seconds(args.since))
    with tempfile.TemporaryDirectory(prefix='omk-v2-replay-') as temp:
        exe=Path(temp)/'policy'
        subprocess.run(['cc','-std=c11','-O2','-I',str(BASE/'src'),str(BASE/'tests/quality_host.c'),str(BASE/'src/native_recovery.c'),'-o',str(exe)],check=True)
        output={':'.join(key):[replay(exe,rows,p) for p in ('start','uniform','end')] for key,rows in streams.items() if len(rows)>1}
    print(json.dumps(dict(log_sha256=hashlib.sha256(args.log.read_bytes()).hexdigest(),since=args.since,
        limitations=['Counter interval placement scenarios, not exact SDK timestamps or rigorous earliest/latest bounds.',
        'Status total error stream and measurement typed error stream are independent, never added.',
        'Queue request denominator and validated receipt arrivals unavailable: those branches not replayed.',
        'Gaps over 180s or role/counter discontinuity excluded; missing intervals are unknown, never healthy.',
        'No counterfactual recovery success: observed later traffic is open-loop only.',
        'Clock estimate uses nearest Gateway receive time; transit delay and startup/parent grace not fully reconstructable.'],results=output),indent=2))

if __name__=='__main__':main()
