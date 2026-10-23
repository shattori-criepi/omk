"""Offline collector analysis: preserved evidence, boot epochs and replay."""
import json
from pathlib import Path
import runpy
import subprocess

SCRIPT = Path(__file__).parents[1] / "scripts/analyze_diagnostics.py"
MODULE = runpy.run_path(str(SCRIPT))


def record(time, topic, payload, retain=False):
    return {"received_at": time, "topic": topic, "payload": payload, "retain": retain}


def sample(seq, enq, boot="a"):
    return {"meta": {"boot_id": boot, "seq": seq, "enqueue_seq": enq}}


def test_midnight_replay_reboot_and_peer_evidence(tmp_path):
    day1, day2 = tmp_path / "22.jsonl", tmp_path / "23.jsonl"
    old = {"view": 2, "boot": "old", "seq": 20, "at_us": 999, "elf": "previous"}
    day1.write_text("\n".join(json.dumps(r) for r in [
        record("2026-10-22T23:50:00+09:00", "omk/node/n/registration/status", {"logical_id": "s"}, True),
        record("2026-10-22T23:59:50+09:00", "omk/s/sen66", sample(10, 8)),
        record("2026-10-22T23:59:51+09:00", "omk/node/n/status", {"boot_count": 4, "uptime_s": 900, "admission": [0]*6}),
        record("2026-10-22T23:59:52+09:00", "omk/node/n/network_diagnostics", old),
    ]) + "\n")
    day2.write_text("\n".join(json.dumps(r) for r in [
        record("2026-10-23T00:00:10+09:00", "omk/s/sen66", sample(14, 10)),
        record("2026-10-23T00:00:11+09:00", "omk/node/n/status", {"boot_count": 4, "uptime_s": 920, "admission": [1]*6}),
        record("2026-10-23T00:00:12+09:00", "omk/node/n/network_diagnostics", old),
        record("2026-10-23T00:00:13+09:00", "omk/node/n/network_diagnostics", {"view": 0, "boot": "new"}),
        record("2026-10-23T00:00:14+09:00", "omk/peer/sen66", sample(5, 5)),
        record("2026-10-23T00:00:20+09:00", "omk/s/sen66", sample(1, 1, "b")),
        record("2026-10-23T00:00:21+09:00", "omk/node/n/status", {"boot_count": 5, "uptime_s": 3, "admission": [0]*6}),
        record("2026-10-23T00:00:30+09:00", "omk/s/sen66", sample(1, 1, "b")),
    ]) + "\ninvalid JSON\n[]\n")
    before = [p.read_bytes() for p in (day1, day2)]
    result = list(MODULE["analyze"]([day2, day1], node="n"))
    data = [r for r in result if r.get("topic") == "omk/s/sen66"]
    assert data[1]["received_interval_s"] == 20
    assert data[1]["read_seq_delta"] == 4 and data[1]["enqueue_seq_delta"] == 2
    assert data[1]["accepted_but_not_in_log"] == 1
    assert data[1]["reads_not_accepted_between"] == 2
    assert data[2]["sensor_boot_changed"] and "read_seq_delta" not in data[2]
    assert data[3]["duplicate_sample"]
    statuses = [r for r in result if r.get("topic") == "omk/node/n/status"]
    assert statuses[1]["admission_delta"] == [1]*6
    assert statuses[2]["counter_epoch_changed"] and "admission_delta" not in statuses[2]
    history = [r for r in result if r.get("history")]
    assert len(history) == 1 and history[0]["matches_latest_live_boot"] is False
    assert result[0]["retain"] is True
    summary = result[-1]
    assert summary["duplicate_history_records"] == 1
    assert summary["mapping_candidates"] == [["n", "s"]]
    assert summary["topic_counts_all_nodes"]["omk/peer/sen66"] == 1
    assert len(summary["input_errors"]) == 2
    assert [p.read_bytes() for p in (day1, day2)] == before
    cli = subprocess.run(["python3", str(SCRIPT), str(day1), str(day2), "--logical-id", "s",
                          "--since", "2026-10-23T00:00:00+09:00"],
                         check=True, text=True, capture_output=True)
    assert json.loads(cli.stdout.splitlines()[-1])["mapping_candidates"] == [["n", "s"]]


def test_wrap_ambiguous_mapping_and_missing_boot(tmp_path):
    file = tmp_path / "input.jsonl"
    payloads = [("omk/node/n/registration/ack", {"logical_id": "s"}),
                ("omk/node/m/registration/status", {"logical_id": "s"}),
                ("omk/s/sen66", sample(0xffffffff, 0xffffffff)),
                ("omk/s/sen66", sample(0, 0)),
                ("omk/s/sen66", {}),
                ("omk/node/n/network_diagnostics", {"view": 1, "boot": "a"})]
    file.write_text("\n".join(json.dumps(record(f"2026-10-23T00:00:{i:02}+09:00", t, p))
                              for i, (t, p) in enumerate(payloads)))
    result = list(MODULE["analyze"]([file], logical_id="s"))
    assert result[3]["read_seq_delta"] == result[3]["enqueue_seq_delta"] == 1
    assert "read_seq_delta" not in result[4]
    assert result[5]["matches_latest_live_boot"] is None
    assert result[-1]["mapping_candidates"] == [["m", "s"], ["n", "s"]]
