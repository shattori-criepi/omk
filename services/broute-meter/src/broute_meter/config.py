"""Application configuration loading and validation.

Configuration is composed in this order:

1. application defaults
2. general and credential YAML files
3. supported environment variables

Only the final, highest-priority value is type-checked.  This permits a valid
environment variable to replace an invalid lower-priority scalar, while YAML
structure and unknown keys are always checked strictly.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_CREDENTIALS_PATH = Path("config/credentials.yaml")
DEFAULT_EXPECTED_SETTINGS: Mapping[str, str] = MappingProxyType(
    {
        "uart_mode": "80",
        "output_mode": "01",
    }
)
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

_WINDOWS_PORT_PATTERN = re.compile(r"COM[1-9][0-9]*\Z", re.IGNORECASE)
_SUPPORTED_ENVIRONMENT_KEYS = frozenset(
    {
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
        "MQTT_ENABLED",
        "MQTT_HOST",
        "MQTT_PORT",
        "MQTT_DEVICE_ID",
        "MQTT_TOPIC_PREFIX",
        "MQTT_CLIENT_ID",
        "MQTT_USERNAME",
        "MQTT_PASSWORD",
    }
)

_SETTINGS_KEYS = frozenset(
    {
        "measurement",
        "serial",
        "adapter",
        "retry",
        "storage",
        "logging",
        "mqtt",
    }
)
_SECTION_KEYS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "measurement": frozenset(
            {
                "instantaneous_interval_seconds",
                "cumulative_check_interval_seconds",
                "cumulative_fetch_delay_seconds",
            }
        ),
        "serial": frozenset({"port", "baudrate", "timeout_seconds"}),
        "adapter": frozenset({"auto_configure", "expected_settings"}),
        "retry": frozenset(
            {
                "request_timeout_seconds",
                "request_max_attempts",
                "reconnect_after_consecutive_failures",
                "reconnect_wait_seconds",
            }
        ),
        "storage": frozenset({"data_directory"}),
        "logging": frozenset({"level", "directory"}),
        "mqtt": frozenset(
            {
                "enabled",
                "host",
                "port",
                "device_id",
                "topic_prefix",
                "client_id",
                "username",
                "password",
            }
        ),
    }
)


class ConfigError(ValueError):
    """Raised when configuration cannot be loaded or validated safely."""

    def __init__(self, errors: str | Iterable[str]) -> None:
        messages = (errors,) if isinstance(errors, str) else tuple(errors)
        if not messages:
            messages = ("Configuration is invalid",)
        self.errors = messages
        super().__init__("Configuration error: " + "; ".join(messages))


@dataclass(frozen=True, slots=True)
class MeasurementConfig:
    """Measurement polling intervals."""

    instantaneous_interval_seconds: int = 10
    cumulative_check_interval_seconds: int = 60
    cumulative_fetch_delay_seconds: int = 5


@dataclass(frozen=True, slots=True)
class SerialConfig:
    """Serial transport settings."""

    port: str | None = None
    baudrate: int = 115_200
    timeout_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    """B-route adapter setup settings."""

    auto_configure: bool = True
    expected_settings: Mapping[str, str] = field(
        default_factory=lambda: DEFAULT_EXPECTED_SETTINGS
    )


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Request retry and reconnection settings."""

    request_timeout_seconds: float = 5.0
    request_max_attempts: int = 3
    reconnect_after_consecutive_failures: int = 5
    reconnect_wait_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class StorageConfig:
    """Measurement storage settings."""

    data_directory: Path = Path("../../data/broute-meter")


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    """Application logging settings."""

    level: str = "INFO"
    directory: Path = Path("../../logs/broute-meter")


