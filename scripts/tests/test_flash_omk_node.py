"""Exercise the real flash script without PlatformIO downloads or USB hardware."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "flash-omk-node.sh"
NODE_ID = "6e6005821cda"  # FNV-1a vector for a synthetic MAC.
SECRET = "ab" * 32  # Synthetic fixture only.

FAKE_ESPTOOL = '''import json, os, pathlib, sys
args = sys.argv[1:]
assert args[:2] == ["--port", "/dev/fake port"]
with open(os.environ["EVENTS"], "a") as log:
    if "read_mac" in args:
        index = pathlib.Path(os.environ["EVENTS"]).read_text().splitlines().count("mac")
        log.write("mac\\n")
        if index == 1 and os.environ.get("CONCURRENT_CREDENTIAL"):
            target = pathlib.Path(os.environ["FIXTURE_ROOT"]) / "data/provisioning/nodes/6e6005821cda.json"
            target.write_text(os.environ["CONCURRENT_CREDENTIAL"])
        if os.environ.get("FAIL_AT") == "mac": sys.exit(1)
        inspections = json.loads(os.environ.get("INSPECTIONS", "[]"))
        if inspections:
            output = inspections[index]
            if output is None: sys.exit(1)
            print(output)
        elif "MAC_OUTPUT" in os.environ:
            print(os.environ.get("CHIP_OUTPUT", "Chip is ESP32-S3 (QFN56) (revision v0.2)"))
            print(os.environ["MAC_OUTPUT"])
        else:
            print(os.environ.get("CHIP_OUTPUT", "Chip is ESP32-S3 (QFN56) (revision v0.2)"))
            print("MAC: " + os.environ.get("MAC_VALUE", "02:00:00:00:00:ab"))
        if os.environ.get("MAC_EXIT_FAILURE"): sys.exit(1)
    else:
        assert args[2:6] == ["--chip", "esp32s3", "write_flash", "0xf000"]
        record = pathlib.Path(args[6])
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

        def run(self, args=None, *, initial=False, **overrides):
            run_env = {**env, **overrides}
            run_env = {k: v for k, v in run_env.items() if v is not None}
            result = subprocess.run(
                [shutil.which("bash"), str(root / "scripts/flash-omk-node.sh"),
                 *(args if args is not None else [*(["--initial-setup"] if initial else []), "atom-s3-lite", "/dev/fake port"])],
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
    result = flash.run(initial=True)
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "resolve", "mac", "secret", "mac", "factory", "mac", "upload"]
    assert json.loads(flash.credential.read_text()) == {
        "node_id": NODE_ID, "provisioning_secret": SECRET, "board": "atom-s3-lite",
    }
    assert flash.credential.stat().st_mode & 0o777 == 0o600
    for directory in [flash.credential.parent, flash.credential.parent.parent]:
        assert directory.stat().st_mode & 0o777 == 0o700


def test_existing_credential_is_preserved_without_factory_write(flash):
    flash.credential.parent.mkdir(parents=True)
    original = json.dumps({"node_id": NODE_ID, "board": "atom-s3-lite", "provisioning_secret": "cd" * 32}) + "\n"
    flash.credential.write_text(original)
    result = flash.run(FAIL_AT="secret")
    assert result.returncode == 0, result.stderr
    assert flash.credential.read_text() == original
    assert flash.events() == ["build", "resolve", "mac", "mac", "upload"]


def test_python_and_esptool_overrides(flash):
    # A launcher without a Python shebang still works with the explicit override.
    wrapper = flash.bin / "wrapper"
    wrapper.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{flash.pio}" "$@"\n')
    wrapper.chmod(0o755)
    result = flash.run(initial=True, PLATFORMIO_CMD=str(wrapper), PLATFORMIO_PYTHON=sys.executable,
                       ESPTOOL_PY=str(flash.source), FAIL_AT="missing_esptool")
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "mac", "secret", "mac", "factory", "mac", "upload"]


@pytest.mark.parametrize("step,expected,message", [
    ("build", ["build"], "firmware build failed"),
    ("resolve", ["build", "resolve"], "Cannot resolve PlatformIO esptool.py after build"),
    ("missing_esptool", ["build", "resolve"], "Cannot locate PlatformIO esptool.py after build"),
    ("mac", ["build", "resolve", "mac"], "Cannot read ESP MAC"),
    ("secret", ["build", "resolve", "mac", "secret"], "Cannot generate provisioning secret"),
    ("factory", ["build", "resolve", "mac", "secret", "mac", "factory"], "Factory provisioning record write failed"),
    ("upload", ["build", "resolve", "mac", "secret", "mac", "factory", "mac", "upload"], "firmware upload failed"),
])
def test_failures_stop_at_the_expected_step_and_clean_up(flash, step, expected, message):
    result = flash.run(initial=True, FAIL_AT=step)
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
    result = flash.run(initial=True, MAC_OUTPUT=output)
    assert result.returncode == 0, result.stderr
    assert flash.events() == ["build", "resolve", "mac", "secret", "mac", "factory", "mac", "upload"]
    assert len(list(flash.credential.parent.glob("*.json"))) == 1


def test_same_mac_with_different_hex_case_is_one_identity(flash):
    result = flash.run(initial=True, MAC_OUTPUT="MAC: 02:00:00:00:00:ab\nMAC: 02:00:00:00:00:AB")
    assert result.returncode == 0, result.stderr
    assert flash.events()[-3:] == ["factory", "mac", "upload"]


@pytest.mark.parametrize("output,message", [
    ("Uploading stub...\nStub running...", "Cannot read a valid ESP MAC"),
    ("MAC: 02:00:00:00:00:01\nMAC: 02:00:00:00:00:02", "Ambiguous MAC output"),
    ("MAC: 02:00:00:00:00:02\nMAC: 02:00:00:00:00:01", "Ambiguous MAC output"),
    ("MAC: malformed\nMAC: 02:00:00:00:00:01", "Cannot read a valid ESP MAC"),
    ("MAC: 02:00:00:00:00:01\nMAC:", "Cannot read a valid ESP MAC"),
    ("MAC: garbage 02:00:00:00:00:01", "Cannot read a valid ESP MAC"),
    ("MAC: 02:00:00:00:00:gg", "Cannot read a valid ESP MAC"),
    ("MAC: 02:00:00:00:00:01 extra", "Cannot read a valid ESP MAC"),
])
def test_missing_ambiguous_or_malformed_mac_reports_stop_before_writes(flash, output, message):
    result = flash.run(initial=True, MAC_OUTPUT=output)
    assert result.returncode != 0
    assert message in result.stderr
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
    result = flash.run(initial=True)
    assert result.returncode != 0
    assert "Cannot create provisioning credential directory" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]


# Locally administered test identities, unrelated to physical Nodes.
IDENTITY_A = "Chip is ESP32-S3 (QFN56) (revision v0.2)\nMAC: 02:00:00:00:00:ab"
IDENTITY_B = "Chip is ESP32-S3 (QFN56) (revision v0.2)\nMAC: 02:00:00:00:00:cd"
BAD_INSPECTIONS = [
    (IDENTITY_B, "Device identity changed"),
    ("Chip is ESP32-C3\nMAC: 02:00:00:00:00:ab", "Unsupported chip"),
    (None, "Cannot read ESP MAC"),  # Disappeared port / esptool failure.
    (IDENTITY_A + "\nMAC: 02:00:00:00:00:cd", "Ambiguous MAC output"),
    ("MAC: 02:00:00:00:00:ab", "Cannot read valid ESP chip"),
    ("Chip is\nMAC: 02:00:00:00:00:ab", "Cannot read valid ESP chip"),
    ("Chip is ESP32-S3\nMAC: malformed", "Cannot read a valid ESP MAC"),
]


@pytest.mark.parametrize("chip", ["ESP32", "ESP32-C3", "ESP32-C6", "ESP8266", "OTHER", "ESP32-S3-not-a-chip"])
def test_unsupported_chip_never_writes(flash, chip):
    result = flash.run(CHIP_OUTPUT=f"Chip is {chip}")
    assert result.returncode != 0
    assert "Unsupported chip" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]
    assert not list(flash.credential.parent.glob("*.json"))


@pytest.mark.parametrize("output", ["", "Chip is", "Chip is ???", "Chip isESP32-S3",
                                     "Chip is ESP32-S3\nChip is ESP32-C3"])
def test_missing_malformed_or_ambiguous_chip_never_writes(flash, output):
    result = flash.run(CHIP_OUTPUT=output)
    assert result.returncode != 0
    assert "chip information" in result.stderr
    assert flash.events() == ["build", "resolve", "mac"]
    assert not list(flash.credential.parent.glob("*.json"))


@pytest.mark.parametrize("replacement,message", BAD_INSPECTIONS)
@pytest.mark.parametrize("boundary", ["factory", "upload", "existing"])
def test_identity_recheck_stops_writes(flash, replacement, message, boundary):
    original = json.dumps({"node_id": NODE_ID, "board": "atom-s3-lite", "provisioning_secret": "cd" * 32}) + "\n"
    if boundary == "existing":
        flash.credential.parent.mkdir(parents=True)
        flash.credential.write_text(original)
    inspections = [IDENTITY_A]
    if boundary == "upload":
        inspections.append(IDENTITY_A)
    inspections.append(replacement)
    result = flash.run(initial=boundary != "existing", INSPECTIONS=json.dumps(inspections))
    assert result.returncode != 0
    assert message in result.stderr
    assert "02:00:00:00:00:" not in result.stdout + result.stderr
    assert flash.events().count("factory") == (1 if boundary == "upload" else 0)
    assert flash.events().count("upload") == 0
    assert flash.credential.exists() == (boundary != "factory")
    if boundary == "existing":
        assert flash.credential.read_text() == original
    if boundary == "upload":
        assert json.loads(flash.credential.read_text())["provisioning_secret"] == SECRET
        assert flash.credential.stat().st_mode & 0o777 == 0o600


def test_rechecks_normalize_case_and_duplicate_mac(flash):
    repeated = IDENTITY_A + "\nUploading stub...\nMAC: 02:00:00:00:00:AB"
    result = flash.run(initial=True, INSPECTIONS=json.dumps([IDENTITY_A, repeated, repeated]))
    assert result.returncode == 0, result.stderr
    assert flash.events().count("mac") == 3
    assert flash.events().count("factory") == 1
    assert flash.events().count("upload") == 1


def test_retry_original_node_after_post_factory_swap_preserves_secret(flash):
    result = flash.run(initial=True, INSPECTIONS=json.dumps([IDENTITY_A, IDENTITY_A, IDENTITY_B]))
    assert result.returncode != 0
    original = flash.credential.read_bytes()
    result = flash.run(FAIL_AT="secret")
    assert result.returncode == 0, result.stderr
    assert flash.credential.read_bytes() == original
    assert flash.events().count("secret") == 1
    assert flash.events().count("factory") == 1
    assert flash.events().count("upload") == 1
    assert flash.events()[-5:] == ["build", "resolve", "mac", "mac", "upload"]


@pytest.mark.parametrize("history", ["existing", "unknown", "fresh-unconfirmed"])
def test_missing_credential_never_authorizes_update(flash, history):
    # None of these histories can be distinguished by ROM MAC/chip inspection.
    # No USB application response (or Wi-Fi state) is used as proof of freshness.
    result = flash.run()
    assert result.returncode != 0
    assert flash.events() == ["build", "resolve", "mac"]
    assert not flash.credential.parent.exists()
    for message in ("No saved Gateway credential", "No firmware was written",
                    "credential/PoP and NVS were not changed", "--reinitialize",
                    "--initial-setup", "clear Wi-Fi/Logical ID"):
        assert message in result.stderr


def test_backup_restored_after_block_allows_only_normal_update(flash):
    assert flash.run().returncode != 0
    flash.credential.parent.mkdir(parents=True)
    original = json.dumps(dict(node_id=NODE_ID, board="atom-s3-lite", provisioning_secret="cd" * 32))
    flash.credential.write_text(original)
    assert flash.run(FAIL_AT="secret").returncode == 0
    assert flash.credential.read_text() == original
    assert "secret" not in flash.events()
    assert "factory" not in flash.events()
    assert flash.events().count("upload") == 1


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize("kind", ["directory", "dangling-symlink", "empty", "invalid-json", "wrong-node", "wrong-board", "missing-secret"])
def test_invalid_saved_credential_never_becomes_fresh(flash, initial, kind):
    flash.credential.parent.mkdir(parents=True)
    record = dict(node_id=NODE_ID, board="atom-s3-lite", provisioning_secret="cd" * 32)
    if kind == "directory":
        flash.credential.mkdir()
    elif kind == "dangling-symlink":
        flash.credential.symlink_to("absent-backup")
    else:
        if kind == "wrong-node": record["node_id"] = "000000000000"
        if kind == "wrong-board": record["board"] = "m5stick-c"
        if kind == "missing-secret": del record["provisioning_secret"]
        flash.credential.write_text("" if kind == "empty" else "broken" if kind == "invalid-json" else json.dumps(record))
    result = flash.run(initial=initial)
    assert result.returncode != 0
    assert flash.events() == ["build", "resolve", "mac"]
    assert "No firmware or credential/PoP write was started" in result.stderr
    assert "cd" * 32 not in result.stdout + result.stderr


def test_initial_setup_cannot_rotate_saved_credential(flash):
    flash.credential.parent.mkdir(parents=True)
    original = json.dumps(dict(node_id=NODE_ID, board="atom-s3-lite", provisioning_secret="cd" * 32))
    flash.credential.write_text(original)
    result = flash.run(initial=True)
    assert result.returncode != 0
    assert flash.credential.read_text() == original
    assert flash.events() == ["build", "resolve", "mac"]
    assert "Use normal update without --initial-setup" in result.stderr


def test_initial_setup_warns_about_new_pop(flash):
    result = flash.run(initial=True)
    assert result.returncode == 0
    assert "A NEW credential/PoP" in result.stderr
    assert "Freshness cannot be verified automatically" in result.stderr


def test_unknown_chip_stops_normal_update_without_creating_credential(flash):
    result = flash.run(CHIP_OUTPUT="")
    assert result.returncode != 0
    assert flash.events() == ["build", "resolve", "mac"]
    assert not flash.credential.parent.exists()


def test_concurrent_credential_creation_stops_before_factory_write(flash):
    original = json.dumps(dict(node_id=NODE_ID, board="atom-s3-lite", provisioning_secret="cd" * 32))
    result = flash.run(initial=True, CONCURRENT_CREDENTIAL=original)
    assert result.returncode != 0
    assert flash.credential.read_text() == original
    assert flash.events() == ["build", "resolve", "mac", "secret", "mac"]
    assert "no factory write was started" in result.stderr


def test_reinitialize_dispatches_only_explicit_operation_to_shared_setup(flash):
    runner = flash.bin / 'setup-python'
    runner.write_text(f'#!{sys.executable}\n' + '''import os, pathlib, sys
args = sys.argv[1:]
assert pathlib.Path(args[0]).name == 'reinitialize_omk_node.py'
assert args[1:4] == ['--reinitialize', '--device', '/dev/fake port']
assert args[4] == '--build-dir'
assert args[5].endswith('/firmware/esp32/omk-node/.pio/build/atom-s3-lite')
with open(os.environ['EVENTS'], 'a') as log: log.write('reinitialize\\n')
''')
    runner.chmod(0o755)
    result = flash.run(['--reinitialize', 'atom-s3-lite', '/dev/fake port'], SYSTEM_MANAGER_PYTHON=str(runner))
    assert result.returncode == 0, result.stderr
    assert flash.events() == ['build', 'reinitialize']
    for message in ('REINITIALIZE', 'Logical ID', 'Wi-Fi', 'Register the Node/attached sensors'):
        assert message in result.stderr


def test_initial_and_reinitialize_cannot_be_combined(flash):
    result = flash.run(['--initial-setup', '--reinitialize', 'atom-s3-lite', '/dev/fake port'])
    assert result.returncode != 0
    assert flash.events() == []


def test_pending_reinitialize_cannot_be_completed_as_ordinary_update(flash):
    flash.credential.parent.mkdir(parents=True)
    original = json.dumps(dict(node_id=NODE_ID, provisioning_secret=SECRET, board='atom-s3-lite', setup_state='reinitialize_pending'))
    flash.credential.write_text(original)
    result = flash.run()
    assert result.returncode != 0
    assert flash.credential.read_text() == original
    assert not {'upload', 'factory', 'secret'} & set(flash.events())
    assert '--reinitialize' in result.stderr
