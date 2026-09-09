"""CLIの単体テスト。"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv6Address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from broute_meter import cli
from broute_meter.adapter import (
    AdapterCommunicationError,
    AdapterConfigurationResult,
    AdapterPanaJoinError,
    AdapterResponseTimeoutError,
    AdapterScanError,
)
from broute_meter.broute import BRouteSessionError, NoSmartMeterFoundError
from broute_meter.config import AppConfig
from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading
from broute_meter.runtime_status import RetryRequestStore, RuntimeStatusStore
from broute_meter.serial.port_detector import PortInfo

ENVIRONMENT_KEYS = (
    "B_ROUTE_ID",
    "B_ROUTE_PASSWORD",
    "B_ROUTE_SERIAL_PORT",
    "B_ROUTE_INSTANT_INTERVAL",
    "B_ROUTE_CUMULATIVE_INTERVAL",
    "B_ROUTE_CUMULATIVE_DELAY",
    "B_ROUTE_DATA_DIR",
    "B_ROUTE_LOG_LEVEL",
    "OMK_DATA_DIR",
    "OMK_LOG_DIR",
)


@pytest.fixture(autouse=True)
def _clean_b_route_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_parser_registers_all_required_subcommands() -> None:
    parser = cli.build_parser()

    for command in (
        "run",
        "list-ports",
        "check-config",
        "test-connection",
        "setup-adapter",
    ):
        args = parser.parse_args([command])
        assert args.command == command
        assert callable(args.handler)


def test_list_ports_does_not_require_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "list_serial_ports",
        lambda: [
            PortInfo(
                device="COM5",
                product="RS-WSUHA-P",
                manufacturer="Example Maker",
                vid=0x123,
                pid=0xABCD,
                serial_number="SERIAL-1",
            )
        ],
    )

    exit_code = cli.main(["list-ports"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "COM5" in captured.out
    assert "RS-WSUHA-P" in captured.out
    assert "0123" in captured.out
    assert "ABCD" in captured.out
    assert "SERIAL-1" in captured.out
    assert captured.err == ""


def test_list_ports_reports_empty_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "list_serial_ports", lambda: [])

    exit_code = cli.main(["list-ports"])

    assert exit_code == 0
    assert "ありません" in capsys.readouterr().out


def test_setup_adapter_auto_detects_only_named_rs_wsuha_p_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "list_serial_ports",
        lambda: [
            PortInfo("COM3", "Another Adapter", None, None, None, None),
            PortInfo("COM5", "RATOC RS-WSUHA-P", None, None, None, None),
        ],
    )

    assert cli._resolve_adapter_port(AppConfig()) == "COM5"


def test_setup_adapter_mock_mode_never_enumerates_or_opens_serial_port(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )

    def fail_if_serial_is_enumerated() -> list[PortInfo]:
        raise AssertionError("mock mode must not enumerate serial ports")

    monkeypatch.setattr(cli, "list_serial_ports", fail_if_serial_is_enumerated)

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "setup-adapter",
            "--mock",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "MockAdapter" in captured.out
    assert "USB通信なし" in captured.out
    assert "00 -> 80" in captured.out


def test_check_config_masks_credentials(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    full_id = "0123456789ABCDEF0123456789ABCDEF"
    password = "SECRET-PASSWORD"
    settings_path = tmp_path / "settings.yaml"
    credentials_path = tmp_path / "credentials.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "logging:",
                "  level: INFO",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    credentials_path.write_text(
        "\n".join(
            (
                "b_route:",
                f'  id: "{full_id}"',
                f'  password: "{password}"',
            )
        ),
        encoding="utf-8",
    )

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "--credentials",
            str(credentials_path),
            "check-config",
        ]
    )

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert exit_code == 0
    assert "設定は有効です" in captured.out
    assert full_id not in combined
    assert password not in combined
    assert "0123" in captured.out
    assert "CDEF" in captured.out

    log_text = (tmp_path / "logs" / "broute-meter.log").read_text(encoding="utf-8")
    assert full_id not in log_text
    assert password not in log_text


def test_check_config_returns_nonzero_when_credentials_are_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("{}\n", encoding="utf-8")

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "--credentials",
            str(tmp_path / "missing-credentials.yaml"),
            "check-config",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "設定エラー" in captured.err


class _FakeSetupAdapter:
    def __init__(
        self,
        result: AdapterConfigurationResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.opened = False
        self.closed = False
        self.configure_calls: list[tuple[dict[str, str], bool]] = []

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True
        self.opened = False

    def configure(
        self,
        expected_settings: Mapping[str, str],
        *,
        write_changes: bool,
    ) -> AdapterConfigurationResult:
        normalized = dict(expected_settings)
        self.configure_calls.append((normalized, write_changes))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _adapter_result(
    *,
    initial: str,
    final: str,
    changed: tuple[str, ...] = (),
    write_changes: bool = True,
) -> AdapterConfigurationResult:
    return AdapterConfigurationResult(
        expected_settings={"uart_mode": "80"},
        initial_settings={"uart_mode": initial},
        final_settings={"uart_mode": final},
        changed_settings=changed,
        write_changes=write_changes,
    )


def test_setup_adapter_configures_without_reading_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "adapter:",
                "  auto_configure: true",
                "  expected_settings:",
                '    uart_mode: "80"',
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    malformed_credentials = tmp_path / "credentials.yaml"
    malformed_credentials.write_text("b_route: [\n", encoding="utf-8")
    monkeypatch.setenv("B_ROUTE_ID", "")
    monkeypatch.setenv("B_ROUTE_PASSWORD", "")
    adapter = _FakeSetupAdapter(
        _adapter_result(initial="00", final="80", changed=("uart_mode",))
    )
    monkeypatch.setattr(
        cli,
        "_create_rs_wsuha_p_adapter",
        lambda _config, _port: adapter,
    )

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "--credentials",
            str(malformed_credentials),
            "setup-adapter",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "使用ポート: COM5" in captured.out
    assert "00 -> 80" in captured.out
    assert "不一致設定だけを書き込み" in captured.out
    assert adapter.configure_calls == [
        ({"uart_mode": "80", "output_mode": "01"}, True)
    ]
    assert adapter.closed
    assert "設定エラー" not in captured.err


@pytest.mark.parametrize("trust,outcome", [(False, "success"), (True, "success"),
    (True, "mismatch"), (True, "timeout"), (True, "empty")])
def test_trust_requires_successful_real_protocol_and_is_optional(monkeypatch, trust, outcome):
    events = []
    values = {"uart_mode": "80", "output_mode": "01"}
    config = SimpleNamespace(adapter=SimpleNamespace(expected_settings=values, auto_configure=False))
    load = Mock(return_value=config)
    monkeypatch.setattr(cli, "_load_application_config", load)
    configure_logging = Mock(return_value=logging.getLogger("test"))
    monkeypatch.setattr(cli, "_configure_command_logging", configure_logging)
    monkeypatch.setattr(cli, "_resolve_adapter_port", lambda _: "/dev/ttyUSB9")
    monkeypatch.setattr(cli.os, "geteuid", lambda: 0)

    class Adapter(_FakeSetupAdapter):
        def configure(self, *_args, **_kwargs):
            events.append("protocol")
            if outcome == "timeout":
                raise AdapterResponseTimeoutError("no response")
            if outcome == "empty":
                return AdapterConfigurationResult({}, {}, {}, (), False)
            actual = values if outcome == "success" else {"uart_mode": "00", "output_mode": "01"}
            return AdapterConfigurationResult(values, actual, actual, (), False)

    adapter = Adapter()
    monkeypatch.setattr(cli, "_create_rs_wsuha_p_adapter", lambda *_: adapter)

    def snapshot(_):
        events.append("snapshot")
        return "verified transport"

    monkeypatch.setattr(cli, "resolve_rs_wsuha_p_usb", snapshot)
    monkeypatch.setattr(cli, "register_trusted_usb_adapter", lambda *_: events.append("register"))
    args = ["setup-adapter"] + (["--trust-usb-recovery"] if trust else [])
    code = cli.main(args)
    if trust and outcome == "success":
        assert events == ["snapshot", "protocol", "snapshot", "register"]
    elif not trust:
        assert events == ["protocol"]  # Missing identity never blocks ordinary setup/measurement.
    else:
        assert "register" not in events
    assert code == (0 if outcome == "success" else 5 if outcome == "mismatch" else 1)
    assert adapter.closed
    assert load.call_args.kwargs == {"require_credentials": False, "include_credentials": False}
    assert configure_logging.call_count == (0 if trust else 1)


@pytest.mark.parametrize("uid,mock", [(1000, False), (0, True)])
def test_trust_flag_rejects_unprivileged_or_mock_before_io(monkeypatch, uid, mock):
    monkeypatch.setattr(cli.os, "geteuid", lambda: uid)
    load = Mock(side_effect=AssertionError("Must not load config or open USB"))
    monkeypatch.setattr(cli, "_load_application_config", load)
    args = ["setup-adapter", "--trust-usb-recovery"] + (["--mock"] if mock else [])
    assert cli.main(args) == 1
    load.assert_not_called()


def test_normal_service_startup_does_not_require_trusted_identity(monkeypatch):
    resetter = Mock(side_effect=AssertionError("Normal communication must not require trust"))
    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", resetter)
    result = _adapter_result(initial="80", final="80")
    adapter = _FakeSetupAdapter(result)
    config = SimpleNamespace(adapter=SimpleNamespace(expected_settings={"uart_mode": "80"}, auto_configure=True))
    state = SimpleNamespace(write=lambda *_args, **_kwargs: None)
    assert cli._configure_adapter_with_usb_recovery(
        adapter, config, "/dev/ttyUSB9", state, threading.Event(), logging.getLogger("test"),
    ) == result
    resetter.assert_not_called()


def test_setup_adapter_reports_mismatch_when_auto_configure_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "adapter:",
                "  auto_configure: false",
                "  expected_settings:",
                '    uart_mode: "80"',
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    adapter = _FakeSetupAdapter(
        _adapter_result(
            initial="00",
            final="00",
            write_changes=False,
        )
    )
    monkeypatch.setattr(
        cli,
        "_create_rs_wsuha_p_adapter",
        lambda _config, _port: adapter,
    )

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "setup-adapter",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == cli.ADAPTER_SETTINGS_MISMATCH_EXIT_CODE
    assert "期待値と一致しません" in captured.err
    assert adapter.configure_calls == [
        ({"uart_mode": "80", "output_mode": "01"}, False)
    ]
    assert adapter.closed


def test_setup_adapter_closes_adapter_after_communication_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    adapter = _FakeSetupAdapter(
        error=AdapterCommunicationError("テスト用の通信失敗")
    )
    monkeypatch.setattr(
        cli,
        "_create_rs_wsuha_p_adapter",
        lambda _config, _port: adapter,
    )

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "setup-adapter",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "アダプター通信エラー" in captured.err
    assert adapter.closed


def test_connection_configures_connects_reads_e7_and_closes_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings_path = tmp_path / "settings.yaml"
    credentials_path = tmp_path / "credentials.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "adapter:",
                "  expected_settings:",
                '    uart_mode: "80"',
                '    output_mode: "01"',
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    credentials_path.write_text(
        "\n".join(
            (
                "b_route:",
                f'  id: "{"A" * 32}"',
                f'  password: "{"P" * 12}"',
            )
        ),
        encoding="utf-8",
    )
    adapter = _FakeSetupAdapter(
        AdapterConfigurationResult(
            expected_settings={"uart_mode": "80", "output_mode": "01"},
            initial_settings={"uart_mode": "80", "output_mode": "01"},
            final_settings={"uart_mode": "80", "output_mode": "01"},
            changed_settings=(),
            write_changes=True,
        )
    )
    address = IPv6Address("fe80::1234")
    connection_calls: list[tuple[str, str]] = []

    class FakeSession:
        def __init__(
            self,
            selected_adapter: object,
            *,
            scan_max_attempts: int,
        ) -> None:
            assert selected_adapter is adapter
            assert scan_max_attempts == 3

        def connect(self, identifier: str, password: str) -> SimpleNamespace:
            connection_calls.append((identifier, password))
            return SimpleNamespace(smart_meter_ipv6=address)

    class FakeMeterClient:
        def __init__(
            self,
            selected_adapter: object,
            selected_address: IPv6Address,
            *,
            request_max_attempts: int,
            request_timeout_seconds: float,
        ) -> None:
            assert selected_adapter is adapter
            assert selected_address == address
            assert request_max_attempts == 3
            assert request_timeout_seconds == 5

        def get_instantaneous_power(self) -> InstantaneousPowerReading:
            return InstantaneousPowerReading(
                measured_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
                net_power_w=-840,
            )

        def get_cumulative_energy(self) -> CumulativeEnergyReading:
            return CumulativeEnergyReading(
                metered_at=datetime(2026, 7, 25, 11, 30, tzinfo=UTC),
                received_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
                forward_raw=1_234_567,
                reverse_raw=23_450,
                forward_total_kwh=Decimal("12345.67"),
                reverse_total_kwh=Decimal("234.50"),
            )

    monkeypatch.setattr(
        cli,
        "_create_rs_wsuha_p_adapter",
        lambda _config, _port: adapter,
    )
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(cli, "SmartMeterClient", FakeMeterClient)

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "--credentials",
            str(credentials_path),
            "test-connection",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "接続成功: fe80::1234" in captured.out
    assert "正味瞬時電力: -840 W" in captured.out
    assert "正方向積算電力量: 12345.67 kWh" in captured.out
    assert "逆方向積算電力量: 234.50 kWh" in captured.out
    assert connection_calls == [("A" * 32, "P" * 12)]
    assert adapter.configure_calls == [
        ({"uart_mode": "80", "output_mode": "01"}, True)
    ]
    assert adapter.closed


def test_run_connection_retries_broute_session_after_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run用の初期接続は一時的なスキャン失敗で終了しない。"""

    attempts: list[tuple[str, str]] = []

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            assert scan_max_attempts == 3

        def connect(self, identifier: str, password: str) -> SimpleNamespace:
            attempts.append((identifier, password))
            if len(attempts) == 1:
                raise BRouteSessionError("候補なし")
            return SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1"))

    class RecordingStopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return False

    stop_event = RecordingStopEvent()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)

    with caplog.at_level(logging.WARNING):
        connection = cli._connect_broute_until_ready(  # type: ignore[arg-type]
            object(),
            config,  # type: ignore[arg-type]
            stop_event,  # type: ignore[arg-type]
            logging.getLogger("broute_meter.test"),
        )

    assert connection is not None
    assert attempts == [("A" * 32, "P" * 12)] * 2
    assert stop_event.waits == [30]
    assert "初期Bルート接続に失敗しました。再試行します" in caplog.text


