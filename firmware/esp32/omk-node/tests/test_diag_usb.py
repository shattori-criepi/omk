"""Real diagnostic C handler plus mock-only USB collection tests."""
from collections import deque
import importlib.util
import json
from pathlib import Path
import subprocess
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "omk-node/src"
NODE = "020000000001"
spec = importlib.util.spec_from_file_location("omk_diag_collect", ROOT / "tools/diag_collect_hostfix.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


@pytest.fixture(scope="module")
def handler(tmp_path_factory):
    directory = tmp_path_factory.mktemp("diag-usb")
    (directory / "driver").mkdir()
    (directory / "freertos").mkdir()
    (directory / "esp_err.h").write_text("#pragma once\ntypedef int esp_err_t;\n#define ESP_OK 0\n")
    (directory / "freertos/FreeRTOS.h").write_text("#define pdMS_TO_TICKS(x) (x)\n")
    (directory / "driver/usb_serial_jtag.h").write_text("#include <stddef.h>\nint usb_serial_jtag_write_bytes(const void *,size_t,unsigned);\n")
    harness = directory / "handler.c"
    harness.write_text(r'''
#include <assert.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "diag_usb.h"
#include "mesh_diag.h"
static unsigned arms,reads,writes,drops;
int usb_serial_jtag_write_bytes(const void *data,size_t length,unsigned ticks) {
    (void)ticks; writes++; assert(length <= 256 && length > 0 && ((const char*)data)[length-1]=='\n');
    if(drops) return 0;
    return (int)fwrite(data,1,length,stdout);
}
uint32_t diag_store_revision(void) { return UINT32_MAX; }
diag_store_status_t diag_store_status(void) {
    return (diag_store_status_t){.ready=1,.attempt_mask=3,.valid_mask=3,.occupied_mask=3,
        .error=INT32_MIN,.last_io_us=UINT32_MAX,.max_io_us=UINT32_MAX};
}
uint32_t diag_store_crc32(const void *data,size_t size) {
    const unsigned char *b=data; uint32_t crc=UINT32_MAX;
    while(size--) { crc^=*b++; for(unsigned i=0;i<8;i++) crc=(crc>>1)^(0xedb88320U & (0U-(crc&1))); }
    return crc^UINT32_MAX;
}
uint32_t mesh_diag_live_begin(size_t *size) { *size=sizeof(diag_core_t); return UINT32_MAX; }
uint32_t mesh_diag_live_crc32(void) { return 0x12345678; }
uint32_t mesh_diag_v2_begin(size_t *size) { *size=1424; return UINT32_MAX; }
esp_err_t mesh_diag_v2_read(uint32_t token,uint32_t offset,void *out,size_t size) {
    if(token!=UINT32_MAX || offset+size>1424)return -1;
    reads++;memset(out,0,size);return 0;
}
esp_err_t mesh_diag_live_read(uint32_t token,uint32_t offset,void *out,size_t size) {
    if(token != UINT32_MAX) return -1;
    assert(offset+size <= sizeof(diag_core_t)); reads++;
    for(size_t i=0;i<size;i++) ((unsigned char*)out)[i]=(unsigned char)(offset+i);
    return 0;
}
esp_err_t diag_store_read(uint32_t offset,void *out,size_t size) {
    assert(offset+size<=8192); reads++;
    for(size_t i=0;i<size;i++) ((unsigned char*)out)[i]=(unsigned char)(offset+i);
    return 0;
}
uint32_t diag_store_phase(void) { return 9; }
uint32_t diag_store_block_reason(void) { return 2; }
esp_err_t diag_store_flash_crc32(uint32_t *out) { *out=0x12345678; return 0; }
void mesh_diag_state(char boot[17],uint64_t *start,uint32_t *gen,uint32_t *reason) {
    strcpy(boot,"0123456789abcdef");*start=UINT64_MAX;*gen=UINT32_MAX;*reason=7;
}
esp_err_t mesh_diag_rearm(void) { arms++; return 0; }
int main(int argc,char **argv) {
    assert(argc==3); drops=(unsigned)atoi(argv[2]);
    cJSON *root=cJSON_Parse(argv[1]); assert(root);
    const cJSON *command=cJSON_GetObjectItemCaseSensitive(root,"command"); assert(cJSON_IsString(command));
    bool handled=diag_usb_handle(root,command->valuestring,"020000000001");
    cJSON_Delete(root);
    printf("{\"handled\":%s,\"arms\":%u,\"reads\":%u,\"writes\":%u}\n",handled?"true":"false",arms,reads,writes);
    return 0;
}
''')
    # Raw harness keeps C escape sequences; correct its literal newline char.
    harness.write_text(harness.read_text().replace("=='\\\\n'", "=='\\n'"))
    cjson = ROOT / "offline-components/cjson/cJSON"
    binary = directory / "handler"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-DOMK_DIAGNOSTICS=1", "-DOMK_MESH_PROBE=1",
                    "-I", str(directory), "-I", str(SOURCE), "-I", str(cjson), str(harness),
                    str(SOURCE / "diag_usb.c"), str(cjson / "cJSON.c"), "-lm", "-o", str(binary)],
                   check=True, capture_output=True, text=True)

    def run(command="diag_info", drop=False, **fields):
        request = dict(protocol_version=2, command=command, expected_node_id=NODE)
        request.update(fields)
        result = subprocess.run([str(binary), json.dumps(request), str(int(drop))],
                                check=True, capture_output=True, text=True)
        return [json.loads(line) for line in result.stdout.splitlines()]
    return run


