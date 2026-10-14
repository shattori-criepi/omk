import subprocess
import textwrap
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_mesh_root_recovery_policy(tmp_path):
    program = tmp_path / "mesh_root_recovery_test.c"
    executable = tmp_path / "mesh_root_recovery_test"
    program.write_text(textwrap.dedent("""
        #include <assert.h>
        #include "mesh_root_recovery.h"

        static void observe(omk_mesh_root_recovery_t *s, unsigned now, int root,
                            int valid, int rssi, unsigned parent, unsigned mqtt) {
            mesh_root_recovery_observe_link(s, now, root, valid, rssi, parent, mqtt);
        }

        int main(void) {
            omk_mesh_root_recovery_t s;
            mesh_root_recovery_init(&s);
            observe(&s, 0, 1, 1, -85, 0, 0);
            assert(!s.pending);
            /* RSSI alone, even very weak for hours, cannot request recovery. */
            for (unsigned t = 30000; t <= 7200000; t += 30000) {
                observe(&s, t, 1, 1, -90, 0, 0);
                assert(!mesh_root_recovery_should_execute(&s, t, 1, 1, 0));
            }

            mesh_root_recovery_init(&s);
            observe(&s, 0, 1, 1, -80, 0, 0);
            /* Preserve weak RSSI and per-observation disconnect boundaries. */
            observe(&s, 30000, 1, 1, -80, 2, 1);
            assert(!s.pending);
            observe(&s, 60000, 1, 1, -80, 5, 1);
            assert(mesh_root_recovery_should_execute(&s, 60000, 1, 1, 0));
            mesh_root_recovery_mark_executed(&s, 60000);
            assert(!s.pending && mesh_root_recovery_in_grace(&s, 60001));

            /* Cooldown updates baselines; its churn is not replayed later. */
            observe(&s, 90000, 1, 1, -85, 8, 3);
            assert(!s.pending);
            observe(&s, 960000, 1, 1, -85, 8, 3);
            assert(!s.pending);
            observe(&s, 990000, 1, 1, -80, 8, 5);
            assert(mesh_root_recovery_should_execute(&s, 990000, 1, 1, 0));

            mesh_root_recovery_init(&s);
            observe(&s, 0, 1, 1, -79, 0, 0);
            observe(&s, 30000, 1, 1, -79, 3, 2);
            assert(!s.pending); /* Strong link. */
            observe(&s, 60000, 1, 0, -90, 6, 4);
            assert(!s.pending); /* Invalid RSSI. */
            observe(&s, 90000, 0, 1, -90, 9, 6);
            assert(!s.pending); /* Child. */
            observe(&s, 120000, 1, 1, -80, 9, 6);
            assert(!s.pending); /* No new disruption after role change. */
            observe(&s, 150000, 1, 1, -80, 12, 6);
            assert(!mesh_root_recovery_should_execute(&s, 150000, 0, 1, 0));
            assert(!mesh_root_recovery_should_execute(&s, 150000, 1, 0, 0));
            assert(!mesh_root_recovery_should_execute(&s, 150000, 1, 1, 1));
            assert(mesh_root_recovery_should_execute(&s, 150000, 1, 1, 0));
            /* A request held while disconnected has no expiring deadline. */
            assert(mesh_root_recovery_should_execute(&s, 30U * 86400000U, 1, 1, 0));

            mesh_root_recovery_init(&s);
            observe(&s, 0, 1, 1, -80, UINT32_MAX - 1, 0);
            observe(&s, 30000, 1, 1, -80, 1, 0);
            assert(s.pending); /* Counter rollover still means three events. */
            return 0;
        }
    """))
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE), str(program),
        str(SOURCE / "mesh_root_recovery.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)


def test_mesh_network_only_votes_for_unhealthy_root_links():
    source = (SOURCE / "mesh_network.c").read_text()
    dispatch = source[source.index("static void try_root_recovery(void)"):
                      source.index("void mesh_network_log_diagnostics")]
    assert "MESH_EVENT_ROUTING_TABLE_ADD" not in source
    assert "esp_mesh_set_self_organized" not in dispatch
    assert "esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED)" in dispatch
    assert "root_link_unhealthy" in dispatch
    assert "mesh_root_recovery_should_execute" in dispatch