def test_run_connection_retry_wait_stops_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """終了要求があれば初期接続の再試行待機を中断する。"""

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            pass

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            raise BRouteSessionError("候補なし")

    class StopDuringWait:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, timeout: float) -> bool:
            assert timeout == 30
            self.stopped = True
            return True

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    stop_event = StopDuringWait()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert (
        cli._connect_broute_until_ready(  # type: ignore[arg-type]
            object(),
            config,  # type: ignore[arg-type]
            stop_event,  # type: ignore[arg-type]
            logging.getLogger("broute_meter.test"),
            runtime_status=RuntimeStatusStore(tmp_path / "status.json"),
        )
        is None
    )
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))[
        "retry_after_seconds"
    ] == 30


def test_connection_retries_do_not_reenter_starting_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            assert scan_max_attempts == 3

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            raise NoSmartMeterFoundError("no scan candidates")

    class StopAfterTwoRetries:
        retries = 0

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            assert timeout == 30
            self.retries += 1
            return self.retries == 2

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[tuple[str, float | None]] = []

        def write(
            self,
            state: str,
            *,
            now: datetime,
            retry_after_seconds: float | None = None,
            connection_attempt: int | None = None,
        ) -> None:
            self.states.append((state, retry_after_seconds))

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    status = RecordingStatus()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert cli._connect_broute_until_ready(  # type: ignore[arg-type]
        object(), config, StopAfterTwoRetries(), logging.getLogger("broute_meter.test"), runtime_status=status
    ) is None
    assert status.states == [
        ("scanning", None),
        ("scan_error", None),
        ("retry_wait", 30),
        ("scanning", None),
        ("scan_error", None),
        ("retry_wait", 30),
    ]
    assert all(state != "starting" for state, _ in status.states)


