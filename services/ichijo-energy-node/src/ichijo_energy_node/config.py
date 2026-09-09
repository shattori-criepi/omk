"""Environment configuration for the Ichijo energy node."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _non_negative_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    device_id: str
    target_ip: str
    interface: str
    poll_interval_seconds: float
    echonet_timeout_seconds: float
    echonet_retry_count: int
    property_interval_seconds: float
    mqtt_host: str
    mqtt_port: int
    mqtt_keepalive_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        device_id = os.getenv("ICHJO_DEVICE_ID", "ichijo-001")
        target_ip = os.getenv("ICHJO_ECHONET_TARGET_IP", "192.168.8.182")
        interface = os.getenv("ICHJO_ECHONET_INTERFACE", "eth0")
        mqtt_host = os.getenv("MQTT_HOST", "127.0.0.1")
        if not all((device_id, target_ip, interface, mqtt_host)):
            raise ValueError("device ID, target IP, interface, and MQTT host must not be empty")
        try:
            mqtt_port = int(os.getenv("MQTT_PORT", "1883"))
            keepalive = int(os.getenv("MQTT_KEEPALIVE_SECONDS", "60"))
        except ValueError as error:
            raise ValueError("MQTT_PORT and MQTT_KEEPALIVE_SECONDS must be integers") from error
        if not 1 <= mqtt_port <= 65535 or keepalive <= 0:
            raise ValueError("MQTT port must be 1-65535 and keepalive must be positive")
        return cls(
            device_id=device_id, target_ip=target_ip, interface=interface,
            poll_interval_seconds=_positive_float("ICHJO_POLL_INTERVAL_SECONDS", 10),
            echonet_timeout_seconds=_positive_float("ICHJO_ECHONET_TIMEOUT_SECONDS", 2),
            echonet_retry_count=_non_negative_int("ICHJO_ECHONET_RETRY_COUNT", 1),
            property_interval_seconds=_positive_float("ICHJO_PROPERTY_INTERVAL_SECONDS", 0.05),
            mqtt_host=mqtt_host, mqtt_port=mqtt_port, mqtt_keepalive_seconds=keepalive,
        )
