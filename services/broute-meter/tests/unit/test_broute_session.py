"""製品アダプターから独立したBルート接続シーケンスの単体テスト。"""

from __future__ import annotations

from ipaddress import IPv6Address

import pytest

from broute_meter.broute import (
    AmbiguousSmartMeterError,
    BRouteConnectionAdapter,
    BRouteSession,
    InvalidBRouteCredentialsError,
    NoSmartMeterFoundError,
)
from broute_meter.models import ActiveScanResult

VALID_B_ROUTE_ID = "A" * 32
VALID_B_ROUTE_PASSWORD = "P" * 12


class FakeConnectionAdapter:
    """呼出し順序と戻り値を観測するBルート接続アダプター。"""

    def __init__(self, candidates: tuple[ActiveScanResult, ...]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[str, object | None]] = []
        self.ipv6_address = IPv6Address("fe80::1234")

    def reset(self) -> None:
        self.calls.append(("reset", None))

    def set_b_route_id(self, b_route_id: str) -> None:
        self.calls.append(("set_b_route_id", b_route_id))

    def set_b_route_password(self, password: str) -> None:
        self.calls.append(("set_b_route_password", password))

    def active_scan(self) -> tuple[ActiveScanResult, ...]:
        self.calls.append(("active_scan", None))
        return self.candidates

    def set_channel(self, channel: str) -> None:
        self.calls.append(("set_channel", channel))

    def set_pan_id(self, pan_id: str) -> None:
        self.calls.append(("set_pan_id", pan_id))

    def resolve_ipv6_address(self, address: str) -> IPv6Address:
        self.calls.append(("resolve_ipv6_address", address))
        return self.ipv6_address

    def join(self, ipv6_address: IPv6Address) -> None:
        self.calls.append(("join", ipv6_address))


def _candidate(address: str = "0011223344556677") -> ActiveScanResult:
    return ActiveScanResult(
        channel="39",
        pan_id="ABCD",
        address=address,
        channel_page="09",
        lqi="A7",
        pair_id="12345678",
    )


def test_fake_adapter_satisfies_connection_protocol() -> None:
    assert isinstance(FakeConnectionAdapter((_candidate(),)), BRouteConnectionAdapter)


def test_connect_runs_confirmed_sequence_and_returns_connection() -> None:
    candidate = _candidate()
    adapter = FakeConnectionAdapter((candidate,))
    session = BRouteSession(adapter)

    connection = session.connect(VALID_B_ROUTE_ID, VALID_B_ROUTE_PASSWORD)

    assert connection.scan_result is candidate
    assert connection.smart_meter_ipv6 == IPv6Address("fe80::1234")
    assert session.connection is connection
    assert adapter.calls == [
        ("reset", None),
        ("set_b_route_id", VALID_B_ROUTE_ID),
        ("set_b_route_password", VALID_B_ROUTE_PASSWORD),
        ("active_scan", None),
        ("set_channel", "39"),
        ("set_pan_id", "ABCD"),
        ("resolve_ipv6_address", "0011223344556677"),
        ("join", IPv6Address("fe80::1234")),
    ]


def test_connect_reports_scan_and_pana_authentication_states() -> None:
    states: list[str] = []

    BRouteSession(
        FakeConnectionAdapter((_candidate(),)),
        on_state_change=states.append,
    ).connect(VALID_B_ROUTE_ID, VALID_B_ROUTE_PASSWORD)

    assert states == ["scanning", "authenticating"]


def test_no_scan_candidate_stops_before_radio_settings() -> None:
    adapter = FakeConnectionAdapter(())
    session = BRouteSession(adapter)

    with pytest.raises(NoSmartMeterFoundError):
        session.connect(VALID_B_ROUTE_ID, VALID_B_ROUTE_PASSWORD)

    assert session.connection is None
    assert adapter.calls[-1] == ("active_scan", None)


def test_empty_scan_is_retried_up_to_configured_attempts() -> None:
    adapter = FakeConnectionAdapter(())
    results = iter(((), (), (_candidate(),)))

    def active_scan() -> tuple[ActiveScanResult, ...]:
        adapter.calls.append(("active_scan", None))
        return next(results)

    adapter.active_scan = active_scan  # type: ignore[method-assign]
    session = BRouteSession(adapter, scan_max_attempts=3)

    connection = session.connect(VALID_B_ROUTE_ID, VALID_B_ROUTE_PASSWORD)

    assert connection.scan_result == _candidate()
    assert adapter.calls.count(("active_scan", None)) == 3


@pytest.mark.parametrize("attempts", [0, -1, True, 1.5])
def test_scan_attempts_must_be_positive_integer(attempts: object) -> None:
    with pytest.raises(ValueError, match="scan_max_attempts"):
        BRouteSession(
            FakeConnectionAdapter(()),
            scan_max_attempts=attempts,  # type: ignore[arg-type]
        )


def test_multiple_scan_candidates_are_never_selected_implicitly() -> None:
    adapter = FakeConnectionAdapter(
        (
            _candidate("0011223344556677"),
            _candidate("8899AABBCCDDEEFF"),
        )
    )
    session = BRouteSession(adapter)

    with pytest.raises(AmbiguousSmartMeterError) as exc_info:
        session.connect(VALID_B_ROUTE_ID, VALID_B_ROUTE_PASSWORD)

    assert exc_info.value.candidate_count == 2
    assert session.connection is None
    assert adapter.calls[-1] == ("active_scan", None)


@pytest.mark.parametrize(
    ("b_route_id", "password"),
    [
        ("short", VALID_B_ROUTE_PASSWORD),
        (VALID_B_ROUTE_ID, "short"),
        ("Ａ" * 32, VALID_B_ROUTE_PASSWORD),
        (VALID_B_ROUTE_ID, "P" * 11 + " "),
    ],
)
def test_invalid_credentials_are_rejected_before_adapter_is_touched(
    b_route_id: str,
    password: str,
) -> None:
    adapter = FakeConnectionAdapter((_candidate(),))
    session = BRouteSession(adapter)

    with pytest.raises(InvalidBRouteCredentialsError):
        session.connect(b_route_id, password)

    assert adapter.calls == []
