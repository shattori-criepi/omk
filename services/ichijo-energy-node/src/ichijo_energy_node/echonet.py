"""Small, GET-only ECHONET Lite Format 1 client."""

from __future__ import annotations

import logging
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable

LOGGER = logging.getLogger(__name__)
EHD = b"\x10\x81"
CONTROLLER_EOJ = bytes.fromhex("05ff01")
GET = 0x62
GET_RESPONSE = 0x72
GET_SNA = 0x52
ECHONET_PORT = 3610


class EchonetError(RuntimeError):
    """A rejected or malformed ECHONET Lite exchange."""


class EchonetTimeout(EchonetError):
    """No usable response arrived before the configured timeout."""


class TidGenerator:
    def __init__(self, initial: int = 1) -> None:
        self._next = initial
        self._lock = threading.Lock()

    def next(self) -> int:
        with self._lock:
            value = self._next
            self._next = (value + 1) & 0xFFFF
            return value


@dataclass(frozen=True, slots=True)
class Frame:
    tid: int
    seoj: bytes
    deoj: bytes
    esv: int
    epc: int
    edt: bytes


def build_get_request(tid: int, deoj: bytes, epc: int) -> bytes:
    if not 0 <= tid <= 0xFFFF or len(deoj) != 3 or not 0 <= epc <= 0xFF:
        raise ValueError("invalid ECHONET GET fields")
    return EHD + tid.to_bytes(2, "big") + CONTROLLER_EOJ + deoj + bytes((GET, 1, epc, 0))


def parse_frame(data: bytes) -> Frame:
    if len(data) < 14:
        raise EchonetError("ECHONET frame is too short")
    if data[:2] != EHD:
        raise EchonetError("unsupported ECHONET header")
    opc = data[11]
    if opc != 1:
        raise EchonetError(f"expected one property, got OPC={opc}")
    epc, pdc = data[12], data[13]
    if len(data) != 14 + pdc:
        raise EchonetError(f"PDC={pdc} does not match EDT length")
    return Frame(int.from_bytes(data[2:4], "big"), data[4:7], data[7:10], data[10], epc, data[14:])


def get_interface_ipv4(interface: str) -> str:
    """Return Linux interface IPv4 without shelling out or routing traffic."""
    try:
        import fcntl
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            encoded = interface.encode("ascii")[:15]
            result = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack("256s", encoded))
        return socket.inet_ntoa(result[20:24])
    except (OSError, UnicodeEncodeError) as error:
        raise EchonetError(f"cannot obtain IPv4 address for interface {interface!r}") from error


class EchonetClient:
    def __init__(self, target_ip: str, local_ip: str, *, timeout_seconds: float, retry_count: int,
                 socket_factory: Callable[..., socket.socket] = socket.socket, tids: TidGenerator | None = None) -> None:
        self.target_ip = target_ip
        self.local_ip = local_ip
        self.timeout_seconds = timeout_seconds
        self.retry_count = retry_count
        self._socket_factory = socket_factory
        self._tids = tids or TidGenerator()

    def get(self, deoj: bytes, epc: int) -> bytes:
        tid = self._tids.next()
        request = build_get_request(tid, deoj, epc)
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            try:
                return self._exchange(request, tid, deoj, epc)
            except EchonetTimeout as error:
                last_error = error
                LOGGER.warning("ECHONET timeout target=%s epc=%02X attempt=%s", self.target_ip, epc, attempt + 1)
            except EchonetError:
                raise
        raise EchonetTimeout(f"no ECHONET response after {self.retry_count + 1} attempts") from last_error

    def _exchange(self, request: bytes, tid: int, deoj: bytes, epc: int) -> bytes:
        with self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.settimeout(self.timeout_seconds)
            udp.bind((self.local_ip, ECHONET_PORT))
            LOGGER.debug("ECHONET GET tx=%s", request.hex())
            udp.sendto(request, (self.target_ip, ECHONET_PORT))
            try:
                response, address = udp.recvfrom(1024)
            except socket.timeout as error:
                raise EchonetTimeout("ECHONET response timed out") from error
        if address[0] != self.target_ip or address[1] != ECHONET_PORT:
            raise EchonetError(f"response from unexpected endpoint {address}")
        LOGGER.debug("ECHONET rx=%s", response.hex())
        frame = parse_frame(response)
        if frame.tid != tid or frame.seoj != deoj or frame.deoj != CONTROLLER_EOJ or frame.epc != epc:
            raise EchonetError("ECHONET response does not match GET request")
        if frame.esv == GET_SNA:
            raise EchonetError("ECHONET Get_SNA response")
        if frame.esv != GET_RESPONSE:
            raise EchonetError(f"unexpected ECHONET ESV 0x{frame.esv:02X}")
        return frame.edt
