"""ECHONET Lite経由のスマートメータープロパティ取得。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from ipaddress import IPv6Address
from typing import Protocol

from broute_meter.adapter.base import AdapterCommunicationError
from broute_meter.echonet.frame import (
    CONTROLLER_EOJ,
    ESV_GET_RESPONSE,
    LOW_VOLTAGE_SMART_METER_EOJ,
    EchonetFrame,
    TidGenerator,
    build_get_request,
)
from broute_meter.echonet.parser import EchonetFrameError, parse_frame
from broute_meter.meter.properties import (
    COEFFICIENT_EPC,
    ENERGY_UNIT_EPC,
    INSTANTANEOUS_POWER_EPC,
    SIGNIFICANT_DIGITS_EPC,
    TIMED_FORWARD_ENERGY_EPC,
    TIMED_REVERSE_ENERGY_EPC,
    MeterPropertyError,
    convert_cumulative_energy,
    parse_coefficient,
    parse_energy_unit,
    parse_instantaneous_power,
    parse_significant_digits,
    parse_timed_cumulative_energy,
)
from broute_meter.models import CumulativeEnergyReading, InstantaneousPowerReading

ESV_GET_SNA = 0x52
logger = logging.getLogger(__name__)


class SmartMeterError(RuntimeError):
    """スマートメーター要求または応答の検証に失敗した。"""


class EchonetDatagramAdapter(Protocol):
    """ECHONET Lite UDPデータグラム交換に必要なアダプター境界。"""

    def exchange_udp(
        self,
        ipv6_address: IPv6Address,
        payload: bytes,
        *,
        response_matcher: Callable[[bytes], bool] | None = None,
    ) -> bytes:
        """要求を送り、1つのUDP応答ペイロードを返す。"""
        ...


class SmartMeterClient:
    """低圧スマート電力量メーターへ直列にGet要求を送る。"""

    def __init__(
        self,
        adapter: EchonetDatagramAdapter,
        ipv6_address: IPv6Address,
        *,
        tid_generator: TidGenerator | None = None,
        now: Callable[[], datetime] | None = None,
        request_max_attempts: int = 1,
        request_timeout_seconds: float = 5.0,
    ) -> None:
        if request_max_attempts < 1:
            raise ValueError("request_max_attempts must be at least one")
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be greater than zero")
        self._adapter = adapter
        self._ipv6_address = ipv6_address
        self._tid_generator = tid_generator or TidGenerator()
        self._now = now or (lambda: datetime.now().astimezone())
        self._request_max_attempts = request_max_attempts
        self._request_timeout_seconds = request_timeout_seconds
        self._coefficient: int | None = None
        self._significant_digits: int | None = None
        self._unit_kwh: Decimal | None = None

    def get_instantaneous_power(self) -> InstantaneousPowerReading:
        """E7を1回取得し、正が買電・負が逆潮流のW値として返す。"""

        # E7は10秒周期を優先する。同期的な即時再試行は次周期を遅延させる
        # ため、失敗時はこの周期の欠測として呼出元へ返す。
        edt = self._get_property(INSTANTANEOUS_POWER_EPC, max_attempts=1)
        assert edt is not None

        try:
            net_power_w = parse_instantaneous_power(edt)
        except MeterPropertyError as exc:
            raise SmartMeterError("E7のEDTが不正です。") from exc

        return InstantaneousPowerReading(
            measured_at=self._now(),
            net_power_w=net_power_w,
        )

    def get_cumulative_energy(self) -> CumulativeEnergyReading:
        """D3/D7/E1を適用し、最新EAおよび任意のEBを取得する。"""

        coefficient, _, unit_kwh = self._get_energy_conversion_properties()
        forward_edt = self._get_property(TIMED_FORWARD_ENERGY_EPC)
        reverse_edt = self._get_property(
            TIMED_REVERSE_ENERGY_EPC,
            optional=True,
        )
        assert forward_edt is not None

        try:
            metered_at, forward_raw = parse_timed_cumulative_energy(forward_edt)
            if forward_raw is None:
                raise SmartMeterError("EAは計測データなしを示しています。")

            reverse_raw: int | None = None
            if reverse_edt is not None:
                reverse_metered_at, reverse_raw = parse_timed_cumulative_energy(
                    reverse_edt
                )
                if reverse_metered_at != metered_at:
                    raise SmartMeterError("EAとEBの計量時刻が一致しません。")

            forward_total = convert_cumulative_energy(
                forward_raw,
                coefficient=coefficient,
                unit_kwh=unit_kwh,
            )
            reverse_total = (
                convert_cumulative_energy(
                    reverse_raw,
                    coefficient=coefficient,
                    unit_kwh=unit_kwh,
                )
                if reverse_raw is not None
                else None
            )
        except MeterPropertyError as exc:
            raise SmartMeterError("積算電力量プロパティのEDTが不正です。") from exc

        return CumulativeEnergyReading(
            metered_at=metered_at,
            received_at=self._now(),
            forward_raw=forward_raw,
            reverse_raw=reverse_raw,
            forward_total_kwh=forward_total,
            reverse_total_kwh=reverse_total,
        )

    def _get_energy_conversion_properties(self) -> tuple[int, int, Decimal]:
        if (
            self._coefficient is not None
            and self._significant_digits is not None
            and self._unit_kwh is not None
        ):
            return (
                self._coefficient,
                self._significant_digits,
                self._unit_kwh,
            )

        coefficient_edt = self._get_property(COEFFICIENT_EPC, optional=True)
        digits_edt = self._get_property(SIGNIFICANT_DIGITS_EPC)
        unit_edt = self._get_property(ENERGY_UNIT_EPC)
        assert digits_edt is not None
        assert unit_edt is not None

        try:
            self._coefficient = (
                parse_coefficient(coefficient_edt)
                if coefficient_edt is not None
                else 1
            )
            self._significant_digits = parse_significant_digits(digits_edt)
            self._unit_kwh = parse_energy_unit(unit_edt)
        except MeterPropertyError as exc:
            raise SmartMeterError("積算電力量換算プロパティのEDTが不正です。") from exc
        return self._coefficient, self._significant_digits, self._unit_kwh

    def _get_property(
        self,
        epc: int,
        *,
        optional: bool = False,
        max_attempts: int | None = None,
    ) -> bytes | None:
        attempts = self._request_max_attempts if max_attempts is None else max_attempts
        if attempts < 1:
            raise ValueError("max_attempts must be at least one")
        last_error: AdapterCommunicationError | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = self._get_property_once(
                    epc,
                    optional=optional,
                    attempt=attempt,
                    max_attempts=attempts,
                )
            except AdapterCommunicationError as exc:
                last_error = exc
                logger.debug(
                    "ECHONET Lite Get失敗 tid_attempt=%d epc=%02X "
                    "timeout_seconds=%s retry_result=%s",
                    attempt,
                    epc,
                    self._request_timeout_seconds,
                    "exhausted" if attempt >= attempts else "will_retry",
                )
                if attempt >= attempts:
                    raise
                logger.warning(
                    "EPC %02Xの取得に失敗したため再試行します attempt=%d/%d",
                    epc,
                    attempt,
                    attempts,
                )
            else:
                logger.debug(
                    "ECHONET Lite Get成功 epc=%02X attempt=%d/%d retry_result=%s",
                    epc,
                    attempt,
                    attempts,
                    "retried_success" if attempt > 1 else "first_attempt_success",
                )
                return result
        assert last_error is not None
        raise last_error

    def _get_property_once(
        self,
        epc: int,
        *,
        optional: bool,
        attempt: int,
        max_attempts: int,
    ) -> bytes | None:
        tid = self._tid_generator.next()
        request = build_get_request(tid, epc)
        sent_at = self._now()
        logger.debug(
            "ECHONET Lite Get送信 tid=%04X epc=%02X sent_at=%s "
            "timeout_seconds=%s attempt=%d/%d",
            tid,
            epc,
            sent_at.isoformat(),
            self._request_timeout_seconds,
            attempt,
            max_attempts,
        )
        try:
            response_payload = self._adapter.exchange_udp(
                self._ipv6_address,
                request.to_bytes(),
                response_matcher=lambda payload: _matches_get_response(
                    payload,
                    expected_tid=tid,
                    expected_epc=epc,
                ),
            )
        except AdapterCommunicationError:
            logger.debug(
                "ECHONET Lite Getタイムアウト tid=%04X epc=%02X sent_at=%s "
                "timeout_seconds=%s attempt=%d/%d",
                tid,
                epc,
                sent_at.isoformat(),
                self._request_timeout_seconds,
                attempt,
                max_attempts,
            )
            raise
        response = parse_frame(response_payload)
        _validate_response_envelope(response, expected_tid=tid)
        matching = [prop for prop in response.properties if prop.epc == epc]
        if len(matching) != 1:
            raise SmartMeterError(f"EPC {epc:02X}が応答に1件だけ含まれていません。")
        logger.debug(
            "ECHONET Lite応答照合成功 request_tid=%04X response_tid=%04X "
            "request_epc=%02X response_epc=%02X",
            tid,
            response.tid,
            epc,
            matching[0].epc,
        )
        if response.esv == ESV_GET_SNA:
            if optional:
                return None
            raise SmartMeterError(
                f"EPC {epc:02X}のGet要求がGet_SNAで拒否されました。"
            )
        return matching[0].edt


def _validate_response_envelope(
    response: EchonetFrame,
    *,
    expected_tid: int,
) -> None:
    if response.tid != expected_tid:
        raise SmartMeterError(
            f"ECHONET Lite応答のTIDが一致しません: expected={expected_tid:04X}"
        )
    if response.seoj != LOW_VOLTAGE_SMART_METER_EOJ:
        raise SmartMeterError("応答SEOJが低圧スマート電力量メーターではありません。")
    if response.deoj != CONTROLLER_EOJ:
        raise SmartMeterError("応答DEOJが要求元コントローラーではありません。")
    if response.esv not in (ESV_GET_RESPONSE, ESV_GET_SNA):
        raise SmartMeterError(
            f"ECHONET Lite Get応答ではありません: ESV={response.esv:02X}"
        )


def _matches_get_response(
    payload: bytes,
    *,
    expected_tid: int,
    expected_epc: int,
) -> bool:
    """不正電文、通知、別要求の応答を要求全体の期限内で読み飛ばす。"""

    try:
        frame = parse_frame(payload)
    except EchonetFrameError:
        logger.debug("ECHONET Lite応答を破棄しました reason=invalid_frame")
        return False
    matched = (
        frame.tid == expected_tid
        and frame.seoj == LOW_VOLTAGE_SMART_METER_EOJ
        and frame.deoj == CONTROLLER_EOJ
        and frame.esv in (ESV_GET_RESPONSE, ESV_GET_SNA)
        and any(prop.epc == expected_epc for prop in frame.properties)
    )
    if not matched:
        response_epcs = ",".join(f"{prop.epc:02X}" for prop in frame.properties)
        logger.debug(
            "ECHONET Lite応答を破棄しました expected_tid=%04X response_tid=%04X "
            "expected_epc=%02X response_epcs=%s",
            expected_tid,
            frame.tid,
            expected_epc,
            response_epcs,
        )
    return matched
