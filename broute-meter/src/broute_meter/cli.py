"""Command line interface for the B-route meter application."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from broute_meter import __version__
from broute_meter.adapter import (
    AdapterConfigurationResult,
    AdapterError,
    MockAdapter,
    RsWsuhaPAdapter,
)
from broute_meter.broute import BRouteSession, BRouteSessionError
from broute_meter.config import AppConfig, ConfigError, load_config, safe_config_summary
from broute_meter.echonet import EchonetFrameError
from broute_meter.logging_config import configure_logging
from broute_meter.meter import SmartMeterClient, SmartMeterError
from broute_meter.models import BRouteConnection
from broute_meter.resilience import RecoveringMeterReader
from broute_meter.scheduler import MeasurementScheduler
from broute_meter.serial.port_detector import (
    PortDetectionError,
    PortInfo,
    find_rs_wsuha_p_ports,
    list_serial_ports,
    resolve_serial_port,
)
from broute_meter.serial.transport import PySerialTransport, TransportError
from broute_meter.storage import CsvMeasurementStorage, StorageError

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_CREDENTIALS_PATH = Path("config/credentials.yaml")
ADAPTER_SETTINGS_MISMATCH_EXIT_CODE = 5

_CREDENTIAL_ENVIRONMENT_KEYS = frozenset(
    {
        "B_ROUTE_ID",
        "B_ROUTE_PASSWORD",
    }
)

CommandHandler = Callable[[argparse.Namespace], int]


def build_parser() -> argparse.ArgumentParser:
    """Build and return the application argument parser."""

    parser = argparse.ArgumentParser(
        prog="broute-meter",
        description="Bルート・スマートメータ計測アプリケーション",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS_PATH,
        help="一般設定YAML（既定: config/settings.yaml）",
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=DEFAULT_CREDENTIALS_PATH,
        help="認証情報YAML（既定: config/credentials.yaml）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="通常計測を開始する")
    run_parser.set_defaults(handler=_run)

    ports_parser = subparsers.add_parser(
        "list-ports",
        help="利用可能なシリアルポートを表示する",
    )
    ports_parser.set_defaults(handler=_list_ports)

    check_parser = subparsers.add_parser(
        "check-config",
        help="設定ファイルと環境変数を検証する",
    )
    check_parser.set_defaults(handler=_check_config)

    connection_parser = subparsers.add_parser(
        "test-connection",
        help="接続と1回のデータ取得を確認する",
    )
    connection_parser.set_defaults(handler=_test_connection)

    setup_parser = subparsers.add_parser(
        "setup-adapter",
        help="アダプター設定を確認し必要時だけ変更する",
    )
    setup_parser.add_argument(
        "--mock",
        action="store_true",
        help="USB通信を行わずMockAdapterで設定フローを確認する",
    )
    setup_parser.set_defaults(handler=_setup_adapter)

    return parser


def _load_application_config(
    args: argparse.Namespace,
    *,
    require_credentials: bool,
    include_credentials: bool = True,
) -> AppConfig:
    environment = None
    credentials_path = args.credentials
    if not include_credentials:
        credentials_path = None
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in _CREDENTIAL_ENVIRONMENT_KEYS
        }

    return load_config(
        settings_path=args.settings,
        credentials_path=credentials_path,
        environ=environment,
        require_credentials=require_credentials,
    )


def _configure_command_logging(config: AppConfig) -> logging.Logger:
    credentials = config.credentials
    secrets = tuple(
        value
        for value in (credentials.b_route_id, credentials.password)
        if value is not None
    )
    return configure_logging(config.logging, secrets=secrets)


def _log_runtime(logger: logging.Logger, command: str) -> None:
    logger.info(
        "アプリケーションコマンド開始 command=%s version=%s python=%s os=%s",
        command,
        __version__,
        platform.python_version(),
        platform.platform(),
    )


def _run(args: argparse.Namespace) -> int:
    config = _load_application_config(args, require_credentials=True)
    logger = _configure_command_logging(config)
    _log_runtime(logger, "run")
    port = _resolve_adapter_port(config)
    logger.info("runに使用するポート: %s", port)
    adapter = _create_rs_wsuha_p_adapter(config, port)
    storage = CsvMeasurementStorage(config.storage.data_directory)
    shutdown_event = threading.Event()
    previous_handlers = _install_stop_signal_handlers(shutdown_event, logger)

    try:
        adapter.open()
        try:
            setup_result = adapter.configure(
                config.adapter.expected_settings,
                write_changes=config.adapter.auto_configure,
            )
            if not setup_result.is_configured:
                mismatched = ", ".join(setup_result.mismatched_settings)
                logger.error(
                    "アダプター設定が期待値と一致しません mismatched=%s",
                    mismatched,
                )
                return ADAPTER_SETTINGS_MISMATCH_EXIT_CODE
            if shutdown_event.is_set():
                logger.info("アダプター設定完了後に終了します")
                return 0

            connection = _connect_broute_until_ready(
                adapter,
                config,
                shutdown_event,
                logger,
            )
            if connection is None:
                logger.info("初期Bルート接続の再試行を終了します")
                return 0
            if shutdown_event.is_set():
                logger.info("Bルート接続処理完了後に終了します")
                return 0

            credentials = config.credentials
            assert credentials.b_route_id is not None
            assert credentials.password is not None

            def reconnect_meter() -> SmartMeterClient:
                logger.warning("シリアルポートとPANA接続を再確立します")
                adapter.close()
                adapter.open()
                reconnect_setup = adapter.configure(
                    config.adapter.expected_settings,
                    write_changes=config.adapter.auto_configure,
                )
                if not reconnect_setup.is_configured:
                    raise AdapterError(
                        "再接続後のアダプター設定が期待値と一致しません。"
                    )
                reconnected = BRouteSession(
                    adapter,
                    scan_max_attempts=config.retry.request_max_attempts,
                ).connect(
                    credentials.b_route_id,
                    credentials.password,
                )
                return SmartMeterClient(
                    adapter,
                    reconnected.smart_meter_ipv6,
                    request_max_attempts=config.retry.request_max_attempts,
                    request_timeout_seconds=config.retry.request_timeout_seconds,
                )

            initial_meter = SmartMeterClient(
                adapter,
                connection.smart_meter_ipv6,
                request_max_attempts=config.retry.request_max_attempts,
                request_timeout_seconds=config.retry.request_timeout_seconds,
            )
            recovering_meter = RecoveringMeterReader(
                initial_meter,
                reconnect_meter,
                recoverable_exceptions=(
                    AdapterError,
                    BRouteSessionError,
                    EchonetFrameError,
                    SmartMeterError,
                    TransportError,
                ),
                reconnect_after_consecutive_failures=(
                    config.retry.reconnect_after_consecutive_failures
                ),
                reconnect_wait_seconds=config.retry.reconnect_wait_seconds,
                stop_event=shutdown_event,
            )
            scheduler = MeasurementScheduler(
                recovering_meter,
                storage,
                instantaneous_interval_seconds=(
                    config.measurement.instantaneous_interval_seconds
                ),
                cumulative_fetch_delay_seconds=(
                    config.measurement.cumulative_fetch_delay_seconds
                ),
                stop_event=shutdown_event,
            )
            logger.info(
                "定期計測を開始します instantaneous_interval=%s "
                "cumulative_fetch_delay=%s",
                config.measurement.instantaneous_interval_seconds,
                config.measurement.cumulative_fetch_delay_seconds,
            )
            scheduler.run()
        except KeyboardInterrupt:
            logger.info("Ctrl+Cによる終了要求を受けました")
            shutdown_event.set()
        except (
            AdapterError,
            BRouteSessionError,
            EchonetFrameError,
            SmartMeterError,
            StorageError,
            TransportError,
        ) as exc:
            if not shutdown_event.is_set():
                raise
            logger.warning(
                "終了要求後に実行中の処理が失敗しました。終了を継続します: %s",
                exc,
            )
        finally:
            storage.close()
            adapter.close()
    finally:
        _restore_signal_handlers(previous_handlers)

    logger.info("アプリケーションコマンド終了 command=run exit_code=0")
    return 0


def _connect_broute_until_ready(
    adapter: RsWsuhaPAdapter,
    config: AppConfig,
    stop_event: threading.Event,
    logger: logging.Logger,
) -> BRouteConnection | None:
    """終了要求までBルート接続シーケンス全体を再試行する。

    ``run`` は無人での継続計測を目的とするため、Wi-SUNの一時的なスキャン
    不成立やPANA接続失敗では終了しない。診断用の ``test-connection`` は
    呼び出さず、単発失敗を利用者へ返す。
    """

    credentials = config.credentials
    assert credentials.b_route_id is not None
    assert credentials.password is not None

    attempt = 0
    while not stop_event.is_set():
        attempt += 1
        try:
            return BRouteSession(
                adapter,
                scan_max_attempts=config.retry.request_max_attempts,
            ).connect(
                credentials.b_route_id,
                credentials.password,
            )
        except (AdapterError, BRouteSessionError, TransportError) as exc:
            logger.warning(
                "初期Bルート接続に失敗しました。再試行します "
                "connection_attempt=%d retry_wait_seconds=%s error=%s",
                attempt,
                config.retry.reconnect_wait_seconds,
                exc,
            )
            if stop_event.wait(config.retry.reconnect_wait_seconds):
                break

    return None


def _install_stop_signal_handlers(
    shutdown_event: threading.Event,
    logger: logging.Logger,
) -> dict[signal.Signals, Any]:
    """SIGINT/SIGTERMで新規計測を停止するハンドラーを設定する。"""

    previous: dict[signal.Signals, Any] = {}

    def request_stop(signum: int, _frame: Any) -> None:
        logger.info("終了シグナルを受信しました signal=%s", signum)
        shutdown_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, request_stop)
    return previous


def _restore_signal_handlers(previous: dict[signal.Signals, Any]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _test_connection(args: argparse.Namespace) -> int:
    config = _load_application_config(args, require_credentials=True)
    logger = _configure_command_logging(config)
    _log_runtime(logger, "test-connection")
    port = _resolve_adapter_port(config)
    logger.info("test-connectionに使用するポート: %s", port)
    adapter = _create_rs_wsuha_p_adapter(config, port)

    adapter.open()
    try:
        setup_result = adapter.configure(
            config.adapter.expected_settings,
            write_changes=config.adapter.auto_configure,
        )
        if not setup_result.is_configured:
            mismatched = ", ".join(setup_result.mismatched_settings)
            logger.error(
                "アダプター設定が期待値と一致しません mismatched=%s",
                mismatched,
            )
            print(
                "アダプター設定が期待値と一致しません: " + mismatched,
                file=sys.stderr,
            )
            return ADAPTER_SETTINGS_MISMATCH_EXIT_CODE

        credentials = config.credentials
        # require_credentials=Trueによる検証後なので、ここでは型を明確化する。
        assert credentials.b_route_id is not None
        assert credentials.password is not None
        connection = BRouteSession(
            adapter,
            scan_max_attempts=config.retry.request_max_attempts,
        ).connect(
            credentials.b_route_id,
            credentials.password,
        )
        meter = SmartMeterClient(
            adapter,
            connection.smart_meter_ipv6,
            request_max_attempts=config.retry.request_max_attempts,
            request_timeout_seconds=config.retry.request_timeout_seconds,
        )
        reading = meter.get_instantaneous_power()
        cumulative = meter.get_cumulative_energy()
    finally:
        adapter.close()

    print(f"接続成功: {connection.smart_meter_ipv6}")
    print(f"計測時刻: {reading.measured_at.isoformat()}")
    print(f"正味瞬時電力: {reading.net_power_w} W")
    print(f"積算計量時刻: {cumulative.metered_at.isoformat()}")
    print(f"正方向積算電力量: {cumulative.forward_total_kwh} kWh")
    if cumulative.reverse_total_kwh is None:
        print("逆方向積算電力量: 非対応または計測データなし")
        logger.warning("逆方向定時積算電力量（EB）は非対応またはデータなしです")
    else:
        print(f"逆方向積算電力量: {cumulative.reverse_total_kwh} kWh")
    logger.info(
        "瞬時電力取得に成功しました ipv6=%s net_power_w=%d measured_at=%s",
        connection.smart_meter_ipv6,
        reading.net_power_w,
        reading.measured_at.isoformat(),
    )
    logger.info(
        "定時積算電力量取得に成功しました metered_at=%s "
        "forward_raw=%d reverse_supported=%s",
        cumulative.metered_at.isoformat(),
        cumulative.forward_raw,
        cumulative.reverse_raw is not None,
    )
    logger.info("アプリケーションコマンド終了 command=test-connection exit_code=0")
    return 0


def _setup_adapter(args: argparse.Namespace) -> int:
    config = _load_application_config(
        args,
        require_credentials=False,
        include_credentials=False,
    )
    logger = _configure_command_logging(config)
    _log_runtime(logger, "setup-adapter")

    if args.mock:
        logger.info("MockAdapterで設定フローを実行します。USB通信は行いません。")
        mock_adapter = MockAdapter(
            {
                "uart_mode": "00",
                "output_mode": "00",
            }
        )
        result = mock_adapter.configure(
            config.adapter.expected_settings,
            write_changes=config.adapter.auto_configure,
        )
        display_target = "実行対象: MockAdapter（USB通信なし）"
    else:
        port = _resolve_adapter_port(config)
        logger.info("RS-WSUHA-P設定確認に使用するポート: %s", port)
        adapter = _create_rs_wsuha_p_adapter(config, port)

        adapter.open()
        try:
            result = adapter.configure(
                config.adapter.expected_settings,
                write_changes=config.adapter.auto_configure,
            )
        finally:
            adapter.close()
        display_target = f"使用ポート: {port}"

    _print_adapter_configuration_result(display_target, result)

    if not result.is_configured:
        mismatched = ", ".join(result.mismatched_settings)
        logger.error(
            "アダプター設定が期待値と一致しません "
            "auto_configure=%s mismatched=%s",
            config.adapter.auto_configure,
            mismatched,
        )
        logger.info(
            "アプリケーションコマンド終了 "
            "command=setup-adapter exit_code=%s reason=settings_mismatch",
            ADAPTER_SETTINGS_MISMATCH_EXIT_CODE,
        )
        return ADAPTER_SETTINGS_MISMATCH_EXIT_CODE

    logger.info(
        "アプリケーションコマンド終了 "
        "command=setup-adapter exit_code=0 changed=%s",
        result.changed,
    )
    return 0


def _resolve_adapter_port(config: AppConfig) -> str:
    """設定優先で、未指定時はRS-WSUHA-P製品名の一意候補を選ぶ。"""

    if config.serial.port is not None:
        return resolve_serial_port(config.serial.port, ())

    ports = list_serial_ports()
    return resolve_serial_port(None, find_rs_wsuha_p_ports(ports))


def _create_rs_wsuha_p_adapter(config: AppConfig, port: str) -> RsWsuhaPAdapter:
    """設定済みのpyserial通信路を持つRS-WSUHA-Pアダプターを作る。"""

    transport = PySerialTransport(
        port=port,
        baudrate=config.serial.baudrate,
        timeout_seconds=config.serial.timeout_seconds,
    )
    return RsWsuhaPAdapter(
        transport,
        response_timeout_seconds=config.retry.request_timeout_seconds,
    )


def _print_adapter_configuration_result(
    display_target: str,
    result: AdapterConfigurationResult,
) -> None:
    """秘密情報を含まないアダプター設定結果を利用者へ表示する。"""

    print(display_target)
    for name, expected in result.expected_settings.items():
        initial = result.initial_settings[name]
        final = result.final_settings[name]
        print(f"{name}: {initial} -> {final} (expected: {expected})")

    if result.changed:
        print(
            "不一致設定だけを書き込み、再読出しで確認しました: "
            + ", ".join(result.changed_settings)
        )
    elif result.is_configured:
        print("アダプター設定は期待値と一致しています。書込みは行っていません。")
    else:
        print(
            "アダプター設定が期待値と一致しません。"
            "auto_configure=falseのため書込みは行っていません: "
            + ", ".join(result.mismatched_settings),
            file=sys.stderr,
        )


def _list_ports(_args: argparse.Namespace) -> int:
    ports = list_serial_ports()
    if not ports:
        print("利用可能なシリアルポートはありません。")
        return 0

    rows = [_port_row(port) for port in ports]
    headers = ("ポート", "製品名", "メーカー", "VID", "PID", "シリアル番号")
    _print_table(headers, rows)
    return 0


def _port_row(port: PortInfo) -> tuple[str, ...]:
    return (
        port.device,
        port.product or "",
        port.manufacturer or "",
        _format_usb_id(port.vid),
        _format_usb_id(port.pid),
        port.serial_number or "",
    )


def _format_usb_id(value: int | None) -> str:
    return "" if value is None else f"{value:04X}"


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _check_config(args: argparse.Namespace) -> int:
    config = _load_application_config(args, require_credentials=True)
    logger = _configure_command_logging(config)
    _log_runtime(logger, "check-config")
    logger.info("設定検証に成功しました")

    summary = _json_safe(safe_config_summary(config))
    print("設定は有効です。")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    logger.info("アプリケーションコマンド終了 command=check-config exit_code=0")
    return 0


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.handler

    try:
        return handler(args)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except PortDetectionError as exc:
        print(f"ポート検出エラー: {exc}", file=sys.stderr)
        return 3
    except (
        AdapterError,
        BRouteSessionError,
        EchonetFrameError,
        SmartMeterError,
        StorageError,
        TransportError,
    ) as exc:
        logging.getLogger("broute_meter").exception("アダプター通信エラー")
        print(f"アダプター通信エラー: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        logging.getLogger("broute_meter").exception("OS入出力エラー")
        print(f"入出力エラー: {exc}", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger("broute_meter").exception("予期しない例外")
        print("予期しないエラーが発生しました。ログを確認してください。", file=sys.stderr)
        return 1
