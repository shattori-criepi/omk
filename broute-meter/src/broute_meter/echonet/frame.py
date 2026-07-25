"""ECHONET Lite形式1電文の型と生成処理。"""

from __future__ import annotations

import threading
from dataclasses import dataclass

ECHONET_LITE_EHD = b"\x10\x81"
CONTROLLER_EOJ = b"\x05\xff\x01"
LOW_VOLTAGE_SMART_METER_EOJ = b"\x02\x88\x01"
ESV_GET = 0x62
ESV_GET_RESPONSE = 0x72


@dataclass(frozen=True, slots=True)
class EchonetProperty:
    """1つのECHONET Liteプロパティ。"""

    epc: int
    edt: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.epc <= 0xFF:
            raise ValueError("epc must fit in one byte")
        if len(self.edt) > 0xFF:
            raise ValueError("edt must be at most 255 bytes")


@dataclass(frozen=True, slots=True)
class EchonetFrame:
    """解析済みECHONET Lite形式1電文。"""

    tid: int
    seoj: bytes
    deoj: bytes
    esv: int
    properties: tuple[EchonetProperty, ...]

    def __post_init__(self) -> None:
        if not 0 <= self.tid <= 0xFFFF:
            raise ValueError("tid must fit in two bytes")
        if len(self.seoj) != 3 or len(self.deoj) != 3:
            raise ValueError("seoj and deoj must each be three bytes")
        if not 0 <= self.esv <= 0xFF:
            raise ValueError("esv must fit in one byte")
        if not 1 <= len(self.properties) <= 0xFF:
            raise ValueError("properties must contain between 1 and 255 items")

    def to_bytes(self) -> bytes:
        """形式1バイト列へ直列化する。"""

        encoded_properties = b"".join(
            bytes((prop.epc, len(prop.edt))) + prop.edt
            for prop in self.properties
        )
        return b"".join(
            (
                ECHONET_LITE_EHD,
                self.tid.to_bytes(2, "big"),
                self.seoj,
                self.deoj,
                bytes((self.esv, len(self.properties))),
                encoded_properties,
            )
        )


class TidGenerator:
    """要求ごとに循環する16-bit TIDをスレッドセーフに生成する。"""

    def __init__(self, initial: int = 1) -> None:
        if not 0 <= initial <= 0xFFFF:
            raise ValueError("initial must fit in two bytes")
        self._value = initial
        self._lock = threading.Lock()

    def next(self) -> int:
        """次のTIDを返す。0xFFFFの次は0へ戻る。"""

        with self._lock:
            value = self._value
            self._value = (self._value + 1) & 0xFFFF
            return value


def build_get_request(tid: int, *epcs: int) -> EchonetFrame:
    """低圧スマート電力量メーター向けGet要求を生成する。"""

    if not epcs:
        raise ValueError("at least one EPC is required")
    return EchonetFrame(
        tid=tid,
        seoj=CONTROLLER_EOJ,
        deoj=LOW_VOLTAGE_SMART_METER_EOJ,
        esv=ESV_GET,
        properties=tuple(EchonetProperty(epc) for epc in epcs),
    )
