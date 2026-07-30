"""Unit tests for configuration loading and validation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from broute_meter.config import (
    AppConfig,
    BRouteCredentials,
    ConfigError,
    MeasurementConfig,
    MqttConfig,
    SerialConfig,
    load_config,
    mask_b_route_id,
    safe_config_summary,
    validate_config,
)


def test_mqtt_yaml_and_environment_are_loaded_with_safe_summary(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        settings="""
mqtt:
  enabled: false
  host: broker.example
  port: 1884
  device_id: test-meter
  topic_prefix: test
  client_id: test-client
""",
        environ={
            "MQTT_ENABLED": "true",
            "MQTT_PORT": "1885",
            "MQTT_USERNAME": "mqtt-user",
            "MQTT_PASSWORD": "mqtt-secret",
        },
    )

    assert config.mqtt == MqttConfig(
        enabled=True,
        host="broker.example",
        port=1885,
        device_id="test-meter",
        topic_prefix="test",
        client_id="test-client",
        username="mqtt-user",
        password="mqtt-secret",
    )
    summary = safe_config_summary(config)
    assert summary["mqtt"]["password_configured"] is True
    assert "mqtt-secret" not in str(summary)


@pytest.mark.parametrize(
    "environ",
    [
        {"MQTT_ENABLED": "maybe"},
        {"MQTT_PORT": "0"},
        {"MQTT_DEVICE_ID": "has/slash"},
        {"MQTT_TOPIC_PREFIX": "/omk"},
        {"MQTT_USERNAME": "only-user"},
    ],
)
def test_invalid_mqtt_environment_is_rejected(tmp_path: Path, environ: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, environ=environ)


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _load(
    tmp_path: Path,
    *,
    settings: str = "",
    credentials: str = "",
    environ: dict[str, str] | None = None,
    require_credentials: bool = False,
) -> AppConfig:
    settings_path = _write(tmp_path / "settings.yaml", settings)
    credentials_path = _write(tmp_path / "credentials.yaml", credentials)
    return load_config(
        settings_path=settings_path,
        credentials_path=credentials_path,
        environ={} if environ is None else environ,
        require_credentials=require_credentials,
    )


def test_load_config_uses_defaults_for_empty_files(tmp_path: Path) -> None:
    config = _load(tmp_path)

    assert config.measurement.instantaneous_interval_seconds == 10
    assert config.measurement.cumulative_check_interval_seconds == 60
    assert config.measurement.cumulative_fetch_delay_seconds == 5
    assert config.serial == SerialConfig()
    assert config.adapter.auto_configure is True
    assert dict(config.adapter.expected_settings) == {
        "uart_mode": "80",
        "output_mode": "01",
    }
    assert config.retry.request_max_attempts == 3
    assert config.storage.data_directory == Path("../data/broute-meter")
    assert config.logging.level == "INFO"
    assert config.logging.directory == Path("../logs/broute-meter")
    assert config.credentials.b_route_id is None
    assert config.credentials.password is None


def test_load_config_accepts_missing_yaml_files(tmp_path: Path) -> None:
    config = load_config(
        settings_path=tmp_path / "missing-settings.yaml",
        credentials_path=tmp_path / "missing-credentials.yaml",
        environ={},
    )

    assert config == AppConfig()


def test_yaml_values_override_defaults(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        settings="""
measurement:
  instantaneous_interval_seconds: 15
  cumulative_check_interval_seconds: 45
  cumulative_fetch_delay_seconds: 7
serial:
  port: /dev/serial/by-id/example-adapter
  baudrate: 57600
  timeout_seconds: 2.5
adapter:
  auto_configure: false
  expected_settings:
    uart_mode: "81"
retry:
  request_timeout_seconds: 4
  request_max_attempts: 2
  reconnect_after_consecutive_failures: 6
  reconnect_wait_seconds: 20
storage:
  data_directory: ./measurements
