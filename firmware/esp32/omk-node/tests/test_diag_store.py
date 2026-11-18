"""Exercise real diag_store.c against NOR-like flash, including torn I/O.

No ESP target is contacted. These tests establish software ordering, bounds and
reboot/readout behavior; they do not establish hardware brownout reliability.
"""
import base64
import ctypes
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import zlib

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "omk-node/src"
DECODER = ROOT / "tools/diag_flash.py"
spec = importlib.util.spec_from_file_location("omk_diag_flash", DECODER)
decoder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decoder)


class Status(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in ("ready", "attempt_mask", "valid_mask", "occupied_mask")]
    _fields_ += [("error", ctypes.c_int32), ("last_io_us", ctypes.c_uint32), ("max_io_us", ctypes.c_uint32)]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    build = tmp_path_factory.mktemp("diag-store")
    (build / "esp_err.h").write_text("""#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_INVALID_ARG 0x102
#define ESP_ERR_INVALID_STATE 0x103
#define ESP_ERR_NOT_FOUND 0x105
#define ESP_ERR_INVALID_CRC 0x109
""")
    (build / "esp_partition.h").write_text("""#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#define ESP_PARTITION_TYPE_DATA 1
typedef struct { uint32_t address, size; bool encrypted, readonly; } esp_partition_t;
const esp_partition_t *esp_partition_find_first(int, int, const char *);
esp_err_t esp_partition_read(const esp_partition_t *, size_t, void *, size_t);
esp_err_t esp_partition_write(const esp_partition_t *, size_t, const void *, size_t);
esp_err_t esp_partition_erase_range(const esp_partition_t *, size_t, size_t);
""")
    (build / "esp_timer.h").write_text("#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n")
    harness = build / "mock.c"
    harness.write_text(r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#include "esp_partition.h"
#include "diag_store.h"
static uint8_t flash[8192];
static esp_partition_t part = {0x210000,8192,false,false};
static int missing, reads, writes, erases, fail_read, fail_write, fail_erase, cut, erase_cut;
static int silent_write, silent_erase, oversized;
static int64_t clock_us;
const esp_partition_t *esp_partition_find_first(int type,int subtype,const char *label) {
    assert(type == 1 && subtype == 0x41 && strcmp(label,"omk_diag") == 0);
    return missing ? 0 : &part;
}
int64_t esp_timer_get_time(void) { return clock_us += 7; }
esp_err_t esp_partition_read(const esp_partition_t *p,size_t off,void *out,size_t n) {
    assert(p == &part && off <= 8192 && n <= 8192-off); reads++; clock_us += 10;
    if(reads == fail_read) return ESP_FAIL;
    memcpy(out,flash+off,n); return ESP_OK;
}
esp_err_t esp_partition_write(const esp_partition_t *p,size_t off,const void *in,size_t n) {
    assert(p == &part && off <= 8192 && n <= 8192-off); writes++; clock_us += 100;
    if(n > 256) oversized++;
    size_t count=n;
    if(writes == fail_write && cut >= 0 && (size_t)cut < count) count=(size_t)cut;
    const uint8_t *bytes=in;
    for(size_t i=0;i<count;i++) { assert((flash[off+i] & bytes[i]) == bytes[i]); flash[off+i] &= bytes[i]; }
    if(writes == fail_write) return silent_write ? ESP_OK : ESP_FAIL;
    return ESP_OK;
}
esp_err_t esp_partition_erase_range(const esp_partition_t *p,size_t off,size_t n) {
    assert(p == &part && off%4096 == 0 && n == 4096 && off+n <= 8192); erases++; clock_us += 200;
    size_t count=n;
    if(erases == fail_erase && erase_cut >= 0 && (size_t)erase_cut < count) count=(size_t)erase_cut;
    memset(flash+off,0xff,count);
    if(erases == fail_erase) return silent_erase ? ESP_OK : ESP_FAIL;
    return ESP_OK;
}
void mock_clear_faults(void) { fail_read=fail_write=fail_erase=0; cut=erase_cut=-1; silent_write=silent_erase=0; }
void mock_reset(void) {
    memset(flash,0xff,sizeof(flash)); part=(esp_partition_t){0x210000,8192,false,false};
    missing=reads=writes=erases=oversized=0; clock_us=0; mock_clear_faults();
}
void mock_boot(void) { mock_clear_faults(); reads=writes=erases=oversized=0; diag_store_init(); }
void mock_fault_write(int nth,int bytes,int silent) { fail_write=writes+nth; cut=bytes; silent_write=silent; }
void mock_fault_read(int nth) { fail_read=reads+nth; }
void mock_fault_erase(int nth,int bytes,int silent) { fail_erase=erases+nth; erase_cut=bytes; silent_erase=silent; }
void mock_corrupt(int offset,int value) { assert(offset>=0 && offset<8192); flash[offset]=(uint8_t)value; }
void mock_missing(int value) { missing=value; }
void mock_layout(int address,int size,int encrypted,int readonly) { part=(esp_partition_t){address,size,encrypted,readonly}; }
int mock_reads(void) { return reads; }
int mock_writes(void) { return writes; }
int mock_erases(void) { return erases; }
int mock_oversized(void) { return oversized; }
void mock_dump(void *out) { memcpy(out,flash,sizeof(flash)); }
''')
    output = build / "diag-store.so"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fPIC", "-shared",
                    "-I", str(build), "-I", str(SOURCE), str(harness), str(SOURCE / "diag_store.c"),
                    "-o", str(output)], check=True)
    lib = ctypes.CDLL(str(output))
    lib.diag_store_status.restype = Status
    lib.diag_store_crc32.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.diag_store_crc32.restype = ctypes.c_uint32
    lib.diag_store_save.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
    lib.diag_store_read.argtypes = [ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t]
    return lib


@pytest.fixture
def flash(library):
    library.mock_reset()
    library.diag_store_init()
    return library


def dump(lib):
    result = ctypes.create_string_buffer(8192)
    lib.mock_dump(result)
    return result.raw


def save(lib, slot, payload):
    return lib.diag_store_save(slot, ctypes.create_string_buffer(payload), len(payload))


def test_crc_golden_vectors_and_slot_boundary(flash):
    for value in (b"", b"123456789", bytes(range(256)), b"x" * 4056):
        assert flash.diag_store_crc32(ctypes.create_string_buffer(value), len(value)) == zlib.crc32(value)
    payload = bytes(range(256)) * 15 + bytes(range(216))
    assert len(payload) == 4056
    assert save(flash, 0, payload) == 0
    assert flash.mock_writes() == 18  # header, 16 payload chunks, independent footer
    assert flash.mock_oversized() == flash.mock_erases() == 0
    first = dump(flash)[:4096]
    assert save(flash, 0, b"replace") != 0
    assert save(flash, 1, b"second") == 0
    state = flash.diag_store_status()
    assert state.ready == 0 and state.attempt_mask == state.valid_mask == state.occupied_mask == 3
    assert state.last_io_us > 0 and state.max_io_us >= state.last_io_us
    assert dump(flash)[:4096] == first
    decoded = decoder.decode_flash(dump(flash))
    assert [s["state"] for s in decoded["slots"]] == ["valid", "valid"]
    assert base64.b64decode(decoded["slots"][0]["payload_base64"]) == payload
    flash.mock_boot()
    assert flash.diag_store_status().ready == 0
    assert flash.diag_store_status().valid_mask == 3
    assert save(flash, 1, b"after reboot") != 0
    assert flash.mock_writes() == flash.mock_erases() == 0
    readout = ctypes.create_string_buffer(8192)
    assert flash.diag_store_read(0, readout, len(readout)) == 0
    assert readout.raw == dump(flash)


@pytest.mark.parametrize("nth,length", [(1, 32)] + [(i, 256) for i in range(2, 17)] + [(17, 216), (18, 8)])
def test_every_write_boundary_torn_power_loss(flash, nth, length):
    payload = bytes((i * 17 + 3) % 256 for i in range(4056))
    # Interrupt at start, interior, and end of each header/data/footer write.
    for cut in sorted({0, 1, length // 2, length - 1, length}):
        flash.mock_reset()
        flash.diag_store_init()
        flash.mock_fault_write(nth, cut, 0)
        assert save(flash, 0, payload) != 0
        count = flash.mock_writes()
        assert count == nth
        assert save(flash, 0, payload) != 0
        assert flash.mock_writes() == count
        state = flash.diag_store_status()
        assert state.attempt_mask == state.occupied_mask == 1
        assert state.valid_mask == 0
        raw = dump(flash)
        manifest = decoder.decode_flash(raw)
        expected = "blank" if nth == 1 and cut == 0 else ("valid" if nth == 18 and cut == 8 else "invalid")
        assert manifest["slots"][0]["state"] == expected
        flash.mock_boot()  # MCU RAM reset; flash remains exactly as cut.
        assert dump(flash) == raw
        assert flash.diag_store_status().ready == (expected == "blank")
        if expected != "blank":
            assert save(flash, 1, b"other boot") != 0
        assert flash.mock_writes() == flash.mock_erases() == 0


def test_slot1_torn_write_never_changes_slot0(flash):
    assert save(flash, 0, b"first evidence") == 0
    first = dump(flash)[:4096]
    flash.mock_fault_write(2, 3, 0)
    assert save(flash, 1, b"follow-up after recovery") != 0
    flash.mock_boot()
    assert dump(flash)[:4096] == first
    state = flash.diag_store_status()
    assert state.ready == 0 and state.valid_mask == 1 and state.occupied_mask == 3


@pytest.mark.parametrize("offset", [0, 32, 2048, 4087, 4088, 4095, 4096, 8191])
def test_nonblank_anywhere_disarms_both_slots(flash, offset):
    flash.mock_corrupt(offset, 0xFE)
    flash.mock_boot()
    before = dump(flash)
    state = flash.diag_store_status()
    assert state.ready == 0 and state.occupied_mask == 1 << (offset // 4096)
    assert save(flash, 0, b"new") != 0 and save(flash, 1, b"new") != 0
    assert dump(flash) == before and flash.mock_erases() == flash.mock_writes() == 0


def test_readback_catches_silent_partial_writes(flash):
    flash.mock_fault_write(2, 3, 1)
    assert save(flash, 0, b"a long payload requiring verification") != 0
    assert flash.mock_writes() == 2  # No footer after bad payload readback.
    assert flash.diag_store_status().valid_mask == 0
    assert decoder.decode_flash(dump(flash))["slots"][0]["state"] == "invalid"


@pytest.mark.parametrize("nth,cut", [(1, 15), (3, 4)])
def test_header_and_commit_silent_torn_writes_are_invalid(flash, nth, cut):
    flash.mock_fault_write(nth, cut, 1)
    assert save(flash, 0, b"payload") != 0
    assert flash.diag_store_status().valid_mask == 0
    flash.mock_boot()
    assert flash.diag_store_status().ready == 0
    assert flash.diag_store_status().valid_mask == 0
    assert decoder.decode_flash(dump(flash))["slots"][0]["state"] == "invalid"


@pytest.mark.parametrize("nth", range(1, 37))
def test_read_failures_during_save_fail_without_retries(flash, nth):
    # 16 blank reads + header/payload reads before and after footer.
    flash.mock_fault_read(nth)
    error = save(flash, 0, b"q" * 2000)
    state = flash.diag_store_status()
    assert state.attempt_mask == 1
    if nth <= 35:
        assert error != 0 and state.valid_mask == 0
    count = flash.mock_writes()
    assert save(flash, 0, b"retry") != 0 and flash.mock_writes() == count
    assert flash.mock_erases() == 0


def test_init_io_failure_and_wrong_partition_fail_closed(flash):
    flash.mock_fault_read(1)
    flash.diag_store_init()
    assert flash.diag_store_status().ready == 0
    assert save(flash, 0, b"not safe") != 0
    for address, size, encrypted, readonly in [(0x220000, 8192, 0, 0), (0x210000, 4096, 0, 0),
                                               (0x210000, 8192, 1, 0), (0x210000, 8192, 0, 1)]:
        flash.mock_reset()
        flash.mock_layout(address, size, encrypted, readonly)
        flash.diag_store_init()
        assert flash.diag_store_status().ready == 0
        assert flash.diag_store_arm() != 0
    flash.mock_reset()
    flash.mock_missing(1)
    flash.diag_store_init()
    assert flash.diag_store_status().ready == 0
    assert save(flash, 0, b"missing") != 0
    assert flash.diag_store_arm() != 0


@pytest.mark.parametrize("sector", [1, 2])
@pytest.mark.parametrize("cut", [0, 1, 32, 2048, 4095, 4096])
@pytest.mark.parametrize("silent", [0, 1])
def test_erase_failures_do_not_arm_or_auto_retry(flash, sector, cut, silent):
    assert save(flash, 0, b"first") == save(flash, 1, b"second") == 0
    flash.mock_fault_erase(sector, cut, silent)
    err = flash.diag_store_arm()
    raw = dump(flash)
    fully_blank = raw == b"\xff" * 8192
    if not silent or not fully_blank:
        assert err != 0 and flash.diag_store_status().ready == 0
        assert save(flash, 0, b"must not write") != 0
    else:
        assert err == 0 and flash.diag_store_status().ready == 1
    erases = flash.mock_erases()
    assert erases <= 2
    flash.mock_boot()
    assert dump(flash) == raw
    assert flash.mock_erases() == flash.mock_writes() == 0
    assert flash.diag_store_status().ready == fully_blank


def test_explicit_arm_after_reboot_and_capacity_limits(flash):
    assert save(flash, 0, b"old") == 0
    flash.mock_boot()
    assert flash.diag_store_status().ready == 0
    assert flash.diag_store_arm() == 0
    assert flash.mock_erases() == 2 and dump(flash) == b"\xff" * 8192
    assert flash.diag_store_status().attempt_mask == 0
    assert save(flash, 2, b"invalid") != 0
    assert save(flash, 0, b"x" * 4057) != 0
    assert flash.diag_store_save(0, None, 1) != 0
    assert flash.diag_store_status().attempt_mask == 0
    assert flash.diag_store_read(8191, ctypes.create_string_buffer(2), 2) != 0
    assert flash.diag_store_read(0xFFFFFFFF, ctypes.create_string_buffer(1), 1) != 0
    assert flash.diag_store_read(0, None, 1) != 0
    assert save(flash, 0, b"new") == 0
    assert save(flash, 1, b"") == 0
    assert flash.diag_store_status().ready == 0


@pytest.mark.parametrize("nth", [1, 16, 17, 32])
def test_arm_requires_successful_full_blank_readback(flash, nth):
    assert save(flash, 0, b"saved evidence") == 0
    flash.mock_fault_read(nth)
    assert flash.diag_store_arm() != 0
    state = flash.diag_store_status()
    assert state.ready == 0 and state.attempt_mask == 3
    writes = flash.mock_writes()
    assert save(flash, 0, b"new") != 0 and flash.mock_writes() == writes
    assert flash.mock_erases() == 2


def test_save_rechecks_blank_sector_after_boot(flash):
    flash.mock_corrupt(4095, 0)
    original = dump(flash)
    assert save(flash, 0, b"must not overwrite") != 0
    assert dump(flash) == original and flash.mock_writes() == flash.mock_erases() == 0
    assert flash.diag_store_status().attempt_mask == 1


def test_decoder_rejects_corruption_and_truncated_read(flash, tmp_path):
    assert save(flash, 0, b"evidence") == 0
    raw = dump(flash)
    for offset in [0, 8, 12, 16, 20, 24, 28, 32, 4088, 4092]:
        damaged = bytearray(raw)
        damaged[offset] ^= 1
        assert decoder.decode_flash(damaged)["slots"][0]["state"] == "invalid"
    truncated = decoder.decode_flash(raw[:-1])
    assert not truncated["size_valid"] and truncated["slots"][1]["state"] == "invalid"
    # A correctly checksummed but out-of-bounds header is still rejected.
    damaged = bytearray(raw)
    struct.pack_into("<I", damaged, 16, 4057)
    struct.pack_into("<I", damaged, 28, zlib.crc32(damaged[:28]))
    assert "payload_length_out_of_bounds" in decoder.decode_flash(damaged)["slots"][0]["errors"]
    path = tmp_path / "diag.bin"
    path.write_bytes(raw)
    prefix = tmp_path / "recovered"
    output = subprocess.run([sys.executable, str(DECODER), str(path), "--extract-prefix", str(prefix)], capture_output=True)
    assert output.returncode == 0
    assert (tmp_path / "recovered-slot0.bin").read_bytes() == b"evidence"
    # Never silently overwrite a recovered evidence file.
    assert subprocess.run([sys.executable, str(DECODER), str(path), "--extract-prefix", str(prefix)], capture_output=True).returncode == 2

@pytest.mark.parametrize('victim', [0, 1])
@pytest.mark.parametrize('failure', ['none', 'erase', 'silent_erase', 'header', 'payload', 'commit', 'survivor_read'])
def test_rotation_keeps_committed_survivor_across_power_loss(flash, victim, failure):
    assert save(flash, 0, b'old onset') == save(flash, 1, b'measured fault') == 0
    survivor = victim ^ 1
    before_revision = flash.diag_store_revision()
    original = dump(flash)[survivor*4096:(survivor+1)*4096]
    if failure == 'erase': flash.mock_fault_erase(1, 2048, 0)
    if failure == 'silent_erase': flash.mock_fault_erase(1, 32, 1)
    if failure in ('header', 'payload', 'commit'):
        flash.mock_fault_write({'header': 1, 'payload': 2, 'commit': 3}[failure], 1, 0)
    if failure == 'survivor_read': flash.mock_fault_read(1)
    new = b'new severe fault'
    flash.diag_store_replace.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
    result = flash.diag_store_replace(victim, ctypes.create_string_buffer(new), len(new))
    assert (result == 0) == (failure == 'none')
    assert dump(flash)[survivor*4096:(survivor+1)*4096] == original
    if failure == 'none':
        assert flash.diag_store_revision() > before_revision
        assert flash.diag_store_block_reason() == 4
        assert base64.b64decode(decoder.decode_flash(dump(flash))['slots'][victim]['payload_base64']) == new
    flash.mock_boot()
    assert decoder.decode_flash(dump(flash))['slots'][survivor]['state'] == 'valid'
    assert flash.diag_store_replace(victim, ctypes.create_string_buffer(new), len(new)) != 0
    assert flash.mock_erases() == flash.mock_writes() == 0


def test_rotation_refuses_missing_or_corrupt_survivor(flash):
    assert save(flash, 0, b'onset') == 0
    assert flash.diag_store_replace(0, ctypes.create_string_buffer(b'x'), 1) != 0
    assert save(flash, 1, b'fault') == 0
    flash.mock_corrupt(4096+32, 0)
    original = dump(flash)
    assert flash.diag_store_replace(0, ctypes.create_string_buffer(b'x'), 1) != 0
    assert dump(flash) == original and flash.mock_erases() == 0
