"""ECHONET Lite形式1電文解析の単体テスト。"""

import pytest

from broute_meter.echonet import (
    EchonetHeaderError,
    EchonetLengthError,
    parse_frame,
)


def test_parse_normal_response_with_multiple_properties_and_unknown_epc() -> None:
    frame = parse_frame(
        bytes.fromhex(
            "1081123402880105FF017202"
            "E704FFFFFF9C"
            "FE02AABB"
        )
    )

    assert frame.tid == 0x1234
    assert frame.seoj == bytes.fromhex("028801")
    assert frame.deoj == bytes.fromhex("05FF01")
    assert frame.esv == 0x72
    assert [(prop.epc, prop.edt) for prop in frame.properties] == [
        (0xE7, bytes.fromhex("FFFFFF9C")),
        (0xFE, bytes.fromhex("AABB")),
    ]


@pytest.mark.parametrize(
    "data",
    [
        bytes.fromhex("1181000102880105FF017201E70400000001"),
        bytes.fromhex("1082000102880105FF017201E70400000001"),
    ],
)
def test_reject_invalid_ehd(data: bytes) -> None:
    with pytest.raises(EchonetHeaderError):
        parse_frame(data)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes.fromhex("1081000102880105FF017201E7"),
        bytes.fromhex("1081000102880105FF017201E7040000"),
        bytes.fromhex("1081000102880105FF017201E70000"),
        bytes.fromhex("1081000102880105FF0172000000"),
    ],
)
def test_reject_invalid_frame_lengths(data: bytes) -> None:
    with pytest.raises(EchonetLengthError):
        parse_frame(data)
