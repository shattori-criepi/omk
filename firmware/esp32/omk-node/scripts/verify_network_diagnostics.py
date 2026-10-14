"""PlatformIO/SCons also verifies incremental builds that skip CMake."""
from pathlib import Path
import runpy

Import("env")  # noqa: F821 -- provided by PlatformIO
root = Path(env.subst("$PROJECT_DIR"))  # noqa: F821
source = root / "managed_components/espressif__mqtt/mqtt_client.c"
# On a first configure the IDF component manager may not have downloaded MQTT
# yet. The mandatory CMake check runs after dependency resolution in that case.
if source.exists():
    prepare = runpy.run_path(str(root / "scripts/prepare_mqtt_diagnostics.py"))["prepare"]
    prepare(source, Path(env.subst("$BUILD_DIR")) / "network_diagnostics/mqtt_client.c")  # noqa: F821
