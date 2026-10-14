"""Execute the production recorder/observer with fake time, NVS, RTC and USB."""
import importlib.util
import json
from pathlib import Path
import subprocess
import os
import pty
import select
import threading
import runpy
import shutil

import pytest

PROJECT = Path(__file__).parents[1]
HOST = Path(__file__).parent / "network_diag_host"


@pytest.fixture(scope="module")
def binary(tmp_path_factory):
    root = tmp_path_factory.mktemp("network-diag")
    for name in ("esp_timer.h", "esp_app_desc.h", "esp_attr.h", "esp_random.h",
                 "esp_system.h", "freertos/FreeRTOS.h", "freertos/task.h",
                 "nvs.h", "nvs_flash.h", "driver/usb_serial_jtag.h"):
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#include "stubs.h"\n')
    output = root / "diag-test"
    result = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread",
                             "-DOMK_NETWORK_DIAGNOSTICS=1", "-I", str(root), "-I", str(HOST),
                             "-I", str(PROJECT / "src"), str(HOST / "test.c"), "-o", str(output)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return output


@pytest.mark.parametrize("scenario", ["timing", "parallel", "capacity", "token_wrap", "error_codes", "event_stall", "persistence",
    "no_periodic_write", "failure", "space", "corrupt", "rtc_interrupted", "budget", "usb", "report", "multiple_restarts", "resolved_pending", "severity", "priority", "early_budget", "report_busy", "budget_restore", "fault_slot_parity", "minor_after_critical"])
def test_recorder_and_storage(binary, scenario):
    result = subprocess.run([str(binary), scenario], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    if scenario == "usb":
        assert len(records) == 81
        assert all(r["nd"] == 1 for r in records)
    if scenario == "report":
        assert len(records) == 3
        assert records[0]["stats"][3][1] == 1
        assert records[0]["active"][0][2] == 3


def test_patch_is_reproducible_and_rejects_other_sources(tmp_path):
    path = PROJECT / "scripts/prepare_mqtt_diagnostics.py"
    spec = importlib.util.spec_from_file_location("prepare_diag", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = PROJECT / "managed_components/espressif__mqtt/mqtt_client.c"
    before = source.read_bytes()
    output = tmp_path / "mqtt_client.c"
    module.prepare(source, output)
    first = output.read_bytes()
    module.prepare(source, output)
    assert output.read_bytes() == first and source.read_bytes() == before
    assert first.count(b"network_diag_begin(") == 3
    assert first.count(b"network_diag_wait(") == 2
    wrong = tmp_path / "wrong.c"
    wrong.write_bytes(before + b"\n")
    with pytest.raises(ValueError, match="refusing build"):
        module.prepare(wrong, output)


def test_platformio_incremental_hook_rechecks_source(tmp_path):
    for folder in ("scripts", "patches"):
        shutil.copytree(PROJECT / folder, tmp_path / folder)
    original = PROJECT / "managed_components/espressif__mqtt/mqtt_client.c"
    source = tmp_path / "managed_components/espressif__mqtt/mqtt_client.c"
    source.parent.mkdir(parents=True)
    shutil.copy2(original, source)

    class Environment:
        def subst(self, name):
            return str(tmp_path if name == "$PROJECT_DIR" else tmp_path / "build")

    def hook():
        runpy.run_path(str(tmp_path / "scripts/verify_network_diagnostics.py"),
                       init_globals={"Import": lambda name: None, "env": Environment()})

    hook()
    generated = tmp_path / "build/network_diagnostics/mqtt_client.c"
    expected = generated.read_bytes()
    generated.write_text("stale copy")
    hook()
    assert generated.read_bytes() == expected
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="refusing build"):
        hook()


@pytest.mark.parametrize("wrong_node", [False, True])
def test_usb_capture_over_native_serial_protocol(tmp_path, wrong_node):
    """Exercise the real CLI through a pseudo-terminal, without any device."""
    master, slave = pty.openpty()
    output = tmp_path / "capture.jsonl"
    commands = []
    failures = []
    stop = threading.Event()

    def node():
        pending = b""
        try:
            while not stop.is_set():
                if not select.select([master], [], [], 0.1)[0]:
                    continue
                pending += os.read(master, 4096)
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    fields = line.decode().split()
                    assert len(fields) == 3 and fields[0] == "OMKDIAG"
                    view, page = map(int, fields[1:])
                    commands.append((view, page))
                    reply = {"nd": 1, "node": "9af9509eb8b6" if wrong_node else "9490b46aee0d",
                             "v": view, "p": page}
                    os.write(master, b"console log\n" + json.dumps(reply).encode() + b"\n")
        except Exception as error:
            failures.append(error)

    worker = threading.Thread(target=node)
    worker.start()
    try:
        result = subprocess.run(["python3", str(PROJECT / "scripts/capture_network_diagnostics.py"),
                                 "--port", os.ttyname(slave), "--node", "9490b46aee0d",
                                 "--output", str(output), "--once"],
                                capture_output=True, text=True, timeout=10)
    finally:
        stop.set()
        worker.join(timeout=2)
        os.close(master)
        os.close(slave)
    assert not failures
    if wrong_node:
        assert result.returncode == 1 and "Node ID mismatch" in result.stderr
        assert commands == [(0, 0)]
    else:
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in output.read_text().splitlines()]
        assert len(records) == 81
        assert commands == [(view, page) for view in range(3) for page in range(27)]
        assert output.stat().st_mode & 0o777 == 0o600


