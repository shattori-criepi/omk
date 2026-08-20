"""実機なしで使用するMockAdapterの単体テスト。"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from broute_meter.adapter.base import (
    AdapterCommunicationError,
    AdapterVerificationError,
    InvalidAdapterSettingValueError,
)
from broute_meter.adapter.mock import MockAdapter


def test_default_mock_is_already_configured() -> None:
    adapter = MockAdapter()

    result = adapter.configure({"uart_mode": "80"})

    assert result.is_configured
    assert not result.changed
    assert adapter.read_history == ["uart_mode"]
    assert adapter.write_history == []


def test_mismatch_is_written_and_read_back() -> None:
    adapter = MockAdapter({"uart_mode": "00"})

    result = adapter.configure({"uart_mode": "80"})

    assert result.is_configured
    assert result.changed_settings == ("uart_mode",)
    assert adapter.settings == {"uart_mode": "80"}
    assert adapter.read_history == ["uart_mode", "uart_mode"]
    assert adapter.write_history == [("uart_mode", "80")]


def test_write_changes_false_keeps_original_setting() -> None:
    adapter = MockAdapter({"uart_mode": "00"})

    result = adapter.configure({"uart_mode": "80"}, write_changes=False)

    assert not result.is_configured
    assert adapter.settings == {"uart_mode": "00"}
    assert adapter.write_history == []


def test_initial_read_failure_never_attempts_write() -> None:
    adapter = MockAdapter({"uart_mode": "00"}, fail_reads=1)

    with pytest.raises(AdapterCommunicationError):
        adapter.configure({"uart_mode": "80"})

    assert adapter.read_history == ["uart_mode"]
    assert adapter.write_history == []
    assert adapter.settings == {"uart_mode": "00"}


def test_write_failure_is_recorded_without_changing_state() -> None:
    adapter = MockAdapter({"uart_mode": "00"}, fail_writes=1)

    with pytest.raises(AdapterCommunicationError):
        adapter.configure({"uart_mode": "80"})

    assert adapter.write_history == [("uart_mode", "80")]
    assert adapter.settings == {"uart_mode": "00"}


def test_not_applying_write_causes_verification_failure() -> None:
    adapter = MockAdapter({"uart_mode": "00"}, apply_writes=False)

    with pytest.raises(AdapterVerificationError):
        adapter.configure({"uart_mode": "80"})

    assert adapter.read_history == ["uart_mode", "uart_mode"]
    assert adapter.write_history == [("uart_mode", "80")]
    assert adapter.settings == {"uart_mode": "00"}


def test_fail_next_methods_accept_injected_exception_instances() -> None:
    adapter = MockAdapter()
    read_error = AdapterCommunicationError("read marker")
    write_error = AdapterCommunicationError("write marker")

    adapter.fail_next_read(read_error)
    with pytest.raises(AdapterCommunicationError) as read_exc:
        adapter.read_setting("uart_mode")
    assert read_exc.value is read_error

    adapter.fail_next_write(write_error)
    with pytest.raises(AdapterCommunicationError) as write_exc:
        adapter.write_setting("uart_mode", "00")
    assert write_exc.value is write_error


def test_set_setting_simulates_external_configuration_change() -> None:
    adapter = MockAdapter()

    adapter.set_setting("uart_mode", "0a")

    assert adapter.settings == {"uart_mode": "0A"}


def test_settings_property_is_a_read_only_snapshot() -> None:
    adapter = MockAdapter()

    snapshot = adapter.settings
    assert isinstance(snapshot, MappingProxyType)
    with pytest.raises(TypeError):
        snapshot["uart_mode"] = "00"  # type: ignore[index]

    adapter.set_setting("uart_mode", "00")
    assert snapshot == {"uart_mode": "80"}
    assert adapter.settings == {"uart_mode": "00"}


@pytest.mark.parametrize("argument", ["fail_reads", "fail_writes"])
def test_failure_counts_must_not_be_negative(argument: str) -> None:
    kwargs = {argument: -1}

    with pytest.raises(ValueError, match=argument):
        MockAdapter(**kwargs)


def test_mock_rejects_non_hex_setting_value() -> None:
    with pytest.raises(InvalidAdapterSettingValueError):
        MockAdapter({"uart_mode": "not-hex"})
