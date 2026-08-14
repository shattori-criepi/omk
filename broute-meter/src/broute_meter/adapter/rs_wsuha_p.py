"""RS-WSUHA-P固有の設定コマンドと応答処理。"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv6Address
from types import MappingProxyType
from typing import TypeVar

from broute_meter.adapter.base import (
    AdapterCommunicationError,
    AdapterCredentialError,
    AdapterOperationCancelled,
    AdapterPanaJoinError,
    AdapterResponseTimeoutError,
    AdapterScanError,
    BaseAdapter,
    InvalidAdapterSettingValueError,
    UnsupportedAdapterSettingError,
)
from broute_meter.models import ActiveScanResult
from broute_meter.serial.transport import (
    SerialDisconnectedError,
    SerialTimeoutError,
    SerialTransport,
    TransportError,
)

logger = logging.getLogger(__name__)

UART_MODE_SETTING = "uart_mode"
OUTPUT_MODE_SETTING = "output_mode"
STARTUP_WAIT_SECONDS = 3.0
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 5.0
DEFAULT_SCAN_TIMEOUT_SECONDS = 60.0
DEFAULT_PANA_JOIN_TIMEOUT_SECONDS = 60.0
COMMAND_TERMINATOR = b"\r\n"
READ_SIZE_BYTES = 1
MAX_LINE_BYTES = 8192
SHUTDOWN_POLL_SECONDS = 1.0

_HEX_BYTE_PATTERN = re.compile(r"[0-9A-Fa-f]{2}\Z")
_READ_SETTING_RESPONSE_PATTERN = re.compile(rb"OK ([0-9A-Fa-f]{2})\Z")

_ResponseValue = TypeVar("_ResponseValue")


@dataclass(frozen=True, slots=True)
class AdapterSettingCommand:
    """1つのRS-WSUHA-P設定に対応する公式read/writeコマンド。"""

    read: str
    write: str


# RS-WSUHA-P設定コマンドはここだけに集約する。初期版では、公式資料で
# 動作を確認できたUARTモードのRUART/WUART以外を推測して追加しない。
RS_WSUHA_P_SETTING_COMMANDS = MappingProxyType(
    {
        UART_MODE_SETTING: AdapterSettingCommand(
            read="RUART",
            write="WUART {value}",
        ),
        OUTPUT_MODE_SETTING: AdapterSettingCommand(
            read="ROPT",
            write="WOPT {value}",
        ),
    }
)

# Phase 3で使用するコマンドは、RATOC公式Python例で確認できたものだけを
# この表へ集約する。未確認の引数や代替形式を推測して追加しない。
RS_WSUHA_P_B_ROUTE_COMMANDS = MappingProxyType(
    {
        "reset": "SKRESET",
        "set_password": "SKSETPWD {length} {value}",
        "set_id": "SKSETRBID {value}",
        "active_scan": "SKSCAN 2 FFFFFFFF 6 0",
        "set_channel": "SKSREG S2 {value}",
        "set_pan_id": "SKSREG S3 {value}",
        "resolve_ipv6": "SKLL64 {value}",
        "join": "SKJOIN {value}",
        "send_udp": "SKSENDTO 1 {address} 0E1A 1 0 {length} ",
    }
)


class _LineTooLongError(RuntimeError):
    """受信中の1行が安全上の上限を超えた。"""


class _ResponseLineBuffer:
    """CR、LF、CRLFのいずれでも終端できる分割受信バッファー。"""

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._max_line_bytes = max_line_bytes
        self._current = bytearray()
        self._discard_until_terminator = False

    def feed(self, chunk: bytes) -> tuple[bytes, ...]:
        """任意サイズの受信片を追加し、完成した空でない行を返す。"""

        lines: list[bytes] = []
        for byte in chunk:
            if byte in (0x0A, 0x0D):
                if self._discard_until_terminator:
                    self._discard_until_terminator = False
                    continue
                if self._current:
                    lines.append(bytes(self._current))
                    self._current.clear()
                continue

            if self._discard_until_terminator:
                continue

            self._current.append(byte)
            if len(self._current) > self._max_line_bytes:
                self._current.clear()
                self._discard_until_terminator = True
                raise _LineTooLongError

        return tuple(lines)

    def clear(self) -> None:
        """未完了行を破棄する。"""

        self._current.clear()
        self._discard_until_terminator = False


class RsWsuhaPAdapter(BaseAdapter):
    """RS-WSUHA-Pのフラッシュ設定を安全に確認・変更する。

    シリアルポートは呼出し側で開くことも、このクラスの ``open`` を使うことも
    できる。ポートを開いた後の最初の要求前には、公式資料に従い3秒待機する。

    Args:
        transport: 開閉・部分read・全量writeを提供するシリアル通信境界。
        sleeper: 起動待機をテストで差し替えるための関数。
        monotonic: 要求全体の期限を測る単調時計。
        response_timeout_seconds: 通知行の有無にかかわらず適用する応答期限。
    """

    def __init__(
        self,
        transport: SerialTransport,
        *,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        response_timeout_seconds: float = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        if (
            response_timeout_seconds <= 0
            or not math.isfinite(response_timeout_seconds)
        ):
            raise ValueError(
                "response_timeout_seconds must be finite and greater than zero"
            )

        super().__init__()
        self._transport = transport
        self._sleeper = sleeper
        self._monotonic = monotonic
        self._response_timeout_seconds = response_timeout_seconds
        self._request_lock = threading.RLock()
        self._line_buffer = _ResponseLineBuffer()
        self._received_lines: deque[bytes] = deque()
        self._startup_wait_pending = True
        self._shutdown_event: threading.Event | None = None

    def set_shutdown_event(self, shutdown_event: threading.Event | None) -> None:
        """Allow long scan/PANA reads to observe service shutdown promptly."""

        self._shutdown_event = shutdown_event

    @property
    def is_open(self) -> bool:
        """基礎シリアル通信路が開いている場合にTrueを返す。"""

        return self._transport.is_open

    def open(self) -> None:
        """通信路を開き、新しいシリアルセッションの起動待機を予約する。"""

        with self._request_lock:
            was_open = self._transport.is_open
            try:
                self._transport.open()
            except TransportError as exc:
                raise AdapterCommunicationError(
                    "RS-WSUHA-Pのシリアルポートを開けませんでした。"
                ) from exc
            if not was_open:
                self._prepare_new_serial_session()

    def close(self) -> None:
        """通信路を閉じ、受信途中の状態を破棄する。"""

        with self._request_lock:
            try:
                self._transport.close()
            except TransportError as exc:
                raise AdapterCommunicationError(
                    "RS-WSUHA-Pのシリアルポートを閉じられませんでした。"
                ) from exc
            finally:
                self._prepare_new_serial_session()

    def reset_after_reconnect(self) -> None:
        """外部で通信路を再オープンした後の起動待機と受信状態をリセットする。"""

        with self._request_lock:
            self._prepare_new_serial_session()

    def normalize_setting_value(self, name: str, value: str) -> str:
        """対応設定名と2桁16進値を検証し、大文字へ正規化する。"""

        self._setting_command(name)
        if not isinstance(value, str) or _HEX_BYTE_PATTERN.fullmatch(value) is None:
            raise InvalidAdapterSettingValueError(
                f"{name}には2桁の16進数を指定してください。"
            )
        return value.upper()

    def read_setting(self, name: str) -> str:
        """RUARTでUARTモードを読み出す。"""

        command = self._setting_command(name)
        return self._execute_command(command.read, self._parse_read_setting_response)

    def write_setting(self, name: str, value: str) -> None:
        """WUARTでUARTモードを書き込み、受付応答を待つ。"""

        normalized = self.normalize_setting_value(name, value)
        command = self._setting_command(name)
        rendered = command.write.format(value=normalized)
        self._execute_command(rendered, self._parse_write_setting_response)

    def reset(self) -> None:
        """SKRESETでドングル内部の通信状態をリセットする。"""

        self._execute_command(
            RS_WSUHA_P_B_ROUTE_COMMANDS["reset"],
            self._parse_ok_response,
        )

    def set_b_route_id(self, b_route_id: str) -> None:
        """SKSETRBIDでBルート識別IDを設定する。"""

        normalized = self._normalize_credential_token(
            b_route_id,
            expected_bytes=32,
            label="ID",
        )
        command = RS_WSUHA_P_B_ROUTE_COMMANDS["set_id"].format(value=normalized)
        self._execute_command(
            command,
            self._parse_ok_response,
            sensitive=True,
        )

    def set_b_route_password(self, password: str) -> None:
        """SKSETPWDでBルートパスワードを設定する。"""

        normalized = self._normalize_credential_token(
            password,
            expected_bytes=12,
            label="パスワード",
        )
        command = RS_WSUHA_P_B_ROUTE_COMMANDS["set_password"].format(
            length=f"{len(normalized.encode('ascii')):X}",
            value=normalized,
        )
        self._execute_command(
            command,
            self._parse_ok_response,
            sensitive=True,
        )

    def active_scan(self) -> tuple[ActiveScanResult, ...]:
        """確認済みのSKSCAN引数でスマートメーター候補を検索する。"""

        command = RS_WSUHA_P_B_ROUTE_COMMANDS["active_scan"]
        with self._request_lock:
            self._wait_for_startup()
            response_deadline = self._response_deadline(
                DEFAULT_SCAN_TIMEOUT_SECONDS
            )
            with self._translate_transport_errors():
                encoded_command = self._send_command(command)
                self._wait_for_response(
                    encoded_command,
                    self._parse_ok_response,
                    response_deadline,
                )
                return self._collect_scan_results(response_deadline)

    def set_channel(self, channel: str) -> None:
        """スキャン結果のChannelをS2レジスターへ設定する。"""

        normalized = self._normalize_hex_token(channel, digits=2, label="Channel")
        command = RS_WSUHA_P_B_ROUTE_COMMANDS["set_channel"].format(
            value=normalized
        )
        self._execute_command(command, self._parse_ok_response)

    def set_pan_id(self, pan_id: str) -> None:
        """スキャン結果のPan IDをS3レジスターへ設定する。"""

        normalized = self._normalize_hex_token(pan_id, digits=4, label="Pan ID")
        command = RS_WSUHA_P_B_ROUTE_COMMANDS["set_pan_id"].format(
            value=normalized
        )
        self._execute_command(command, self._parse_ok_response)

    def resolve_ipv6_address(self, address: str) -> IPv6Address:
        """SKLL64で64-bitアドレスをリンクローカルIPv6へ変換する。"""

        normalized = self._normalize_hex_token(address, digits=16, label="Addr")
        command = RS_WSUHA_P_B_ROUTE_COMMANDS["resolve_ipv6"].format(
            value=normalized
        )
        return self._execute_command(command, self._parse_ipv6_response)

    def join(self, ipv6_address: IPv6Address) -> None:
        """SKJOINを発行し、EVENT 25またはEVENT 24まで待つ。"""

        if not isinstance(ipv6_address, IPv6Address):
            raise AdapterPanaJoinError("PANA接続先IPv6アドレスが不正です。")

        command = RS_WSUHA_P_B_ROUTE_COMMANDS["join"].format(
            # メーカー公開サンプルと同じく、SKLL64応答相当の固定長表記を渡す。
            value=ipv6_address.exploded.upper()
        )
        with self._request_lock:
            self._wait_for_startup()
            response_deadline = self._response_deadline(
                DEFAULT_PANA_JOIN_TIMEOUT_SECONDS
            )
            with self._translate_transport_errors():
                encoded_command = self._send_command(command)
                self._wait_for_response(
                    encoded_command,
                    self._parse_ok_response,
                    response_deadline,
                )
                while True:
                    line = self._read_response_line(response_deadline).strip(b" \t")
                    if line.startswith(b"EVENT 25"):
                        return
                    if line.startswith(b"EVENT 24"):
                        raise AdapterPanaJoinError(
                            "PANA接続に失敗しました。Bルート認証情報を確認してください。"
                        )
                    if line.startswith(b"ERXUDP "):
                        # PANAハンドシェイクのUDPペイロードには、認証IDを
                        # 復元できるデータが含まれる場合があるため記録しない。
                        logger.debug(
                            "PANA接続待ちにERXUDPを受信しました（内容は省略）。"
                        )
                        continue
                    logger.debug(
                        "PANA接続待ちの要求外行を無視しました: %s",
                        line.decode("ascii", errors="replace"),
                    )

    def exchange_udp(
        self,
        ipv6_address: IPv6Address,
        payload: bytes,
        *,
        response_matcher: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """SKSENDTOでECHONET Lite UDP要求を送り、ERXUDPのデータ部を返す。

        ERXUDPのデータ部は``output_mode=01``（WOPT 01）により16進ASCIIで
        受信する前提とする。設定は``configure``で実機読出し・差分書込み・
        再確認されるため、このメソッドから無条件にFLASHへ書き込まない。
        """

        if not isinstance(ipv6_address, IPv6Address):
            raise AdapterCommunicationError("UDP送信先IPv6アドレスが不正です。")
        if not isinstance(payload, bytes) or not payload:
            raise AdapterCommunicationError("UDP送信データが空または不正です。")
        if len(payload) > 0xFFFF:
            raise AdapterCommunicationError("UDP送信データが65535バイトを超えています。")

        command_prefix = RS_WSUHA_P_B_ROUTE_COMMANDS["send_udp"].format(
            # SKSENDTOもメーカー公開サンプルに合わせ固定長IPv6表記を使用する。
            address=ipv6_address.exploded.upper(),
            length=f"{len(payload):04X}",
        ).encode("ascii")

        with self._request_lock:
            self._wait_for_startup()
            response_deadline = self._response_deadline(
                self._response_timeout_seconds
            )
            with self._translate_transport_errors():
                self._transport.write(command_prefix + payload)
                logger.debug(
                    "RS-WSUHA-PへSKSENDTO要求を送信しました bytes=%d",
                    len(payload),
                )
                self._wait_for_udp_send_ack(response_deadline)
                return self._wait_for_udp_payload(
                    response_deadline,
                    expected_sender=ipv6_address,
                    response_matcher=response_matcher,
                )

    def _setting_command(self, name: str) -> AdapterSettingCommand:
        try:
            return RS_WSUHA_P_SETTING_COMMANDS[name]
        except KeyError as exc:
            raise UnsupportedAdapterSettingError(
                f"未対応のRS-WSUHA-P設定です: {name}"
            ) from exc

    def _execute_command(
        self,
        command: str,
        response_parser: Callable[[bytes], _ResponseValue | None],
        *,
        sensitive: bool = False,
        timeout_seconds: float | None = None,
    ) -> _ResponseValue:
        with self._request_lock:
            self._wait_for_startup()
            response_deadline = self._response_deadline(
                self._response_timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            )
            with self._translate_transport_errors():
                encoded_command = self._send_command(
                    command,
                    sensitive=sensitive,
                )
                return self._wait_for_response(
                    encoded_command,
                    response_parser,
                    response_deadline,
                    sensitive=sensitive,
                )

    def _send_command(self, command: str, *, sensitive: bool = False) -> bytes:
        encoded_command = command.encode("ascii")
        self._transport.write(encoded_command + COMMAND_TERMINATOR)
        if sensitive:
            command_name = command.partition(" ")[0]
            logger.debug(
                "RS-WSUHA-P認証コマンドを送信しました: %s ***",
                command_name,
            )
        else:
            logger.debug("RS-WSUHA-Pコマンドを送信しました: %s", command)
        return encoded_command

    def _wait_for_response(
        self,
        encoded_command: bytes,
        response_parser: Callable[[bytes], _ResponseValue | None],
        response_deadline: float,
        *,
        sensitive: bool = False,
    ) -> _ResponseValue:
        while True:
            line = self._read_response_line(response_deadline)
            normalized_line = line.strip(b" \t")
            if normalized_line == encoded_command:
                logger.debug("RS-WSUHA-Pのコマンドechoを受信しました。")
                continue

            parsed = response_parser(normalized_line)
            if parsed is not None:
                return parsed

            # TODO: 公式資料でエラー応答の形式を確認できた場合に限り、
            # 専用例外への変換を追加する。
            if sensitive:
                logger.debug(
                    "RS-WSUHA-P認証コマンドの要求外行をマスクして無視しました。"
                )
            else:
                logger.debug(
                    "RS-WSUHA-Pの要求外行を無視しました: %s",
                    normalized_line.decode("ascii", errors="replace"),
                )

    def _collect_scan_results(
        self,
        response_deadline: float,
    ) -> tuple[ActiveScanResult, ...]:
        results: list[ActiveScanResult] = []
        current_fields: dict[str, str] | None = None

        while True:
            line = self._read_response_line(response_deadline).strip(b" \t")
            if line.startswith(b"EVENT 22"):
                if current_fields is not None:
                    results.append(self._parse_scan_result(current_fields))
                return tuple(results)

            if line == b"EPANDESC":
                if current_fields is not None:
                    results.append(self._parse_scan_result(current_fields))
                current_fields = {}
                continue

            if current_fields is None or b":" not in line:
                logger.debug(
                    "スキャン中の要求外行を無視しました: %s",
                    line.decode("ascii", errors="replace"),
                )
                continue

            key_bytes, value_bytes = line.split(b":", 1)
            try:
                key = key_bytes.strip().decode("ascii")
                value = value_bytes.strip().decode("ascii")
            except UnicodeDecodeError as exc:
                raise AdapterScanError(
                    "アクティブスキャン応答に非ASCIIフィールドがあります。"
                ) from exc
            if not key or not value:
                raise AdapterScanError(
                    "アクティブスキャン応答に空のフィールドがあります。"
                )
            current_fields[key] = value

    def _wait_for_udp_send_ack(self, response_deadline: float) -> None:
        while True:
            line = self._read_response_line(response_deadline).strip(b" \t")
            if line == b"OK":
                return
            if line.startswith(b"SKSENDTO"):
                logger.debug("RS-WSUHA-PのSKSENDTO echoを受信しました。")
                continue
            logger.debug(
                "SKSENDTO受付待ちの要求外行を無視しました: %s",
                line.decode("ascii", errors="replace"),
            )

    def _wait_for_udp_payload(
        self,
        response_deadline: float,
        *,
        expected_sender: IPv6Address,
        response_matcher: Callable[[bytes], bool] | None,
    ) -> bytes:
        while True:
            line = self._read_response_line(response_deadline).strip(b" \t")
            if not line.startswith(b"ERXUDP "):
                logger.debug(
                    "ERXUDP待ちの要求外行を無視しました: %s",
                    line.decode("ascii", errors="replace"),
                )
                continue

            fields = line.split()
            if len(fields) != 10:
                logger.warning("不正なERXUDPフィールド数を無視しました。")
                continue
            try:
                sender = IPv6Address(fields[1].decode("ascii"))
                data_length = int(fields[8], 16)
                payload_hex = fields[9].decode("ascii")
                payload = bytes.fromhex(payload_hex)
            except (UnicodeDecodeError, ValueError) as exc:
                logger.warning("不正なERXUDPを無視しました: %s", exc)
                continue
            if sender != expected_sender:
                logger.debug("異なる送信元のERXUDPを無視しました: %s", sender)
                continue
            if len(payload) != data_length:
                logger.warning(
                    "ERXUDPデータ長が一致しないため無視しました: expected=%d actual=%d",
                    data_length,
                    len(payload),
                )
                continue
            if response_matcher is not None and not response_matcher(payload):
                logger.debug("要求と対応しないERXUDPデータを無視しました。")
                continue
            return payload

    def _parse_scan_result(
        self,
        fields: Mapping[str, str],
    ) -> ActiveScanResult:
        channel = self._scan_hex_field(fields, "Channel", digits=2)
        pan_id = self._scan_hex_field(fields, "Pan ID", digits=4)
        address = self._scan_hex_field(fields, "Addr", digits=16)
        channel_page = self._optional_scan_hex_field(
            fields,
            "Channel Page",
            digits=2,
        )
        lqi = self._optional_scan_hex_field(fields, "LQI", digits=2)
        pair_id = self._optional_scan_hex_field(fields, "PairID", digits=8)
        normalized_fields = {
            key: value.upper() for key, value in fields.items()
        }
        return ActiveScanResult(
            channel=channel,
            pan_id=pan_id,
            address=address,
            channel_page=channel_page,
            lqi=lqi,
            pair_id=pair_id,
            raw_fields=normalized_fields,
        )

    def _scan_hex_field(
        self,
        fields: Mapping[str, str],
        name: str,
        *,
        digits: int,
    ) -> str:
        try:
            value = fields[name]
        except KeyError as exc:
            raise AdapterScanError(
                f"アクティブスキャン応答に{name}がありません。"
            ) from exc
        return self._normalize_hex_token(value, digits=digits, label=name)

    def _optional_scan_hex_field(
        self,
        fields: Mapping[str, str],
        name: str,
        *,
        digits: int,
    ) -> str | None:
        value = fields.get(name)
        if value is None:
            return None
        return self._normalize_hex_token(value, digits=digits, label=name)

    @staticmethod
    def _normalize_hex_token(value: str, *, digits: int, label: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != digits
            or any(character not in "0123456789abcdefABCDEF" for character in value)
        ):
            raise AdapterScanError(
                f"{label}には{digits}桁の16進数が必要です。"
            )
        return value.upper()

    @staticmethod
    def _normalize_credential_token(
        value: str,
        *,
        expected_bytes: int,
        label: str,
    ) -> str:
        try:
            encoded = value.encode("ascii")
        except (AttributeError, UnicodeEncodeError):
            encoded = b""
        if len(encoded) != expected_bytes or any(
            byte < 0x21 or byte > 0x7E for byte in encoded
        ):
            raise AdapterCredentialError(
                f"Bルート{label}の形式が不正です。"
                f"空白を含まないASCII {expected_bytes}バイトで指定してください。"
            )
        return value

    def _response_deadline(self, timeout_seconds: float) -> float:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        return self._monotonic() + timeout_seconds

    @contextmanager
    def _translate_transport_errors(self) -> Iterator[None]:
        try:
            yield
        except SerialTimeoutError as exc:
            self._clear_receive_state()
            raise AdapterResponseTimeoutError(
                "RS-WSUHA-Pから確認済み形式の応答を受信できませんでした。"
            ) from exc
        except SerialDisconnectedError as exc:
            self._clear_receive_state()
            raise AdapterCommunicationError(
                "RS-WSUHA-Pとのシリアル接続が切断されました。"
            ) from exc
        except TransportError as exc:
            self._clear_receive_state()
            raise AdapterCommunicationError(
                "RS-WSUHA-Pとのシリアル通信に失敗しました。"
            ) from exc

    def _wait_for_startup(self) -> None:
        if not self._transport.is_open:
            raise AdapterCommunicationError(
                "RS-WSUHA-Pのシリアルポートが開かれていません。"
            )
        if self._startup_wait_pending:
            self._sleeper(STARTUP_WAIT_SECONDS)
            self._startup_wait_pending = False

    def _read_response_line(self, response_deadline: float) -> bytes:
        while True:
            if self._shutdown_event is not None and self._shutdown_event.is_set():
                raise AdapterOperationCancelled("終了要求によりRS-WSUHA-P操作を中断しました。")
            if self._received_lines:
                return self._received_lines.popleft()

            remaining_seconds = response_deadline - self._monotonic()
            if remaining_seconds <= 0:
                raise SerialTimeoutError(
                    "RS-WSUHA-P応答の要求全体タイムアウトです。"
                )

            chunk = self._transport.read(
                READ_SIZE_BYTES,
                timeout_seconds=min(
                    remaining_seconds,
                    SHUTDOWN_POLL_SECONDS if self._shutdown_event is not None else remaining_seconds,
                ),
            )
            try:
                self._received_lines.extend(self._line_buffer.feed(chunk))
            except _LineTooLongError:
                # 不要通知が壊れていてもメモリーを無制限に使わず、次の行を待つ。
                logger.warning(
                    "RS-WSUHA-Pから上限を超える受信行を破棄しました。"
                )

    @staticmethod
    def _parse_read_setting_response(line: bytes) -> str | None:
        match = _READ_SETTING_RESPONSE_PATTERN.fullmatch(line)
        if match is None:
            return None
        return match.group(1).decode("ascii").upper()

    @staticmethod
    def _parse_write_setting_response(line: bytes) -> bool | None:
        return True if line == b"OK" else None

    @staticmethod
    def _parse_ok_response(line: bytes) -> bool | None:
        return True if line == b"OK" else None

    @staticmethod
    def _parse_ipv6_response(line: bytes) -> IPv6Address | None:
        try:
            return IPv6Address(line.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None

    def _prepare_new_serial_session(self) -> None:
        self._startup_wait_pending = True
        self._clear_receive_state()

    def _clear_receive_state(self) -> None:
        self._line_buffer.clear()
        self._received_lines.clear()


# 製品名をそのまま大文字で表した表記も利用可能にする。
RSWSUHAPAdapter = RsWsuhaPAdapter
