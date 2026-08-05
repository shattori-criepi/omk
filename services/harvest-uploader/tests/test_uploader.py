import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from harvest_uploader.aggregation import MinuteAggregator
from harvest_uploader.queue import RetryQueue
from harvest_uploader.service import Uploader

JST = ZoneInfo("Asia/Tokyo")


def at(second: int, minute: int = 34) -> datetime:
    return datetime(2026, 8, 5, 10, minute, second, tzinfo=JST)


def test_aggregation_merges_topics_averages_and_uses_latest_values():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/sen66-001/sen66", {"temperature_celsius": 26, "co2_ppm": None}, at(5))
    aggregator.ingest("omk/sen66-001/sen66", {"temperature_celsius": 28, "co2_ppm": 612}, at(15))
    aggregator.ingest("omk/broute-001/power", {"net_power_w": -5800}, at(20))
    aggregator.ingest("omk/broute-001/cumulative-energy", {"cumulative_energy_import_kwh": 10, "cumulative_energy_export_kwh": 20}, at(25))
    aggregator.ingest("omk/ichijo-001/power-flow", {"pv_power_w": 6999, "battery_soc_percent": 99, "battery_operating_state": "charging"}, at(30))
    aggregator.ingest("omk/ichijo-001/power-flow", {"pv_power_w": 7001, "battery_soc_percent": 100}, at(50))
    record = aggregator.flush_due(at(0, 35))
    assert record == {"time": "2026-08-05T10:34:00+09:00", "sen66_temperature_c": 27, "sen66_co2_ppm": 612, "broute_grid_power_w": -5800, "power_system_pv_power_w": 7000, "broute_grid_import_energy_kwh": 10, "broute_grid_export_energy_kwh": 20, "power_system_battery_soc_percent": 100, "broute_grid_import_power_w": 0, "broute_grid_export_power_w": 5800}


def test_empty_and_invalid_payloads_do_not_create_records():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/a/sen66", {"temperature_celsius": None}, at(5))
    assert aggregator.flush_due(at(0, 35)) is None


def test_missing_values_are_omitted_and_latest_is_selected_by_receive_time():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/a/cumulative-energy", {"cumulative_energy_import_kwh": 20}, at(50))
    aggregator.ingest("omk/a/cumulative-energy", {"cumulative_energy_import_kwh": 10}, at(10))
    aggregator.ingest("omk/a/power-flow", {"battery_soc_percent": 80, "battery_operating_state_raw": 67}, at(15))
    record = aggregator.flush_due(at(0, 35))
    assert record == {"time": "2026-08-05T10:34:00+09:00", "broute_grid_import_energy_kwh": 20, "power_system_battery_soc_percent": 80}


def test_unrelated_and_excluded_fields_never_enter_harvest_payload():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/a/status", {"temperature_celsius": 22}, at(5))
    aggregator.ingest("omk/a/power-flow", {"battery_operating_state": "charging", "battery_operating_state_raw": 66, "grid_voltage_r_v": 101}, at(10))
    assert aggregator.flush_due(at(0, 35)) is None


class Sender:
    def __init__(self, fail=False): self.fail, self.calls = fail, []
    def send(self, payload):
        self.calls.append(payload)
        if self.fail: raise OSError("offline")


def test_queue_success_failure_retry_and_expiry(tmp_path: Path):
    now = at(0)
    queue, sender = RetryQueue(tmp_path / "queue.sqlite3"), Sender(fail=True)
    uploader = Uploader(queue, sender, clock=lambda: now)
    uploader.receive("omk/a/sen66", json.dumps({"temperature_celsius": 20}).encode(), at(5))
    uploader.tick()  # minute 10:34 is still open
    now = at(0, 35)
    uploader.tick()
    assert queue.count() == 1
    sender.fail = False
    now += timedelta(seconds=3)
    uploader.tick()
    assert queue.count() == 0 and sender.calls[-1]["sen66_temperature_c"] == 20
    queue.enqueue({"time": "old"}, now - timedelta(seconds=3601))
    assert queue.discard_expired(now, 3600) == 1


def test_valid_broute_positive_power_creates_import_derivative():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/b/power", {"net_power_w": 123}, at(5))
    assert aggregator.flush_due(at(0, 35)) == {"time": "2026-08-05T10:34:00+09:00", "broute_grid_power_w": 123, "broute_grid_import_power_w": 123, "broute_grid_export_power_w": 0}


def test_invalid_mqtt_payload_is_ignored_without_affecting_service(tmp_path: Path):
    queue = RetryQueue(tmp_path / "queue.sqlite3")
    uploader = Uploader(queue, Sender(), clock=lambda: at(5))
    uploader.receive("omk/a/sen66", b"not json")
    uploader.receive("omk/a/sen66", b"[]")
    uploader.tick()
    assert queue.count() == 0


def test_restart_does_not_restore_open_minute(tmp_path: Path):
    queue = RetryQueue(tmp_path / "queue.sqlite3")
    uploader = Uploader(queue, Sender(), clock=lambda: at(5))
    uploader.receive("omk/a/sen66", b'{"temperature_celsius":20}', at(5))
    assert queue.count() == 0
