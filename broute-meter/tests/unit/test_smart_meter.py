"""スマートメータークライアントの要求・応答検証テスト。"""

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv6Address

import pytest

from broute_meter.adapter import AdapterCommunicationError
from broute_meter.echonet import TidGenerator
from broute_meter.meter import SmartMeterClient, SmartMeterError


class FakeDatagramAdapter:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls: list[tuple[IPv6Address, bytes]] = []

    def exchange_udp(
        self,
        address: IPv6Address,
        payload: bytes,
        *,
        response_matcher: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        self.calls.append((address, payload))
        return self.response


class QueuedDatagramAdapter:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = responses
        self.calls: list[tuple[IPv6Address, bytes]] = []

    def exchange_udp(
        self,
        address: IPv6Address,
        payload: bytes,
        *,
        response_matcher: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        self.calls.append((address, payload))
        return self.responses.pop(0)


class FlakyDatagramAdapter:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.calls: list[bytes] = []

    def exchange_udp(
        self,
        _address: IPv6Address,
        payload: bytes,
        *,
        response_matcher: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        self.calls.append(payload)
        if len(self.calls) == 1:
            raise AdapterCommunicationError("temporary failure")
        return self.response


def _response(
    *,
    tid: int = 0x1234,
    seoj: str = "028801",
    deoj: str = "05FF01",
    esv: int = 0x72,
    properties: str = "01E704FFFFFCB8",
) -> bytes:
    return bytes.fromhex(
        f"1081{tid:04X}{seoj}{deoj}{esv:02X}{properties}"
    )


def _property_response(
    tid: int,
    epc: int,
    edt: str,
    *,
    esv: int = 0x72,
) -> bytes:
    return _response(
        tid=tid,
        esv=esv,
        properties=f"01{epc:02X}{len(bytes.fromhex(edt)):02X}{edt}",
    )


def test_get_instantaneous_power_builds_request_and_parses_negative_value() -> None:
    address = IPv6Address("fe80::1")
    measured_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    adapter = FakeDatagramAdapter(_response())
    client = SmartMeterClient(
        adapter,
        address,
        tid_generator=TidGenerator(0x1234),
        now=lambda: measured_at,
    )

    reading = client.get_instantaneous_power()

    assert reading.measured_at == measured_at
    assert reading.net_power_w == -840
    assert adapter.calls == [
        (
            address,
            bytes.fromhex("1081123405FF010288016201E700"),
        )
    ]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_response(tid=0x1235), "TID"),
        (_response(seoj="05FF01"), "SEOJ"),
        (_response(deoj="028801"), "DEOJ"),
        (_response(esv=0x52), "Get_SNA"),
        (_response(properties="01EA0400000001"), "E7"),
        (_response(properties="02E70400000001E70400000002"), "E7"),
        (_response(properties="01E703000000"), "EDT"),
    ],
)
def test_reject_unexpected_response(response: bytes, message: str) -> None:
    client = SmartMeterClient(
        FakeDatagramAdapter(response),
        IPv6Address("fe80::1"),
        tid_generator=TidGenerator(0x1234),
    )

    with pytest.raises(SmartMeterError, match=message):
        client.get_instantaneous_power()


def test_get_cumulative_energy_applies_coefficient_and_unit() -> None:
    received_at = datetime(2026, 7, 25, 18, 31, 2, tzinfo=UTC)
    adapter = QueuedDatagramAdapter(
        [
            _property_response(0x1234, 0xD3, "00000001"),
            _property_response(0x1235, 0xD7, "08"),
            _property_response(0x1236, 0xE1, "02"),
            _property_response(0x1237, 0xEA, "07EA0719121E000012D687"),
            _property_response(0x1238, 0xEB, "07EA0719121E0000005B9A"),
        ]
    )
    client = SmartMeterClient(
        adapter,
        IPv6Address("fe80::1"),
        tid_generator=TidGenerator(0x1234),
        now=lambda: received_at,
    )

    reading = client.get_cumulative_energy()

    assert reading.metered_at.isoformat() == "2026-07-25T18:30:00+09:00"
    assert reading.received_at == received_at
    assert reading.forward_raw == 1_234_567
    assert reading.reverse_raw == 23_450
    assert reading.forward_total_kwh == Decimal("12345.67")
    assert reading.reverse_total_kwh == Decimal("234.50")
    requested_epcs = [call[1][-2] for call in adapter.calls]
    assert requested_epcs == [0xD3, 0xD7, 0xE1, 0xEA, 0xEB]


def test_get_cumulative_energy_defaults_optional_coefficient_and_reverse() -> None:
    adapter = QueuedDatagramAdapter(
        [
            _property_response(1, 0xD3, "", esv=0x52),
            _property_response(2, 0xD7, "07"),
            _property_response(3, 0xE1, "03"),
            _property_response(4, 0xEA, "07EA07191200000000007B"),
            _property_response(5, 0xEB, "", esv=0x52),
        ]
    )
    client = SmartMeterClient(adapter, IPv6Address("fe80::1"))

    reading = client.get_cumulative_energy()

    assert reading.forward_raw == 123
    assert reading.forward_total_kwh == Decimal("0.123")
    assert reading.reverse_raw is None
    assert reading.reverse_total_kwh is None


def test_get_cumulative_energy_treats_reverse_no_measurement_as_none() -> None:
    adapter = QueuedDatagramAdapter(
        [
            _property_response(1, 0xD3, "00000001"),
            _property_response(2, 0xD7, "08"),
            _property_response(3, 0xE1, "00"),
            _property_response(4, 0xEA, "07EA07191200000000007B"),
            _property_response(5, 0xEB, "07EA0719120000FFFFFFFE"),
        ]
    )

    reading = SmartMeterClient(adapter, IPv6Address("fe80::1")).get_cumulative_energy()

    assert reading.reverse_raw is None
    assert reading.reverse_total_kwh is None


def test_reject_different_forward_and_reverse_metered_times() -> None:
    adapter = QueuedDatagramAdapter(
        [
            _property_response(1, 0xD3, "00000001"),
            _property_response(2, 0xD7, "08"),
            _property_response(3, 0xE1, "00"),
            _property_response(4, 0xEA, "07EA07191200000000007B"),
            _property_response(5, 0xEB, "07EA0719121E000000007B"),
        ]
    )

    with pytest.raises(SmartMeterError, match="計量時刻"):
        SmartMeterClient(adapter, IPv6Address("fe80::1")).get_cumulative_energy()


def test_retry_communication_failure_with_new_tid() -> None:
    adapter = FlakyDatagramAdapter(
        _property_response(0x1235, 0xE7, "00000064")
    )
    client = SmartMeterClient(
        adapter,
        IPv6Address("fe80::1"),
        tid_generator=TidGenerator(0x1234),
        request_max_attempts=2,
    )

    reading = client.get_instantaneous_power()

    assert reading.net_power_w == 100
    assert [payload[2:4] for payload in adapter.calls] == [
        bytes.fromhex("1234"),
        bytes.fromhex("1235"),
    ]