@pytest.mark.parametrize("command", ["diag_info", "diag_live", "diag_read", "diag_arm", "diag_v2"])
@pytest.mark.parametrize("node", [None, "", "020000000002", NODE + "x", 1])
def test_every_command_checks_target_before_work(handler, command, node):
    response, state = handler(command, expected_node_id=node, confirm=collector.CONFIRM)
    assert response["status"] == "node_identity_changed"
    assert state["arms"] == state["reads"] == 0 and state["writes"] == 1


def test_info_live_and_single_write_capacity(handler):
    info, state = handler()
    assert info["flash"] == 8192 and info["live"] <= 4056
    assert info["error"] == -2147483648 and info["max_io_us"] == 4294967295
    assert state["writes"] == 1
    live, _ = handler("diag_live")
    assert live["token"] == 4294967295 and live["crc32"] == "12345678"
    for space, offset, token in [("flash", 8144, 0), ("live", 0, 4294967295)]:
        read, state = handler("diag_read", space=space, offset=offset, count=48, token=token)
        assert read["status"] == "ok" and read["token"] == token
        assert len(read["data"]) == 96 and int(read["crc32"], 16) == zlib.crc32(bytes.fromhex(read["data"]))
        assert state["writes"] == state["reads"] == 1

def test_v2_usb(handler):
    info,state=handler('diag_v2')
    assert info['length']==1424 and state['reads']==state['arms']==0
    data,state=handler('diag_read',space='v2',offset=1400,count=24,token=info['token'])
    assert data['status']=='ok' and state['reads']==1 and not state['arms']
    data,state=handler('diag_read',space='v2',offset=1400,count=25,token=info['token'])
    assert data['status']=='invalid_request' and not state['reads']


@pytest.mark.parametrize("updates", [{"count": 0}, {"count": 49}, {"count": 1.5}, {"count": True},
                                     {"offset": -1}, {"offset": 8192}, {"offset": 4294967296},
                                     {"space": "unknown"}, {"space": "live", "token": 0},
                                     {"space": "live", "token": 4294967296}, {"space": "live", "offset": 8191}])
def test_read_bounds_types_and_token(handler, updates):
    args = dict(space="flash", offset=0, count=1, token=4294967295)
    args.update(updates)
    response, state = handler("diag_read", **args)
    assert response["status"] == "invalid_request" and state["reads"] == 0


def test_stale_token_and_confirmed_arm(handler):
    response, state = handler("diag_read", space="live", offset=0, count=1, token=1)
    assert response["status"] == "stale_token" and state["reads"] == 0
    for confirm in [None, "", "yes", True]:
        response, state = handler("diag_arm", confirm=confirm)
        assert response["status"] == "confirmation_required" and state["arms"] == 0
    response, state = handler("diag_arm", confirm=collector.CONFIRM, expected_boot_id="0123456789abcdef", backup_crc32=0x12345678)
    assert response["status"] == "accepted" and state["arms"] == 1
    response, state = handler("diag_arm", confirm=collector.CONFIRM, protocol_version=1)
    assert response["status"] == "invalid_request" and state["arms"] == 0


def test_transport_write_failure_does_not_log_retry_or_repeat_mutation(handler):
    state, = handler("diag_arm", confirm=collector.CONFIRM, drop=True, expected_boot_id="0123456789abcdef", backup_crc32=0x12345678)
    assert state == dict(handled=True, arms=1, reads=0, writes=1)


class MockPort:
    def __init__(self, *, node=NODE, corrupt=False, stale=False, mixed=False, live_fail=False):
        self.node, self.corrupt, self.stale = node, corrupt, stale
        self.mixed, self.live_fail = mixed, live_fail
        self.pending, self.requests = deque(), []
        self.flash = bytes(range(256)) * 32
        self.live = b"live evidence" * 12
        self.token = 0

    def write(self, wire):
        request = json.loads(wire)
        self.requests.append(request)
        command = request["command"]
        assert len(wire) <= 256
        result = dict(status="ok", protocol_version=2, node_id=self.node, command=command)
        if command == "identify":
            result["wifi_configured"] = True
        else:
            assert request["expected_node_id"] == NODE
        if command == "diag_info":
            result.update(flash=8192, live=len(self.live), ready=0, valid=3, occupied=3, attempt=3)
        elif command == "diag_live":
            self.token += 1
            result.update(token=self.token, length=len(self.live), crc32=f"{zlib.crc32(self.live):08x}")
        elif command == "diag_read":
            token = request.get("token", 0)
            if request["space"] == "live" and (self.stale or self.live_fail):
                self.stale = False
                result["status"] = "stale_token"
            else:
                offset, count = request["offset"], request["count"]
                data = (self.live if token else self.flash)[offset:offset + count]
                if token and self.mixed:
                    self.mixed = False
                    data = bytes([data[0] ^ 1]) + data[1:]
                result.update(offset=offset, token=token, data=data.hex(), crc32=f"{zlib.crc32(data):08x}")
                if self.corrupt:
                    self.corrupt = False
                    bad = dict(result, crc32="ffffffff")
                    self.pending.append((json.dumps(bad) + "\n").encode())
        elif command == "diag_arm":
            assert request["confirm"] == collector.CONFIRM
            result["status"] = "accepted"
        self.pending.extend([b"I (123) wifi: SDK log\n", b'{"truncated":', (json.dumps(result) + "\n").encode()])
        return len(wire)

    def readline(self, limit):
        assert limit <= 512
        return self.pending.popleft() if self.pending else b""


