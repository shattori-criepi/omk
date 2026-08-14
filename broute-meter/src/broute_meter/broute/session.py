"""製品固有処理から分離したBルート接続シーケンス。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from ipaddress import IPv6Address
from typing import Callable, Protocol, runtime_checkable

from broute_meter.models import ActiveScanResult, BRouteConnection

logger = logging.getLogger(__name__)


class BRouteSessionError(RuntimeError):
    """Bルート接続シーケンスを完了できなかった。"""


class NoSmartMeterFoundError(BRouteSessionError):
    """アクティブスキャンでスマートメーター候補が見つからなかった。"""


class AmbiguousSmartMeterError(BRouteSessionError):
    """複数候補が見つかり、安全に接続先を選べなかった。"""

    def __init__(self, candidate_count: int) -> None:
        self.candidate_count = candidate_count
        super().__init__(
            f"スマートメーター候補が{candidate_count}件あります。"
            "接続先を一意に決定できません。"
        )


class InvalidBRouteCredentialsError(BRouteSessionError):
    """Bルート認証情報がコマンドへ安全に渡せる形式ではなかった。"""


@runtime_checkable
class BRouteConnectionAdapter(Protocol):
    """Bルート接続に必要な製品アダプターの最小契約。"""

    def reset(self) -> None:
        """アダプター内部の通信状態をリセットする。"""
        ...

    def set_b_route_id(self, b_route_id: str) -> None:
        """Bルート識別IDを設定する。"""
        ...

    def set_b_route_password(self, password: str) -> None:
        """Bルートパスワードを設定する。"""
        ...

    def active_scan(self) -> Sequence[ActiveScanResult]:
        """アクティブスキャンで候補を返す。"""
        ...

    def set_channel(self, channel: str) -> None:
        """接続に使用するチャンネルを設定する。"""
        ...

    def set_pan_id(self, pan_id: str) -> None:
        """接続に使用するPAN IDを設定する。"""
        ...

    def resolve_ipv6_address(self, address: str) -> IPv6Address:
        """64-bitアドレスをリンクローカルIPv6アドレスへ変換する。"""
        ...

    def join(self, ipv6_address: IPv6Address) -> None:
        """PANA接続を開始し、完了通知まで待つ。"""
        ...


class BRouteSession:
    """Bルート認証からPANA接続完了までを順序どおり実行する。"""

    def __init__(
        self,
        adapter: BRouteConnectionAdapter,
        *,
        scan_max_attempts: int = 1,
        on_state_change: Callable[[str], None] | None = None,
    ) -> None:
        if (
            not isinstance(scan_max_attempts, int)
            or isinstance(scan_max_attempts, bool)
            or scan_max_attempts <= 0
        ):
            raise ValueError("scan_max_attempts must be a positive integer")
        self._adapter = adapter
        self._scan_max_attempts = scan_max_attempts
        self._on_state_change = on_state_change
        self._connection: BRouteConnection | None = None

    @property
    def connection(self) -> BRouteConnection | None:
        """現在確立済みの接続情報を返す。"""

        return self._connection

    def connect(self, b_route_id: str, password: str) -> BRouteConnection:
        """認証情報を設定し、唯一のスキャン候補へPANA接続する。"""

        _validate_credential_token(b_route_id, expected_bytes=32, label="ID")
        _validate_credential_token(password, expected_bytes=12, label="パスワード")
        self._connection = None
        logger.info("RS-WSUHA-Pの通信状態をリセットします")
        self._adapter.reset()

        # ユーザー指定の接続順序に従い、IDを先に設定する。値はログへ出さない。
        logger.info("Bルート識別IDを設定します")
        self._adapter.set_b_route_id(b_route_id)
        logger.info("Bルートパスワードを設定します")
        self._adapter.set_b_route_password(password)

        candidates: tuple[ActiveScanResult, ...] = ()
        for attempt in range(1, self._scan_max_attempts + 1):
            self._notify_state("scanning")
            logger.info(
                "スマートメーターのアクティブスキャンを開始します "
                "attempt=%d/%d",
                attempt,
                self._scan_max_attempts,
            )
            candidates = tuple(self._adapter.active_scan())
            logger.info(
                "アクティブスキャンが完了しました "
                "attempt=%d/%d candidates=%d",
                attempt,
                self._scan_max_attempts,
                len(candidates),
            )
            if candidates:
                break
            if attempt < self._scan_max_attempts:
                logger.warning(
                    "スマートメーター候補がないためスキャンを再試行します "
                    "next_attempt=%d",
                    attempt + 1,
                )
        if not candidates:
            raise NoSmartMeterFoundError(
                "アクティブスキャンでスマートメーターが見つかりませんでした"
                f"（{self._scan_max_attempts}回試行）。"
            )
        if len(candidates) > 1:
            raise AmbiguousSmartMeterError(len(candidates))

        selected = candidates[0]
        self._adapter.set_channel(selected.channel)
        self._adapter.set_pan_id(selected.pan_id)
        ipv6_address = self._adapter.resolve_ipv6_address(selected.address)

        self._notify_state("authenticating")
        logger.info("PANA接続を開始します ipv6=%s", ipv6_address)
        self._adapter.join(ipv6_address)
        connection = BRouteConnection(
            scan_result=selected,
            smart_meter_ipv6=ipv6_address,
        )
        self._connection = connection
        logger.info("PANA接続に成功しました ipv6=%s", ipv6_address)
        return connection

    def _notify_state(self, state: str) -> None:
        if self._on_state_change is not None:
            self._on_state_change(state)


def _validate_credential_token(
    value: str,
    *,
    expected_bytes: int,
    label: str,
) -> None:
    try:
        encoded = value.encode("ascii")
    except (AttributeError, UnicodeEncodeError):
        encoded = b""
    if len(encoded) != expected_bytes or any(
        byte < 0x21 or byte > 0x7E for byte in encoded
    ):
        raise InvalidBRouteCredentialsError(
            f"Bルート{label}の形式が不正です。"
            f"空白を含まないASCII {expected_bytes}バイトで指定してください。"
        )