def test_missing_adapter_waits_then_initializes_when_device_reappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return False

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    presence = iter((False, False, True))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: next(presence))
    stop_event = StopEvent()
    status = RecordingStatus()

    assert cli._wait_for_adapter_device(  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        status,  # type: ignore[arg-type]
        stop_event,  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
    )
    assert stop_event.waits == [cli.ADAPTER_PRESENCE_CHECK_SECONDS] * 2
    assert status.states == ["adapter_missing", "adapter_missing", "adapter_initializing"]


def test_startup_missing_adapter_does_not_open_or_exit_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopAfterFirstCheck:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            assert timeout == cli.ADAPTER_PRESENCE_CHECK_SECONDS
            return True

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    adapter = _FakeSetupAdapter()
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: False)
    status = RecordingStatus()

    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        object(),  # type: ignore[arg-type]
        StopAfterFirstCheck(),  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        status,  # type: ignore[arg-type]
    ) is None
    assert not adapter.opened
    assert status.states == ["adapter_missing"]


def test_adapter_open_race_rechecks_path_and_enters_missing_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopAfterMissingCheck:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            assert timeout in {
                cli.ADAPTER_SETTLE_SECONDS,
                cli.ADAPTER_PRESENCE_CHECK_SECONDS,
            }
            return timeout == cli.ADAPTER_PRESENCE_CHECK_SECONDS

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    calls = 0

    def open_then_fail(*_args: object, **_kwargs: object) -> AdapterConfigurationResult:
        nonlocal calls
        calls += 1
        raise AdapterCommunicationError("serial path disappeared")

    presence = iter((True, False, False))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: next(presence))
    monkeypatch.setattr(cli, "_configure_adapter_with_usb_recovery", open_then_fail)
    status = RecordingStatus()

    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        _FakeSetupAdapter(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        object(),  # type: ignore[arg-type]
        StopAfterMissingCheck(),  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        status,  # type: ignore[arg-type]
    ) is None
    assert calls == 1
    assert status.states == ["adapter_initializing", "adapter_missing"]


