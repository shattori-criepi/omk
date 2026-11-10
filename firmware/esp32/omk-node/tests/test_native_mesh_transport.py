from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def native_transport_host(tmp_path_factory):
    firmware = Path(__file__).resolve().parents[1]
    executable = tmp_path_factory.mktemp("native-transport") / "test"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
        "-I", str(firmware / "src"),
        str(firmware / "src/mesh_wire.c"),
        str(firmware / "src/mesh_session.c"),
        str(firmware / "tests/native_transport_host.c"),
        "-o", str(executable),
    ], check=True)
    return executable


@pytest.mark.parametrize("case", [
    "wire", "topics", "fragments", "demotion", "disconnect", "backpressure", "rootless", "expiry",
])
def test_native_transport_contract(native_transport_host, case):
    subprocess.run([str(native_transport_host), case], check=True)
