import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from harvest_uploader.aggregation import MinuteAggregator
from harvest_uploader.queue import RetryQueue
from harvest_uploader.service import MqttRuntime, Uploader

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


def test_environment_sensors_average_independently_and_coexist_with_sen66():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/sen66-001/sen66", {"temperature_celsius": 27}, at(5))
    aggregator.ingest("omk/th-001/environment", {"temperature_c": 28, "relative_humidity_percent": 40}, at(10))
    aggregator.ingest("omk/th-001/environment", {"temperature_c": 30, "relative_humidity_percent": 44}, at(20))
    aggregator.ingest("omk/th-002/environment", {"temperature_c": 26, "relative_humidity_percent": 89}, at(30))
    aggregator.ingest("omk/co2-001/environment", {"temperature_c": 27, "relative_humidity_percent": 39, "co2_ppm": 580}, at(40))
    aggregator.ingest("omk/co2-001/environment", {"temperature_c": 29, "relative_humidity_percent": 41, "co2_ppm": 592}, at(50))
    assert aggregator.flush_due(at(0, 35)) == {
        "time": "2026-08-05T10:34:00+09:00",
        "sen66_temperature_c": 27,
        "th-001_temperature_c": 29,
        "th-001_relative_humidity_percent": 42,
        "th-002_temperature_c": 26,
        "th-002_relative_humidity_percent": 89,
        "co2-001_temperature_c": 28,
        "co2-001_relative_humidity_percent": 40,
        "co2-001_co2_ppm": 586,
    }


def test_environment_ignores_missing_none_nonfinite_invalid_and_excluded_topics():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/th-001/environment", {"temperature_c": None, "relative_humidity_percent": float("nan")}, at(5))
    aggregator.ingest("omk/th-001/environment", {"temperature_c": 25}, at(10))
    aggregator.ingest("omk/bad.id/environment", {"temperature_c": 99}, at(15))
    aggregator.ingest("omk/th-001/status", {"temperature_c": 99}, at(30))
    assert aggregator.flush_due(at(0, 35)) == {
        "time": "2026-08-05T10:34:00+09:00", "th-001_temperature_c": 25,
    }


def test_motion_and_contact_states_are_aggregated_per_sensor_with_environment():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/th-001/environment", {"temperature_c": 28}, at(5))
    for second, state in ((10, 0), (20, 1), (30, 0)):
        aggregator.ingest("omk/motion-001/motion", {"motion_state": state}, at(second))
    aggregator.ingest("omk/motion-002/motion", {"motion_state": 0}, at(35))
    for second, state in ((10, 0), (20, 1), (30, 1), (40, 0)):
        aggregator.ingest("omk/contact-001/contact", {"contact_state": state}, at(second))
    for second in (15, 45):
        aggregator.ingest("omk/contact-002/contact", {"contact_state": 1}, at(second))
    assert aggregator.flush_due(at(0, 35)) == {
        "time": "2026-08-05T10:34:00+09:00",
        "th-001_temperature_c": 28,
        "motion-001_motion_state": 1,
        "motion-002_motion_state": 0,
        "contact-001_contact_changed": 1,
        "contact-001_contact_state": 0,
        "contact-002_contact_changed": 0,
        "contact-002_contact_state": 1,
    }


def test_contact_tracks_state_changes_across_minute_boundaries_and_ignores_invalid_states():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/contact-001/contact", {"contact_state": 0}, at(50))
    first = aggregator.ingest("omk/contact-001/contact", {"contact_state": 1}, at(5, 35))
    assert first == {
        "time": "2026-08-05T10:34:00+09:00",
        "contact-001_contact_changed": 0,
        "contact-001_contact_state": 0,
    }
    aggregator.ingest("omk/contact-001/contact", {"contact_state": None}, at(10, 35))
    aggregator.ingest("omk/contact-001/contact", {"contact_state": True}, at(15, 35))
    aggregator.ingest("omk/contact-001/contact", {"contact_state": 2}, at(20, 35))
    aggregator.ingest("omk/bad.id/contact", {"contact_state": 0}, at(25, 35))
    assert aggregator.flush_due(at(0, 36)) == {
        "time": "2026-08-05T10:35:00+09:00",
        "contact-001_contact_changed": 1,
        "contact-001_contact_state": 1,
    }