logging:
  level: debug
  directory: ./diagnostics
""",
        credentials="""
b_route:
  id: FILE-ID-EXAMPLE
  password: FILE-PASSWORD-EXAMPLE
""",
        require_credentials=True,
    )

    assert config.measurement == MeasurementConfig(15, 45, 7)
    assert config.serial.port == "/dev/serial/by-id/example-adapter"
    assert config.serial.baudrate == 57_600
    assert config.serial.timeout_seconds == 2.5
    assert config.adapter.auto_configure is False
    assert dict(config.adapter.expected_settings) == {
        "uart_mode": "81",
        "output_mode": "01",
    }
    assert config.retry.request_timeout_seconds == 4
    assert config.retry.request_max_attempts == 2
    assert config.retry.reconnect_after_consecutive_failures == 6
    assert config.retry.reconnect_wait_seconds == 20
    assert config.storage.data_directory == Path("measurements")
    assert config.logging.level == "DEBUG"
    assert config.logging.directory == Path("diagnostics")
    assert config.credentials.b_route_id == "FILE-ID-EXAMPLE"
    assert config.credentials.password == "FILE-PASSWORD-EXAMPLE"


def test_all_supported_environment_variables_override_yaml(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        settings="""
measurement:
  instantaneous_interval_seconds: 20
  cumulative_check_interval_seconds: 120
  cumulative_fetch_delay_seconds: 9
serial:
  port: COM2
storage:
  data_directory: ./from-file
logging:
  level: WARNING
""",
        credentials="""
b_route:
  id: FILE-ID
  password: FILE-PASSWORD
""",
        environ={
            "B_ROUTE_ID": "ENVIRONMENT-ID",
            "B_ROUTE_PASSWORD": "ENVIRONMENT-PASSWORD",
            "B_ROUTE_SERIAL_PORT": "/dev/ttyUSB7",
            "B_ROUTE_INSTANT_INTERVAL": "30",
            "B_ROUTE_CUMULATIVE_INTERVAL": "90",
            "B_ROUTE_CUMULATIVE_DELAY": "11",
            "B_ROUTE_DATA_DIR": "./from-environment",
            "B_ROUTE_LOG_LEVEL": "error",
            "OMK_DATA_DIR": "./omk-data",
            "OMK_LOG_DIR": "./omk-logs",
        },
        require_credentials=True,
    )

    assert config.credentials.b_route_id == "ENVIRONMENT-ID"
    assert config.credentials.password == "ENVIRONMENT-PASSWORD"
    assert config.serial.port == "/dev/ttyUSB7"
    assert config.measurement.instantaneous_interval_seconds == 30
    assert config.measurement.cumulative_check_interval_seconds == 90
    assert config.measurement.cumulative_fetch_delay_seconds == 11
    assert config.storage.data_directory == Path("omk-data")
    assert config.logging.level == "ERROR"
    assert config.logging.directory == Path("omk-logs")


def test_valid_environment_can_replace_invalid_lower_priority_scalar(
    tmp_path: Path,
) -> None:
    config = _load(
        tmp_path,
        settings="""
measurement:
  instantaneous_interval_seconds: not-an-integer
""",
        environ={"B_ROUTE_INSTANT_INTERVAL": "10"},
    )

    assert config.measurement.instantaneous_interval_seconds == 10


@pytest.mark.parametrize(
    "environment_key",
    [
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
    ],
)
@pytest.mark.parametrize("empty_value", ["", "   "])
def test_present_but_empty_environment_value_is_an_error(
    tmp_path: Path,
    environment_key: str,
    empty_value: str,
) -> None:
    with pytest.raises(ConfigError) as caught:
        _load(
            tmp_path,
            credentials="""
b_route:
  id: SAFE-FILE-ID
  password: SAFE-FILE-PASSWORD