def test_reinserted_adapter_initializes_then_scans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopEvent:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float) -> bool:
            return False

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            pass

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            return SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1"))

    adapter = _FakeSetupAdapter()
    presence = iter((False, True, True))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: next(presence))
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(
        cli,
        "_configure_adapter_with_usb_recovery",
        lambda selected_adapter, *_args, **_kwargs: (
            selected_adapter.open(),
            AdapterConfigurationResult(
                expected_settings={}, initial_settings={}, final_settings={}, changed_settings=(), write_changes=True
            ),
        )[1],
    )
    status = RecordingStatus()
    stop_event = StopEvent()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        config,  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        object(),  # type: ignore[arg-type]
        stop_event,  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        status,  # type: ignore[arg-type]
    ) is not None
    assert adapter.opened
    assert cli._connect_broute_until_ready(  # type: ignore[arg-type]
        adapter,
        config,  # type: ignore[arg-type]
        stop_event,  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        runtime_status=status,  # type: ignore[arg-type]
        port="/dev/serial/by-id/rs-wsuha-p",
    ) is not None
    assert status.states == [
        "adapter_missing",
        "adapter_initializing",
        "adapter_initializing",
        "scanning",
        "connected",
    ]


def test_present_adapter_open_error_does_not_become_adapter_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopAfterInitializationRetry:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            return timeout == cli.ADAPTER_INITIALIZATION_RETRY_SECONDS

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    monkeypatch.setattr(
        cli,
        "_configure_adapter_with_usb_recovery",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AdapterCommunicationError("I/O error")),
    )

    status = RecordingStatus()
    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        _FakeSetupAdapter(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        object(),  # type: ignore[arg-type]
        StopAfterInitializationRetry(),  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        status,  # type: ignore[arg-type]
    ) is None
    assert status.states == ["adapter_initializing"]


def test_initializing_retries_timeouts_and_usb_reset_cooldown_without_exiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return False

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    attempts = 0

    def configure_with_delayed_ready(*_args: object, **_kwargs: object) -> AdapterConfigurationResult:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise AdapterResponseTimeoutError("USB reset cooldown is active")
        return AdapterConfigurationResult(
            expected_settings={}, initial_settings={}, final_settings={}, changed_settings=(), write_changes=True
        )

    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    monkeypatch.setattr(cli, "_configure_adapter_with_usb_recovery", configure_with_delayed_ready)
    stop_event = StopEvent()
    status = RecordingStatus()

    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        _FakeSetupAdapter(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        "/dev/serial/by-id/rs-wsuha-p",
        object(),  # type: ignore[arg-type]
        stop_event,  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        status,  # type: ignore[arg-type]
    ) is not None
    assert attempts == 3
    assert status.states == ["adapter_initializing"] * 3
    assert stop_event.waits.count(cli.ADAPTER_INITIALIZATION_RETRY_SECONDS) == 2


def test_post_reset_readiness_retries_before_success() -> None:
    class StopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return False

    class FlakyAdapter(_FakeSetupAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.open_calls = 0

        def open(self) -> None:
            self.open_calls += 1
            super().open()

        def configure(
            self,
            expected_settings: Mapping[str, str],
            *,
            write_changes: bool,
        ) -> AdapterConfigurationResult:
            if self.open_calls < 3:
                raise AdapterResponseTimeoutError("adapter not ready")
            return AdapterConfigurationResult(
                expected_settings=dict(expected_settings),
                initial_settings={}, final_settings={}, changed_settings=(), write_changes=write_changes
            )

    adapter = FlakyAdapter()
    stop_event = StopEvent()
    config = SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True))

    assert cli._configure_after_usb_reset(  # type: ignore[arg-type]
        adapter, config, stop_event  # type: ignore[arg-type]
    ).is_configured
    assert adapter.open_calls == 3
    assert stop_event.waits == [cli.ADAPTER_READINESS_RETRY_SECONDS] * 2


def test_startup_timeout_recovers_with_one_skreset_before_usb_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    class Adapter(_FakeSetupAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.configure_calls = 0
            self.reset_calls = 0
        def configure(self, *_args: object, **_kwargs: object) -> AdapterConfigurationResult:
            self.configure_calls += 1
            if self.reset_calls == 0:
                raise AdapterResponseTimeoutError("RUART timeout")
            return AdapterConfigurationResult({}, {}, {}, (), True)
        def reset(self) -> None:
            self.reset_calls += 1

    adapter = Adapter()
    config = SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True))
    result = cli._configure_adapter_with_usb_recovery(adapter, config, "/dev/null", SimpleNamespace(write=lambda *_args, **_kwargs: None, cooldown_active=lambda **_kwargs: False), threading.Event(), logging.getLogger("broute_meter.test"))  # type: ignore[arg-type]
    assert result.is_configured
    assert adapter.reset_calls == 1
    assert adapter.configure_calls == cli.ADAPTER_READINESS_RETRY_ATTEMPTS + 1


def test_skreset_timeout_falls_through_to_usb_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    class Adapter(_FakeSetupAdapter):
        def configure(self, *_args: object, **_kwargs: object) -> AdapterConfigurationResult:
            raise AdapterResponseTimeoutError("RUART timeout")
        def reset(self) -> None:
            raise AdapterResponseTimeoutError("SKRESET timeout")
    class Stop:
        def wait(self, _timeout: float) -> bool:
            return False
    class State:
        def __init__(self) -> None:
            self.states: list[str] = []
        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)
        def cooldown_active(self, **_kwargs: object) -> bool:
            return False
    class Resetter:
        def __init__(self, *_args: object) -> None:
            pass
        def reset(self) -> SimpleNamespace:
            return SimpleNamespace(vendor="0403", product="6015", serial="TEST_ADAPTER_A", sysfs_path="/sys/test")
    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", Resetter)
    monkeypatch.setattr(cli, "_configure_after_usb_reset", lambda *_args: AdapterConfigurationResult({}, {}, {}, (), True))
    state = State()
    result = cli._configure_adapter_with_usb_recovery(Adapter(), SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True)), "/dev/null", state, Stop(), logging.getLogger("broute_meter.test"))  # type: ignore[arg-type]
    assert result.is_configured
    assert "usb_resetting" in state.states


