"""OSに依存しないシリアルポート列挙・選択処理。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from serial.tools import list_ports

RS_WSUHA_P_PRODUCT_NAME = "RS-WSUHA-P"

# 実機は FT230X Basic UART / 0403:6015 として見えるが、他のFT230Xと
# 区別できない。製品名による候補抽出はbest-effortであり、この実機では
# stable by-idの明示設定を必要とする。VID/PIDだけで選択しない。


class _SerialPortLike(Protocol):
    """pyserialのListPortInfoから利用する属性。"""

    device: str
    product: str | None
    description: str | None
    manufacturer: str | None
    vid: int | None
    pid: int | None
    serial_number: str | None


PortProvider = Callable[[], Iterable[_SerialPortLike]]


@dataclass(frozen=True, slots=True)
class PortInfo:
    """CLI表示およびポート選択に使用するシリアルポート情報。"""

    device: str
    product: str | None
    manufacturer: str | None
    vid: int | None
    pid: int | None
    serial_number: str | None


class PortDetectionError(RuntimeError):
    """シリアルポートの検出または選択に失敗した。"""


class NoMatchingPortError(PortDetectionError):
    """RS-WSUHA-Pの候補ポートが見つからなかった。"""

    def __init__(self) -> None:
        super().__init__(
            "RS-WSUHA-Pの候補ポートが見つかりません。"
            "FT230XのUSB descriptorだけでは機種を確認できません。"
            "シリアルポートを明示的に設定してください（stable by-id推奨）。"
        )


class AmbiguousPortsError(PortDetectionError):
    """RS-WSUHA-Pの候補ポートが複数あり、一意に選択できなかった。"""

    def __init__(self, candidates: Sequence[PortInfo]) -> None:
        self.candidates = tuple(candidates)
        devices = ", ".join(candidate.device for candidate in self.candidates)
        super().__init__(
            f"RS-WSUHA-Pの候補ポートが複数あります: {devices}。"
            "シリアルポートを明示的に設定してください。"
        )


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_port_info(port: _SerialPortLike) -> PortInfo:
    raw_device = getattr(port, "device", None)
    if raw_device is None:
        raise PortDetectionError("デバイス名がないシリアルポートを検出しました。")
    device = str(raw_device)
    if not device:
        raise PortDetectionError("デバイス名が空のシリアルポートを検出しました。")

    product = _optional_text(getattr(port, "product", None))
    if product is None:
        product = _optional_text(getattr(port, "description", None))

    return PortInfo(
        device=device,
        product=product,
        manufacturer=_optional_text(getattr(port, "manufacturer", None)),
        vid=getattr(port, "vid", None),
        pid=getattr(port, "pid", None),
        serial_number=_optional_text(getattr(port, "serial_number", None)),
    )


def list_serial_ports(provider: PortProvider | None = None) -> list[PortInfo]:
    """利用可能なポートを列挙し、デバイス名順で返す。

    Args:
        provider: テスト用に差し替え可能なポート列挙関数。省略時はpyserialを使う。

    Raises:
        PortDetectionError: ポート列挙または取得結果の正規化に失敗した場合。
    """

    selected_provider: PortProvider = provider if provider is not None else list_ports.comports
    try:
        ports = [_to_port_info(port) for port in selected_provider()]
    except PortDetectionError:
        raise
    except OSError as exc:
        raise PortDetectionError("シリアルポートの列挙に失敗しました。") from exc
    return sorted(ports, key=lambda port: port.device)


def format_vid_pid(value: int | None) -> str:
    """VIDまたはPIDを4桁の大文字16進数へ整形する。

    値を取得できない場合は、CLIで空欄表示できるよう空文字列を返す。
    """

    return "" if value is None else f"{value:04X}"


def find_rs_wsuha_p_ports(ports: Iterable[PortInfo]) -> list[PortInfo]:
    """製品名を含む場合だけ候補を返す（実機全般の自動検出ではない）。

    汎用FT230X descriptorは機種の確証にならず、明示portが必要。
    この候補抽出はprivileged recoveryのtrust確立には使用しない。
    """

    marker = RS_WSUHA_P_PRODUCT_NAME.casefold()
    return [port for port in ports if port.product and marker in port.product.casefold()]


def resolve_serial_port(
    configured_port: str | None,
    candidates: Iterable[PortInfo],
) -> str:
    """明示設定または自動検出候補から使用するデバイス名を決定する。

    明示設定がある場合は候補一覧を参照せず、その値を優先する。自動検出候補が
    0件または複数件の場合は、推測で選択せず例外を送出する。
    """

    if configured_port is not None and configured_port.strip():
        return configured_port.strip()

    detected = tuple(candidates)
    if not detected:
        raise NoMatchingPortError
    if len(detected) > 1:
        raise AmbiguousPortsError(detected)
    return detected[0].device
