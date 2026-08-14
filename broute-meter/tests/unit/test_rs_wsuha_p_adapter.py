"""RS-WSUHA-P設定アダプターの単体テスト。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Iterable
from ipaddress import IPv6Address

import pytest

from broute_meter.adapter.base import (
    AdapterCommunicationError,
    AdapterCredentialError,
    AdapterOperationCancelled,
    AdapterPanaJoinError,
    AdapterResponseTimeoutError,
    AdapterScanError,
    AdapterVerificationError,
    InvalidAdapterSettingValueError,
    UnsupportedAdapterSettingError,
)
from broute_meter.adapter.rs_wsuha_p import (
    RS_WSUHA_P_B_ROUTE_COMMANDS,
    RS_WSUHA_P_SETTING_COMMANDS,
    RsWsuhaPAdapter,
)
from broute_meter.serial.transport import (
    SerialDisconnectedError,
    SerialTimeoutError,
)


class ScriptedTransport:
    """バイト列と例外を順番に返すSerialTransport互換モック。"""

    def __init__(
        self,
        responses: Iterable[bytes | Exception] = (),
        *,
        is_open: bool = True,
        on_read_timeout: Callable[[], None] | None = None,
    ) -> None:
        self.is_open = is_open
        self._responses = deque(responses)
        self.writes: list[bytes] = []
        self.read_sizes: list[int] = []
        self.read_timeouts: list[float | None] = []
        self.open_count = 0
        self.close_count = 0
        self.reset_count = 0
        self._on_read_timeout = on_read_timeout

    def open(self) -> None:
        self.open_count += 1
        self.is_open = True

    def close(self) -> None:
        self.close_count += 1
        self.is_open = False

    def read(
        self,
        size: int = 1,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        self.read_sizes.append(size)
        self.read_timeouts.append(timeout_seconds)
        while self._responses:
            item = self._responses.popleft()
            if isinstance(item, Exception):
                if isinstance(item, SerialTimeoutError) and self._on_read_timeout:
                    self._on_read_timeout()
                raise item
            if not item:
                continue
            head = item[:size]
            tail = item[size:]
            if tail:
                self._responses.appendleft(tail)
            return head
        if self._on_read_timeout:
            self._on_read_timeout()
        raise SerialTimeoutError("script exhausted")

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def reset_input_buffer(self) -> None:
        self.reset_count += 1


def _adapter(
    responses: Iterable[bytes | Exception],
) -> tuple[RsWsuhaPAdapter, ScriptedTransport, list[float]]:
    current_time = 0.0

    def advance_past_deadline() -> None:
        nonlocal current_time
        current_time += 10.0

    transport = ScriptedTransport(
        responses,
        on_read_timeout=advance_past_deadline,
    )
    sleeps: list[float] = []
    adapter = RsWsuhaPAdapter(
        transport,
        sleeper=sleeps.append,
        monotonic=lambda: current_time,
    )
    return adapter, transport, sleeps


def test_command_table_contains_only_publicly_confirmed_setting_commands() -> None:
    assert list(RS_WSUHA_P_SETTING_COMMANDS) == ["uart_mode", "output_mode"]
    uart_command = RS_WSUHA_P_SETTING_COMMANDS["uart_mode"]
    assert uart_command.read == "RUART"
    assert uart_command.write == "WUART {value}"
    output_command = RS_WSUHA_P_SETTING_COMMANDS["output_mode"]
    assert output_command.read == "ROPT"
    assert output_command.write == "WOPT {value}"


def test_b_route_command_table_contains_only_publicly_confirmed_commands() -> None:
    assert RS_WSUHA_P_B_ROUTE_COMMANDS == {
        "reset": "SKRESET",
        "set_password": "SKSETPWD {length} {value}",
        "set_id": "SKSETRBID {value}",
        "active_scan": "SKSCAN 2 FFFFFFFF 6 0",
        "set_channel": "SKSREG S2 {value}",
        "set_pan_id": "SKSREG S3 {value}",
        "resolve_ipv6": "SKLL64 {value}",
        "join": "SKJOIN {value}",
        "send_udp": "SKSENDTO 1 {address} 0E1A 1 0 {length} ",
    }


@pytest.mark.parametrize(
    "response",
    [
        b"OK 80\r",
        b"OK 80\r\n",
        b"OK 80\n",
    ],
)
def test_read_setting_accepts_cr_crlf_and_lf(response: bytes) -> None:
    adapter, transport, sleeps = _adapter([response])

    assert adapter.read_setting("uart_mode") == "80"
    assert transport.writes == [b"RUART\r\n"]
    assert set(transport.read_sizes) == {1}
    assert sleeps == [3.0]


def test_output_mode_uses_ropt_and_wopt_commands() -> None:
    adapter, transport, _ = _adapter(
        [b"OK 00\r\nOK\r\nOK 01\r\n"]
    )

    result = adapter.configure({"output_mode": "01"})

    assert result.is_configured
    assert result.changed_settings == ("output_mode",)
    assert transport.writes == [
        b"ROPT\r\n",
        b"WOPT 01\r\n",
        b"ROPT\r\n",
    ]


def test_read_setting_handles_echo_split_input_multiple_lines_and_notifications() -> None:
    adapter, transport, _ = _adapter(
        [
            b"RUA",
            b"RT\r",
            b"\nEVENT 20\r\n",
            b"ANOTHER NOTICE\nOK ",
            b"80\r\n",
        ]
    )

    assert adapter.read_setting("uart_mode") == "80"
    assert transport.writes == [b"RUART\r\n"]


def test_matching_setting_is_read_every_time_and_never_written() -> None:
    adapter, transport, sleeps = _adapter([b"OK 80\r\nOK 80\r\n"])

    first = adapter.configure({"uart_mode": "80"})
    second = adapter.configure({"uart_mode": "80"})

    assert first.is_configured
    assert not first.changed
    assert second.is_configured
    assert transport.writes == [b"RUART\r\n", b"RUART\r\n"]
    assert sleeps == [3.0]


def test_mismatch_is_written_once_then_read_back() -> None:
    adapter, transport, _ = _adapter(
        [
            (
                b"RUART\r\nNOTICE\r\nOK 00\r\n"
                b"WUART 80\r\nOK\r\n"
                b"RUART\rOK 80\r"
            )
        ]
    )

    result = adapter.configure({"uart_mode": "80"})

    assert result.is_configured
    assert result.changed
    assert result.changed_settings == ("uart_mode",)
    assert result.initial_settings == {"uart_mode": "00"}
    assert result.final_settings == {"uart_mode": "80"}
    assert transport.writes == [
        b"RUART\r\n",
        b"WUART 80\r\n",
        b"RUART\r\n",
    ]


def test_write_changes_false_reports_mismatch_without_writing_setting() -> None:
    adapter, transport, _ = _adapter([b"OK 00\r\n"])

    result = adapter.configure({"uart_mode": "80"}, write_changes=False)

    assert not result.is_configured
    assert not result.changed
    assert result.mismatched_settings == ("uart_mode",)
    assert transport.writes == [b"RUART\r\n"]


def test_read_failure_does_not_fall_back_to_unconditional_write() -> None:
    adapter, transport, _ = _adapter(
        [b"RUART\r\nUNCONFIRMED ERROR\r\n", SerialTimeoutError("timed out")]
    )

    with pytest.raises(AdapterResponseTimeoutError):
        adapter.configure({"uart_mode": "80"})

    assert transport.writes == [b"RUART\r\n"]
    assert all(not request.startswith(b"WUART") for request in transport.writes)


def test_unconfirmed_error_line_is_ignored_until_timeout() -> None:
    timeout = SerialTimeoutError("timed out")
    adapter, _, _ = _adapter([b"ERROR UNKNOWN\r\n", timeout])

    with pytest.raises(AdapterResponseTimeoutError) as exc_info:
        adapter.read_setting("uart_mode")

    assert exc_info.value.__cause__ is timeout


def test_continuous_unrelated_bytes_cannot_extend_whole_request_deadline() -> None:
    transport = ScriptedTransport([b"NOTICE\r\n" * 100])
    current_time = 0.0

    def advancing_monotonic() -> float:
        nonlocal current_time
        current_time += 0.1
        return current_time

    adapter = RsWsuhaPAdapter(
        transport,
        sleeper=lambda _seconds: None,
        monotonic=advancing_monotonic,
        response_timeout_seconds=0.5,
    )

    with pytest.raises(AdapterResponseTimeoutError):
        adapter.read_setting("uart_mode")

    assert transport.writes == [b"RUART\r\n"]
    assert transport.read_timeouts
    assert all(
        timeout is not None and 0 < timeout <= 0.5
        for timeout in transport.read_timeouts
    )


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("nan"), float("inf")],
)
def test_adapter_rejects_invalid_whole_response_timeout(timeout: float) -> None:
    transport = ScriptedTransport()

    with pytest.raises(ValueError, match="response_timeout_seconds"):
        RsWsuhaPAdapter(transport, response_timeout_seconds=timeout)


def test_write_is_rechecked_and_mismatch_raises_verification_error() -> None:
    adapter, transport, _ = _adapter(
        [b"OK 00\r\n", b"OK\r\n", b"OK 00\r\n"]
    )

    with pytest.raises(AdapterVerificationError) as exc_info:
        adapter.configure({"uart_mode": "80"})

    assert exc_info.value.name == "uart_mode"
    assert exc_info.value.expected == "80"
    assert exc_info.value.actual == "00"
    assert transport.writes == [
        b"RUART\r\n",
        b"WUART 80\r\n",
        b"RUART\r\n",
    ]


def test_serial_disconnect_is_wrapped_as_adapter_communication_error() -> None:
    disconnected = SerialDisconnectedError("removed")
    adapter, _, _ = _adapter([disconnected])

    with pytest.raises(AdapterCommunicationError) as exc_info:
        adapter.read_setting("uart_mode")

    assert exc_info.value.__cause__ is disconnected


@pytest.mark.parametrize("value", ["", "8", "800", "GG", "0x80", "８０"])
def test_setting_value_must_be_exactly_two_hex_digits(value: str) -> None:
    adapter, transport, _ = _adapter([])

    with pytest.raises(InvalidAdapterSettingValueError):
        adapter.write_setting("uart_mode", value)

    assert transport.writes == []


def test_lowercase_hex_value_is_normalized_before_write() -> None:
    adapter, transport, _ = _adapter([b"OK\r\n"])

    adapter.write_setting("uart_mode", "af")

    assert transport.writes == [b"WUART AF\r\n"]


def test_unsupported_setting_does_not_send_a_command() -> None:
    adapter, transport, _ = _adapter([])

    with pytest.raises(UnsupportedAdapterSettingError):
        adapter.read_setting("unknown")

    assert transport.writes == []


def test_reset_and_credentials_use_confirmed_commands_without_log_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    b_route_id = "A" * 32
    password = "P" * 12
    adapter, transport, _ = _adapter(
        [
            b"SKRESET\r\nOK\r\n",
            f"SKSETRBID {b_route_id}\r\nOK\r\n".encode(),
            f"SKSETPWD C {password}\r\nOK\r\n".encode(),
        ]
    )

    adapter_logger = logging.getLogger("broute_meter.adapter.rs_wsuha_p")
    adapter_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(
            logging.DEBUG,
            logger="broute_meter.adapter.rs_wsuha_p",
        ):
            adapter.reset()
            adapter.set_b_route_id(b_route_id)
            adapter.set_b_route_password(password)
    finally:
        adapter_logger.removeHandler(caplog.handler)

    assert transport.writes == [
        b"SKRESET\r\n",
        f"SKSETRBID {b_route_id}\r\n".encode(),
        f"SKSETPWD C {password}\r\n".encode(),
    ]
    assert b_route_id not in caplog.text
    assert password not in caplog.text
    assert "SKSETRBID ***" in caplog.text
    assert "SKSETPWD ***" in caplog.text


def test_exchange_udp_sends_binary_payload_and_parses_hex_erxudp() -> None:
    address = IPv6Address("fe80::1")
    request = bytes.fromhex("1081123405FF010288016201E700")
    response = bytes.fromhex("1081123402880105FF017201E704FFFFFF9C")
    adapter, transport, _ = _adapter(
        [
            (
                b"SKSENDTO 1 fe80::1 0E1A 1 0 000E\r\n"
                b"EVENT 21 fe80::1 0 00\r\n"
                b"OK\r\n"
                b"NOTICE\r\n"
                b"ERXUDP FE80::1 FE80::2 0E1A 0E1A "
                b"0011223344556677 1 0 0012 "
                + response.hex().upper().encode()
                + b"\r\n"
            )
        ]
    )

    assert adapter.exchange_udp(address, request) == response
    assert transport.writes == [
        b"SKSENDTO 1 FE80:0000:0000:0000:0000:0000:0000:0001 "
        b"0E1A 1 0 000E "
        + request
    ]


def test_exchange_udp_ignores_other_sender_and_invalid_length() -> None:
    address = IPv6Address("fe80::1")
    request = bytes.fromhex("1081000105FF010288016201E700")
    expected = bytes.fromhex("1081000102880105FF017201E70400000001")
    adapter, _, _ = _adapter(
        [
            (
                b"OK\r\n"
                b"ERXUDP FE80::2 FE80::3 0E1A 0E1A "
                b"0011223344556677 1 0 0012 "
                + expected.hex().encode()
                + b"\r\n"
                b"ERXUDP FE80::1 FE80::3 0E1A 0E1A "
                b"0011223344556677 1 0 0001 "
                + expected.hex().encode()
                + b"\r\n"
                b"ERXUDP FE80::1 FE80::3 0E1A 0E1A "
                b"0011223344556677 1 0 0012 "
                + expected.hex().encode()
                + b"\r\n"
            )
        ]
    )

    assert adapter.exchange_udp(address, request) == expected


def test_exchange_udp_uses_matcher_to_ignore_stale_tid() -> None:
    address = IPv6Address("fe80::1")
    request = bytes.fromhex("1081000205FF010288016201E700")
    stale = bytes.fromhex("1081000102880105FF017201E70400000001")
    expected = bytes.fromhex("1081000202880105FF017201E70400000002")

    def erxudp(payload: bytes) -> bytes:
        return (
            b"ERXUDP FE80::1 FE80::3 0E1A 0E1A "
            b"0011223344556677 1 0 0012 "
            + payload.hex().encode()
            + b"\r\n"
        )

    adapter, _, _ = _adapter([b"OK\r\n" + erxudp(stale) + erxudp(expected)])

    response = adapter.exchange_udp(
        address,
        request,
        response_matcher=lambda payload: payload[2:4] == b"\x00\x02",
    )

    assert response == expected


@pytest.mark.parametrize(
    ("method_name", "value"),
    [
        ("set_b_route_id", "short"),
        ("set_b_route_id", "Ａ" * 32),
        ("set_b_route_password", "short"),
        ("set_b_route_password", "P" * 11 + " "),
    ],
)
def test_invalid_credentials_never_send_a_command(
    method_name: str,
    value: str,
) -> None:
    adapter, transport, _ = _adapter([])

    with pytest.raises(AdapterCredentialError):
        getattr(adapter, method_name)(value)

    assert transport.writes == []


def test_active_scan_parses_epandesc_and_ignores_unrelated_events() -> None:
    adapter, transport, _ = _adapter(
        [
            (
                b"SKSCAN 2 FFFFFFFF 6 0\r\nOK\r\n"
                b"EVENT 20\r\nEPANDESC\r\n"
                b"  Channel:39\r\n"
                b"  Channel Page:09\r\n"
                b"  Pan ID:a1b2\r\n"
                b"  Addr:0011223344556677\r\n"
                b"  LQI:a7\r\n"
                b"  PairID:1234abcd\r\n"
                b"EVENT 22\r\n"
            )
        ]
    )

    results = adapter.active_scan()

    assert len(results) == 1
    result = results[0]
    assert result.channel == "39"
    assert result.channel_page == "09"
    assert result.pan_id == "A1B2"
    assert result.address == "0011223344556677"
    assert result.lqi == "A7"
    assert result.pair_id == "1234ABCD"
    assert result.raw_fields["Pan ID"] == "A1B2"
    assert transport.writes == [b"SKSCAN 2 FFFFFFFF 6 0\r\n"]


def test_active_scan_returns_all_candidates_for_session_to_disambiguate() -> None:
    adapter, _, _ = _adapter(
        [
            (
                b"OK\r\n"
                b"EPANDESC\r\nChannel:39\r\nPan ID:1111\r\n"
                b"Addr:0011223344556677\r\n"
                b"EPANDESC\r\nChannel:3A\r\nPan ID:2222\r\n"
                b"Addr:8899AABBCCDDEEFF\r\nEVENT 22\r\n"
            )
        ]
    )

    results = adapter.active_scan()

    assert [result.pan_id for result in results] == ["1111", "2222"]
    assert [result.address for result in results] == [
        "0011223344556677",
        "8899AABBCCDDEEFF",
    ]


def test_active_scan_rejects_descriptor_missing_required_field() -> None:
    adapter, _, _ = _adapter(
        [b"OK\r\nEPANDESC\r\nChannel:39\r\nPan ID:1111\r\nEVENT 22\r\n"]
    )

    with pytest.raises(AdapterScanError, match="Addr"):
        adapter.active_scan()


def test_active_scan_retries_single_read_timeout_until_whole_deadline() -> None:
    current_time = 0.0

    def advance_one_second() -> None:
        nonlocal current_time
        current_time += 1.0

    transport = ScriptedTransport(
        [SerialTimeoutError("one read timed out"), b"OK\r\nEVENT 22\r\n"],
        on_read_timeout=advance_one_second,
    )
    adapter = RsWsuhaPAdapter(
        transport,
        sleeper=lambda _seconds: None,
        monotonic=lambda: current_time,
    )

    assert adapter.active_scan() == ()
    assert transport.writes == [b"SKSCAN 2 FFFFFFFF 6 0\r\n"]


def test_active_scan_timeout_logs_classified_response_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    timeout = SerialTimeoutError("whole scan timed out")
    adapter, _, _ = _adapter(
        [
            b"SKSCAN 2 FFFFFFFF 6 0\r\nOK\r\n"
            b"EVENT 20 0011223344556677 0\r\n"
            b"unexpected-notice\r\n"
            b"\xff\r\n",
            timeout,
        ]
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(AdapterResponseTimeoutError) as exc_info:
            adapter.active_scan()

    assert isinstance(exc_info.value.__cause__, SerialTimeoutError)
    messages = [record.getMessage() for record in caplog.records]
    diagnostic = next(
        message
        for message in messages
        if "RS-WSUHA-P応答待機失敗" in message
    )
    assert "category=timeout" in diagnostic
    assert "operation=active_scan" in diagnostic
    assert "expected_response=scan_completion_event_22" in diagnostic
    assert "received_line_count=5" in diagnostic
    assert "unexpected_response_count=1" in diagnostic
    assert "malformed_response_count=1" in diagnostic
    assert "event_types=20" in diagnostic
    assert "unexpected-notice" not in diagnostic


def test_radio_settings_and_ipv6_resolution_use_scan_values() -> None:
    adapter, transport, _ = _adapter(
        [
            b"SKSREG S2 39\r\nOK\r\n",
            b"SKSREG S3 A1B2\r\nOK\r\n",
            (
                b"SKLL64 0011223344556677\r\n"
                b"FE80:0000:0000:0000:0211:22FF:FE33:4455\r\n"
            ),
        ]
    )

    adapter.set_channel("39")
    adapter.set_pan_id("a1b2")
    ipv6_address = adapter.resolve_ipv6_address("0011223344556677")

    assert ipv6_address == IPv6Address("fe80::211:22ff:fe33:4455")
    assert transport.writes == [
        b"SKSREG S2 39\r\n",
        b"SKSREG S3 A1B2\r\n",
        b"SKLL64 0011223344556677\r\n",
    ]


def test_join_ignores_intermediate_events_and_accepts_event_25() -> None:
    ipv6_address = IPv6Address("fe80::211:22ff:fe33:4455")
    adapter, transport, _ = _adapter(
        [
            (
                b"SKJOIN FE80:0000:0000:0000:0211:22FF:FE33:4455\r\nOK\r\n"
                b"EVENT 21 fe80::211:22ff:fe33:4455\r\n"
                b"EVENT 02 fe80::211:22ff:fe33:4455\r\n"
                b"EVENT 25 fe80::211:22ff:fe33:4455\r\n"
            )
        ]
    )

    adapter.join(ipv6_address)

    assert transport.writes == [
        b"SKJOIN FE80:0000:0000:0000:0211:22FF:FE33:4455\r\n"
    ]


def test_join_never_logs_pana_udp_payload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_derived_payload = b"5345435245542D444552495645442D44415441"
    adapter, _, _ = _adapter(
        [
            b"OK\r\n"
            b"ERXUDP FE80::1 FE80::2 02CC 02CC "
            b"0011223344556677 0 0 0013 "
            + secret_derived_payload
            + b"\r\n"
            b"EVENT 25 FE80::1\r\n"
        ]
    )
    adapter_logger = logging.getLogger("broute_meter.adapter.rs_wsuha_p")
    adapter_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(
            logging.DEBUG,
            logger="broute_meter.adapter.rs_wsuha_p",
        ):
            adapter.join(IPv6Address("fe80::1"))
    finally:
        adapter_logger.removeHandler(caplog.handler)

    assert secret_derived_payload.decode() not in caplog.text
    assert "内容は省略" in caplog.text


def test_join_event_24_is_reported_without_exposing_credentials() -> None:
    adapter, _, _ = _adapter(
        [b"OK\r\nEVENT 24 fe80::211:22ff:fe33:4455\r\n"]
    )

    with pytest.raises(AdapterPanaJoinError, match="認証情報"):
        adapter.join(IPv6Address("fe80::211:22ff:fe33:4455"))


def test_active_scan_observes_shutdown_while_waiting_for_radio_response() -> None:
    class ShutdownOnReadTransport(ScriptedTransport):
        def __init__(self, event: threading.Event) -> None:
            super().__init__()
            self._event = event

        def read(self, size: int = 1, *, timeout_seconds: float | None = None) -> bytes:
            self.read_sizes.append(size)
            self.read_timeouts.append(timeout_seconds)
            self._event.set()
            return b""

    shutdown_event = threading.Event()
    transport = ShutdownOnReadTransport(shutdown_event)
    adapter = RsWsuhaPAdapter(transport, sleeper=lambda _: None)
    adapter.set_shutdown_event(shutdown_event)

    with pytest.raises(AdapterOperationCancelled):
        adapter.active_scan()

    assert transport.writes == [b"SKSCAN 2 FFFFFFFF 6 0\r\n"]


def test_open_close_and_reopen_schedule_startup_wait_for_each_session() -> None:
    transport = ScriptedTransport(
        [b"OK 80\r\n", b"OK 80\r\n"],
        is_open=False,
    )
    sleeps: list[float] = []
    adapter = RsWsuhaPAdapter(transport, sleeper=sleeps.append)

    adapter.open()
    assert adapter.read_setting("uart_mode") == "80"
    adapter.close()
    adapter.open()
    assert adapter.read_setting("uart_mode") == "80"

    assert transport.open_count == 2
    assert transport.close_count == 1
    assert sleeps == [3.0, 3.0]


class BlockingTransport(ScriptedTransport):
    """最初のreadを停止し、要求lockの直列化を観測する。"""

    def __init__(self) -> None:
        super().__init__([b"OK 80\r\nOK 80\r\n"])
        self.first_read_entered = threading.Event()
        self.release_first_read = threading.Event()
        self._block_first_read = True

    def read(
        self,
        size: int = 1,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        if self._block_first_read:
            self._block_first_read = False
            self.first_read_entered.set()
            if not self.release_first_read.wait(timeout=2):
                raise AssertionError("test did not release first read")
        return super().read(size, timeout_seconds=timeout_seconds)


def test_requests_are_serialized_with_one_in_flight_command() -> None:
    transport = BlockingTransport()
    adapter = RsWsuhaPAdapter(transport, sleeper=lambda _seconds: None)
    results: list[str] = []
    second_started = threading.Event()

    first = threading.Thread(
        target=lambda: results.append(adapter.read_setting("uart_mode"))
    )

    def run_second() -> None:
        second_started.set()
        results.append(adapter.read_setting("uart_mode"))

    second = threading.Thread(target=run_second)
    first.start()
    assert transport.first_read_entered.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)

    assert transport.writes == [b"RUART\r\n"]

    transport.release_first_read.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == ["80", "80"]
    assert transport.writes == [b"RUART\r\n", b"RUART\r\n"]