def test_relay_only_node_records_saves_and_sends_without_waiting_for_mqtt(tmp_path):
    """Real recorder/store + registration/sender + patched try-enqueue helper."""
    diag_headers = tmp_path / "diag"
    delivery_headers = tmp_path / "delivery"
    for root, adapter, names in (
        (diag_headers, "stubs.h", ("esp_timer.h", "esp_app_desc.h", "esp_attr.h", "esp_random.h",
         "esp_system.h", "freertos/FreeRTOS.h", "freertos/task.h", "nvs.h", "nvs_flash.h",
         "driver/usb_serial_jtag.h")),
        (delivery_headers, "idf_stubs.h", ("esp_err.h", "esp_timer.h", "esp_random.h", "esp_log.h",
         "esp_event.h", "esp_netif_ip_addr.h", "esp_wifi.h", "nvs.h", "mqtt_client.h",
         "freertos/FreeRTOS.h", "freertos/task.h", "driver/i2c_master.h")),
    ):
        for name in names:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f'#include "{adapter}"\n')
    module = runpy.run_path(str(PROJECT / "scripts/prepare_mqtt_diagnostics.py"))
    generated = tmp_path / "mqtt_client.c"
    module["prepare"](PROJECT / "managed_components/espressif__mqtt/mqtt_client.c", generated)
    vendor = generated.read_text()
    start = vendor.index("int esp_mqtt_client_enqueue_bounded(")
    end = vendor.index("esp_err_t esp_mqtt_client_register_event(", start)
    (delivery_headers / "diagnostic_mqtt.inc").write_text(vendor[start:end])
    start = vendor.index("esp_err_t esp_mqtt_dispatch_custom_event(")
    end = vendor.index("static esp_err_t deliver_publish(", start)
    (delivery_headers / "mqtt_dispatch.inc").write_text(vendor[start:end])
    flags = ["cc", "-std=c11", "-D_GNU_SOURCE", "-DOMK_NETWORK_DIAGNOSTICS=1",
             "-Wall", "-Wextra", "-Werror", "-pthread", "-I", str(PROJECT / "src")]
    bridge = tmp_path / "bridge.o"
    result = subprocess.run(flags + ["-I", str(diag_headers), "-I", str(HOST),
        "-c", str(HOST / "bridge.c"), "-o", str(bridge)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    delivery = Path(__file__).parent / "delivery_host"
    cjson = PROJECT / "managed_components/espressif__cjson/cJSON"
    binary = tmp_path / "relay-test"
    result = subprocess.run(flags + ["-I", str(delivery_headers), "-I", str(delivery),
        "-I", str(cjson), str(delivery / "delivery_test.c"), str(cjson / "cJSON.c"),
        str(bridge), "-lm", "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary), "relay_diagnostics"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["schema"] == 2 and record["critical"] == 1 and record["saves"] == 1
