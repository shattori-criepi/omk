# Original DIAG1 Python protocol regression cases, copied without C build fixtures.
from collections import deque
import json
import zlib
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import diag_collect_hostfix as collector
NODE = "020000000001"

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


@pytest.mark.parametrize("changed_field", ["attempt", "revision"])
def test_flash_change_during_download_restarts_complete_flash(changed_field):
    class ChangingFlash(MockPort):
        info_calls = 0
        def write(self, wire):
            count = super().write(wire)
            if json.loads(wire)["command"] == "diag_info":
                self.info_calls += 1
                result = json.loads(self.pending.pop())
                result[changed_field] = 0 if self.info_calls == 1 else 1
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
