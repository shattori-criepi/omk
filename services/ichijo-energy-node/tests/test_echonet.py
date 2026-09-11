import socket

import pytest

from ichijo_energy_node.echonet import (
    CONTROLLER_EOJ, EHD, GET_RESPONSE, EchonetClient, EchonetError, TidGenerator,
    build_get_request, parse_frame,
)


def response(tid=1, eoj=bytes.fromhex("027901"), epc=0xE0, edt=bytes.fromhex("06d1"), esv=GET_RESPONSE):
    return EHD + tid.to_bytes(2, "big") + eoj + CONTROLLER_EOJ + bytes((esv, 1, epc, len(edt))) + edt


def test_get_request_and_tid_generation():
    assert build_get_request(0x1234, bytes.fromhex("027901"), 0xE0).hex() == "1081123405ff010279016201e000"
    tids = TidGenerator(0xFFFF)
    assert (tids.next(), tids.next()) == (0xFFFF, 0)


def test_parse_normal_response():
    frame = parse_frame(response())
    assert frame.tid == 1 and frame.epc == 0xE0 and frame.edt == bytes.fromhex("06d1")


@pytest.mark.parametrize("data", [b"\x10\x81", response()[:-1]])
def test_parse_rejects_short_or_bad_pdc(data):
    with pytest.raises(EchonetError):
        parse_frame(data)


class FakeSocket:
    def __init__(self, packet): self.packet, self.bound = packet, None
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def settimeout(self, value): self.timeout = value
    def bind(self, address): self.bound = address
    def sendto(self, data, address): self.request, self.target = data, address
    def recvfrom(self, size): return self.packet, ("192.0.2.10", 3610)


@pytest.mark.parametrize("packet", [response(tid=2), response(epc=0xE1), response(esv=0x52)])
def test_client_rejects_mismatched_or_get_sna(packet):
    client = EchonetClient("192.0.2.10", "192.0.2.20", timeout_seconds=1, retry_count=0,
                           socket_factory=lambda *_: FakeSocket(packet))
    with pytest.raises(EchonetError):
        client.get(bytes.fromhex("027901"), 0xE0)


def test_client_binds_3610_and_returns_edt():
    fake = FakeSocket(response())
    client = EchonetClient("192.0.2.10", "192.0.2.20", timeout_seconds=1, retry_count=0,
                           socket_factory=lambda *_: fake)
    assert client.get(bytes.fromhex("027901"), 0xE0) == bytes.fromhex("06d1")
    assert fake.bound == ("192.0.2.20", 3610)