def _vbus_recovery_inputs() -> tuple[object, object, object, object]:
    class Adapter(_FakeSetupAdapter):
        def configure(self, *_args: object, **_kwargs: object) -> AdapterConfigurationResult:
            raise AdapterResponseTimeoutError("RUART timeout")

        def reset(self) -> None:
            raise AdapterResponseTimeoutError("SKRESET timeout")

    class Stop:
        def wait(self, _timeout: float) -> bool:
            return False

    class State:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

        def read(self) -> dict[str, str]:
            return {"status": self.states[-1]} if self.states else {}

        def cooldown_active(self, **_kwargs: object) -> bool:
            return False

        def vbus_cooldown_active(self, **_kwargs: object) -> bool:
            return False

    class Resetter:
        def __init__(self, *_args: object) -> None:
            pass

        def reset(self) -> SimpleNamespace:
            return SimpleNamespace(vendor="0403", product="6015", serial="TEST_ADAPTER_B", sysfs_path="/sys/test")

    return Adapter(), Stop(), State(), Resetter


def test_vbus_recovery_after_logical_reset_configures_once_and_continues(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, stop, state, resetter = _vbus_recovery_inputs()
    result = AdapterConfigurationResult({}, {}, {}, (), True)
    post_reset_calls = 0
    vbus_calls = 0

    def configure_after_reset(*_args: object) -> AdapterConfigurationResult:
        nonlocal post_reset_calls
        post_reset_calls += 1
        if post_reset_calls == 1:
            raise AdapterResponseTimeoutError("logical reset did not recover UART")
        return result

    class Cycler:
        def __init__(self, *_args: object) -> None:
            pass

        def cycle(self) -> None:
            nonlocal vbus_calls
            vbus_calls += 1

    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", resetter)
    monkeypatch.setattr(cli, "GatewayVbusCycler", Cycler)
    monkeypatch.setattr(cli, "_configure_after_usb_reset", configure_after_reset)
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)

    with caplog.at_level(logging.INFO, logger="broute_meter.test"):
        assert cli._configure_adapter_with_usb_recovery(  # type: ignore[arg-type]
            adapter, SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True)),
            "/dev/serial/by-id/rs-wsuha-p", state, stop, logging.getLogger("broute_meter.test"),
        ) is result
    assert vbus_calls == 1
    assert post_reset_calls == 2
    assert state.states[-1] == "vbus_recovered"
    assert "USB VBUS cycle後のRS-WSUHA-P設定読出しに成功しました" in caplog.messages


@pytest.mark.parametrize("failure", ["helper", "device", "configure"])
def test_vbus_failure_paths_never_repeat_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    adapter, stop, state, resetter = _vbus_recovery_inputs()
    vbus_calls = 0

    class Cycler:
        def __init__(self, *_args: object) -> None:
            pass

        def cycle(self) -> None:
            nonlocal vbus_calls
            vbus_calls += 1
            if failure == "helper":
                raise cli.UsbRecoveryError("helper failure")

    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", resetter)
    monkeypatch.setattr(cli, "GatewayVbusCycler", Cycler)
    monkeypatch.setattr(cli, "_configure_after_usb_reset", lambda *_args: (_ for _ in ()).throw(AdapterResponseTimeoutError("not ready")))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    if failure == "device":
        monkeypatch.setattr(cli, "_wait_for_vbus_device", lambda *_args: False)

    with pytest.raises(AdapterResponseTimeoutError):
        cli._configure_adapter_with_usb_recovery(  # type: ignore[arg-type]
            adapter, SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True)),
            "/dev/serial/by-id/rs-wsuha-p", state, stop, logging.getLogger("broute_meter.test"),
        )
    assert vbus_calls == 1
    assert state.states[-1] == "vbus_recovery_failed"


def test_vbus_cooldown_suppresses_the_helper_and_marks_unresponsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, stop, state, resetter = _vbus_recovery_inputs()
    vbus_calls = 0
    state.vbus_cooldown_active = lambda **_kwargs: True  # type: ignore[attr-defined]

    class Cycler:
        def __init__(self, *_args: object) -> None:
            pass

        def cycle(self) -> None:
            nonlocal vbus_calls
            vbus_calls += 1

    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", resetter)
    monkeypatch.setattr(cli, "GatewayVbusCycler", Cycler)
    monkeypatch.setattr(cli, "_configure_after_usb_reset", lambda *_args: (_ for _ in ()).throw(AdapterResponseTimeoutError("not ready")))

    with pytest.raises(AdapterResponseTimeoutError):
        cli._configure_adapter_with_usb_recovery(  # type: ignore[arg-type]
            adapter, SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True)),
            "/dev/serial/by-id/rs-wsuha-p", state, stop, logging.getLogger("broute_meter.test"),
        )
    assert vbus_calls == 0
    assert state.states[-1] == "vbus_recovery_failed"


