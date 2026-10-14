"""Exercise production timer expiry after long uptime and uint32 rollover."""
import subprocess
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def timer_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh-root-timers")
    program = directory / "timers.c"
    program.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "mesh_root_recovery.h"

int main(int argc, char **argv) {
    assert(argc == 4);
    uint32_t start = (uint32_t)strtoul(argv[2], NULL, 10);
    uint32_t later = start + (uint32_t)strtoull(argv[3], NULL, 10);
    omk_mesh_root_recovery_t s;
    mesh_root_recovery_init(&s);
    mesh_root_recovery_observe_link(&s, start, true, true, -80, 0, 0);
    mesh_root_recovery_mark_executed(&s, start);

    if (!strcmp(argv[1], "boundaries")) {
        assert(mesh_root_recovery_in_grace(&s, start + 119999U));
        assert(!mesh_root_recovery_in_grace(&s, start + 120000U));
        mesh_root_recovery_observe_link(&s, start + 899999U, true, true, -80, 3, 0);
        assert(!mesh_root_recovery_should_execute(&s, start + 899999U, true, true, false));
        mesh_root_recovery_observe_link(&s, start + 900000U, true, true, -80, 6, 0);
        assert(mesh_root_recovery_should_execute(&s, start + 900000U, true, true, false));
    } else {
        if (!strcmp(argv[1], "expired")) {
            assert(!mesh_root_recovery_in_grace(&s, start + 120000U));
            mesh_root_recovery_observe_link(&s, start + 900000U, true, true, -80, 0, 0);
        }
        /* A late first observation must expire the timers too. After expiry,
         * even a whole uint32 cycle must not resurrect either timer. */
        assert(!mesh_root_recovery_in_grace(&s, later));
        mesh_root_recovery_observe_link(&s, later, true, true, -80, 3, 0);
        assert(mesh_root_recovery_should_execute(&s, later, true, true, false));
        /* A new successful vote still arms fresh timers normally. */
        mesh_root_recovery_mark_executed(&s, later);
        assert(mesh_root_recovery_in_grace(&s, later + 119999U));
        assert(!mesh_root_recovery_in_grace(&s, later + 120000U));
        mesh_root_recovery_observe_link(&s, later + 899999U, true, true, -80, 6, 0);
        assert(!mesh_root_recovery_should_execute(&s, later + 899999U, true, true, false));
        mesh_root_recovery_observe_link(&s, later + 900000U, true, true, -80, 9, 0);
        assert(mesh_root_recovery_should_execute(&s, later + 900000U, true, true, false));
    }
    return 0;
}
''')
    executable = directory / "timers"
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
        str(program), str(SOURCE / "mesh_root_recovery.c"), "-o", str(executable),
    ], check=True)
    return executable


@pytest.mark.parametrize("start", [0, 2**32 - 60000, 2**32 - 300000])
def test_grace_and_cooldown_boundaries_across_wrap(timer_binary, start):
    subprocess.run([str(timer_binary), "boundaries", str(start), "0"], check=True)


@pytest.mark.parametrize("days", [25, 30])
@pytest.mark.parametrize("mode", ["late", "expired"])
@pytest.mark.parametrize("start", [0, 2**32 - 60000])
def test_long_uptime_does_not_restore_grace_or_cooldown(timer_binary, days, mode, start):
    subprocess.run([str(timer_binary), mode, str(start), str(days * 86400000)], check=True)


@pytest.mark.parametrize("elapsed", [2**32, 2**32 + 60000, 2 * 2**32 + 60000])
def test_expired_timers_stay_expired_after_full_clock_cycles(timer_binary, elapsed):
    subprocess.run([str(timer_binary), "expired", "1000", str(elapsed)], check=True)
