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

            /* Boot counters establish a baseline, including zero. */
            observe(&s, 0, 1, 1, -85, 0, 0);
            assert(!s.pending);
            /* RSSI alone, even very weak, must never ask for a vote. */
            observe(&s, 30000, 1, 1, -90, 0, 0);
            assert(!s.pending);
            /* Actual repeated uplink disruption plus weak RSSI does. */
            observe(&s, 60000, 1, 1, -85, 3, 0);
            assert(s.pending && s.reason == OMK_MESH_ROOT_REELECTION_ROOT_LINK_UNHEALTHY);
            assert(mesh_root_recovery_should_execute(&s, 60000, 1, 1, 0));
            mesh_root_recovery_mark_executed(&s, 60000, OMK_MESH_ROOT_RECOVERY_REELECTION);
            assert(mesh_root_recovery_in_grace(&s, 60001));

            /* Cooldown suppresses repeated link faults and topology churn. */
            observe(&s, 90000, 1, 1, -85, 6, 0);
            assert(!s.pending);
            mesh_root_recovery_note_topology_change(&s, 90000, 1);
            assert(!s.pending);
            /* Link improves, so only Level 1 is relevant to the topology vote. */
            observe(&s, 120000, 1, 1, -79, 6, 0);
            /* Once cooldown ends a topology batch waits for one quiet period. */
            mesh_root_recovery_note_topology_change(&s, 960000, 1);
            mesh_root_recovery_note_topology_change(&s, 970000, 1);
            assert(s.pending && s.reason == OMK_MESH_ROOT_REELECTION_TOPOLOGY_CHANGE);
            assert(!mesh_root_recovery_should_execute(&s, 1029999, 1, 1, 0));
            assert(mesh_root_recovery_should_execute(&s, 1030000, 1, 1, 0));

            /* Children and disconnected roots can neither request nor execute. */
            mesh_root_recovery_init(&s);
            mesh_root_recovery_note_topology_change(&s, 1, 0);
            assert(!s.pending);
            observe(&s, 1, 0, 1, -90, 0, 0);
            observe(&s, 30001, 0, 1, -90, 4, 4);
            assert(!s.pending);
            mesh_root_recovery_note_topology_change(&s, 1, 1);
            assert(!mesh_root_recovery_should_execute(&s, 60001, 1, 0, 0));
            assert(!mesh_root_recovery_should_execute(&s, 60001, 1, 1, 1));

            /* Unsigned timing still works across the 32-bit millisecond wrap. */
            mesh_root_recovery_init(&s);
            mesh_root_recovery_note_topology_change(&s, 0xfffffff0U, 1);
            assert(mesh_root_recovery_should_execute(&s, 59984U, 1, 1, 0));
            return 0;
        }
    """))
    subprocess.run([
        "cc", "-std=c11", "-Wall", "-Werror", "-I", str(SOURCE), str(program),
        str(SOURCE / "mesh_root_recovery.c"), "-o", str(executable),
    ], check=True)
    subprocess.run([str(executable)], check=True)


def test_mesh_network_uses_root_only_standard_vote_api():
    source = (SOURCE / "mesh_network.c").read_text()
    assert "MESH_EVENT_ROUTING_TABLE_ADD" in source
    assert "esp_mesh_waive_root(NULL, MESH_VOTE_REASON_ROOT_INITIATED)" in source
    assert "mesh_root_recovery_should_execute" in source
    assert "mesh_root_recovery_in_grace" in (SOURCE / "mesh_recovery.c").read_text()