def test_collector_mixed_lines_crc_stale_and_whole_snapshot_recovery():
    port = MockPort(corrupt=True, stale=True, mixed=True)
    flash, live, manifest = collector.collect(collector.Session(port, NODE))
    assert flash == port.flash and live == port.live
    assert manifest["live"]["token"] == 3
    assert all(r["command"] != "diag_arm" for r in port.requests)


def test_collector_identity_failure_stops_before_any_diagnostic_request():
    port = MockPort(node="020000000002")
    with pytest.raises(collector.CollectionError, match="identity"):
        collector.collect(collector.Session(port, NODE))
    assert len(port.requests) == 1 and port.requests[0]["command"] == "identify"


def test_corrupt_only_reply_causes_bounded_read_retry(monkeypatch):
    class CorruptFirst(MockPort):
        first = True
        def write(self, wire):
            count = super().write(wire)
            if self.first and json.loads(wire)["command"] == "diag_read":
                self.first = False
                bad = json.loads(self.pending.pop())
                bad["crc32"] = "ffffffff"
                self.pending.append((json.dumps(bad) + "\n").encode())
            return count
    now = iter(i * 0.01 for i in range(100))
    monkeypatch.setattr(collector.time, "monotonic", lambda: next(now))
    port = CorruptFirst()
    assert collector.Session(port, NODE, timeout=0.1).read("flash", 1) == b"\x00"
    assert len(port.requests) == 2


def test_flash_change_during_download_restarts_complete_flash():
    class ChangingFlash(MockPort):
        info_calls = 0
        def write(self, wire):
            count = super().write(wire)
            if json.loads(wire)["command"] == "diag_info":
                self.info_calls += 1
                result = json.loads(self.pending.pop())
                result["attempt"] = 0 if self.info_calls == 1 else 1
                self.pending.append((json.dumps(result) + "\n").encode())
                if self.info_calls == 2:
                    self.flash = b"\xaa" * 8192
            return count
    port = ChangingFlash()
    flash, _, _ = collector.collect(collector.Session(port, NODE))
    assert flash == b"\xaa" * 8192
    assert sum(r["command"] == "diag_read" and r["space"] == "flash" and r["offset"] == 0 for r in port.requests) == 2


def test_flash_is_backed_up_even_when_live_never_completes():
    port = MockPort(live_fail=True)
    saved = []
    with pytest.raises(collector.CollectionError, match="repeatedly"):
        collector.collect(collector.Session(port, NODE), saved.append)
    assert saved == [port.flash]


def test_bounded_retry_and_arm_never_retried(monkeypatch):
    class SilentPort:
        writes = 0
        def write(self, value):
            self.writes += 1
            return len(value)
        def readline(self, limit):
            return b""
    now = iter(i * 0.1 for i in range(100))
    monkeypatch.setattr(collector.time, "monotonic", lambda: next(now))
    port = SilentPort()
    session = collector.Session(port, NODE, timeout=0.15)
    with pytest.raises(collector.CollectionError):
        session.request("diag_info")
    assert port.writes == 3
    with pytest.raises(collector.CollectionError, match="outcome unknown"):
        session.request("diag_arm", attempts=1, confirm=collector.CONFIRM)
    assert port.writes == 4


def test_collector_refuses_overwriting_and_arm_without_confirmation(tmp_path):
    saved = tmp_path / "evidence"
    collector.write_new(saved, b"old")
    with pytest.raises(FileExistsError):
        collector.write_new(saved, b"new")
    assert saved.read_bytes() == b"old"
    with pytest.raises(SystemExit) as error:
        collector.main(["--port", "unused", "--node", NODE, "--arm"])
    assert error.value.code == 2


def test_usb_hook_keeps_diagnostics_optional_and_polling_without_input():
    source = (SOURCE / "usb_provisioning.c").read_text()
    start = source.index("if (diag_usb_handle")
    assert source.rfind("#if OMK_DIAGNOSTICS", 0, start) > source.index("if (reboot_scheduled)")
    loop = source[source.index("static void usb_provisioning_task"):]
    assert loop.index("mesh_diag_poll();") < loop.index("usb_serial_jtag_read_bytes")
