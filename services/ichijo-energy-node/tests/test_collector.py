from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from ichijo_energy_node.collector import Collector


class Reader:
    values = {
        (bytes.fromhex("027901"), 0xE0): bytes.fromhex("019a"),
        (bytes.fromhex("027d01"), 0xE4): bytes.fromhex("30"),
        (bytes.fromhex("027d01"), 0xD3): bytes.fromhex("fffffd55"),
        (bytes.fromhex("027d01"), 0xCF): bytes.fromhex("43"),
        (bytes.fromhex("028701"), 0xC6): bytes.fromhex("0000000e"),
        (bytes.fromhex("02a501"), 0xE7): bytes.fromhex("fffffbbb"),
    }
    def get(self, eoj, epc): return self.values[(eoj, epc)]


def test_collector_uses_cycle_end_timestamp_and_maps_values():
    collector = Collector(Reader(), device_id="ichijo-001", property_interval_seconds=0,
                          now=lambda: datetime(2026, 8, 3, 15, 32, 14, tzinfo=ZoneInfo("Asia/Tokyo")), sleep=lambda _: None)
    result = collector.collect()
    assert result["battery_operating_state"] == "discharging"
    assert result["battery_operating_state_raw"] == 0x43
    assert result["load_power_w"] == 1107


def test_collector_keeps_partial_cycle():
    class Partial(Reader):
        def get(self, eoj, epc):
            if epc == 0xE4: raise RuntimeError("timeout")
            return super().get(eoj, epc)
    result = Collector(Partial(), device_id="id", property_interval_seconds=0, sleep=lambda _: None).collect()
    assert result["battery_soc_percent"] is None
    assert result["quality"] == "degraded"
    assert result["errors"] == ["battery_soc_read_failed"]


def test_collector_log_includes_normalizer_added_error(caplog):
    class NegativeLoad(Reader):
        values = {**Reader.values,
                  (bytes.fromhex("028701"), 0xC6): bytes.fromhex("ffffff38"),
                  (bytes.fromhex("02a501"), 0xE7): bytes.fromhex("ffffff9c")}

    with caplog.at_level(logging.INFO, logger="ichijo_energy_node.collector"):
        result = Collector(NegativeLoad(), device_id="id", property_interval_seconds=0, sleep=lambda _: None).collect()
    assert result["errors"] == ["negative_load_power"]
    assert "Collection completed quality=degraded errors=1" in caplog.text
