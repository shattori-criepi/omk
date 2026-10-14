"""Production PUBACK tracking: one outstanding probe, races and wraparound."""
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_progress_boundaries_and_races(tmp_path):
    source = tmp_path / "progress.c"
    source.write_text(r'''
#include <assert.h>
#include "mesh_liveness.h"
int main(void) {
    omk_mqtt_progress_t s = {0};
    assert(!mqtt_progress_is_fresh(&s,0));
    assert(!mqtt_progress_begin(&s,0,false));
    assert(mqtt_progress_begin(&s,0,true));
    assert(!mqtt_progress_begin(&s,90000,true)); /* blocked enqueue */
    mqtt_progress_event(&s,1,7,true); /* unrelated registration ACK */
    mqtt_progress_result(&s,0,42); /* installed under MQTT mutex */
    mqtt_progress_event(&s,2,42,true);
    assert(!s.pending_id && s.ack_count==1 && s.ack_ms==2);
    assert(mqtt_progress_is_fresh(&s,90001));
    assert(!mqtt_progress_is_fresh(&s,90002));
    assert(!mqtt_progress_begin(&s,29999,true));
    assert(mqtt_progress_begin(&s,30000,true));
    mqtt_progress_result(&s,30000,43);
    mqtt_progress_event(&s,30001,0,true);
    mqtt_progress_event(&s,30002,42,true);
    mqtt_progress_event(&s,30003,44,false);
    assert(s.pending_id==43 && s.ack_count==1);
    for (unsigned t=60000;t<7200000;t+=1000) {
        assert(!mqtt_progress_begin(&s,t,false));
        assert(!mqtt_progress_begin(&s,t,true)); /* reconnect cannot duplicate */
    }
    mqtt_progress_event(&s,7200000,43,false); /* expiry is NOT progress */
    assert(!s.pending_id && s.ack_count==1 && !mqtt_progress_is_fresh(&s,7200000));
    assert(mqtt_progress_begin(&s,7200000,true));
    mqtt_progress_result(&s,7200000,-1); /* enqueue failure rate limited */
    assert(!mqtt_progress_begin(&s,7229999,true));
    assert(mqtt_progress_begin(&s,7230000,true));
    mqtt_progress_result(&s,7230000,45);
    mqtt_progress_event(&s,7230001,45,false);
    assert(!s.pending_id && s.ack_count==1);
    assert(mqtt_progress_begin(&s,7260000,true));
    for (int i=1;i<=100;i++) mqtt_progress_event(&s,7260001,i,true);
    mqtt_progress_result(&s,7260000,46); /* no finite early journal to overflow */
    mqtt_progress_event(&s,7260002,46,true);
    assert(!s.pending_id && s.ack_count==2);
    assert(mqtt_progress_begin(&s,9000000,true));
    mqtt_progress_result(&s,9000000,46);
    mqtt_progress_event(&s,UINT32_MAX-10,46,true);
    assert(mqtt_progress_is_fresh(&s,89988));
    assert(!mqtt_progress_is_fresh(&s,89989));
    return 0;
}
''')
    binary = tmp_path / "progress"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                    str(source), str(SOURCE / "mesh_liveness.c"), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
