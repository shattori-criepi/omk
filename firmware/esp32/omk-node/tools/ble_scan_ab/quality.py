#!/usr/bin/env python3
"""Offline SEN66 gaps and observed BLE state changes. Never estimates RF truth."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import sys
from ab import records,utc_seconds
# Use the saved Native Mesh v2 decoder, without importing/starting the service.
GATEWAY=Path(__file__).resolve().parents[5]/'services/ble-sensor-manager/src'
sys.path.insert(0,str(GATEWAY))
from omk_ble.switchbot import decode

def quantiles(values):
    values=sorted(values)
    if not values:return {'n':0,'p50':None,'p95':None,'max':None}
    import math
    return {'n':len(values),'p50':values[math.ceil(len(values)*.5)-1],
            'p95':values[math.ceil(len(values)*.95)-1],'max':values[-1]}

def read_observations(log,models,hci=None):
    observations=[];sen=defaultdict(list);seen=set();unknown=malformed=0
    last_state={};reception={}
    def observe(x):
        key=(x['path'],x['address'],x['field'])
        summary=reception.setdefault('|'.join(key),{'received':0,'first':x['time'],'last':x['time'],'max_gap_s':0,'gaps_gt60s':0,'minutes':set()})
        gap=x['time']-summary['last']
        if gap<0:raise ValueError('out-of-order observations require timestamp normalization')
        summary['max_gap_s']=max(summary['max_gap_s'],gap);summary['gaps_gt60s']+=gap>60
        summary['last']=x['time'];summary['received']+=1;summary['minutes'].add(int(x['time']//60))
        if key not in last_state or last_state[key]!=x['value']:observations.append(x)
        last_state[key]=x['value']
    for row in records(log):
        if 'invalid' in row:malformed+=1;continue
        if row['retained']:continue
        p=row['payload'];topic=row['topic'];kind=topic.rsplit('/',2)[-2:]
        if kind not in (['ble','raw'],['sen66','raw']):continue
        key=(topic,p.get('boot_id'),p.get('seq'))
        if key in seen:continue
        seen.add(key)
        if kind==['sen66','raw']:
            sen[(p.get('node_id'),p.get('boot_id'))].append(row['time']);continue
        try:
            address=p['ble_address'].lower();hint=models.get(address)
            if not hint:continue
            md={int(x['company_id']):bytes.fromhex(x['data']) for x in p['manufacturer_data']}
            sd={x['uuid']:bytes.fromhex(x['data']) for x in p['service_data']}
            decoded=decode(address,p['rssi'],md,sd,str(row['time']),model_hint=hint)
            if not decoded or not decoded.values:unknown+=1;continue
            for field in ('motion_state','contact_state'):
                if field in decoded.values:observe({'time':row['time'],'address':address,'path':p['node_id'],'field':field,'value':decoded.values[field]})
        except (ValueError,KeyError,TypeError):malformed+=1
    if hci:
        # Normalized observations are explicitly marked HCI, not Bleak callbacks.
        # btmon's human-readable/binary format is version-dependent: no guessed
        # parser is allowed to turn missing records into a negative observation.
        for line in Path(hci).read_text().splitlines():
            p=json.loads(line);address=p['address'].replace(':','').lower()
            if address not in models:continue
            if p.get('source')!='hci':raise ValueError('HCI normalization must identify source=hci')
            md={int(k,16):bytes.fromhex(v) for k,v in p.get('manufacturer_data',{}).items()}
            sd={k:bytes.fromhex(v) for k,v in p.get('service_data',{}).items()}
            decoded=decode(address,p['rssi'],md,sd,p['timestamp'],model_hint=models[address])
            if not decoded or not decoded.values:unknown+=1;continue
            for field in ('motion_state','contact_state'):
                if field in decoded.values:observe({'time':utc_seconds(p['timestamp']),'address':address,'path':'gateway_hci','field':field,'value':decoded.values[field]})
    sen_summary={f'{n}|{b}':{'received':len(times),'gaps_s':quantiles([v-u for u,v in zip(sorted(times),sorted(times)[1:])]),'gaps_gt60s':sum(v-u>60 for u,v in zip(sorted(times),sorted(times)[1:]))} for (n,b),times in sen.items()}
    for value in reception.values():value['observed_60s_bins']=len(value.pop('minutes'))
    return observations,sen_summary,{'unknown_ble':unknown,'malformed':malformed,'ble_reception':reception}

def changes(observations):
    state={};out=[];initial=0
    for x in sorted(observations,key=lambda v:v['time']):
        key=(x['path'],x['address'],x['field']);old=state.get(key);state[key]=x['value']
        if old is None:initial+=1
        elif old!=x['value']:out.append(x)
    return out,initial

def evaluate(observations,paths,manual=None):
    edges,initial=changes(observations);clusters=[]
    for e in edges:
        matches=[c for c in clusters if (c['address'],c['field'],c['value'])==(e['address'],e['field'],e['value']) and 0<=e['time']-c['time']<=5 and e['path'] not in c['paths']]
        if matches:matches[-1]['paths'][e['path']]=e['time']
        else:clusters.append({**{k:e[k] for k in ('time','address','field','value')},'paths':{e['path']:e['time']},'ambiguous':False})
    # Short round-trips/delayed contradictory states cannot be assigned a unique
    # union event. Keep them visible, exclude them from relative capture ratios.
    for i,c in enumerate(clusters):
        for d in clusters[i+1:]:
            if d['time']-c['time']>5:break
            if (c['address'],c['field'])==(d['address'],d['field']) and c['value']!=d['value']:c['ambiguous']=d['ambiguous']=True
    valid=[c for c in clusters if not c['ambiguous']]
    natural={path:{'observed_reference_events':len(valid),'captured':sum(path in c['paths'] for c in valid),'relative_delay_s':quantiles([c['paths'][path]-c['time'] for c in valid if path in c['paths']])} for path in paths}
    manual_results=[]
    if manual:
        trials=list(csv.DictReader(Path(manual).open()))
        used=set()
        for trial in trials:
            stamp=utc_seconds(trial['timestamp']);address=trial['address'].replace(':','').lower();field=trial['field'];value=int(trial['value'])
            next_op=min([utc_seconds(t['timestamp']) for t in trials if t['address'].replace(':','').lower()==address and t['field']==field and utc_seconds(t['timestamp'])>stamp]+[stamp+30])
            end=min(stamp+30,next_op)
            found={}
            for path in paths:
                candidates=[(i,e) for i,e in enumerate(edges) if i not in used and e['path']==path and e['address']==address and e['field']==field and e['value']==value and stamp<=e['time']<end]
                if candidates:
                    i,e=candidates[0];used.add(i);found[path]=e['time']-stamp
                else:found[path]=None
            manual_results.append({**trial,'window_s':end-stamp,'delay_s':found,'within5s':{p:v is not None and v<=5 for p,v in found.items()}})
    return {'initial_states_excluded':initial,'ambiguous_clusters':sum(c['ambiguous'] for c in clusters),'observed_union':natural,'events':clusters,'manual':manual_results,
            'limits':['Observed union is not all physical events; common misses remain invisible.','HCI is controller-to-host observation, not RF reception or Bleak callback.','Manual delays include sensor detection/hold times; ambiguous short round-trips need raw review.','Separate A/B intervals externally using actual applied reports; do not pool boot totals as a randomized experiment.']}
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('log');p.add_argument('--models',required=True);p.add_argument('--paths',nargs='+',required=True);p.add_argument('--hci');p.add_argument('--manual');a=p.parse_args()
    obs,sen,errors=read_observations(a.log,json.loads(Path(a.models).read_text()),a.hci)
    print(json.dumps({'ble':evaluate(obs,a.paths,a.manual),'sen66':sen,'input':errors},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
