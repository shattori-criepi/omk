"""Execute the production lifecycle/RX functions against a deterministic SDK double.

The double models IDF's copied input + FIFO tcpip IPC, not the radio or lwIP itself.
OMK_NETIF_SOURCE can point at a saved baseline for the two negative regressions.
"""
import os
from pathlib import Path
import subprocess

import pytest

PROJECT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def lifecycle_binary(tmp_path_factory):
    source = Path(os.environ.get("OMK_NETIF_SOURCE", PROJECT / "src/mesh_netif.c")).read_text()
    # Compile the real state, attach callbacks, RX and ALL lifecycle entry points.
    # TX has its own production-code harness (test_mesh_tx_queue.py).
    state = source[source.index("#define MESH_NETIF_RX_SIZE"):source.index("bool mesh_netif_allow_data")]
    helpers = source[source.index("static const char *dhcp_status_name"):source.index("/* Copy Ethernet frames")]
    lifecycle = source[source.index("static esp_err_t attach_root_ap_driver"):]
    harness = (PROJECT / "tests/netif_host/harness.c").read_text()
    program = harness.replace("/* PRODUCTION */", state + helpers + lifecycle)
    if "static void receive_frame(" in source:
        program = "#define FIXED_LIFECYCLE 1\n" + program
    directory = tmp_path_factory.mktemp("netif")
    path = directory / "netif.c"
    path.write_text(program)
    binary = directory / "netif"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    "-pthread", "-fsanitize=undefined", "-g", str(path), "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("scenario", ["rx_destroy", "late_rx", "partial_retry", "failures", "roles", "buffers", "events"])
def test_production_lifecycle(lifecycle_binary, scenario):
    subprocess.run([str(lifecycle_binary), scenario], check=True, timeout=10)
