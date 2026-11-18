"""Real fixed-memory diagnostic core; no radio/scheduler behavior is simulated."""
from pathlib import Path
import importlib.util
import subprocess
import pytest

BASE = Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module")
def diagnostic_core(tmp_path_factory):
    binary = tmp_path_factory.mktemp("diag-core") / "test"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
        "-fsanitize=undefined", "-fno-sanitize-recover=all",
        "-I", str(BASE / "src"), str(BASE / "src/diag_core.c"),
        str(BASE / "tests/diag_core_host.c"), "-o", str(binary),
    ], check=True)
    return binary

@pytest.mark.parametrize("case", [
    "receipt_clock", "normal", "transitions", "sustained", "inflight", "occasional", "kind",
    "episodes", "demand", "recovery", "ring", "root", "root_failure", "root_downlink", "role_clocks", "idle_resume", "idle_kind_inflight", "demand_gap", "peers", "time64", "parser", "buckets",
])
def test_diagnostic_evidence(diagnostic_core, case):
    subprocess.run([str(diagnostic_core), case], check=True, timeout=10)


def test_payload_decoder_roundtrip(diagnostic_core):
    payload = subprocess.check_output([str(diagnostic_core), "dump"])
    spec = importlib.util.spec_from_file_location("diag_payload", BASE.parent / "tools/diag_payload.py")
    decoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    result = decoder.decode_payload(payload)
    assert len(payload) == 4056
    assert result["role_changed_ms"] == 0
    # Schema 3 uses payload length to retain compatibility with pre-P1 Flash.
    old = bytearray(payload[:4048]); old[4:8] = (4048).to_bytes(4, "little")
    legacy = decoder.decode_payload(bytes(old))
    assert legacy["counts"] == result["counts"] and "role_changed_ms" not in legacy
    assert result["first_error"]["code_name"] == "ESP_ERR_MESH_TIMEOUT"
    assert result["first_error"]["identity"]["seq"] == 2
    assert result["counts"]["ESP_OK"] == 2
    assert result["events"][1]["code_name"] == "ESP_ERR_MESH_NO_MEMORY"
    assert result["events"][2]["code_name"] == "ESP_OK"
    assert result["interval_from_baseline"]["ESP_ERR_MESH_TIMEOUT"] == 2
    assert result["capture_reason_name"] == "FOLLOWUP"
    assert result["context"]["topology"]["layer"] == 3
    assert result["first_seen_ms"]["ESP_ERR_MESH_TIMEOUT"] == 7000
    assert result["interval_from_current"]["ESP_ERR_MESH_TIMEOUT"] == 1
    assert result["observation_start_ms"] == 0
    for invalid in (payload[:-1], b"\x04\x00\x00\x00" + payload[4:]):
        with pytest.raises(ValueError):
            decoder.decode_payload(invalid)


def test_synthetic_reconstruction(diagnostic_core):
    payloads = subprocess.check_output([str(diagnostic_core), "synthetic_dump"])
    assert len(payloads) == 2 * 4056
    spec = importlib.util.spec_from_file_location("diag_payload", BASE.parent / "tools/diag_payload.py")
    decoder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(decoder)
    first = decoder.decode_payload(payloads[:4056])
    after = decoder.decode_payload(payloads[4056:])
    assert first["capture_reason_name"] == "KIND_STALL"
    assert first["counts"]["ESP_ERR_MESH_NO_MEMORY"] == 18
    assert after["context"]["recovery_requests"] == 1
    assert after["context"]["recovery_ok"] == 1
    assert after["context"]["recovery_link"] == 1
    assert after["context"]["root_changes"] == 1
    assert after["last_ok_identity"]["seq"] == 43
    assert after["last_receipt"]["seq"] == 9
    assert after["context"]["mqtt_pubacks"] == 0
    assert after["first_error"]["code_name"] == "ESP_ERR_MESH_TIMEOUT"
    assert after["event_overwrites"] > 0