""",
            environ={environment_key: empty_value},
        )

    assert environment_key in str(caught.value)
    assert "SAFE-FILE-ID" not in str(caught.value)
    assert "SAFE-FILE-PASSWORD" not in str(caught.value)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("instantaneous_interval_seconds", 9),
        ("instantaneous_interval_seconds", 0),
        ("instantaneous_interval_seconds", -1),
        ("cumulative_check_interval_seconds", 0),
        ("cumulative_check_interval_seconds", -1),
        ("cumulative_fetch_delay_seconds", 0),
        ("cumulative_fetch_delay_seconds", 1800),
    ],
)
def test_invalid_measurement_intervals_are_rejected(
    field_name: str,
    value: int,
) -> None:
    measurement = replace(MeasurementConfig(), **{field_name: value})

    with pytest.raises(ConfigError) as caught:
        validate_config(replace(AppConfig(), measurement=measurement))

    assert field_name in str(caught.value)


@pytest.mark.parametrize(
    ("instantaneous", "cumulative"),
    [(10, 1), (11, 60), (3600, 3600)],
)
def test_valid_measurement_interval_boundaries(
    instantaneous: int,
    cumulative: int,
) -> None:
    config = replace(
        AppConfig(),
        measurement=MeasurementConfig(instantaneous, cumulative),
    )

    validate_config(config)


@pytest.mark.parametrize(
    "port",
    [
        "COM1",
        "COM5",
        "com12",
        "/dev/ttyUSB0",
        "/dev/ttyACM0",
        "/dev/ttyAMA0",
        "/dev/serial/by-id/usb-RS-WSUHA-P-example",
    ],
)
def test_cross_platform_serial_port_syntax_is_accepted(port: str) -> None:
    validate_config(replace(AppConfig(), serial=SerialConfig(port=port)))


@pytest.mark.parametrize(
    "port",
    [
        "",
        "COM0",
        "COM",
        "COM-1",
        " COM5 ",
        "ttyUSB0",
        "/tmp/ttyUSB0",
        "/dev/",
        "/dev/../etc",
        "/dev//ttyUSB0",
        "/dev/ttyUSB0\n",
        "/dev/ttyUSB0\x00",
    ],
)
def test_invalid_serial_port_syntax_is_rejected(port: str) -> None:
    config = replace(AppConfig(), serial=SerialConfig(port=port))

    with pytest.raises(ConfigError) as caught:
        validate_config(config)

    assert "serial.port" in str(caught.value)


@pytest.mark.parametrize(
    "settings",
    [
        "unknown_section: {}\n",
        "measurement:\n  instant_interval_typo: 10\n",
    ],
)
def test_unknown_settings_keys_are_rejected(
    tmp_path: Path,
    settings: str,
) -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        _load(tmp_path, settings=settings)


def test_unknown_credentials_key_is_not_echoed(tmp_path: Path) -> None:
    secret_misplaced_as_key = "DO-NOT-PRINT-THIS-SECRET"

    with pytest.raises(ConfigError) as caught:
        _load(
            tmp_path,
            credentials=f"""
b_route:
  {secret_misplaced_as_key}: value
""",
        )

    assert secret_misplaced_as_key not in str(caught.value)


def test_adapter_expected_settings_accepts_adapter_specific_keys(
    tmp_path: Path,
) -> None:
    config = _load(
        tmp_path,
        settings="""
adapter:
  expected_settings:
    future_adapter_setting: "enabled"
""",
    )

    assert config.adapter.expected_settings["future_adapter_setting"] == "enabled"


@pytest.mark.parametrize(
    "content",
    [
        "- list-root\n",
        "measurement: 10\n",
        "adapter:\n  expected_settings: 80\n",
    ],
)
def test_non_mapping_yaml_structures_are_rejected(
    tmp_path: Path,
    content: str,
) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, settings=content)


def test_invalid_yaml_error_does_not_include_source_excerpt(
    tmp_path: Path,
) -> None:
    secret_canary = "SECRET-SOURCE-CANARY"

    with pytest.raises(ConfigError) as caught:
        _load(
            tmp_path,
            credentials=f"""