def test_plug_power_averages_and_keeps_last_switch_state_without_affecting_broute():
    aggregator = MinuteAggregator()
    aggregator.ingest("omk/plug-001/power", {"power_w": 5.0, "switch_state": 0}, at(5))
    aggregator.ingest("omk/plug-001/power", {"power_w": 5.2, "switch_state": 1}, at(15))
    aggregator.ingest("omk/plug-001/power", {"power_w": 5.4, "switch_state": 1}, at(25))
    aggregator.ingest("omk/plug-002/power", {"power_w": 173.2, "switch_state": 1}, at(35))
    aggregator.ingest("omk/plug-001/power", {"power_w": None, "switch_state": 2}, at(40))
    aggregator.ingest("omk/plug-001/power", {"power_w": float("nan"), "switch_state": True}, at(45))
    aggregator.ingest("omk/broute-001/power", {"net_power_w": -100}, at(50))
    aggregator.ingest("omk/bad.id/power", {"power_w": 999, "switch_state": 1}, at(55))
    assert aggregator.flush_due(at(0, 35)) == {
        "time": "2026-08-05T10:34:00+09:00",
        "plug-001_power_w": 5.2,
        "plug-001_switch_state": 1,
        "plug-002_power_w": 173.2,
        "plug-002_switch_state": 1,
        "broute_grid_power_w": -100,
        "broute_grid_import_power_w": 0,
        "broute_grid_export_power_w": 100,
    }


class Sender:
    def __init__(self, fail=False): self.fail, self.calls = fail, []
    def send(self, payload):
        self.calls.append(payload)
        if self.fail: raise OSError("offline")


class FakeMqttClient:
    def __init__(self, **_kwargs):
        self.on_connect = self.on_disconnect = self.on_message = None

    def reconnect_delay_set(self, **_kwargs): pass
    def connect_async(self, *_args, **_kwargs): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass


class Message:
    def __init__(self, topic: str, payload: bytes):
        self.topic, self.payload = topic, payload


def runtime_for(uploader: Uploader, callback_clock) -> MqttRuntime:
    return MqttRuntime(
        uploader, host="mqtt", port=1883, client_id="test", client_factory=FakeMqttClient, clock=callback_clock,
    )


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


def test_mqtt_callback_thread_only_enqueues_before_main_thread_drain(tmp_path: Path, caplog):
    queue = RetryQueue(tmp_path / "queue.sqlite3")
    sender = Sender(fail=True)
    uploader = Uploader(queue, sender, clock=lambda: at(0, 35))
    uploader.receive("omk/a/sen66", b'{"temperature_celsius":20}', at(5))
    runtime = runtime_for(uploader, lambda: at(5, 35))
    errors = []

    def invoke_callback() -> None:
        try:
            runtime._on_message(None, None, Message("omk/a/sen66", b'{"temperature_celsius":30}'))
        except Exception as error:  # pragma: no cover - asserted below
            errors.append(error)

    with caplog.at_level(logging.ERROR, logger="harvest_uploader.service"):
        callback_thread = threading.Thread(target=invoke_callback)
        callback_thread.start()
        callback_thread.join()

    assert errors == []
    assert not any(
        isinstance(record.exc_info[1], sqlite3.ProgrammingError)
        for record in caplog.records
        if record.exc_info is not None
    )
    assert queue.count() == 0
    runtime._drain_inbox()
    assert queue.count() == 1


def test_mqtt_callback_time_is_used_when_drain_crosses_minute_boundary(tmp_path: Path):
    queue = RetryQueue(tmp_path / "queue.sqlite3")
    sender = Sender()
    uploader = Uploader(queue, sender, clock=lambda: at(1, 35))
    runtime = runtime_for(uploader, lambda: at(59, 34))

    runtime._on_message(None, None, Message("omk/a/sen66", b'{"temperature_celsius":20}'))
    runtime._drain_inbox()
    uploader.tick()

    assert sender.calls == [{"time": "2026-08-05T10:34:00+09:00", "sen66_temperature_c": 20}]


def test_mqtt_inbox_fifo_completes_one_old_minute_without_leaking_into_next(tmp_path: Path):
    callback_times = iter((at(10, 34), at(40, 34), at(0, 35)))
    queue = RetryQueue(tmp_path / "queue.sqlite3")
    now = at(1, 35)
    uploader = Uploader(queue, Sender(fail=True), clock=lambda: now)
    runtime = runtime_for(uploader, lambda: next(callback_times))

    for value in (10, 20, 30):
        runtime._on_message(None, None, Message("omk/a/sen66", json.dumps({"temperature_celsius": value}).encode()))
    runtime._drain_inbox()

    first = queue.next_due(at(1, 35))
    assert first is not None
    assert first[1] == {"time": "2026-08-05T10:34:00+09:00", "sen66_temperature_c": 15}
    now = at(0, 36)
    uploader.tick()
    assert queue.count() == 2
    second = queue.next_due(at(0, 36))
    assert second is not None
    assert second[1] == {"time": "2026-08-05T10:35:00+09:00", "sen66_temperature_c": 30}
