"""実アダプター処理を通るBルート接続・E7取得の模擬統合テスト。"""

from collections import deque
from datetime import UTC, datetime

from broute_meter.adapter import RsWsuhaPAdapter
from broute_meter.broute import BRouteSession
from broute_meter.config import DEFAULT_EXPECTED_SETTINGS
from broute_meter.echonet import TidGenerator
from broute_meter.meter import SmartMeterClient
from broute_meter.serial.transport import SerialTimeoutError


class ScriptedSerialTransport:
    """一連の公式公開形式応答を部分readで返す統合テスト用通信路。"""

    def __init__(self, response: bytes) -> None:
        self.is_open = True
        self._chunks = deque([response])
        self.writes: list[bytes] = []

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def read(
        self,
        size: int = 1,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        del timeout_seconds
        if not self._chunks:
            raise SerialTimeoutError("script exhausted")
        chunk = self._chunks.popleft()
        head, tail = chunk[:size], chunk[size:]
        if tail:
            self._chunks.appendleft(tail)
        return head

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        self._chunks.clear()


def test_connect_and_read_negative_instantaneous_power_without_hardware() -> None:
    identifier = "A" * 32
    password = "P" * 12
    ipv6 = "FE80:0000:0000:0000:021D:1291:0004:BE8A"
    echonet_response = "1081000102880105FF017201E704FFFFFCB8"
    response = (
        b"OK 80\r\n"
        b"OK 01\r\n"
        b"OK\r\n"
        + f"SKSETRBID {identifier}\r\nOK\r\n".encode()
        + f"SKSETPWD C {password}\r\nOK\r\n".encode()
        + b"OK\r\n"
        b"EPANDESC\r\n"
        b"  Channel:2F\r\n"
        b"  Channel Page:09\r\n"
        b"  Pan ID:88AA\r\n"
        b"  Addr:001D12910004BE8A\r\n"
        b"  LQI:BC\r\n"
        b"  PairID:01AD1234\r\n"
        b"EVENT 22\r\n"
        b"OK\r\n"
        b"OK\r\n"
        + f"{ipv6}\r\n".encode()
        + b"OK\r\n"
        + f"EVENT 25 {ipv6}\r\n".encode()
        + b"OK\r\n"
        + (
            f"ERXUDP {ipv6} FE80::2 0E1A 0E1A "
            f"001D12910004BE8A 1 0 0012 {echonet_response}\r\n"
        ).encode()
    )
    transport = ScriptedSerialTransport(response)
    adapter = RsWsuhaPAdapter(transport, sleeper=lambda _seconds: None)

    setup = adapter.configure(DEFAULT_EXPECTED_SETTINGS)
    connection = BRouteSession(adapter).connect(identifier, password)
    measured_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    reading = SmartMeterClient(
        adapter,
        connection.smart_meter_ipv6,
        tid_generator=TidGenerator(1),
        now=lambda: measured_at,
    ).get_instantaneous_power()

    assert setup.is_configured
    assert not setup.changed
    assert connection.smart_meter_ipv6.compressed == "fe80::21d:1291:4:be8a"
    assert reading.measured_at == measured_at
    assert reading.net_power_w == -840
    assert transport.writes[-1].startswith(
        b"SKSENDTO 1 FE80:0000:0000:0000:021D:1291:0004:BE8A "
        b"0E1A 1 0 000E "
    )
