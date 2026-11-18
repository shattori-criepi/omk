import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import zlib
import pytest
BASE=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('probe_tools',BASE/'tools/ble_scan_ab/probe_v2.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)

def payload():
    b=bytearray(1424);struct.pack_into('<II',b,0,1,len(b))
    b[16:29]=b'020000000001\0';b[29:46]=b'0123456789abcdef\0'
    return bytes(b)

@pytest.mark.parametrize('mutation',['short','version','length','node','head','count'])
def test_decoder_rejects(mutation):
    b=bytearray(payload())
    if mutation=='short':b.pop()
    elif mutation=='node':b[16]=ord('z')
    else:struct.pack_into('<I',b,{'version':0,'length':4,'head':52,'count':56}[mutation],9999)
    with pytest.raises(ValueError):probe.decode(b)

class StaleToken(Exception):pass
class FakeSession:
    retries=3
    def __init__(self,stale=False,crc_bad=False):self.starts=0;self.stale=stale;self.crc_bad=crc_bad;self.requests=[]
    def request(self,command,**kw):
        self.requests.append(command)
        data=payload()
        if command=='diag_v2':
            self.starts+=1;return dict(length=1424,token=self.starts,crc32=f'{zlib.crc32(data)+(1 if self.crc_bad else 0):08x}')
        assert command=='diag_read' and kw['space']=='v2' and kw['token']==self.starts
        if self.stale and self.starts==1:raise StaleToken()
        part=data[kw['offset']:kw['offset']+kw['count']]
        reply=dict(offset=kw['offset'],token=self.starts,data=part.hex(),crc32=f'{zlib.crc32(part):08x}')
        assert kw['validate'](reply);return reply

@pytest.mark.parametrize('stale',[False,True])
def test_guarded_usb_reuse(stale):
    s=FakeSession(stale);assert probe.collect(s)==payload()
    assert s.starts==(2 if stale else 1) and set(s.requests)=={'diag_v2','diag_read'}

def test_bounded_crc_retry():
    s=FakeSession(crc_bad=True)
    with pytest.raises(ValueError):probe.collect(s)
    assert s.starts==3
