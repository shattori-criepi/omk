#!/usr/bin/env python3
"""Future opt-in capture: existing MQTT + HCI monitor, no scan/service changes."""
import argparse
from datetime import datetime,timezone
import json
import os
import tempfile
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
TOPICS=['omk-relay/+/ble/raw','omk-relay/+/sen66/raw','omk/node/+/status','omk/node/+/measurement']
STOP_SIGNALS=(signal.SIGHUP,signal.SIGTERM,signal.SIGINT)
STOP_STEPS=((signal.SIGINT,5),(signal.SIGTERM,2),(signal.SIGKILL,2))

def stop_child(name,child):
    """Reap each owned direct child, with bounded waits even after SIGKILL."""
    result={'command':name,'pid':child.pid,'signals':[],'wait_timeouts':[],
            'reaped':False,'exit_code':None,'forced':False,'errors':[]}
    for sig,timeout in STOP_STEPS:
        try:
            if child.poll() is None:
                child.send_signal(sig)
                result['signals'].append(signal.Signals(sig).name)
                if sig==signal.SIGKILL:result['forced']=True
        except Exception as exc:
            result['errors'].append(f'{signal.Signals(sig).name}: {type(exc).__name__}: {exc}')
        try:
            result['exit_code']=child.wait(timeout=timeout)
            result['reaped']=True
            break
        except subprocess.TimeoutExpired:
            result['wait_timeouts'].append(signal.Signals(sig).name)
        except Exception as exc:
            result['errors'].append(f'wait: {type(exc).__name__}: {exc}')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--adapter',required=True);p.add_argument('--broker',default='localhost');p.add_argument('--seconds',type=int,required=True);p.add_argument('--max-bytes',type=int,required=True);a=p.parse_args()
    if not 1<=a.seconds<=36000 or a.max_bytes<1048576:p.error('duration 1..36000s, budget >=1MiB')
    for tool in ('btmon','mosquitto_sub'):
        if not shutil.which(tool):p.error(f'{tool} missing; no automatic installation')
    a.out.mkdir(parents=True,exist_ok=False)
    children=[];files=[];reason='duration';config=None;stop_requested=None
    started=datetime.now(timezone.utc).isoformat();previous={};cleanup_errors=[];stops=[]
    def interrupted(signum,frame):
        # Do not raise between Popen returning and children.append, or during
        # cleanup. Repeated signals cannot interrupt child tracking/reaping.
        nonlocal stop_requested
        if stop_requested is None:stop_requested=signum
    try:
        for sig in STOP_SIGNALS:previous[sig]=signal.signal(sig,interrupted)
        config=tempfile.TemporaryDirectory(prefix='omk-ab-mqtt-')
        if shutil.disk_usage(a.out).free<a.max_bytes+268435456:raise OSError('insufficient disk reserve')
        mqtt_cmd=['mosquitto_sub','-h',a.broker,'-F','%j']
        for t in TOPICS:mqtt_cmd+=['-t',t]
        for cmd,out,err in ((mqtt_cmd,'mqtt.jsonl','mqtt.stderr'),(['btmon','-i',a.adapter,'-w',str(a.out/'hci.btsnoop')],'btmon.stdout','btmon.stderr')):
            if stop_requested is not None:break
            stdout=(a.out/out).open('xb');files.append(stdout)
            stderr=(a.out/err).open('xb');files.append(stderr)
            child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,start_new_session=True,
                                   env=dict(os.environ,XDG_CONFIG_HOME=config.name))
            children.append((cmd[0],child))
        until=time.monotonic()+a.seconds
        while stop_requested is None and time.monotonic()<until:
            if any(c.poll() is not None for _,c in children):reason='collector_exit';break
            if sum(f.stat().st_size for f in a.out.iterdir() if f.is_file())>=a.max_bytes:reason='size_limit';break
            if shutil.disk_usage(a.out).free<268435456:reason='disk_reserve';break
            time.sleep(1)
        if stop_requested is not None:reason='operator_stop'
    except KeyboardInterrupt:
        reason='operator_stop'
        if stop_requested is None:stop_requested=signal.SIGINT
    except Exception as exc:reason=f'error: {type(exc).__name__}: {exc}'
    finally:
        # Keep handlers installed until all cleanup attempts and metadata writes
        # finish. A failure for one child/file must not prevent the others.
        for name,child in children:
            try:stops.append(stop_child(name,child))
            except Exception as exc:
                stops.append({'command':name,'pid':child.pid,'reaped':False,
                              'exit_code':None,'forced':False,'errors':[f'{type(exc).__name__}: {exc}']})
        for f in files:
            try:f.close()
            except Exception as exc:cleanup_errors.append(f'close: {type(exc).__name__}: {exc}')
        if config is not None:
            try:config.cleanup()
            except Exception as exc:cleanup_errors.append(f'temporary config: {type(exc).__name__}: {exc}')
        if stop_requested is not None and reason=='duration':reason='operator_stop'
        cleanup_ok=all(s['reaped'] and not s['errors'] for s in stops) and not cleanup_errors
        metadata={'started':started,'finished':datetime.now(timezone.utc).isoformat(),'reason':reason,
                  'signal':signal.Signals(stop_requested).name if stop_requested is not None else None,
                  'adapter':a.adapter,'limit_seconds':a.seconds,'limit_bytes':a.max_bytes,
                  'exit_codes':[s['exit_code'] for s in stops],'children':stops,
                  'cleanup_ok':cleanup_ok,'cleanup_errors':cleanup_errors,
                  'note':'HCI observation; not RF truth. Inspect mqtt.stderr for reconnect gaps. SIGKILL/parent death cannot guarantee cleanup; use a managed tmux session.'}
        try:(a.out/'capture.json').write_text(json.dumps(metadata,indent=2)+'\n')
        except OSError as exc:
            cleanup_ok=False
            metadata['cleanup_ok']=False;metadata['cleanup_errors'].append(f'capture.json: {exc}')
            print(json.dumps(metadata),file=sys.stderr)
        finally:
            for sig,handler in previous.items():signal.signal(sig,handler)
    return 0 if reason in ('duration','operator_stop') and cleanup_ok and not any(s['forced'] for s in stops) else 2
if __name__=='__main__':raise SystemExit(main())
