import json
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ble_route_selector import BleRouteSelector, should_store_record  # noqa: E402
from latest_store import stable_item_id  # noqa: E402
from main import JsonlWriter, LatestDataWriter, build_record  # noqa: E402


class Message:
    def __init__(self, payload: bytes, topic: str = "omk/test-001/example") -> None:
        self.topic = topic
        self.qos = 0
        self.retain = False
        self.payload = payload


class CollectorTests(unittest.TestCase):
    received_at = datetime(2026, 7, 30, 10, 54, 12, 123000, tzinfo=ZoneInfo("Asia/Tokyo"))

    def test_valid_json_is_nested_without_interpretation(self) -> None:
        record = build_record(Message(b'{"unknown_field":{"value":3}}'), self.received_at)
        self.assertEqual(record["payload"], {"unknown_field": {"value": 3}})
        self.assertEqual(record["received_at"], "2026-07-30T10:54:12.123+09:00")

    def test_invalid_and_binary_payloads_are_preserved(self) -> None:
        invalid = build_record(Message(b"not-json"), self.received_at)
        binary = build_record(Message(b"\xff\x00"), self.received_at)
        self.assertEqual(invalid["payload_raw"], "not-json")
        self.assertIn("payload_parse_error", invalid)
        self.assertEqual(binary["payload_base64"], "/wA=")
        self.assertEqual(binary["payload_encoding"], "base64")

    def test_writer_appends_jsonl_in_jst_date_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = JsonlWriter(Path(directory))
            writer.write({"topic": "omk/a"}, self.received_at)
            writer.write({"topic": "omk/b"}, self.received_at)
            output = Path(directory) / "2026" / "07" / "30.jsonl"
            lines = output.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["topic"] for line in lines], ["omk/a", "omk/b"])

    def test_latest_writer_saves_each_supported_topic_with_full_record(self) -> None:
        cases = {
            "omk/broute-001/power": ("broute_power.json", {"device_id": "broute-001", "net_power_w": 3}),
            "omk/sen66-001/sen66": ("sen66.json", {"device_id": "sen66-001", "value": 3}),
            "omk/ichijo-001/power-flow": ("ichijo_power_flow.json", {"device_id": "ichijo-001", "value": 3}),
        }
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            for topic, (filename, payload) in cases.items():
                record = build_record(Message(json.dumps(payload).encode(), topic), self.received_at)
                writer.write(record)
                self.assertEqual(json.loads((Path(directory) / filename).read_text(encoding="utf-8")), record)

    def test_latest_writer_ignores_unsupported_and_non_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            writer.write(build_record(Message(b'{"value":3}', "omk/test-001/status"), self.received_at))
            writer.write(build_record(Message(b'{"device_id":"test-001","value":3}', "omk/test-001/other"), self.received_at))
            writer.write(build_record(Message(b"not-json", "omk/test-001/power"), self.received_at))
            writer.write(build_record(Message(b"\xff", "omk/test-001/power"), self.received_at))
            # Generic latest values intentionally include unknown data types;
            # only invalid/non-JSON messages leave no output.
            self.assertTrue((Path(directory) / "catalog.json").exists())
            self.assertFalse((Path(directory) / "broute_power.json").exists())

    def test_retained_offline_status_does_not_replace_fresh_sen66_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = LatestDataWriter(root)
            offline = build_record(
                Message(b'{"device_id":"sen66-001","status":"offline"}', "omk/sen66-001/status"),
                self.received_at,
            )
            fresh_sen66 = build_record(
                Message(b'{"device_id":"sen66-001","temperature_celsius":25.4}', "omk/sen66-001/sen66"),
                self.received_at,
            )

            writer.write(offline)
            writer.write(fresh_sen66)

            self.assertEqual(
                json.loads((root / "sen66.json").read_text(encoding="utf-8")),
                fresh_sen66,
            )

    def test_latest_writer_replaces_existing_data_without_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            first = build_record(Message(b'{"device_id":"test-001","net_power_w":100}', "omk/test-001/power"), self.received_at)
            second = build_record(Message(b'{"device_id":"test-001","net_power_w":200}', "omk/test-001/power"), self.received_at)
            writer.write(first)
            writer.write(second)

            output = Path(directory) / "broute_power.json"
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), second)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o644)
            self.assertTrue((Path(directory) / "catalog.json").exists())

    def test_generic_latest_separates_same_topic_type_and_same_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = LatestDataWriter(root)
            broute = build_record(
                Message(b'{"device_id":"broute-001","net_power_w":1200}', "omk/broute-001/power"),
                self.received_at,
            )
            plug = build_record(
                Message(b'{"device_id":"plug-001","power_w":12.3,"switch_state":true}', "omk/plug-001/power"),
                self.received_at,
            )
            living = build_record(
                Message(b'{"device_id":"living","temperature_c":25.0}', "omk/living/environment"),
                self.received_at,
            )
            bedroom = build_record(
                Message(b'{"device_id":"bedroom","temperature_c":23.0}', "omk/bedroom/environment"),
                self.received_at,
            )
            for record in (broute, plug, living, bedroom):
                writer.write(record)

            catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
            ids = {item["id"] for item in catalog["items"]}
            expected = {
                stable_item_id("omk/broute-001/power", "broute-001", "net_power_w"),
                stable_item_id("omk/plug-001/power", "plug-001", "power_w"),
                stable_item_id("omk/plug-001/power", "plug-001", "switch_state"),
                stable_item_id("omk/living/environment", "living", "temperature_c"),
                stable_item_id("omk/bedroom/environment", "bedroom", "temperature_c"),
            }
            self.assertEqual(ids, expected)
            self.assertEqual(len(list((root / "items").glob("*.json"))), len(expected))
            self.assertEqual(json.loads((root / "broute_power.json").read_text(encoding="utf-8")), broute)

    def test_generic_latest_excludes_metadata_and_keeps_scalar_enums_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = build_record(Message(json.dumps({
                "device_id": "node-001", "measured_at": "2026-07-30T10:54:00+09:00",
                "received_at": "2026-07-30T10:54:01+09:00", "timestamp": "123", "quality": "normal",
                "errors": [], "relay_node_id": "relay-1", "temperature_c": 25.2,
                "switch_state": False, "battery_state": "charging",
            }).encode(), "omk/node-001/environment"), self.received_at)
            LatestDataWriter(root).write(record)
            # A fresh writer must load, retain, and extend the previous catalog.
            LatestDataWriter(root).write(build_record(
                Message(b'{"device_id":"node-001","contact_state":"open"}', "omk/node-001/contact"),
                self.received_at,
            ))
            catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
            fields = {item["field"]: item["value_type"] for item in catalog["items"]}
            self.assertEqual(fields, {
                "temperature_c": "number", "switch_state": "boolean",
                "battery_state": "string", "contact_state": "string",
            })

    def test_ble_route_selection_prefers_direct_and_limits_relay_fallback(self) -> None:
        selector = BleRouteSelector()

        def environment(device_id: str, source: str | None) -> dict:
            payload = {"device_id": device_id, "temperature_c": 24.4}
            if source is not None:
                payload["source"] = source
            return {"topic": f"omk/{device_id}/environment", "payload": payload}

        self.assertTrue(should_store_record(environment("meter-001", "direct"), selector, 0.0))
        self.assertFalse(should_store_record(environment("meter-001", "relay"), selector, 0.0))
        self.assertFalse(should_store_record(environment("meter-001", "relay"), selector, 29.999))
        self.assertTrue(should_store_record(environment("meter-001", "relay"), selector, 30.0))
        self.assertTrue(should_store_record(environment("meter-001", "direct"), selector, 31.0))
        self.assertFalse(should_store_record(environment("meter-001", "relay"), selector, 31.001))
        self.assertTrue(should_store_record(environment("meter-002", "relay"), selector, 31.001))
        self.assertTrue(should_store_record(environment("meter-003", None), selector, 31.001))
        self.assertTrue(should_store_record(
            {"topic": "omk/meter-004/sen66", "payload": {"device_id": "meter-004", "source": "direct"}},
            selector,
            31.001,
        ))
        self.assertTrue(should_store_record(environment("meter-004", "relay"), selector, 31.001))


if __name__ == "__main__":
    unittest.main()
