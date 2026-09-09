"""pyserialを交換可能にする、スレッドセーフなシリアル通信境界。"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self, runtime_checkable

import serial as pyserial


class _SerialConnection(Protocol):
    """PySerialTransportが利用するpyserial接続の最小契約。"""

    is_open: bool
    timeout: float | None

    def open(self) -> None: ...

    def close(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def reset_input_buffer(self) -> None: ...


SerialFactory = Callable[..., _SerialConnection]


@runtime_checkable
class SerialTransport(Protocol):
    """RS-WSUHA-Pアダプターから使用するシリアル通信の抽象インターフェース。"""

    @property
    def is_open(self) -> bool:
        """通信路が開いている場合にTrueを返す。"""
        ...

    def open(self) -> None:
        """通信路を開く。"""
        ...

    def close(self) -> None:
        """通信路を閉じる。"""
        ...

    def read(
        self,
        size: int = 1,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        """最大sizeバイトを、任意の一時タイムアウトで読み取る。"""
        ...

    def write(self, data: bytes) -> int:
        """dataをすべて書き込み、書き込んだバイト数を返す。"""
        ...

    def reset_input_buffer(self) -> None:
        """未処理の受信データを破棄する。"""
        ...

    def __enter__(self) -> Self:
        """通信路を開き、コンテキストへ返す。"""
        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """コンテキスト終了時に通信路を閉じる。"""
        ...


class TransportError(RuntimeError):
    """シリアル通信処理に失敗した。"""


class SerialOpenError(TransportError):
    """シリアルポートを開けなかった。"""


class SerialDisconnectedError(TransportError):
    """未接続、切断、またはシリアルI/Oエラーを検出した。"""


class SerialTimeoutError(TransportError):
    """シリアル通信がタイムアウトした、または書込みが完了しなかった。"""


class PySerialTransport:
    """pyserialを使用するスレッドセーフなシリアル通信実装。

    Args:
        port: WindowsのCOMポート名、またはLinuxのデバイスパス。
        baudrate: シリアル通信速度。
        timeout_seconds: 読込みおよび書込みのタイムアウト秒数。
        serial_factory: テスト時に差し替え可能なpyserial互換ファクトリ。
        port_resolver: open前後に現在のidentityを検証し、通信先を返す関数。
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout_seconds: float,
        serial_factory: SerialFactory | None = None,
        port_resolver: Callable[[], str] | None = None,
    ) -> None:
        if not port.strip():
            raise ValueError("port must not be empty")
        if baudrate <= 0:
            raise ValueError("baudrate must be greater than zero")
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be finite and greater than zero")

        self._port = port.strip()
        self._port_resolver = port_resolver
        self._baudrate = baudrate
        self._timeout_seconds = timeout_seconds
        self._serial_factory: SerialFactory = (
            serial_factory if serial_factory is not None else pyserial.Serial
        )
        self._serial: _SerialConnection | None = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        """通信路が開いている場合にTrueを返す。"""

        with self._lock:
            return self._serial is not None and bool(self._serial.is_open)

    def open(self) -> None:
        """8 data bits、パリティなし、1 stop bitでポートを開く。

        Raises:
            SerialOpenError: pyserialまたはOSがポートを開けなかった場合。
        """

        with self._lock:
            if self._serial is not None and self._serial.is_open:
                return
            self._serial = None

            if self._port_resolver is not None:
                self._port = self._port_resolver()
            try:
                connection = self._serial_factory(
                    port=self._port,
                    baudrate=self._baudrate,
                    bytesize=pyserial.EIGHTBITS,
                    parity=pyserial.PARITY_NONE,
                    stopbits=pyserial.STOPBITS_ONE,
                    timeout=self._timeout_seconds,
                    write_timeout=self._timeout_seconds,
                    rtscts=False,
                    xonxoff=False,
                )
                # TODO: WUART 80適用後にホスト側RTS/CTSが必要か、公式仕様で確認する。
                # 確認できるまでは公式公開例に合わせ、フロー制御を無効のままとする。
                if not connection.is_open:
                    connection.open()
                if not connection.is_open:
                    raise SerialOpenError(
                        f"シリアルポートが開いた状態になりませんでした: {self._port}"
                    )
            except OSError as exc:
                self._serial = None
                raise SerialOpenError(
                    f"シリアルポートを開けませんでした: {self._port}"
                ) from exc

            if self._port_resolver is not None:
                try:
                    if self._port_resolver() != self._port:
                        raise SerialOpenError("USB port changed while opening; retry required.")
                except Exception:
                    connection.close()
                    raise
            self._serial = connection

    def close(self) -> None:
        """通信路を閉じる。未接続の場合は何もしない。

        Raises:
            SerialDisconnectedError: pyserialまたはOSがクローズに失敗した場合。
        """

        with self._lock:
            connection = self._serial
            self._serial = None
            if connection is None:
                return
            try:
                if connection.is_open:
                    connection.close()
            except OSError as exc:
                raise SerialDisconnectedError(
                    f"シリアルポートを閉じられませんでした: {self._port}"
                ) from exc

    def read(
        self,
        size: int = 1,
        *,
        timeout_seconds: float | None = None,
    ) -> bytes:
        """最大sizeバイトを読み取る。

        タイムアウトまでに一部だけ受信した場合はそのデータを返す。1バイトも
        受信しなかった場合はタイムアウトとして扱う。``timeout_seconds``を
        指定した場合、そのreadだけに適用し、接続の既定値は復元する。

        Raises:
            ValueError: sizeまたは一時タイムアウトが不正な場合。
            SerialDisconnectedError: ポートが未接続、切断、または読込み失敗の場合。
            SerialTimeoutError: 1バイトも受信せずタイムアウトした場合。
        """

        if size < 0:
            raise ValueError("size must not be negative")
        if timeout_seconds is not None and (
            timeout_seconds <= 0 or not math.isfinite(timeout_seconds)
        ):
            raise ValueError(
                "timeout_seconds must be finite and greater than zero"
            )

        with self._lock:
            connection = self._require_connection()
            if size == 0:
                return b""

            original_timeout = connection.timeout
            override_applied = False
            try:
                if timeout_seconds is not None:
                    connection.timeout = timeout_seconds
                    override_applied = True
                data = connection.read(size)
            except (pyserial.SerialTimeoutException, TimeoutError) as exc:
                raise SerialTimeoutError("シリアル読込みがタイムアウトしました。") from exc
            except OSError as exc:
                raise SerialDisconnectedError("シリアル読込みに失敗しました。") from exc
            finally:
                if override_applied:
                    try:
                        connection.timeout = original_timeout
                    except OSError as exc:
                        raise SerialDisconnectedError(
                            "シリアル読込みタイムアウトを復元できませんでした。"
                        ) from exc

            if not data:
                raise SerialTimeoutError("シリアル読込みがタイムアウトしました。")
            return bytes(data)

    def write(self, data: bytes) -> int:
        """dataをすべて書き込み、書き込んだバイト数を返す。

        Raises:
            SerialDisconnectedError: ポートが未接続、切断、または書込み失敗の場合。
            SerialTimeoutError: タイムアウトまたは部分書込みを検出した場合。
        """

        with self._lock:
            connection = self._require_connection()
            try:
                written = connection.write(data)
            except (pyserial.SerialTimeoutException, TimeoutError) as exc:
                raise SerialTimeoutError("シリアル書込みがタイムアウトしました。") from exc
            except OSError as exc:
                raise SerialDisconnectedError("シリアル書込みに失敗しました。") from exc

            expected = len(data)
            actual = 0 if written is None else written
            if actual != expected:
                raise SerialTimeoutError(
                    f"シリアル書込みが完了しませんでした: {actual}/{expected} bytes"
                )
            return actual

    def reset_input_buffer(self) -> None:
        """未処理の受信データを破棄する。

        Raises:
            SerialDisconnectedError: ポートが未接続、切断、または処理失敗の場合。
        """

        with self._lock:
            connection = self._require_connection()
            try:
                connection.reset_input_buffer()
            except OSError as exc:
                raise SerialDisconnectedError(
                    "シリアル受信バッファーのリセットに失敗しました。"
                ) from exc

    def __enter__(self) -> Self:
        """通信路を開き、コンテキストへ返す。"""

        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """コンテキスト終了時に通信路を閉じる。"""

        self.close()

    def _require_connection(self) -> _SerialConnection:
        connection = self._serial
        if connection is None or not connection.is_open:
            raise SerialDisconnectedError(
                f"シリアルポートが接続されていません: {self._port}"
            )
        return connection
