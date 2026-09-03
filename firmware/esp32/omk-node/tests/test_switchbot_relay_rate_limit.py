from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src" / "switchbot_relay.c"


class _RelayPolicy:
    """Timestamp-controlled model of the fixed-slot relay send policy."""

    def __init__(self) -> None:
        self.last_publish: float | None = None
        self.published_parts = (False, False)

    def observe(self, at: float, manufacturer: bool, service: bool) -> bool:
        if self.last_publish is None or at - self.last_publish >= 10.0:
            allowed = True
        else:
            allowed = (manufacturer and not self.published_parts[0]) or (service and not self.published_parts[1])
        if allowed:
            self.last_publish = at
            self.published_parts = (manufacturer, service)
        return allowed


def test_relay_rate_limit_is_per_device_and_not_payload_hash_based() -> None:
    source = SOURCE.read_text()
    assert "payload_hash" not in source
    assert "SWITCHBOT_RELAY_INTERVAL_US" in source
    assert "Payload/counter/RSSI changes" in source
    assert "published_has_manufacturer" in source
    assert "published_has_service" in source


def test_manufacturer_changes_are_suppressed_until_ten_seconds() -> None:
    policy = _RelayPolicy()
    assert policy.observe(0.0, True, False)
    # Each call represents a changed counter/payload from the same device.
    assert not any(policy.observe(float(second), True, False) for second in range(1, 10))
    assert policy.observe(10.0, True, False)


def test_fragment_completion_has_one_immediate_exception_then_is_limited() -> None:
    policy = _RelayPolicy()
    assert policy.observe(0.0, True, False)  # ADV
    assert policy.observe(0.2, True, True)  # SCAN_RSP completes the observation
    assert not policy.observe(0.3, True, True)
    assert not policy.observe(1.0, True, True)
    assert policy.observe(10.2, True, True)


def test_service_only_observation_remains_supported() -> None:
    policy = _RelayPolicy()
    assert policy.observe(0.0, False, True)
    assert not policy.observe(1.0, False, True)
    assert policy.observe(10.0, False, True)
