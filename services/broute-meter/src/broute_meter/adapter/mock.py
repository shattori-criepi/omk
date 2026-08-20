"""実機なしの設定・障害テストに使用するBルートアダプター。"""

from __future__ import annotations

import re
import threading
from collections import deque
from collections.abc import Mapping
from types import MappingProxyType

from broute_meter.adapter.base import (
    AdapterCommunicationError,
    BaseAdapter,
    InvalidAdapterSettingValueError,
    UnsupportedAdapterSettingError,
)
from broute_meter.adapter.rs_wsuha_p import OUTPUT_MODE_SETTING, UART_MODE_SETTING

_HEX_BYTE_PATTERN = re.compile(r"[0-9A-Fa-f]{2}\Z")


class MockAdapter(BaseAdapter):
    """設定状態、書込み履歴、および通信失敗を制御できるモック。

    Args:
        settings: 初期設定。省略時は ``uart_mode=80``。
        fail_reads: 最初の何回の読出しを失敗させるか。
        fail_writes: 最初の何回の書込みを失敗させるか。
        apply_writes: Falseの場合、書込み要求を記録するが状態へ反映しない。
            書込み後の再確認失敗をテストするために使用する。
    """

    def __init__(
        self,
        settings: Mapping[str, str] | None = None,
        *,
        fail_reads: int = 0,
        fail_writes: int = 0,
        apply_writes: bool = True,
    ) -> None:
        super().__init__()
        if fail_reads < 0:
            raise ValueError("fail_reads must not be negative")
        if fail_writes < 0:
            raise ValueError("fail_writes must not be negative")

        selected_settings = (
            dict(settings) if settings is not None else {UART_MODE_SETTING: "80"}
        )
        self._state_lock = threading.RLock()
        self._settings = {
            name: self.normalize_setting_value(name, value)
            for name, value in selected_settings.items()
        }
        self._apply_writes = apply_writes
        self._read_failures: deque[Exception] = deque(
            AdapterCommunicationError("モックの設定読出しに失敗しました。")
            for _ in range(fail_reads)
        )
        self._write_failures: deque[Exception] = deque(
            AdapterCommunicationError("モックの設定書込みに失敗しました。")
            for _ in range(fail_writes)
        )
        self.read_history: list[str] = []
        self.write_history: list[tuple[str, str]] = []

    @property
    def settings(self) -> Mapping[str, str]:
        """現在のモック設定の読み取り専用コピーを返す。"""

        with self._state_lock:
            return MappingProxyType(dict(self._settings))

    def normalize_setting_value(self, name: str, value: str) -> str:
        """RS-WSUHA-PのUARTモード名と2桁16進値を検証する。"""

        if name not in (UART_MODE_SETTING, OUTPUT_MODE_SETTING):
            raise UnsupportedAdapterSettingError(
                f"未対応のアダプター設定です: {name}"
            )
        if not isinstance(value, str) or _HEX_BYTE_PATTERN.fullmatch(value) is None:
            raise InvalidAdapterSettingValueError(
                f"{name}には2桁の16進数を指定してください。"
            )
        return value.upper()

    def read_setting(self, name: str) -> str:
        """現在値を返す。予約済みの読出し障害があれば先に送出する。"""

        self.normalize_setting_value(name, "00")
        with self._state_lock:
            self.read_history.append(name)
            if self._read_failures:
                raise self._read_failures.popleft()
            try:
                return self._settings[name]
            except KeyError as exc:
                raise AdapterCommunicationError(
                    f"モックに設定値がありません: {name}"
                ) from exc

    def write_setting(self, name: str, value: str) -> None:
        """書込み要求を記録し、障害がなければ現在値へ反映する。"""

        normalized = self.normalize_setting_value(name, value)
        with self._state_lock:
            self.write_history.append((name, normalized))
            if self._write_failures:
                raise self._write_failures.popleft()
            if self._apply_writes:
                self._settings[name] = normalized

    def fail_next_read(self, error: Exception | None = None) -> None:
        """次の設定読出しで指定例外を送出する。"""

        selected_error = error or AdapterCommunicationError(
            "モックの設定読出しに失敗しました。"
        )
        with self._state_lock:
            self._read_failures.append(selected_error)

    def fail_next_write(self, error: Exception | None = None) -> None:
        """次の設定書込みで指定例外を送出する。"""

        selected_error = error or AdapterCommunicationError(
            "モックの設定書込みに失敗しました。"
        )
        with self._state_lock:
            self._write_failures.append(selected_error)

    def set_setting(self, name: str, value: str) -> None:
        """ドングル交換・外部変更を模擬して現在値を直接変更する。"""

        normalized = self.normalize_setting_value(name, value)
        with self._state_lock:
            self._settings[name] = normalized