def test_vbus_device_wait_and_settle_are_interruptible(monkeypatch: pytest.MonkeyPatch) -> None:
    class StopDuringDeviceWait:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return True

    stop = StopDuringDeviceWait()
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: False)
    assert not cli._wait_for_vbus_device("/dev/serial/by-id/rs-wsuha-p", stop)  # type: ignore[arg-type]
    assert stop.waits == [cli.VBUS_DEVICE_POLL_SECONDS]

    adapter, _unused_stop, state, resetter = _vbus_recovery_inputs()

    class SettleStop:
        def wait(self, timeout: float) -> bool:
            return timeout == cli.VBUS_SETTLE_SECONDS

    class Cycler:
        def __init__(self, *_args: object) -> None:
            pass

        def cycle(self) -> None:
            pass

    monkeypatch.setattr(cli, "RsWsuhaPUsbResetter", resetter)
    monkeypatch.setattr(cli, "GatewayVbusCycler", Cycler)
    monkeypatch.setattr(cli, "_configure_after_usb_reset", lambda *_args: (_ for _ in ()).throw(AdapterResponseTimeoutError("not ready")))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    with pytest.raises(AdapterResponseTimeoutError):
        cli._configure_adapter_with_usb_recovery(  # type: ignore[arg-type]
            adapter, SimpleNamespace(adapter=SimpleNamespace(expected_settings={}, auto_configure=True)),
            "/dev/serial/by-id/rs-wsuha-p", state, SettleStop(), logging.getLogger("broute_meter.test"),
        )
    assert state.states[-1] == "vbus_recovery_failed"


def test_vbus_device_wait_uses_the_bounded_reappearance_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop:
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return False

    moments = iter((0.0, 0.0, cli.VBUS_DEVICE_REAPPEAR_TIMEOUT_SECONDS))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: False)
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(moments))
    stop = Stop()
    assert not cli._wait_for_vbus_device("/dev/serial/by-id/rs-wsuha-p", stop)  # type: ignore[arg-type]
    assert stop.waits == [cli.VBUS_DEVICE_POLL_SECONDS]


@pytest.mark.parametrize("recovery_state", ["recovery_failed", "vbus_recovery_failed"])
def test_post_reset_failure_uses_interruptible_low_frequency_retry(
    monkeypatch: pytest.MonkeyPatch,
    recovery_state: str,
) -> None:
    class StopEvent:
        def __init__(self) -> None:
            self.waits: list[float] = []
        def is_set(self) -> bool:
            return False
        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return timeout == cli.ADAPTER_UNRESPONSIVE_RETRY_SECONDS

    class State:
        def read(self) -> dict[str, str]:
            return {"status": recovery_state}

    class Status:
        def __init__(self) -> None:
            self.states: list[tuple[str, object]] = []
        def write(self, state: str, **kwargs: object) -> None:
            self.states.append((state, kwargs.get("retry_after_seconds")))

    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    monkeypatch.setattr(cli, "_configure_adapter_with_usb_recovery", lambda *_args: (_ for _ in ()).throw(AdapterResponseTimeoutError("recovery failed")))
    stop_event, status = StopEvent(), Status()
    assert cli._configure_adapter_after_adapter_presence(  # type: ignore[arg-type]
        _FakeSetupAdapter(), object(), "/dev/serial/by-id/rs-wsuha-p", State(), stop_event,
        logging.getLogger("broute_meter.test"), status,  # type: ignore[arg-type]
    ) is None
    assert cli.ADAPTER_UNRESPONSIVE_RETRY_SECONDS in stop_event.waits
    assert ("adapter_unresponsive", cli.ADAPTER_UNRESPONSIVE_RETRY_SECONDS) in status.states


def test_serial_disconnect_with_missing_device_is_not_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            pass

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            raise AdapterCommunicationError("serial disconnected")

    class StopDuringAdapterWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            assert timeout == cli.ADAPTER_PRESENCE_CHECK_SECONDS
            return True

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    presence = iter((True, False, False))
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: next(presence))
    status = RecordingStatus()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert cli._connect_broute_until_ready(  # type: ignore[arg-type]
        object(),
        config,  # type: ignore[arg-type]
        StopDuringAdapterWait(),  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        runtime_status=status,  # type: ignore[arg-type]
        port="/dev/serial/by-id/rs-wsuha-p",
    ) is None
    assert status.states == ["scanning", "adapter_missing"]
    assert "connection_error" not in status.states


def test_serial_error_with_present_device_remains_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            pass

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            raise AdapterCommunicationError("serial I/O failed")

    class StopDuringConnectionWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            assert timeout == 30
            return True

    class RecordingStatus:
        def __init__(self) -> None:
            self.states: list[str] = []

        def write(self, state: str, **_kwargs: object) -> None:
            self.states.append(state)

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(cli, "_adapter_device_present", lambda _port: True)
    status = RecordingStatus()
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert cli._connect_broute_until_ready(  # type: ignore[arg-type]
        object(),
        config,  # type: ignore[arg-type]
        StopDuringConnectionWait(),  # type: ignore[arg-type]
        logging.getLogger("broute_meter.test"),
        runtime_status=status,  # type: ignore[arg-type]
        port="/dev/serial/by-id/rs-wsuha-p",
    ) is None
    assert status.states == [
        "scanning",
        "connection_error",
        "retry_wait",
    ]


def test_manual_retry_request_ends_retry_wait_without_shutdown(tmp_path: Path) -> None:
    request_path = tmp_path / "retry-request"
    request_path.write_text("retry\n", encoding="ascii")

    class StopEvent:
        def wait(self, _timeout: float) -> bool:
            raise AssertionError("manual retry must be consumed before waiting")

    assert not cli._wait_for_connection_retry(  # type: ignore[arg-type]
        StopEvent(),
        300,
        retry_request=RetryRequestStore(request_path),
        logger=logging.getLogger("broute_meter.test"),
    )
    assert not request_path.exists()


