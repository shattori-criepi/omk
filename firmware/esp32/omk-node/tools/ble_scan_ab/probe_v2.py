#!/usr/bin/env python3
"""V2 RAM sidecar decoder/collector. Python 3.10+. No erase/arm/config APIs.
`decode` is offline; `collect` requires separately authorized native USB access.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import zlib

SIZE=1424
EVENTS=('current_pre','first_timeout_pre','first_timeout_post','first_nomem_pre','first_nomem_post',
        'first_reject_delta_post','last_error_pre','last_error_post','recovery_pre','recovery_api',
        'recovery_disconnected','recovery_linked','recovery_outcome')
def decode(data):
    if len(data)!=SIZE or struct.unpack_from('<II',data)!=(1,SIZE):raise ValueError('unsupported V2 RAM record')
    u=lambda offset:struct.unpack_from('<I',data,offset)[0]
    text=lambda start,end:data[start:end].split(b'\0',1)[0].decode('ascii')
    node,boot=text(16,29),text(29,46)
    if not re.fullmatch('[0-9a-f]{12}',node) or not re.fullmatch('[0-9a-f]{16}',boot):raise ValueError('invalid identity')
    def raw(offset):
        values=struct.unpack_from('<QIIHHHHII',data,offset)
        return dict(zip(('us','alloc_raw','recycled_raw','used','limit','malloc_failed_u16','cap_rejected_u16','flags','reserved'),values))
    events={}
    for index,name in enumerate(EVENTS):
        o=128+index*80;r=raw(o)
        r.update(call_id=u(o+32),epoch=u(o+36),scan_change=u(o+40),bracket_us=u(o+44),
                 result=struct.unpack_from('<i',data,o+48)[0],meta=u(o+52),parent=data[o+56:o+62].hex(),root=data[o+62:o+68].hex(),
                 largest_internal_8bit=u(o+68),free_internal_dma_8bit=u(o+72),largest_internal_dma_8bit=u(o+76))
        events[name]=r
    head,count=u(52),u(56)
    if head>=8 or count>8:raise ValueError('invalid ring')
    return dict(version=1,node_id=node,boot_id=boot,abi_flags=u(8),ring_overwrites=u(12),call_sequence=u(48),seen=u(60),
        recovery_attempt=struct.unpack_from('<Q',data,64)[0],phase_ms=struct.unpack_from('<Q',data,72)[0],
        quality=dict(zip(('attempts','ok','timeout','no_memory','requests','queue_drops','mature_statuses','validated_receipts','reasons','flags'),struct.unpack_from('<10I',data,80))),
        recovery_phase=u(120),subtree_routes=u(124),events=events,
        recent=[raw(1168+((head-count+i)%8)*32) for i in range(count)],
        limitations=['Private global counters, not per-packet or per-peer proof.',
                     'u16/u32 wrap and driver reset require timestamps/context; alloc_raw is NOT successful allocations.',
                     'Bracket duration includes bookkeeping; existing 256B histogram remains the SDK timing reference.',
                     'Snapshot flags: valid=1, changing=2, mixed=4. seen bit 3 denotes an in-flight SDK call.',
                     'Latest recovery only; first errors retained until boot. RAM is lost on reset.'])

def collect(session):
    """Use existing guarded Session.request, with bounded complete retries."""
    for _ in range(session.retries):
        header=session.request('diag_v2')
        token=header.get('token');crc=header.get('crc32')
        if header.get('length')!=SIZE or type(token)is not int or not 0<token<=0xffffffff or not isinstance(crc,str) or not re.fullmatch('[0-9a-fA-F]{8}',crc):
            raise ValueError('invalid V2 header')
        data=bytearray()
        try:
            for offset in range(0,SIZE,48):
                count=min(48,SIZE-offset)
                def valid(r):
                    try:
                        b=bytes.fromhex(r['data'])
                        return r['offset']==offset and r['token']==token and len(b)==count and zlib.crc32(b)==int(r['crc32'],16)
                    except (KeyError,ValueError,TypeError):return False
                r=session.request('diag_read',space='v2',offset=offset,count=count,token=token,validate=valid)
                data.extend(bytes.fromhex(r['data']))
        except Exception as error:
            if error.__class__.__name__=='StaleToken':continue
            raise
        if zlib.crc32(data)==int(crc,16):return bytes(data)
    raise ValueError('V2 snapshot repeatedly changed; preserve existing Flash dump')

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    d=sub.add_parser('decode');d.add_argument('file',type=Path)
    c=sub.add_parser('collect');c.add_argument('--port',required=True);c.add_argument('--node',required=True)
    c.add_argument('--expected-boot-id',required=True);c.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='decode':print(json.dumps(decode(args.file.read_bytes()),indent=2));return
    if not re.fullmatch('[0-9a-f]{12}',args.node) or not re.fullmatch('[0-9a-f]{16}',args.expected_boot_id):parser.error('lowercase hex node/boot required')
    legacy=Path(__file__).resolve().parents[3]/'tools/diag_collect_hostfix.py'
    spec=importlib.util.spec_from_file_location('legacy_diag',legacy);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    args.out.mkdir(parents=True,exist_ok=False)
    port=module.open_native_port(module.native_usb_port(args.port),1.5)
    try:
        session=module.Session(port,args.node)
        before=session.request('diag_state')
        if before.get('boot_id')!=args.expected_boot_id:raise ValueError('boot changed before RAM collection')
        data=collect(session);module.write_new(args.out/'probe.bin',data)
        decoded=decode(data);after=session.request('diag_state')
        valid=decoded['node_id']==args.node and decoded['boot_id']==args.expected_boot_id and after.get('boot_id')==args.expected_boot_id
        report=dict(before=before,after=after,decoded=decoded,sha256=hashlib.sha256(data).hexdigest(),accepted=valid,post_close_boot_verified=False)
        module.write_new(args.out/'manifest.json',(json.dumps(report,indent=2)+'\n').encode())
        if not valid:raise ValueError('boot changed; raw saved but acceptance failed')
    finally:port.close()

if __name__=='__main__':main()
