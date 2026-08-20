"""設定読込みから必要時だけの書込み・再確認までをモックで統合検証する。"""

from __future__ import annotations

import pytest

from broute_meter.adapter import AdapterCommunicationError, MockAdapter
from broute_meter.config import load_config


def _expected_settings() -> dict[str, str]:
    config = load_config(
        settings_path=None,
        credentials_path=None,
        environ={},
    )
    return dict(config.adapter.expected_settings)


def test_matching_live_settings_are_read_without_flash_write() -> None:
    expected = _expected_settings()
    adapter = MockAdapter(expected)

    result = adapter.ensure_settings(expected)

    assert result.is_configured
    assert result.initial_settings == expected
    assert result.final_settings == expected
    assert result.changed_settings == ()
    assert adapter.read_history == ["uart_mode", "output_mode"]
    assert adapter.write_history == []


def test_mismatched_live_setting_is_written_once_and_read_back() -> None:
    expected = _expected_settings()
    adapter = MockAdapter({"uart_mode": "00", "output_mode": "01"})

    result = adapter.ensure_settings(expected)

    assert result.is_configured
    assert result.initial_settings == {"uart_mode": "00", "output_mode": "01"}
    assert result.final_settings == expected
    assert result.changed_settings == ("uart_mode",)
    assert adapter.settings == expected
    assert adapter.read_history == ["uart_mode", "output_mode", "uart_mode"]
    assert adapter.write_history == [("uart_mode", "80")]


def test_initial_read_failure_never_falls_back_to_unconditional_write() -> None:
    expected = _expected_settings()
    adapter = MockAdapter(
        {"uart_mode": "00", "output_mode": "01"},
        fail_reads=1,
    )

    with pytest.raises(AdapterCommunicationError):
        adapter.ensure_settings(expected)

    assert adapter.settings == {
        "uart_mode": "00",
        "output_mode": "01",
    }
    assert adapter.read_history == ["uart_mode"]
    assert adapter.write_history == []