def test_pana_authentication_rejection_is_not_overwritten_by_retry_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong password can be followed by a retry timeout; retain EVENT 24's diagnosis."""

    failures = iter(
        (
            AdapterPanaJoinError("PANA authentication rejected"),
            AdapterResponseTimeoutError("PANA response timed out"),
        )
    )

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change) -> None:
            assert scan_max_attempts == 3
            self._on_state_change = on_state_change

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            self._on_state_change("scanning")
            self._on_state_change("authenticating")
            raise next(failures)

    class StopAfterRetry:
        attempts = 0

        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float) -> bool:
            self.attempts += 1
            return self.attempts == 2

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    status_path = tmp_path / "status.json"
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    assert cli._connect_broute_until_ready(  # type: ignore[arg-type]
        object(), config, StopAfterRetry(), logging.getLogger("broute_meter.test"), runtime_status=RuntimeStatusStore(status_path)
    ) is None

    assert "authentication_error" in status_path.read_text(encoding="utf-8")
    assert "P" * 12 not in status_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("failure", "expected_state"),
    [
        (AdapterResponseTimeoutError("PANA response timed out"), "connection_error"),
        (AdapterCommunicationError("serial disconnected"), "connection_error"),
        (AdapterScanError("smart meter not found"), "scan_error"),
        (NoSmartMeterFoundError("no scan candidates"), "scan_error"),
    ],
)
def test_connection_failures_are_classified_before_retry_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_state: str,
) -> None:
    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change) -> None:
            self._on_state_change = on_state_change

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            self._on_state_change("authenticating")
            raise failure

    class StopImmediately:
        def is_set(self) -> bool:
            return False

        def wait(self, _timeout: float) -> bool:
            return True

    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    status_path = tmp_path / "status.json"
    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        retry=SimpleNamespace(request_max_attempts=3, reconnect_wait_seconds=30),
    )

    cli._connect_broute_until_ready(  # type: ignore[arg-type]
        object(), config, StopImmediately(), logging.getLogger("broute_meter.test"), runtime_status=RuntimeStatusStore(status_path)
    )

    assert cli._error_connection_state(failure) == expected_state
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "retry_wait"
    assert status["retry_after_seconds"] == 30

