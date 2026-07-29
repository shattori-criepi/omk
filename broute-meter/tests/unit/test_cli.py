"""CLIの単体テスト。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from ipaddress import IPv6Address
from pathlib import Path
from types import SimpleNamespace

import pytest

from broute_meter import cli
from broute_meter.adapter import (
    AdapterCommunicationError,
    AdapterConfigurationResult,
)
from broute_meter.config import AppConfig
from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading
from broute_meter.serial.port_detector import PortInfo

ENVIRONMENT_KEYS = (
    "B_ROUTE_ID",
    "B_ROUTE_PASSWORD",
    "B_ROUTE_SERIAL_PORT",
    "B_ROUTE_INSTANT_INTERVAL",
    "B_ROUTE_CUMULATIVE_INTERVAL",
    "B_ROUTE_DATA_DIR",
    "B_ROUTE_LOG_LEVEL",
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
        def __init__(self, _adapter: object, *, scan_max_attempts: int) -> None:
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
