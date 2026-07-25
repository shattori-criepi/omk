"""pyserial通信境界の単体テスト。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
import serial as pyserial

from broute_meter.serial.transport import (
    PySerialTransport,
    SerialDisconnectedError,
    SerialOpenError,
    SerialTimeoutError,
    SerialTransport,
    TransportError,
)

_DEFAULT_WRITE_RESULT = object()


class FakeSerial:
    """テストで使用するpyserial互換オブジェクト。"""

    def __init__(self) -> None:
        self.is_open = True
        self.timeout: float | None = 5.0
        self.read_result = b"response"
        self.read_error: BaseException | None = None
        self.write_result: int | None | object = _DEFAULT_WRITE_RESULT
        self.write_error: BaseException | None = None
        self.close_error: BaseException | None = None
        self.reset_error: BaseException | None = None
        self.open_count = 0
        self.close_count = 0
        self.reset_count = 0
        self.read_sizes: list[int] = []
        self.read_timeouts: list[float | None] = []
        self.written_data: list[bytes] = []

    def open(self) -> None:
        self.open_count += 1
        self.is_open = True

    def close(self) -> None:
        if self.close_error is not None:
            raise self.close_error
        self.close_count += 1
        self.is_open = False

    def read(self, size: int = 1) -> bytes:
        self.read_sizes.append(size)
        self.read_timeouts.append(self.timeout)
        if self.read_error is not None:
            raise self.read_error
        return self.read_result

    def write(self, data: bytes) -> int | None:
        self.written_data.append(data)
        if self.write_error is not None:
            raise self.write_error
        if self.write_result is _DEFAULT_WRITE_RESULT:
            return len(data)
        assert isinstance(self.write_result, int) or self.write_result is None
        return self.write_result

    def reset_input_buffer(self) -> None:
        if self.reset_error is not None:
            raise self.reset_error
        self.reset_count += 1


class RecordingFactory:
    """呼出し引数を保存してFakeSerialを返す。"""

    def __init__(
        self,
        connection: FakeSerial,
        error: BaseException | None = None,
    ) -> None:
        self.connection = connection
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeSerial:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.connection


def _transport(
    connection: FakeSerial | None = None,
    *,
    factory: Callable[..., FakeSerial] | None = None,
) -> tuple[PySerialTransport, FakeSerial, RecordingFactory | Callable[..., FakeSerial]]:
    selected_connection = connection if connection is not None else FakeSerial()
    selected_factory: RecordingFactory | Callable[..., FakeSerial]
    selected_factory = factory if factory is not None else RecordingFactory(selected_connection)
    transport = PySerialTransport(
        port="COM5",
        baudrate=115200,
        timeout_seconds=5,
        serial_factory=selected_factory,
    )
    return transport, selected_connection, selected_factory


def test_transport_satisfies_protocol() -> None:
    transport, _, _ = _transport()

    assert isinstance(transport, SerialTransport)


def test_open_uses_8n1_without_flow_control_and_is_idempotent() -> None:
    transport, connection, factory = _transport()
    assert isinstance(factory, RecordingFactory)

    transport.open()
    transport.open()

    assert transport.is_open
    assert len(factory.calls) == 1
    assert factory.calls[0] == {
        "port": "COM5",
        "baudrate": 115200,
        "bytesize": pyserial.EIGHTBITS,
        "parity": pyserial.PARITY_NONE,
        "stopbits": pyserial.STOPBITS_ONE,
        "timeout": 5,
        "write_timeout": 5,
        "rtscts": False,
        "xonxoff": False,
    }
    assert connection.open_count == 0


def test_open_opens_factory_result_when_it_is_initially_closed() -> None:
    connection = FakeSerial()
    connection.is_open = False
    transport, _, _ = _transport(connection)

    transport.open()

    assert connection.is_open
    assert connection.open_count == 1


def test_open_rejects_connection_that_remains_closed() -> None:
    class NeverOpensSerial(FakeSerial):
        def open(self) -> None:
            self.open_count += 1

    connection = NeverOpensSerial()
    connection.is_open = False
    transport, _, _ = _transport(connection)

    with pytest.raises(SerialOpenError, match="開いた状態"):
        transport.open()

    assert not transport.is_open


@pytest.mark.parametrize(
    "error",
    [
        pyserial.SerialException("unavailable"),
        OSError("unavailable"),
    ],
)
def test_open_wraps_pyserial_and_os_errors(error: BaseException) -> None:
    connection = FakeSerial()
    factory = RecordingFactory(connection, error)
    transport, _, _ = _transport(connection, factory=factory)

    with pytest.raises(SerialOpenError) as exc_info:
        transport.open()

    assert exc_info.value.__cause__ is error
    assert not transport.is_open


def test_context_manager_opens_and_closes_connection() -> None:
    transport, connection, _ = _transport()

    with transport as opened:
        assert opened is transport
        assert transport.is_open

    assert not transport.is_open
    assert connection.close_count == 1


def test_close_is_idempotent() -> None:
    transport, connection, _ = _transport()
    transport.open()

    transport.close()
    transport.close()

    assert connection.close_count == 1
    assert not transport.is_open


def test_close_wraps_serial_error_and_marks_transport_closed() -> None:
    connection = FakeSerial()
    connection.close_error = pyserial.SerialException("disconnected")
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialDisconnectedError) as exc_info:
        transport.close()

    assert exc_info.value.__cause__ is connection.close_error
    assert not transport.is_open


def test_read_returns_received_and_partial_data() -> None:
    connection = FakeSerial()
    connection.read_result = b"ab"
    transport, _, _ = _transport(connection)
    transport.open()

    assert transport.read(4) == b"ab"
    assert connection.read_sizes == [4]


def test_read_applies_temporary_timeout_and_restores_connection_default() -> None:
    connection = FakeSerial()
    transport, _, _ = _transport(connection)
    transport.open()

    assert transport.read(1, timeout_seconds=0.25) == b"response"

    assert connection.read_timeouts == [0.25]
    assert connection.timeout == 5.0


def test_read_zero_size_does_not_call_pyserial() -> None:
    transport, connection, _ = _transport()
    transport.open()

    assert transport.read(0) == b""
    assert connection.read_sizes == []


@pytest.mark.parametrize(
    "timeout",
    [0, -1, float("nan"), float("inf")],
)
def test_read_rejects_invalid_temporary_timeout(timeout: float) -> None:
    transport, _, _ = _transport()
    transport.open()

    with pytest.raises(ValueError, match="timeout_seconds"):
        transport.read(1, timeout_seconds=timeout)


def test_empty_read_is_timeout() -> None:
    connection = FakeSerial()
    connection.read_result = b""
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialTimeoutError):
        transport.read(1)


@pytest.mark.parametrize(
    "error",
    [
        pyserial.SerialTimeoutException("timed out"),
        TimeoutError("timed out"),
    ],
)
def test_read_wraps_timeout_errors(error: BaseException) -> None:
    connection = FakeSerial()
    connection.read_error = error
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialTimeoutError) as exc_info:
        transport.read(1)

    assert exc_info.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        pyserial.SerialException("disconnected"),
        OSError("disconnected"),
    ],
)
def test_read_wraps_disconnection_errors(error: BaseException) -> None:
    connection = FakeSerial()
    connection.read_error = error
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialDisconnectedError) as exc_info:
        transport.read(1)

    assert exc_info.value.__cause__ is error


def test_write_returns_full_byte_count() -> None:
    transport, connection, _ = _transport()
    transport.open()

    assert transport.write(b"SKVER\r\n") == 7
    assert connection.written_data == [b"SKVER\r\n"]


@pytest.mark.parametrize("written", [0, 2, None])
def test_write_rejects_partial_or_unknown_byte_count(written: int | None) -> None:
    connection = FakeSerial()
    connection.write_result = written
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialTimeoutError, match=r"/4 bytes"):
        transport.write(b"test")


@pytest.mark.parametrize(
    ("error", "expected_exception"),
    [
        (pyserial.SerialTimeoutException("timed out"), SerialTimeoutError),
        (TimeoutError("timed out"), SerialTimeoutError),
        (pyserial.SerialException("disconnected"), SerialDisconnectedError),
        (OSError("disconnected"), SerialDisconnectedError),
    ],
)
def test_write_wraps_io_errors(
    error: BaseException,
    expected_exception: type[Exception],
) -> None:
    connection = FakeSerial()
    connection.write_error = error
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(expected_exception) as exc_info:
        transport.write(b"test")

    assert exc_info.value.__cause__ is error


def test_reset_input_buffer_delegates_to_pyserial() -> None:
    transport, connection, _ = _transport()
    transport.open()

    transport.reset_input_buffer()

    assert connection.reset_count == 1


def test_reset_input_buffer_wraps_serial_errors() -> None:
    connection = FakeSerial()
    connection.reset_error = OSError("disconnected")
    transport, _, _ = _transport(connection)
    transport.open()

    with pytest.raises(SerialDisconnectedError) as exc_info:
        transport.reset_input_buffer()

    assert exc_info.value.__cause__ is connection.reset_error


@pytest.mark.parametrize(
    "operation",
    [
        lambda transport: transport.read(),
        lambda transport: transport.write(b"test"),
        lambda transport: transport.reset_input_buffer(),
    ],
)
def test_io_operations_require_open_connection(
    operation: Callable[[PySerialTransport], object],
) -> None:
    transport, _, _ = _transport()

    with pytest.raises(SerialDisconnectedError):
        operation(transport)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"port": " ", "baudrate": 115200, "timeout_seconds": 5}, "port"),
        ({"port": "COM5", "baudrate": 0, "timeout_seconds": 5}, "baudrate"),
        ({"port": "COM5", "baudrate": 115200, "timeout_seconds": 0}, "timeout_seconds"),
        (
            {"port": "COM5", "baudrate": 115200, "timeout_seconds": float("nan")},
            "timeout_seconds",
        ),
        (
            {"port": "COM5", "baudrate": 115200, "timeout_seconds": float("inf")},
            "timeout_seconds",
        ),
    ],
)
def test_constructor_rejects_invalid_values(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        PySerialTransport(**kwargs)  # type: ignore[arg-type]


def test_writes_are_serialized_between_threads() -> None:
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    call_count = 0

    class BlockingSerial(FakeSerial):
        def write(self, data: bytes) -> int:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                first_entered.set()
                assert release_first.wait(timeout=2)
            else:
                second_entered.set()
            return len(data)

    connection = BlockingSerial()
    transport, _, _ = _transport(connection)
    transport.open()
    errors: list[BaseException] = []

    def write(data: bytes) -> None:
        if data == b"second":
            second_started.set()
        try:
            transport.write(data)
        except TransportError as exc:  # pragma: no cover - assertion aid for worker threads
            errors.append(exc)

    first = threading.Thread(target=write, args=(b"first",))
    second = threading.Thread(target=write, args=(b"second",))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()

    assert second_started.wait(timeout=2)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not errors
    assert second_entered.is_set()