def test_run_treats_inflight_failure_after_stop_signal_as_graceful(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    credentials_path = tmp_path / "credentials.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
                "storage:",
                f'  data_directory: "{(tmp_path / "data").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    credentials_path.write_text(
        "\n".join(
            (
                "b_route:",
                f'  id: "{"A" * 32}"',
                f'  password: "{"P" * 12}"',
            )
        ),
        encoding="utf-8",
    )
    adapter = _FakeSetupAdapter(
        AdapterConfigurationResult(
            expected_settings={"uart_mode": "80", "output_mode": "01"},
            initial_settings={"uart_mode": "80", "output_mode": "01"},
            final_settings={"uart_mode": "80", "output_mode": "01"},
            changed_settings=(),
            write_changes=True,
        )
    )

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            assert scan_max_attempts == 3

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            return SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1"))

    class FakeStorage:
        closed = False

        def close(self) -> None:
            self.closed = True

    storage = FakeStorage()

    class FakeScheduler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            self._stop_event = _kwargs["stop_event"]

        def run(self) -> None:
            self._stop_event.set()
            raise AdapterCommunicationError("終了中のタイムアウト")

    monkeypatch.setattr(
        cli,
        "_create_rs_wsuha_p_adapter",
        lambda _config, _port: adapter,
    )
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(
        cli,
        "SmartMeterClient",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(cli, "CsvMeasurementStorage", lambda _path: storage)
    monkeypatch.setattr(cli, "MeasurementScheduler", FakeScheduler)
    monkeypatch.setattr(
        cli,
        "_install_stop_signal_handlers",
        lambda _scheduler, _logger: {},
    )

    exit_code = cli.main(
        [
            "--settings",
            str(settings_path),
            "--credentials",
            str(credentials_path),
            "run",
        ]
    )

    assert exit_code == 0
    assert adapter.closed
    assert storage.closed


def test_run_continues_when_mqtt_publisher_start_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings_path = tmp_path / "settings.yaml"
    credentials_path = tmp_path / "credentials.yaml"
    settings_path.write_text(
        "\n".join(
            (
                "serial:",
                "  port: COM5",
                "logging:",
                f'  directory: "{(tmp_path / "logs").as_posix()}"',
                "storage:",
                f'  data_directory: "{(tmp_path / "data").as_posix()}"',
            )
        ),
        encoding="utf-8",
    )
    credentials_path.write_text(
        "\n".join(
            (
                "b_route:",
                f'  id: "{"A" * 32}"',
                f'  password: "{"P" * 12}"',
            )
        ),
        encoding="utf-8",
    )

    class RecordingAdapter(_FakeSetupAdapter):
        def __init__(self) -> None:
            super().__init__(
                AdapterConfigurationResult(
                    expected_settings={"uart_mode": "80", "output_mode": "01"},
                    initial_settings={"uart_mode": "80", "output_mode": "01"},
                    final_settings={"uart_mode": "80", "output_mode": "01"},
                    changed_settings=(),
                    write_changes=True,
                )
            )
            self.open_calls = 0

        def open(self) -> None:
            self.open_calls += 1
            super().open()

    class BrokenPublisher:
        def start(self) -> None:
            raise RuntimeError("test MQTT startup failure")

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change=None) -> None:
            assert scan_max_attempts == 3

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            return SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1"))

    class FakeStorage:
        def close(self) -> None:
            pass

    scheduler_started = False

    class FakeScheduler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> None:
            nonlocal scheduler_started
            scheduler_started = True

    adapter = RecordingAdapter()
    monkeypatch.setattr(cli, "_create_rs_wsuha_p_adapter", lambda _config, _port: adapter)
    monkeypatch.setattr(cli, "create_measurement_publisher", lambda _config: BrokenPublisher())
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(cli, "SmartMeterClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "CsvMeasurementStorage", lambda _path: FakeStorage())
    monkeypatch.setattr(cli, "MeasurementScheduler", FakeScheduler)
    monkeypatch.setattr(cli, "_install_stop_signal_handlers", lambda _event, _logger: {})
    monkeypatch.setattr(cli, "_configure_command_logging", lambda _config: logging.getLogger())

    with caplog.at_level(logging.WARNING):
        exit_code = cli.main(
            [
                "--settings",
                str(settings_path),
                "--credentials",
                str(credentials_path),
                "run",
            ]
        )

    assert exit_code == 0
    assert adapter.open_calls == 1
    assert scheduler_started
    assert caplog.messages.count("MQTT publisherを開始できませんでした。計測を継続します") == 1


def test_reconnect_updates_runtime_status_to_connected_after_pana_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """計測中のPANA再接続も初回接続と同じconnected状態で完了する。"""

    adapter = _FakeSetupAdapter(_adapter_result(initial="80", final="80"))
    states: list[str] = []

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change) -> None:
            assert scan_max_attempts == 3
            self._on_state_change = on_state_change

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            self._on_state_change("authenticating")
            return SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::2"))

    class FakePublisher:
        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeStorage:
        def close(self) -> None:
            pass

    class FakeRecoveringMeterReader:
        def __init__(self, _meter: object, reconnect, **_kwargs: object) -> None:
            self.reconnected_meter = reconnect()

    class FakeScheduler:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def run(self) -> None:
            pass

    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        storage=SimpleNamespace(data_directory=tmp_path / "data"),
        mqtt=SimpleNamespace(),
        adapter=SimpleNamespace(
            expected_settings={"uart_mode": "80"}, auto_configure=True
        ),
        retry=SimpleNamespace(
            request_max_attempts=3,
            request_timeout_seconds=5,
            reconnect_after_consecutive_failures=1,
            reconnect_wait_seconds=0,
        ),
        measurement=SimpleNamespace(
            instantaneous_interval_seconds=10,
            cumulative_fetch_delay_seconds=5,
        ),
    )
    original_write = cli._write_runtime_status

    def record_runtime_status(store, state: str, logger, **kwargs: object) -> None:
        states.append(state)
        original_write(store, state, logger, **kwargs)

    monkeypatch.setattr(cli, "_load_application_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cli, "_configure_command_logging", lambda _config: logging.getLogger())
    monkeypatch.setattr(cli, "_resolve_adapter_port", lambda _config: "COM5")
    monkeypatch.setattr(cli, "_create_rs_wsuha_p_adapter", lambda _config, _port: adapter)
    monkeypatch.setattr(
        cli,
        "_configure_adapter_after_adapter_presence",
        lambda *_args: adapter.result,
    )
    monkeypatch.setattr(
        cli,
        "_connect_broute_until_ready",
        lambda *_args, **_kwargs: SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1")),
    )
    monkeypatch.setattr(cli, "_wait_for_adapter_device", lambda *_args: True)
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(cli, "SmartMeterClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "create_measurement_publisher", lambda _config: FakePublisher())
    monkeypatch.setattr(cli, "CsvMeasurementStorage", lambda _path: FakeStorage())
    monkeypatch.setattr(cli, "RecoveringMeterReader", FakeRecoveringMeterReader)
    monkeypatch.setattr(cli, "MeasurementScheduler", FakeScheduler)
    monkeypatch.setattr(cli, "_install_stop_signal_handlers", lambda *_args: {})
    monkeypatch.setattr(cli, "_write_runtime_status", record_runtime_status)

    assert cli._run(SimpleNamespace()) == 0
    assert states[states.index("scanning") : states.index("scanning") + 3] == [
        "scanning",
        "authenticating",
        "connected",
    ]


def test_reconnect_failure_does_not_write_connected_runtime_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PANA再接続に失敗した場合はconnectedへ遷移しない。"""

    adapter = _FakeSetupAdapter(_adapter_result(initial="80", final="80"))
    states: list[str] = []

    class FakeSession:
        def __init__(self, _adapter: object, *, scan_max_attempts: int, on_state_change) -> None:
            assert scan_max_attempts == 3
            self._on_state_change = on_state_change

        def connect(self, _identifier: str, _password: str) -> SimpleNamespace:
            self._on_state_change("authenticating")
            raise BRouteSessionError("PANA reconnect failed")

    class FakePublisher:
        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeStorage:
        def close(self) -> None:
            pass

    class FakeRecoveringMeterReader:
        def __init__(self, _meter: object, reconnect, **_kwargs: object) -> None:
            reconnect()

    config = SimpleNamespace(
        credentials=SimpleNamespace(b_route_id="A" * 32, password="P" * 12),
        storage=SimpleNamespace(data_directory=tmp_path / "data"),
        mqtt=SimpleNamespace(),
        adapter=SimpleNamespace(
            expected_settings={"uart_mode": "80"}, auto_configure=True
        ),
        retry=SimpleNamespace(
            request_max_attempts=3,
            request_timeout_seconds=5,
            reconnect_after_consecutive_failures=1,
            reconnect_wait_seconds=0,
        ),
        measurement=SimpleNamespace(
            instantaneous_interval_seconds=10,
            cumulative_fetch_delay_seconds=5,
        ),
    )
    original_write = cli._write_runtime_status

    def record_runtime_status(store, state: str, logger, **kwargs: object) -> None:
        states.append(state)
        original_write(store, state, logger, **kwargs)

    monkeypatch.setattr(cli, "_load_application_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cli, "_configure_command_logging", lambda _config: logging.getLogger())
    monkeypatch.setattr(cli, "_resolve_adapter_port", lambda _config: "COM5")
    monkeypatch.setattr(cli, "_create_rs_wsuha_p_adapter", lambda _config, _port: adapter)
    monkeypatch.setattr(
        cli,
        "_configure_adapter_after_adapter_presence",
        lambda *_args: adapter.result,
    )
    monkeypatch.setattr(
        cli,
        "_connect_broute_until_ready",
        lambda *_args, **_kwargs: SimpleNamespace(smart_meter_ipv6=IPv6Address("fe80::1")),
    )
    monkeypatch.setattr(cli, "_wait_for_adapter_device", lambda *_args: True)
    monkeypatch.setattr(cli, "BRouteSession", FakeSession)
    monkeypatch.setattr(cli, "SmartMeterClient", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "create_measurement_publisher", lambda _config: FakePublisher())
    monkeypatch.setattr(cli, "CsvMeasurementStorage", lambda _path: FakeStorage())
    monkeypatch.setattr(cli, "RecoveringMeterReader", FakeRecoveringMeterReader)
    monkeypatch.setattr(cli, "_install_stop_signal_handlers", lambda *_args: {})
    monkeypatch.setattr(cli, "_write_runtime_status", record_runtime_status)

    with pytest.raises(BRouteSessionError, match="PANA reconnect failed"):
        cli._run(SimpleNamespace())

    assert states == [
        "starting",
        "scanning",
        "authenticating",
        "connection_error",
        "stopped",
    ]
