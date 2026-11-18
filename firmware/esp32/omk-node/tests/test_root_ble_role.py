import subprocess
import os
from pathlib import Path
import pytest
BASE = Path(__file__).resolve().parents[1]
@pytest.fixture(scope="module")
def role_host(tmp_path_factory):
    work = tmp_path_factory.mktemp("ble-role")
    for name in ("esp_err.h", "esp_timer.h", "esp_bt.h", "esp_log.h", "esp_bt_main.h", "esp_gap_ble_api.h", "freertos/FreeRTOS.h"):
        f=work/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#include <stdint.h>\nint64_t esp_timer_get_time(void);\n' if name=='esp_timer.h' else '#define portMUX_TYPE int\n#define portMUX_INITIALIZER_UNLOCKED 0\n#define portENTER_CRITICAL(p) ((void)(p))\n#define portEXIT_CRITICAL(p) ((void)(p))\n' if name=='freertos/FreeRTOS.h' else '#include "stubs.h"\n')
    cjson=Path(os.environ["OMK_TEST_CJSON_DIR"])
    exe=work/"test"
    subprocess.run(["cc","-std=c11","-Wall","-Wextra","-Werror","-Wno-unused-variable","-fsanitize=undefined","-fno-sanitize-recover=all","-I",str(work),"-I",str(BASE/"tests/ble_role_host"),"-I",str(BASE/"src"),str(BASE/"tests/ble_role_host/test.c"),"-I",str(cjson),*[str(BASE/"src"/n) for n in ("ble_scan_trial.c","ble_scan_command.c","ble_scan_metrics.c","ble_scan_runtime.c")],str(cjson/"cJSON.c"),"-lm","-o",str(exe)],check=True)
    return exe
@pytest.mark.parametrize("case",range(10))
def test_role_transitions(role_host,case):
    subprocess.run([str(role_host),str(case)],check=True)
