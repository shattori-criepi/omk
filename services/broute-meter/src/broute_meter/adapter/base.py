"""Bルートアダプター設定処理の抽象境界。"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

logger = logging.getLogger(__name__)


class AdapterError(RuntimeError):
    """Bルートアダプターの処理に失敗した。"""


class AdapterCommunicationError(AdapterError):
    """アダプターとの通信に失敗した。"""


class AdapterResponseTimeoutError(AdapterCommunicationError):
    """アダプターから確認済み形式の応答を受信できなかった。"""


class AdapterOperationCancelled(AdapterError):
    """終了要求により進行中のアダプター操作を中断した。"""


class AdapterSettingError(AdapterError):
    """アダプター設定が不正、未対応、または検証不能だった。"""


class UnsupportedAdapterSettingError(AdapterSettingError):
    """アダプターが対応していない設定名を指定した。"""


class InvalidAdapterSettingValueError(AdapterSettingError):
    """アダプター設定値の形式が不正だった。"""


class AdapterVerificationError(AdapterSettingError):
    """設定書込み後の再読出し結果が期待値と一致しなかった。"""

    def __init__(self, name: str, expected: str, actual: str) -> None:
        self.name = name
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"アダプター設定の書込みを確認できませんでした: {name} "
            f"(expected={expected}, actual={actual})"
        )


class AdapterCredentialError(AdapterError):
    """Bルート認証情報を安全なコマンドへ変換できなかった。"""


class AdapterScanError(AdapterError):
    """アクティブスキャンの応答が欠落または不正だった。"""


class AdapterPanaJoinError(AdapterError):
    """PANA接続がドングルから失敗として通知された。"""


# 呼出し側で簡潔な名前を使えるようにする後方互換用の別名。
AdapterTimeoutError = AdapterResponseTimeoutError


@dataclass(frozen=True, slots=True)
class AdapterConfigurationResult:
    """設定確認・変更処理の結果。

    ``initial_settings`` は必ずドングルから読んだ値であり、ローカルフラグから
    復元した値ではない。``changed_settings`` には、今回実際に書き込んで再確認
    できた設定名だけが入る。
    """

    expected_settings: Mapping[str, str]
    initial_settings: Mapping[str, str]
    final_settings: Mapping[str, str]
    changed_settings: tuple[str, ...]
    write_changes: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_settings",
            MappingProxyType(dict(self.expected_settings)),
        )
        object.__setattr__(
            self,
            "initial_settings",
            MappingProxyType(dict(self.initial_settings)),
        )
        object.__setattr__(
            self,
            "final_settings",
            MappingProxyType(dict(self.final_settings)),
        )
        object.__setattr__(self, "changed_settings", tuple(self.changed_settings))

    @property
    def is_configured(self) -> bool:
        """最終読出し値がすべての期待値と一致する場合にTrueを返す。"""

        return all(
            self.final_settings.get(name) == expected
            for name, expected in self.expected_settings.items()
        )

    @property
    def changed(self) -> bool:
        """今回1件以上の設定を書き込んだ場合にTrueを返す。"""

        return bool(self.changed_settings)

    @property
    def mismatched_settings(self) -> tuple[str, ...]:
        """最終読出し値が期待値と一致しない設定名を返す。"""

        return tuple(
            name
            for name, expected in self.expected_settings.items()
            if self.final_settings.get(name) != expected
        )


# CLI側で「setup」の語を使う場合にも同じ型を参照できる。
AdapterSetupResult = AdapterConfigurationResult


class BaseAdapter(ABC):
    """設定のread/compare/write/verify手順を共通化する基底クラス。"""

    def __init__(self) -> None:
        self._configuration_lock = threading.RLock()

    @abstractmethod
    def normalize_setting_value(self, name: str, value: str) -> str:
        """設定名と値を検証し、比較・送信用の正規形を返す。"""

    @abstractmethod
    def read_setting(self, name: str) -> str:
        """指定した設定を実機またはモックから読み出す。"""

    @abstractmethod
    def write_setting(self, name: str, value: str) -> None:
        """指定した設定を書き込む。書込み後の検証は呼出し側で行う。"""

    def configure(
        self,
        expected_settings: Mapping[str, str],
        *,
        write_changes: bool = True,
    ) -> AdapterConfigurationResult:
        """毎回現在値を読み、不一致の場合だけ任意で書き込んで再確認する。

        最初に全設定を読み出すため、読出しに失敗した場合は書込みを開始しない。
        ``write_changes=False`` の場合も比較結果は返すが、書込みは行わない。

        Args:
            expected_settings: 設定名から期待値へのマッピング。
            write_changes: 不一致設定の書込みを許可するか。

        Raises:
            AdapterError: 読出し、書込み、または書込み後の確認に失敗した場合。
        """

        normalized_expected = {
            name: self.normalize_setting_value(name, value)
            for name, value in expected_settings.items()
        }

        with self._configuration_lock:
            initial = {
                name: self.normalize_setting_value(name, self.read_setting(name))
                for name in normalized_expected
            }
            final = dict(initial)
            mismatched = tuple(
                name
                for name, expected in normalized_expected.items()
                if initial[name] != expected
            )

            if not mismatched:
                logger.info("アダプター設定は期待値と一致しています。")
                return AdapterConfigurationResult(
                    expected_settings=normalized_expected,
                    initial_settings=initial,
                    final_settings=final,
                    changed_settings=(),
                    write_changes=write_changes,
                )

            if not write_changes:
                logger.warning(
                    "アダプター設定に不一致がありますが、書込みは無効です: %s",
                    ", ".join(mismatched),
                )
                return AdapterConfigurationResult(
                    expected_settings=normalized_expected,
                    initial_settings=initial,
                    final_settings=final,
                    changed_settings=(),
                    write_changes=False,
                )

            changed: list[str] = []
            for name in mismatched:
                expected = normalized_expected[name]
                logger.info("不一致のアダプター設定を書き込みます: %s", name)
                self.write_setting(name, expected)
                actual = self.normalize_setting_value(name, self.read_setting(name))
                final[name] = actual
                if actual != expected:
                    raise AdapterVerificationError(name, expected, actual)
                changed.append(name)

            return AdapterConfigurationResult(
                expected_settings=normalized_expected,
                initial_settings=initial,
                final_settings=final,
                changed_settings=tuple(changed),
                write_changes=True,
            )

    def ensure_settings(
        self,
        expected_settings: Mapping[str, str],
        *,
        write_changes: bool = True,
    ) -> AdapterConfigurationResult:
        """``configure`` の意図を明示するための同義メソッド。"""

        return self.configure(expected_settings, write_changes=write_changes)
