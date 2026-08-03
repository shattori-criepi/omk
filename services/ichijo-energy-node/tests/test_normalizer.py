from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from ichijo_energy_node.normalizer import battery_state, normalize, signed_4, unsigned_1, unsigned_2


@pytest.mark.parametrize(("function", "value", "expected"), [
    (unsigned_2, "06d1", 1745), (unsigned_1, "30", 48),
    (signed_4, "0000017b", 379), (signed_4, "fffffd55", -683),
    (signed_4, "0000007f", 127), (signed_4, "ffffffe4", -28),
    (signed_4, "fffff92f", -1745), (signed_4, "fffffbbb", -1093),
])
def test_confirmed_numeric_decoding(function, value, expected):
    assert function(bytes.fromhex(value)) == expected


@pytest.mark.parametrize(("value", "expected"), [("42", "charging"), ("43", "discharging"), ("44", "standby"), ("99", "unknown")])
def test_battery_state(value, expected):
    assert battery_state(bytes.fromhex(value)) == expected


@pytest.mark.parametrize(("raw", "expected_load"), [
    ({"pv_power_w": 1745, "battery_raw_w": 0, "grid_raw_w": 127, "pcs_raw_w": -1745}, 1872),
    ({"pv_power_w": 1527, "battery_raw_w": 379, "grid_raw_w": -28, "pcs_raw_w": -1148}, 1120),
    ({"pv_power_w": 410, "battery_raw_w": -683, "grid_raw_w": 14, "pcs_raw_w": -1093}, 1107),
])
def test_power_flow_normalization(raw, expected_load):
    message = normalize(raw, device_id="ichijo-001", measured_at=datetime(2026, 8, 3, tzinfo=ZoneInfo("Asia/Tokyo")), errors=[])
    assert message["load_power_w"] == expected_load
    assert message["quality"] == "normal"


def test_missing_required_power_degrades_and_nulls_load():
    message = normalize({"pv_power_w": 410, "battery_raw_w": -683}, device_id="id", measured_at=datetime.now(ZoneInfo("Asia/Tokyo")), errors=["grid_power_read_failed"])
    assert message["load_power_w"] is None and message["quality"] == "degraded"