b_route:
  password: [{secret_canary}
""",
        )

    assert secret_canary not in str(caught.value)


def test_non_utf8_yaml_is_reported_as_config_error(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(
            settings_path=settings_path,
            credentials_path=None,
            environ={},
        )


@pytest.mark.parametrize(
    "settings",
    [
        "measurement:\n  instantaneous_interval_seconds: true\n",
        "serial:\n  baudrate: false\n",
        "adapter:\n  auto_configure: 1\n",
        "logging:\n  level: 20\n",
    ],
)
def test_scalar_types_are_strict(tmp_path: Path, settings: str) -> None:
    with pytest.raises(ConfigError):
        _load(tmp_path, settings=settings)


def test_require_credentials_rejects_missing_or_partial_values(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError) as missing:
        _load(tmp_path, require_credentials=True)
    assert "b_route.id is required" in str(missing.value)
    assert "b_route.password is required" in str(missing.value)

    with pytest.raises(ConfigError) as partial:
        _load(
            tmp_path,
            credentials="""
b_route:
  id: PARTIAL-ID-CANARY
""",
            require_credentials=True,
        )
    assert "b_route.password is required" in str(partial.value)
    assert "PARTIAL-ID-CANARY" not in str(partial.value)


def test_credentials_never_appear_in_repr_or_safe_summary(
    tmp_path: Path,
) -> None:
    identifier = "1234567890ABCDEF1234567890ABCDEF"
    password = "PASSWORD-SECRET-CANARY"
    config = _load(
        tmp_path,
        credentials=f"""
b_route:
  id: {identifier}
  password: {password}
""",
        require_credentials=True,
    )

    representation = repr(config)
    summary = safe_config_summary(config)
    summary_text = repr(summary)

    assert identifier not in representation
    assert password not in representation
    assert identifier not in summary_text
    assert password not in summary_text
    assert summary["b_route"] == {
        "id": "1234************************CDEF",
        "password_configured": True,
    }


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        (None, "<not configured>"),
        ("", "<not configured>"),
        ("A", "*"),
        ("12345678", "********"),
        ("123456789", "1234*6789"),
        (
            "1234567890ABCDEF1234567890ABCDEF",
            "1234************************CDEF",
        ),
    ],
)
def test_mask_b_route_id(
    identifier: str | None,
    expected: str,
) -> None:
    assert mask_b_route_id(identifier) == expected


def test_environment_number_error_names_key_but_not_value(
    tmp_path: Path,
) -> None:
    invalid_value = "SECRET-LIKE-NON-NUMBER"

    with pytest.raises(ConfigError) as caught:
        _load(
            tmp_path,
            environ={"B_ROUTE_INSTANT_INTERVAL": invalid_value},
        )

    message = str(caught.value)
    assert "B_ROUTE_INSTANT_INTERVAL" in message
    assert invalid_value not in message


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_timeouts_are_rejected(value: float) -> None:
    serial = replace(SerialConfig(), timeout_seconds=value)

    with pytest.raises(ConfigError, match="serial.timeout_seconds"):
        validate_config(replace(AppConfig(), serial=serial))


def test_safe_summary_contains_non_secret_effective_settings() -> None:
    config = replace(
        AppConfig(),
        serial=SerialConfig(port="COM7", baudrate=9_600, timeout_seconds=2),
        credentials=BRouteCredentials(
            b_route_id="ABCDEFGHIJKL",
            password="configured",
        ),
    )

    summary = safe_config_summary(config)

    assert summary["serial"] == {
        "port": "COM7",
        "baudrate": 9_600,
        "timeout_seconds": 2,
    }
    assert summary["b_route"] == {
        "id": "ABCD****IJKL",
        "password_configured": True,
    }
