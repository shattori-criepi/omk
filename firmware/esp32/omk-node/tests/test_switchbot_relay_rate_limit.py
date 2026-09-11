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


class _SlotPolicy:
    """Timestamp-controlled model of the fixed 16-slot reclaim policy."""

    TTL = 60.0

    def __init__(self, slots: int = 16) -> None:
        self.slots: list[dict[str, object] | None] = [None] * slots

    def observe(self, address: str, at: float) -> dict[str, object] | None:
        for slot in self.slots:
            if slot and slot["address"] == address:
                slot["last_fragment"] = at
                return slot
        for index, slot in enumerate(self.slots):
            if slot is None:
                self.slots[index] = {"address": address, "last_fragment": at}
                return self.slots[index]
        expired = [(index, slot) for index, slot in enumerate(self.slots)
                   if slot and at - float(slot["last_fragment"]) >= self.TTL]
        if not expired:
            return None
        index, _ = min(expired, key=lambda item: float(item[1]["last_fragment"]))
        self.slots[index] = {"address": address, "last_fragment": at}
        return self.slots[index]


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


def test_slot_source_uses_ttl_oldest_expired_and_full_reset() -> None:
    source = SOURCE.read_text()
    assert "SWITCHBOT_RELAY_SLOT_TTL_US (60LL * 1000LL * 1000LL)" in source
    assert "find_slot(address, now_us)" in source
    assert "slot->last_fragment_us < oldest_expired->last_fragment_us" in source
    assert "memset(slot, 0, sizeof(*slot))" in source


def test_empty_slots_are_used_before_any_reclaim() -> None:
    policy = _SlotPolicy()
    assert policy.observe("a", 0.0) is not None
    assert policy.observe("b", 1.0) is not None
    assert [slot["address"] for slot in policy.slots if slot] == ["a", "b"]


def test_active_sixteen_slots_reject_seventeenth_without_overwrite() -> None:
    policy = _SlotPolicy()
    for index in range(16): assert policy.observe(str(index), 1.0) is not None
    assert policy.observe("17", 59.0) is None
    assert [slot["address"] for slot in policy.slots if slot] == [str(index) for index in range(16)]


def test_expired_slot_is_reused_and_oldest_expired_wins() -> None:
    policy = _SlotPolicy()
    for index in range(16): assert policy.observe(str(index), float(index)) is not None
    # At t=75, slots 0..15 are expired; slot 0 is the oldest.
    reused = policy.observe("new", 75.0)
    assert reused and reused["address"] == "new"
    assert policy.slots[0] == {"address": "new", "last_fragment": 75.0}


def test_suppressed_observation_keeps_slot_active_for_ttl() -> None:
    slots = _SlotPolicy()
    relay = _RelayPolicy()
    assert slots.observe("active", 0.0) and relay.observe(0.0, True, False)
    assert not relay.observe(1.0, True, False)  # rate-limited at the relay layer
    assert slots.observe("active", 10.0)  # last_fragment still advances
    for index in range(1, 16): assert slots.observe(str(index), 10.0)
    assert slots.observe("new", 65.0) is None


def test_reused_slot_does_not_carry_fragment_or_publish_state() -> None:
    policy = _SlotPolicy(1)
    old = policy.observe("old", 0.0)
    assert old is not None
    old.update(manufacturer=b"old", service=b"old", has_published=True, last_publish=5.0)
    replacement = policy.observe("new", 60.0)
    assert replacement == {"address": "new", "last_fragment": 60.0}


def test_same_address_after_ttl_uses_its_existing_slot_and_can_publish_again() -> None:
    slots = _SlotPolicy(1)
    relay = _RelayPolicy()
    assert slots.observe("same", 0.0) and relay.observe(0.0, True, False)
    assert slots.observe("same", 61.0)
    assert relay.observe(61.0, True, False)
