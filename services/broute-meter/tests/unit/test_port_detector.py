"""シリアルポート検出処理の単体テスト。"""

from types import SimpleNamespace

import pytest

from broute_meter.serial.port_detector import (
    AmbiguousPortsError,
    NoMatchingPortError,
    PortDetectionError,
    PortInfo,
    find_rs_wsuha_p_ports,
    format_vid_pid,
    list_serial_ports,
    resolve_serial_port,
)


def _raw_port(
    device: str,
    *,
    product: str | None = None,
    description: str | None = None,
    manufacturer: str | None = None,
    vid: int | None = None,
    pid: int | None = None,
    serial_number: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        product=product,
        description=description,
        manufacturer=manufacturer,
        vid=vid,
        pid=pid,
        serial_number=serial_number,
    )


def _port(device: str, product: str | None = "RS-WSUHA-P") -> PortInfo:
    return PortInfo(
        device=device,
        product=product,
        manufacturer=None,
        vid=None,
        pid=None,
        serial_number=None,
    )


def test_list_serial_ports_normalizes_metadata_and_sorts_by_device() -> None:
    raw_ports = [
        _raw_port(
            "COM9",
            product=None,
            description="RS-WSUHA-P",
            manufacturer="RATOC",
            vid=0x123,
            pid=0xABCD,
            serial_number="SERIAL-2",
        ),
        _raw_port("COM3"),
    ]

    result = list_serial_ports(lambda: raw_ports)

    assert result == [
        PortInfo(
            device="COM3",
            product=None,
            manufacturer=None,
            vid=None,
            pid=None,
            serial_number=None,
        ),
        PortInfo(
            device="COM9",
            product="RS-WSUHA-P",
            manufacturer="RATOC",
            vid=0x123,
            pid=0xABCD,
            serial_number="SERIAL-2",
        ),
    ]


def test_list_serial_ports_wraps_provider_failure() -> None:
    def failing_provider() -> list[SimpleNamespace]:
        raise OSError("device enumeration failed")

    with pytest.raises(PortDetectionError, match="列挙に失敗"):
        list_serial_ports(failing_provider)


def test_list_serial_ports_rejects_missing_device_name() -> None:
    with pytest.raises(PortDetectionError, match="デバイス名がない"):
        list_serial_ports(lambda: [_raw_port(None)])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ""),
        (0x123, "0123"),
        (0xABCD, "ABCD"),
    ],
)
def test_format_vid_pid(value: int | None, expected: str) -> None:
    assert format_vid_pid(value) == expected


def test_find_rs_wsuha_p_ports_matches_only_known_product_name() -> None:
    ports = [
        _port("COM3", "RATOC RS-WSUHA-P USB Adapter"),
        _port("COM4", "rs-wsuha-p"),
        _port("COM5", "Another Wi-SUN adapter"),
        _port("COM6", None),
    ]

    assert find_rs_wsuha_p_ports(ports) == ports[:2]


def test_resolve_serial_port_prefers_explicit_configuration() -> None:
    candidates = [_port("COM3"), _port("COM4")]

    assert resolve_serial_port("  COM9  ", candidates) == "COM9"


def test_resolve_serial_port_returns_only_candidate() -> None:
    assert resolve_serial_port(None, [_port("/dev/ttyUSB0")]) == "/dev/ttyUSB0"


def test_resolve_serial_port_raises_when_no_candidate_exists() -> None:
    with pytest.raises(NoMatchingPortError):
        resolve_serial_port(None, [])


def test_resolve_serial_port_keeps_ambiguous_candidates() -> None:
    candidates = [_port("COM3"), _port("COM4")]

    with pytest.raises(AmbiguousPortsError) as exc_info:
        resolve_serial_port(None, candidates)

    assert exc_info.value.candidates == tuple(candidates)
    assert "COM3" in str(exc_info.value)
    assert "COM4" in str(exc_info.value)