@dataclass(frozen=True, slots=True, repr=False)
class MqttConfig:
    """MQTT real-time delivery settings."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 1883
    device_id: str = "broute-001"
    topic_prefix: str = "omk"
    client_id: str = "omk-broute-001"
    username: str | None = None
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True, repr=False)
class BRouteCredentials:
    """B-route credentials.

    ``repr`` is deliberately disabled so credentials cannot be disclosed by a
    dataclass representation in an exception or debug log.
    """

    b_route_id: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Complete application configuration."""

    measurement: MeasurementConfig = field(default_factory=MeasurementConfig)
    serial: SerialConfig = field(default_factory=SerialConfig)
    adapter: AdapterConfig = field(default_factory=AdapterConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    credentials: BRouteCredentials = field(
        default_factory=BRouteCredentials,
        repr=False,
    )


def load_config(
    settings_path: Path | None = DEFAULT_SETTINGS_PATH,
    credentials_path: Path | None = DEFAULT_CREDENTIALS_PATH,
    environ: Mapping[str, str] | None = None,
    require_credentials: bool = False,
) -> AppConfig:
    """Load, merge, and validate application configuration.

    Missing YAML files are treated as absent configuration.  This allows the
    application defaults to support commands such as ``list-ports`` while
    communication commands can set ``require_credentials=True``.

    Args:
        settings_path: General settings YAML path, or ``None`` to skip it.
        credentials_path: Credential YAML path, or ``None`` to skip it.
        environ: Environment mapping. ``os.environ`` is used when omitted.
        require_credentials: Require both B-route ID and password when true.

    Returns:
        A validated immutable configuration model.

    Raises:
        ConfigError: If a file, key, value, or required credential is invalid.
    """

    settings = _read_yaml_mapping(settings_path, "settings")
    credentials = _read_yaml_mapping(credentials_path, "credentials")

    _validate_settings_structure(settings)
    _validate_credentials_structure(credentials)

    values = _default_values()
    _merge_settings(values, settings)
    _merge_credentials(values, credentials)
    _merge_environment(values, os.environ if environ is None else environ)

    config = _build_config(values)
    validate_config(config, require_credentials=require_credentials)
    return config


def validate_config(
    config: AppConfig,
    *,
    require_credentials: bool = False,
) -> None:
    """Validate a configuration model without exposing supplied values."""

    errors: list[str] = []

    if not _is_int(config.measurement.instantaneous_interval_seconds):
        errors.append("measurement.instantaneous_interval_seconds must be an integer")
    elif config.measurement.instantaneous_interval_seconds < 10:
        errors.append(
            "measurement.instantaneous_interval_seconds must be at least 10"
        )

    if not _is_int(config.measurement.cumulative_check_interval_seconds):
        errors.append(
            "measurement.cumulative_check_interval_seconds must be an integer"
        )
    elif config.measurement.cumulative_check_interval_seconds <= 0:
        errors.append(
            "measurement.cumulative_check_interval_seconds must be greater than 0"
        )

    if not _is_int(config.measurement.cumulative_fetch_delay_seconds):
        errors.append("measurement.cumulative_fetch_delay_seconds must be an integer")
    elif not 1 <= config.measurement.cumulative_fetch_delay_seconds < 1800:
        errors.append(
            "measurement.cumulative_fetch_delay_seconds must be between 1 and 1799"
        )

    if config.serial.port is not None:
        if not isinstance(config.serial.port, str):
            errors.append("serial.port must be a string or null")
        elif not _is_valid_serial_port(config.serial.port):
            errors.append(
                "serial.port must be COMn or an absolute path below /dev"
            )

    if not _is_int(config.serial.baudrate):
        errors.append("serial.baudrate must be an integer")
    elif config.serial.baudrate <= 0:
        errors.append("serial.baudrate must be greater than 0")

    if not _is_number(config.serial.timeout_seconds):
        errors.append("serial.timeout_seconds must be a number")
    elif config.serial.timeout_seconds <= 0:
        errors.append("serial.timeout_seconds must be greater than 0")

    if not isinstance(config.adapter.auto_configure, bool):
        errors.append("adapter.auto_configure must be a boolean")

    if not isinstance(config.adapter.expected_settings, Mapping):
        errors.append("adapter.expected_settings must be a mapping")
    else:
        for key, value in config.adapter.expected_settings.items():
            if (
                not isinstance(key, str)
                or not key.strip()
                or not isinstance(value, str)
                or not value.strip()
            ):
                errors.append(
                    "adapter.expected_settings keys and values "
                    "must be non-empty strings"
                )
                break

    _validate_positive_number(
        errors,
        "retry.request_timeout_seconds",
        config.retry.request_timeout_seconds,
    )
    _validate_positive_integer(
        errors,
        "retry.request_max_attempts",
        config.retry.request_max_attempts,
    )
    _validate_positive_integer(
        errors,
        "retry.reconnect_after_consecutive_failures",
        config.retry.reconnect_after_consecutive_failures,
    )
    _validate_positive_number(
        errors,
        "retry.reconnect_wait_seconds",
        config.retry.reconnect_wait_seconds,
    )

    if not isinstance(config.storage.data_directory, Path):
        errors.append("storage.data_directory must be a path")

    if not isinstance(config.logging.level, str):
        errors.append("logging.level must be a string")
    elif config.logging.level.upper() not in VALID_LOG_LEVELS:
        errors.append("logging.level is not supported")

    if not isinstance(config.logging.directory, Path):
        errors.append("logging.directory must be a path")

    mqtt = config.mqtt
    if not isinstance(mqtt.enabled, bool):
        errors.append("mqtt.enabled must be a boolean")
    if not isinstance(mqtt.host, str) or not mqtt.host.strip():
        errors.append("mqtt.host must be a non-empty string")
    if not _is_int(mqtt.port) or not 1 <= mqtt.port <= 65535:
        errors.append("mqtt.port must be an integer between 1 and 65535")
    if not isinstance(mqtt.device_id, str) or not mqtt.device_id.strip():
        errors.append("mqtt.device_id must be a non-empty string")
    elif "/" in mqtt.device_id:
        errors.append("mqtt.device_id must not contain '/'")
    if not isinstance(mqtt.topic_prefix, str) or not mqtt.topic_prefix.strip():
        errors.append("mqtt.topic_prefix must be a non-empty string")
    elif mqtt.topic_prefix.strip("/") != mqtt.topic_prefix:
        errors.append("mqtt.topic_prefix must not start or end with '/'")
    if not isinstance(mqtt.client_id, str) or not mqtt.client_id.strip():
        errors.append("mqtt.client_id must be a non-empty string")
    for field_name, value in (
        ("username", mqtt.username),
        ("password", mqtt.password),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors.append(f"mqtt.{field_name} must be a non-empty string or null")
    if (mqtt.username is None) != (mqtt.password is None):
        errors.append(
            "mqtt.username and mqtt.password must both be configured or both be absent"
        )

    identifier = config.credentials.b_route_id
    password = config.credentials.password
    if identifier is not None and (
        not isinstance(identifier, str) or not identifier.strip()
    ):
        errors.append("b_route.id must be a non-empty string")
    if password is not None and (
        not isinstance(password, str) or not password.strip()
    ):
        errors.append("b_route.password must be a non-empty string")

    if require_credentials:
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append("b_route.id is required")
        if not isinstance(password, str) or not password.strip():
            errors.append("b_route.password is required")

    if errors:
        raise ConfigError(errors)


def mask_b_route_id(value: str | None) -> str:
    """Return a display-safe representation of a B-route ID."""

    if value is None or not value:
        return "<not configured>"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def safe_config_summary(config: AppConfig) -> dict[str, object]:
    """Return configuration suitable for CLI display and diagnostic logging."""

    return {
        "measurement": {
            "instantaneous_interval_seconds": (
                config.measurement.instantaneous_interval_seconds
            ),
            "cumulative_check_interval_seconds": (
                config.measurement.cumulative_check_interval_seconds
            ),
            "cumulative_fetch_delay_seconds": (
                config.measurement.cumulative_fetch_delay_seconds
            ),
        },
        "serial": {
            "port": config.serial.port,
            "baudrate": config.serial.baudrate,
            "timeout_seconds": config.serial.timeout_seconds,
        },
        "adapter": {
            "auto_configure": config.adapter.auto_configure,
            "expected_settings": dict(config.adapter.expected_settings),
        },
        "retry": {
            "request_timeout_seconds": config.retry.request_timeout_seconds,
            "request_max_attempts": config.retry.request_max_attempts,
            "reconnect_after_consecutive_failures": (
                config.retry.reconnect_after_consecutive_failures
            ),
            "reconnect_wait_seconds": config.retry.reconnect_wait_seconds,
        },
        "storage": {
            "data_directory": str(config.storage.data_directory),
        },
        "logging": {
            "level": config.logging.level,
            "directory": str(config.logging.directory),
        },
        "mqtt": {
            "enabled": config.mqtt.enabled,
            "host": config.mqtt.host,
            "port": config.mqtt.port,
            "device_id": config.mqtt.device_id,
            "topic_prefix": config.mqtt.topic_prefix,
            "client_id": config.mqtt.client_id,
            "username_configured": config.mqtt.username is not None,
            "password_configured": config.mqtt.password is not None,
        },
        "b_route": {
            "id": mask_b_route_id(config.credentials.b_route_id),
            "password_configured": bool(
                isinstance(config.credentials.password, str)
                and config.credentials.password.strip()
            ),
        },
    }


def _default_values() -> dict[str, dict[str, Any]]:
    return {
        "measurement": {
            "instantaneous_interval_seconds": 10,
            "cumulative_check_interval_seconds": 60,
            "cumulative_fetch_delay_seconds": 5,
        },
        "serial": {
            "port": None,
            "baudrate": 115_200,
            "timeout_seconds": 5,
        },
        "adapter": {
            "auto_configure": True,
            "expected_settings": dict(DEFAULT_EXPECTED_SETTINGS),
        },
        "retry": {
            "request_timeout_seconds": 5,
            "request_max_attempts": 3,
            "reconnect_after_consecutive_failures": 5,
            "reconnect_wait_seconds": 30,
        },
        "storage": {"data_directory": "../../data/broute-meter"},
        "logging": {"level": "INFO", "directory": "../../logs/broute-meter"},
        "mqtt": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 1883,
            "device_id": "broute-001",
            "topic_prefix": "omk",
            "client_id": "omk-broute-001",
            "username": None,
            "password": None,
        },
        "b_route": {"id": None, "password": None},
    }


def _read_yaml_mapping(
    path: Path | None,
    label: str,
) -> Mapping[str, Any]:
    if path is None:
        return {}
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except UnicodeError:
        raise ConfigError(f"{label} YAML must be UTF-8") from None
    except OSError:
        raise ConfigError(f"Unable to read {label} YAML file") from None

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        # PyYAML messages can contain source excerpts.  Do not attach them,
        # particularly for the credentials file.
        raise ConfigError(f"{label} YAML is invalid") from None

    if loaded is None:
        return {}
    if not isinstance(loaded, Mapping):
        raise ConfigError(f"{label} YAML root must be a mapping")
    return loaded


def _validate_settings_structure(settings: Mapping[str, Any]) -> None:
    _reject_unknown_keys(settings, _SETTINGS_KEYS, "settings")
    for section_name, allowed_keys in _SECTION_KEYS.items():
        if section_name not in settings:
            continue
        section = settings[section_name]
        if not isinstance(section, Mapping):
            raise ConfigError(f"settings.{section_name} must be a mapping")
        _reject_unknown_keys(
            section,
            allowed_keys,
            f"settings.{section_name}",
        )

    adapter = settings.get("adapter")
    if isinstance(adapter, Mapping) and "expected_settings" in adapter:
        expected = adapter["expected_settings"]
        if not isinstance(expected, Mapping):
            raise ConfigError(
                "settings.adapter.expected_settings must be a mapping"
            )


def _validate_credentials_structure(credentials: Mapping[str, Any]) -> None:
    _reject_unknown_keys(credentials, frozenset({"b_route"}), "credentials")
    if "b_route" not in credentials:
        return
    b_route = credentials["b_route"]
    if not isinstance(b_route, Mapping):
        raise ConfigError("credentials.b_route must be a mapping")
    _reject_unknown_keys(
        b_route,
        frozenset({"id", "password"}),
        "credentials.b_route",
    )


def _reject_unknown_keys(
    values: Mapping[object, object],
    allowed: frozenset[str],
    location: str,
) -> None:
    if any(not isinstance(key, str) or key not in allowed for key in values):
        # Do not echo an unknown key: a malformed credentials file could have
        # placed a secret in the key position.
        raise ConfigError(f"{location} contains an unknown key")


def _merge_settings(
    values: dict[str, dict[str, Any]],
    settings: Mapping[str, Any],
) -> None:
    for section_name in _SECTION_KEYS:
        section = settings.get(section_name)
        if not isinstance(section, Mapping):
            continue
        if section_name == "adapter" and "expected_settings" in section:
            expected = section["expected_settings"]
            if isinstance(expected, Mapping):
                values["adapter"]["expected_settings"].update(expected)
            section = {
                key: value
                for key, value in section.items()
                if key != "expected_settings"
            }
        values[section_name].update(section)


def _merge_credentials(
    values: dict[str, dict[str, Any]],
    credentials: Mapping[str, Any],
) -> None:
    b_route = credentials.get("b_route")
    if isinstance(b_route, Mapping):
        values["b_route"].update(b_route)


def _merge_environment(
    values: dict[str, dict[str, Any]],
    environ: Mapping[str, str],
) -> None:
    for key in _SUPPORTED_ENVIRONMENT_KEYS:
        if key not in environ:
            continue
        value = environ[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{key} must not be empty")

    environment_targets: tuple[tuple[str, str, str], ...] = (
        (
            "B_ROUTE_ID",
            "b_route",
            "id",
        ),
        (
            "B_ROUTE_PASSWORD",
            "b_route",
            "password",
        ),
        (
            "B_ROUTE_SERIAL_PORT",
            "serial",
            "port",
        ),
        (
            "B_ROUTE_INSTANT_INTERVAL",
            "measurement",
            "instantaneous_interval_seconds",
        ),
        (
            "B_ROUTE_CUMULATIVE_INTERVAL",
            "measurement",
            "cumulative_check_interval_seconds",
        ),
        (
            "B_ROUTE_CUMULATIVE_DELAY",
            "measurement",
            "cumulative_fetch_delay_seconds",
        ),
        (
            "B_ROUTE_DATA_DIR",
            "storage",
            "data_directory",
        ),
        (
            "OMK_DATA_DIR",
            "storage",
            "data_directory",
        ),
        (
            "B_ROUTE_LOG_LEVEL",
            "logging",
            "level",
        ),
        (
            "OMK_LOG_DIR",
            "logging",
            "directory",
        ),
        ("MQTT_ENABLED", "mqtt", "enabled"),
        ("MQTT_HOST", "mqtt", "host"),
        ("MQTT_PORT", "mqtt", "port"),
        ("MQTT_DEVICE_ID", "mqtt", "device_id"),
        ("MQTT_TOPIC_PREFIX", "mqtt", "topic_prefix"),
        ("MQTT_CLIENT_ID", "mqtt", "client_id"),
        ("MQTT_USERNAME", "mqtt", "username"),
        ("MQTT_PASSWORD", "mqtt", "password"),
    )
    for environment_key, section, field_name in environment_targets:
        if environment_key in environ:
            environment_value: object = environ[environment_key]
            if environment_key in {
                "B_ROUTE_INSTANT_INTERVAL",
                "B_ROUTE_CUMULATIVE_INTERVAL",
                "B_ROUTE_CUMULATIVE_DELAY",
                "MQTT_PORT",
            }:
                try:
                    environment_value = int(environ[environment_key], 10)
                except ValueError:
                    raise ConfigError(
                        f"{environment_key} must be an integer"
                    ) from None
            elif environment_key == "MQTT_ENABLED":
                environment_value = _environment_boolean(environ[environment_key])
            values[section][field_name] = environment_value


def _build_config(values: Mapping[str, Mapping[str, Any]]) -> AppConfig:
    instantaneous = _integer_value(
        values["measurement"]["instantaneous_interval_seconds"],
        "measurement.instantaneous_interval_seconds",
    )
    cumulative = _integer_value(
        values["measurement"]["cumulative_check_interval_seconds"],
        "measurement.cumulative_check_interval_seconds",
    )
    cumulative_delay = _integer_value(
        values["measurement"]["cumulative_fetch_delay_seconds"],
        "measurement.cumulative_fetch_delay_seconds",
    )

    port_value = values["serial"]["port"]
    if port_value is not None and not isinstance(port_value, str):
        raise ConfigError("serial.port must be a string or null")

    expected_settings = values["adapter"]["expected_settings"]
    if not isinstance(expected_settings, Mapping):
        raise ConfigError("adapter.expected_settings must be a mapping")
    converted_expected_settings: dict[str, str] = {}
    for key, value in expected_settings.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ConfigError(
                "adapter.expected_settings values must be strings"
            )
        converted_expected_settings[key] = value

    identifier = _optional_secret_string(values["b_route"]["id"], "b_route.id")
    password = _optional_secret_string(
        values["b_route"]["password"],
        "b_route.password",
    )

    level_value = _string_value(values["logging"]["level"], "logging.level")
    data_directory = _path_value(
        values["storage"]["data_directory"],
        "storage.data_directory",
    )
    log_directory = _path_value(
        values["logging"]["directory"],
        "logging.directory",
    )
    mqtt = values["mqtt"]

    config = AppConfig(
        measurement=MeasurementConfig(
            instantaneous_interval_seconds=instantaneous,
            cumulative_check_interval_seconds=cumulative,
            cumulative_fetch_delay_seconds=cumulative_delay,
        ),
        serial=SerialConfig(
            port=port_value,
            baudrate=_integer_value(
                values["serial"]["baudrate"],
                "serial.baudrate",
            ),
            timeout_seconds=_number_value(
                values["serial"]["timeout_seconds"],
                "serial.timeout_seconds",
            ),
        ),
        adapter=AdapterConfig(
            auto_configure=_boolean_value(
                values["adapter"]["auto_configure"],
                "adapter.auto_configure",
            ),
            expected_settings=MappingProxyType(converted_expected_settings),
        ),
        retry=RetryConfig(
            request_timeout_seconds=_number_value(
                values["retry"]["request_timeout_seconds"],
                "retry.request_timeout_seconds",
            ),
            request_max_attempts=_integer_value(
                values["retry"]["request_max_attempts"],
                "retry.request_max_attempts",
            ),
            reconnect_after_consecutive_failures=_integer_value(
                values["retry"]["reconnect_after_consecutive_failures"],
                "retry.reconnect_after_consecutive_failures",
            ),
            reconnect_wait_seconds=_number_value(
                values["retry"]["reconnect_wait_seconds"],
                "retry.reconnect_wait_seconds",
            ),
        ),
        storage=StorageConfig(data_directory=data_directory),
        logging=LoggingConfig(
            level=level_value.upper(),
            directory=log_directory,
        ),
        mqtt=MqttConfig(
            enabled=_boolean_value(mqtt["enabled"], "mqtt.enabled"),
            host=_string_value(mqtt["host"], "mqtt.host"),
            port=_integer_value(mqtt["port"], "mqtt.port"),
            device_id=_string_value(mqtt["device_id"], "mqtt.device_id"),
            topic_prefix=_string_value(mqtt["topic_prefix"], "mqtt.topic_prefix"),
            client_id=_string_value(mqtt["client_id"], "mqtt.client_id"),
            username=_optional_secret_string(mqtt["username"], "mqtt.username"),
            password=_optional_secret_string(mqtt["password"], "mqtt.password"),
        ),
        credentials=BRouteCredentials(
            b_route_id=identifier,
            password=password,
        ),
    )
    return config


def _integer_value(value: object, location: str) -> int:
    if not _is_int(value):
        if isinstance(value, str):
            with suppress(ValueError):
                return int(value, 10)
        raise ConfigError(f"{location} must be an integer") from None
    return value


def _number_value(value: object, location: str) -> float:
    if not _is_number(value):
        raise ConfigError(f"{location} must be a number")
    return float(value)


def _boolean_value(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{location} must be a boolean")
    return value


def _environment_boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError("MQTT_ENABLED must be a boolean")


def _string_value(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value


def _optional_secret_string(value: object, location: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty string")
    return value


def _path_value(value: object, location: str) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{location} must be a non-empty path")
    return Path(value)


def _is_valid_serial_port(value: str) -> bool:
    if not value or value != value.strip():
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    if _WINDOWS_PORT_PATTERN.fullmatch(value):
        return True
    if not value.startswith("/dev/"):
        return False
    parts = value.split("/")
    return (
        len(parts) >= 3
        and parts[0] == ""
        and parts[1] == "dev"
        and all(part not in {"", ".", ".."} for part in parts[2:])
    )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _validate_positive_integer(
    errors: list[str],
    location: str,
    value: object,
) -> None:
    if not _is_int(value):
        errors.append(f"{location} must be an integer")
    elif value <= 0:
        errors.append(f"{location} must be greater than 0")


def _validate_positive_number(
    errors: list[str],
    location: str,
    value: object,
) -> None:
    if not _is_number(value):
        errors.append(f"{location} must be a number")
    elif value <= 0:
        errors.append(f"{location} must be greater than 0")
