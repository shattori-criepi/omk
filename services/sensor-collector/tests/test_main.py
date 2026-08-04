import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

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
            "omk/broute-001/power": "broute_power.json",
            "omk/sen66-001/environment": "sen66.json",
            "omk/ichijo-001/power-flow": "ichijo_power_flow.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            for topic, filename in cases.items():
                record = build_record(Message(b'{"value":3}', topic), self.received_at)
                writer.write(record)
                self.assertEqual(json.loads((Path(directory) / filename).read_text(encoding="utf-8")), record)

    def test_latest_writer_ignores_unsupported_and_non_json_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            writer.write(build_record(Message(b'{"value":3}', "omk/test-001/status"), self.received_at))
            writer.write(build_record(Message(b'{"value":3}', "omk/test-001/other"), self.received_at))
            writer.write(build_record(Message(b"not-json", "omk/test-001/power"), self.received_at))
            writer.write(build_record(Message(b"\xff", "omk/test-001/power"), self.received_at))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_latest_writer_replaces_existing_data_without_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = LatestDataWriter(Path(directory))
            first = build_record(Message(b'{"watts":100}', "omk/test-001/power"), self.received_at)
            second = build_record(Message(b'{"watts":200}', "omk/test-001/power"), self.received_at)
            writer.write(first)
            writer.write(second)

            output = Path(directory) / "broute_power.json"
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), second)
            self.assertEqual(list(Path(directory).iterdir()), [output])


if __name__ == "__main__":
    unittest.main()
