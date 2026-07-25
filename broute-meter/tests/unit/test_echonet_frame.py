"""ECHONET Lite形式1電文生成の単体テスト。"""

import pytest

from broute_meter.echonet import (
    CONTROLLER_EOJ,
    ESV_GET,
    LOW_VOLTAGE_SMART_METER_EOJ,
    TidGenerator,
    build_get_request,
)


def test_build_single_property_get_request() -> None:
    frame = build_get_request(0x1234, 0xE7)

    assert frame.tid == 0x1234
    assert frame.seoj == CONTROLLER_EOJ
    assert frame.deoj == LOW_VOLTAGE_SMART_METER_EOJ
    assert frame.esv == ESV_GET
    assert frame.to_bytes() == bytes.fromhex(
        "1081123405FF010288016201E700"
    )


def test_build_multiple_property_get_request() -> None:
    frame = build_get_request(1, 0xD3, 0xE1, 0xD7)

    assert frame.to_bytes() == bytes.fromhex(
        "1081000105FF010288016203D300E100D700"
    )


def test_get_request_requires_at_least_one_epc() -> None:
    with pytest.raises(ValueError, match="EPC"):
        build_get_request(1)


def test_tid_generator_increments_and_wraps() -> None:
    generator = TidGenerator(0xFFFF)

    assert generator.next() == 0xFFFF
    assert generator.next() == 0
    assert generator.next() == 1


def test_tid_generator_default_avoids_zero_used_by_unsolicited_notifications() -> None:
    generator = TidGenerator()

    assert generator.next() == 1
