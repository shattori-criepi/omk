"""Exercise the real flash script without PlatformIO downloads or USB hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "flash-omk-node.sh"
NODE_ID = "9af9509eb8b6"  # Existing FNV-1a vector for ac:a7:04:03:d7:f8.
SECRET = "ab" * 32  # Synthetic fixture only.

FAKE_ESPTOOL = '''import json, os, pathlib, sys
args = sys.argv[1:]
assert args[:2] == ["--port", "/dev/fake port"]
with open(os.environ["EVENTS"], "a") as log:
    if "read_mac" in args:
        log.write("mac\\n")
        if os.environ.get("FAIL_AT") == "mac": sys.exit(1)
        if "MAC_OUTPUT" in os.environ:
            print(os.environ["MAC_OUTPUT"])
        else:
            print("MAC: " + os.environ.get("MAC_VALUE", "ac:a7:04:03:d7:f8"))
        if os.environ.get("MAC_EXIT_FAILURE"): sys.exit(1)
    else:
        assert args[2:4] == ["write_flash", "0xf000"]
        record = pathlib.Path(args[4])
        assert record.read_bytes() == b"OMKP" + bytes.fromhex("ab" * 32)
        assert record.stat().st_mode & 0o777 == 0o600
        assert record.parent.stat().st_mode & 0o777 == 0o700
        store = pathlib.Path(os.environ["FIXTURE_ROOT"]) / "data/provisioning/nodes"
        credential = json.loads(next(store.glob("*.json")).read_text())
        assert credential["provisioning_secret"] == "ab" * 32
        log.write("factory\\n")
        if os.environ.get("FAIL_AT") == "factory": sys.exit(1)
'''


@pytest.fixture
def flash(tmp_path):
    root = tmp_path / "repository with spaces"
    (root / "scripts").mkdir(parents=True)
    project = root / "firmware/esp32/omk-node"
    project.mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts/flash-omk-node.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    events = tmp_path / "events"
    core = tmp_path / "custom core"
    package = core / "packages/tool-esptoolpy-selected/esptool.py"
    source = tmp_path / "fake_esptool.py"
    source.write_text(FAKE_ESPTOOL)
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    modules = tmp_path / "modules"
    (modules / "platformio/platform").mkdir(parents=True)
    (modules / "platformio/__init__.py").touch()
    (modules / "platformio/platform/__init__.py").touch()
    (modules / "platformio/platform/factory.py").write_text('''
import os
from pathlib import Path
class PlatformFactory:
    @classmethod
    def from_env(cls, env):
        assert env == "atom-s3-lite"
        assert Path.cwd() == Path(os.environ["FIXTURE_ROOT"]) / "firmware/esp32/omk-node"
        assert Path(os.environ["EVENTS"]).read_text().splitlines()[0] == "build"
        with open(os.environ["EVENTS"], "a") as log: log.write("resolve\\n")
        return cls()
    def get_package_dir(self, name):
        assert name == "tool-esptoolpy"
        if os.environ.get("FAIL_AT") == "resolve": return None
        return str(Path(os.environ["PLATFORMIO_CORE_DIR"]) / "packages/tool-esptoolpy-selected")
''')
    pio = bin_dir / "pio"
    pio.write_text(f"#!{sys.executable}\n" + '''import os, pathlib, shutil, sys
args = sys.argv[1:]
project = str(pathlib.Path(os.environ["FIXTURE_ROOT"]) / "firmware/esp32/omk-node")
assert args[:5] == ["run", "-d", project, "-e", "atom-s3-lite"]
step = "upload" if "upload" in args else "build"
with open(os.environ["EVENTS"], "a") as log: log.write(step + "\\n")
if os.environ.get("FAIL_AT") == step: sys.exit(1)
if step == "build" and os.environ.get("FAIL_AT") != "missing_esptool":
    target = pathlib.Path(os.environ["PLATFORMIO_CORE_DIR"]) / "packages/tool-esptoolpy-selected/esptool.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(os.environ["ESPTOOL_SOURCE"], target)
if step == "upload": assert args[5:] == ["-t", "upload", "--upload-port", "/dev/fake port"]
''')
    pio.chmod(0o755)
    openssl = bin_dir / "openssl"
    openssl.write_text(f"#!{sys.executable}\n" + '''import os, sys
assert sys.argv[1:] == ["rand", "-hex", "32"]
with open(os.environ["EVENTS"], "a") as log: log.write("secret\\n")
if os.environ.get("FAIL_AT") == "secret": sys.exit(1)
print("ab" * 32)
''')
    openssl.chmod(0o755)
    # Needed for a PATH containing no real pio in the missing-command case.
    (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
    env = {k: v for k, v in os.environ.items() if k not in {
        "PLATFORMIO_CMD", "PLATFORMIO_PYTHON", "PLATFORMIO_CORE_DIR", "ESPTOOL_PY",
    }}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", PLATFORMIO_CMD=str(pio),
               PLATFORMIO_CORE_DIR=str(core), PYTHONPATH=str(modules),
               EVENTS=str(events), FIXTURE_ROOT=str(root), ESPTOOL_SOURCE=str(source),
               TMPDIR=str(temporary))
    class Flash:
        credential = root / f"data/provisioning/nodes/{NODE_ID}.json"

        def run(self, args=None, **overrides):
            run_env = {**env, **overrides}
            run_env = {k: v for k, v in run_env.items() if v is not None}
            result = subprocess.run(
                [shutil.which("bash"), str(root / "scripts/flash-omk-node.sh"),
                 *(args if args is not None else ["atom-s3-lite", "/dev/fake port"])],
                env=run_env, capture_output=True, text=True,
            )
            assert SECRET not in result.stdout + result.stderr
            assert list(temporary.iterdir()) == []
            assert not list((root / "data/provisioning/nodes").glob(".credential.*"))
            return result

        def events(self):
            return events.read_text().splitlines() if events.exists() else []

    instance = Flash()
    instance.bin = bin_dir
    instance.package = package
    instance.source = source
    instance.pio = pio
    return instance


@pytest.mark.parametrize("args,message", [
    (["m5stick-c", "/dev/fake port"], "unsupported Node board"),
    (["atom-s3-lite"], "Specify serial port as second argument"),
    ([], "usage:"),
    (["atom-s3-lite", "/dev/fake port", "extra"], "usage:"),
])
def test_invalid_arguments_stop_before_build(flash, args, message):
    result = flash.run(args)
    assert result.returncode == 2
    assert message in result.stderr
    assert flash.events() == []


@pytest.mark.parametrize("python_override", [None, sys.executable])
def test_missing_pio_even_with_python_override(flash, python_override):
    result = flash.run(PLATFORMIO_CMD="missing-pio", PLATFORMIO_PYTHON=python_override)
    assert result.returncode != 0
    assert "Cannot locate PlatformIO CLI" in result.stderr
    assert flash.events() == []


def test_missing_default_pio(flash):
    flash.pio.unlink()
    result = flash.run(PLATFORMIO_CMD=None, PATH=str(flash.bin))
    assert result.returncode != 0
    assert "Cannot locate PlatformIO CLI: pio" in result.stderr


def test_first_build_installs_package_before_resolution_and_factory_flash(flash):
    assert not flash.package.exists()
    result = flash.run()
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "resolve", "mac", "secret", "factory", "upload"]
    assert json.loads(flash.credential.read_text()) == {
        "node_id": NODE_ID, "provisioning_secret": SECRET, "board": "atom-s3-lite",
    }
    assert flash.credential.stat().st_mode & 0o777 == 0o600
    for directory in [flash.credential.parent, flash.credential.parent.parent]:
        assert directory.stat().st_mode & 0o777 == 0o700


def test_existing_credential_is_preserved_without_factory_write(flash):
    flash.credential.parent.mkdir(parents=True)
    original = '{"provisioning_secret": "existing-secret"}\n'
    flash.credential.write_text(original)
    result = flash.run(FAIL_AT="secret")
    assert result.returncode == 0, result.stderr
    assert flash.credential.read_text() == original
    assert flash.events() == ["build", "resolve", "mac", "upload"]


def test_python_and_esptool_overrides(flash):
    # A launcher without a Python shebang still works with the explicit override.
    wrapper = flash.bin / "wrapper"
    wrapper.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{flash.pio}" "$@"\n')
    wrapper.chmod(0o755)
    result = flash.run(PLATFORMIO_CMD=str(wrapper), PLATFORMIO_PYTHON=sys.executable,
                       ESPTOOL_PY=str(flash.source), FAIL_AT="missing_esptool")
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "mac", "secret", "factory", "upload"]


@pytest.mark.parametrize("step,expected,message", [
    ("build", ["build"], "firmware build failed"),
    ("resolve", ["build", "resolve"], "Cannot resolve PlatformIO esptool.py after build"),
    ("missing_esptool", ["build", "resolve"], "Cannot locate PlatformIO esptool.py after build"),
    ("mac", ["build", "resolve", "mac"], "Cannot read ESP MAC"),
    ("secret", ["build", "resolve", "mac", "secret"], "Cannot generate provisioning secret"),
    ("factory", ["build", "resolve", "mac", "secret", "factory"], "Factory provisioning record write failed"),
    ("upload", ["build", "resolve", "mac", "secret", "factory", "upload"], "firmware upload failed"),
])
def test_failures_stop_at_the_expected_step_and_clean_up(flash, step, expected, message):
    result = flash.run(FAIL_AT=step)
    assert result.returncode != 0
    assert message in result.stderr
    assert flash.events() == expected
    assert flash.credential.exists() == (step in {"factory", "upload"})


@pytest.mark.parametrize("mac", ["", "not-a-mac", "aa:bb:cc:dd:ee"])
def test_invalid_mac_stops_before_credential_creation(flash, mac):
    result = flash.run(MAC_VALUE=mac)
    assert result.returncode != 0
    assert "Cannot read a valid ESP MAC" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]


@pytest.mark.parametrize("copies", [1, 2, 3])
def test_identical_mac_reports_allow_factory_write_and_upload(flash, copies):
    # Locally administered synthetic MAC, never taken from a physical Node.
    output = "\nUploading stub...\nRunning stub...\nStub running...\n".join(
        ["MAC: 02:00:00:00:00:ab"] * copies)
    result = flash.run(MAC_OUTPUT=output)
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "resolve", "mac", "secret", "factory", "upload"]
    assert len(list(flash.credential.parent.glob("*.json"))) == 1


def test_same_mac_with_different_hex_case_is_one_identity(flash):
    result = flash.run(MAC_OUTPUT="MAC: 02:00:00:00:00:ab\nMAC: 02:00:00:00:00:AB")
    assert result.returncode == 0, result.stderr
    assert flash.events()[-2:] == ["factory", "upload"]


@pytest.mark.parametrize("output", [
    "Uploading stub...\nStub running...",  # No MAC report.
    "MAC: 02:00:00:00:00:01\nMAC: 02:00:00:00:00:02",
    "MAC: 02:00:00:00:00:02\nMAC: 02:00:00:00:00:01",
    "MAC: malformed\nMAC: 02:00:00:00:00:01",
    "MAC: 02:00:00:00:00:01\nMAC:",
    "MAC: garbage 02:00:00:00:00:01",
    "MAC: 02:00:00:00:00:gg",
    "MAC: 02:00:00:00:00:01 extra",
])
def test_missing_ambiguous_or_malformed_mac_reports_stop_before_writes(flash, output):
    result = flash.run(MAC_OUTPUT=output)
    assert result.returncode != 0
    assert "Cannot read a valid ESP MAC" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]
    assert not list(flash.credential.parent.glob("*.json"))


def test_esptool_failure_is_not_hidden_by_valid_duplicate_mac_reports(flash):
    result = flash.run(MAC_OUTPUT="MAC: 02:00:00:00:00:01\nMAC: 02:00:00:00:00:01",
                       MAC_EXIT_FAILURE="1")
    assert result.returncode != 0
    assert "Cannot read ESP MAC" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]


def test_credential_directory_failure_stops_before_factory_write(flash):
    flash.credential.parent.parent.parent.mkdir(parents=True)
    flash.credential.parent.parent.write_text("not a directory")
    result = flash.run()
    assert result.returncode != 0
    assert "Cannot create provisioning credential directory" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]
