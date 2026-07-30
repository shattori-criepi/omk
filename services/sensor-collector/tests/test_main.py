import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from main import JsonlWriter, build_record  # noqa: E402


class Message:
    def __init__(self, payload: bytes) -> None:
        self.topic = "omk/test-001/example"
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


if __name__ == "__main__":
    unittest.main()
