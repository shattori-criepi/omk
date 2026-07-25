"""ECHONET Lite形式1電文の厳密な解析。"""

from __future__ import annotations

from broute_meter.echonet.frame import ECHONET_LITE_EHD, EchonetFrame, EchonetProperty

MINIMUM_FRAME_LENGTH = 14


class EchonetFrameError(ValueError):
    """ECHONET Lite電文が不正。"""


class EchonetHeaderError(EchonetFrameError):
    """EHDがECHONET Lite形式1ではない。"""


class EchonetLengthError(EchonetFrameError):
    """OPC、PDCまたは電文全体の長さが不正。"""


def parse_frame(data: bytes) -> EchonetFrame:
    """形式1電文を解析し、PDCと終端位置を検証する。"""

    if len(data) < MINIMUM_FRAME_LENGTH:
        raise EchonetLengthError("ECHONET Lite電文が短すぎます。")
    if data[:2] != ECHONET_LITE_EHD:
        raise EchonetHeaderError("ECHONET Lite形式1のEHDではありません。")

    tid = int.from_bytes(data[2:4], "big")
    seoj = data[4:7]
    deoj = data[7:10]
    esv = data[10]
    opc = data[11]
    if opc == 0:
        raise EchonetLengthError("OPCが0です。")

    offset = 12
    properties: list[EchonetProperty] = []
    for _ in range(opc):
        if offset + 2 > len(data):
            raise EchonetLengthError("EPCまたはPDCが途中で切れています。")
        epc = data[offset]
        pdc = data[offset + 1]
        offset += 2
        end = offset + pdc
        if end > len(data):
            raise EchonetLengthError("PDCが残りの電文長を超えています。")
        properties.append(EchonetProperty(epc=epc, edt=data[offset:end]))
        offset = end

    if offset != len(data):
        raise EchonetLengthError("OPCで示されない余分なデータがあります。")

    return EchonetFrame(
        tid=tid,
        seoj=seoj,
        deoj=deoj,
        esv=esv,
        properties=tuple(properties),
    )
