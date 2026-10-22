"""Compile the production arbiter and exercise failure/role/clock transitions."""
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(scope="module")
def policy_binary(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh-recovery")
    program = directory / "policy.c"
    program.write_text(r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "mesh_recovery.h"
static omk_mesh_recovery_t s;
static omk_mesh_root_recovery_t root;
static omk_mesh_recovery_input_t in;
static omk_mesh_recovery_action_t tick(uint32_t t) {
    return mesh_recovery_choose(&s, t, &in, &root);
}
static void init(bool spent) {
    mesh_recovery_init(&s, spent, 0x112233445566ULL);
    s.jitter_ms = 0; /* Exact boundary tests; jitter is separately exercised. */
    mesh_root_recovery_init(&root);
    in = (omk_mesh_recovery_input_t){.started=true, .mqtt_started=true};
}
static void healthy(void) {
    in.parent_connected = in.has_ip = in.mqtt_connected = in.mqtt_progress = true;
    in.rootless = false;
}
static void accepted(uint32_t t) {
    omk_mesh_recovery_action_t a = tick(t);
    assert(a == (in.is_root ? OMK_RECOVERY_ROUTER_RECONNECT : OMK_RECOVERY_CHILD_RESELECT));
    mesh_recovery_complete(&s, t, a, true);
}
static void transient(void) {
    assert(tick(0) == OMK_RECOVERY_NONE);
    assert(tick(119999) == OMK_RECOVERY_NONE);
    healthy();
    assert(tick(120000) == OMK_RECOVERY_NONE);
    assert(s.stage == OMK_RECOVERY_HEALTHY && !s.loss_active && !s.explicit_accepted);
    in.parent_connected = false;
    assert(tick(120001) == OMK_RECOVERY_NONE);
    assert(tick(240000) == OMK_RECOVERY_NONE);
    accepted(240001);
}
static void reselection(void) {
    tick(0);
    accepted(120000);
    assert(s.stage == OMK_RECOVERY_EXPLICIT && s.parent_reselection_count == 1);
    assert(tick(150000) == OMK_RECOVERY_NONE); /* ESP_OK is only acceptance. */
    healthy();
    assert(tick(160000) == OMK_RECOVERY_NONE);
    assert(s.stage == OMK_RECOVERY_HEALTHY && !s.explicit_accepted);
    assert(tick(600000) == OMK_RECOVERY_NONE);
}
static void repeated_reselection(void) {
    tick(0);
    accepted(120000);
    /* A delayed control cycle accepts another request shortly before the
     * total isolation timeout. Give that latest request its full settle time. */
    accepted(590000);
    assert(tick(600000) == OMK_RECOVERY_NONE);
    assert(tick(769999) == OMK_RECOVERY_NONE);
    assert(tick(770000) == OMK_RECOVERY_RESTART);
}
static void restart(void) {
    tick(0);
    assert(tick(600000) == OMK_RECOVERY_CHILD_RESELECT); /* no accepted request yet */
    mesh_recovery_complete(&s, 600000, OMK_RECOVERY_CHILD_RESELECT, true);
    assert(tick(779999) == OMK_RECOVERY_NONE);
    assert(tick(780000) == OMK_RECOVERY_RESTART);
    mesh_recovery_complete(&s, 780000, OMK_RECOVERY_RESTART, true);
    assert(s.restart_spent);
    for (uint32_t t = 810000; t < 900000; t += 30000) {
        omk_mesh_recovery_action_t a = tick(t);
        assert(a != OMK_RECOVERY_RESTART && a != OMK_RECOVERY_REARM);
        if (a != OMK_RECOVERY_NONE) mesh_recovery_complete(&s, t, a, true);
    }
    /* Brief success followed by MQTT-only failure is the SAME budget. */
    healthy(); tick(900000);
    in.mqtt_connected = false;
    tick(900001);
    assert(tick(1200000) == OMK_RECOVERY_NONE);
    init(true); /* Simulate boot loading spent NVS marker. */
    tick(0); accepted(120000);
    for (uint32_t t = 150000; t < OMK_MESH_RESTART_RETRY_MS; t += 30000) {
        omk_mesh_recovery_action_t a = tick(t);
        assert(a != OMK_RECOVERY_RESTART);
        if (a != OMK_RECOVERY_NONE) mesh_recovery_complete(&s, t, a, true);
    }
}
static void rearm(void) {
    init(true); healthy(); tick(0);
    assert(tick(899999) == OMK_RECOVERY_NONE);
    assert(tick(900000) == OMK_RECOVERY_REARM);
    mesh_recovery_complete(&s, 900000, OMK_RECOVERY_REARM, false);
    assert(s.restart_spent);
    assert(tick(930000) == OMK_RECOVERY_REARM);
    mesh_recovery_complete(&s, 930000, OMK_RECOVERY_REARM, true);
    assert(!s.restart_spent && tick(960000) == OMK_RECOVERY_NONE);
}
static void healthy_interrupt(const char *which) {
    init(true); healthy(); tick(0);
    if (!strcmp(which,"mqtt_flap")) {
        ++in.mqtt_disconnect_count; /* Disconnected/reconnected between polls. */
    } else if (!strcmp(which,"parent_flap")) in.parent_connected = false;
    else if (!strcmp(which,"ip_flap")) in.has_ip = false;
    else in.rootless = true;
    tick(899999);
    healthy(); tick(900000);
    assert(tick(1799998) != OMK_RECOVERY_REARM);
    assert(s.restart_spent);
    assert(tick(1800000) == OMK_RECOVERY_REARM);
}
static void roles(void) {
    tick(0); accepted(120000);
    in.is_root = true;
    assert(tick(150000) == OMK_RECOVERY_NONE);
    assert(!s.explicit_accepted && s.loss_since_ms==150000);
    assert(tick(270000) == OMK_RECOVERY_ROUTER_RECONNECT);
    in.is_root = false;
    assert(tick(300000) == OMK_RECOVERY_NONE);
    accepted(420000);
    in.is_root = true;
    assert(tick(450000) == OMK_RECOVERY_NONE);
    assert(tick(570000) == OMK_RECOVERY_ROUTER_RECONNECT);
    assert(tick(1050000) == OMK_RECOVERY_ROUTER_RECONNECT); // child API never authorizes Root reboot
    mesh_recovery_complete(&s,1050000,OMK_RECOVERY_ROUTER_RECONNECT,true);
    assert(tick(1230000)==OMK_RECOVERY_RESTART); // Root's own reconnect got settle time
    in.parent_connected = true; in.rootless = true;
    assert(tick(1230001) == OMK_RECOVERY_NONE); // Intact router protected
    in.is_root = false;
    assert(tick(1230002) == OMK_RECOVERY_NONE);
    assert(tick(1350002) == OMK_RECOVERY_CHILD_RESELECT);
}
static void priority(void) {
    in.is_root = true;
    mesh_root_recovery_observe_link(&root, 0, true, true, -90, 0, 0);
    mesh_root_recovery_observe_link(&root, 30000, true, true, -90, 3, 0);
    tick(30000);
    assert(tick(210000) == OMK_RECOVERY_ROUTER_RECONNECT);
    healthy(); in.mqtt_connected = false;
    tick(210001);
    assert(tick(390001) == OMK_RECOVERY_ROOT_VOTE);
    mesh_root_recovery_mark_executed(&root, 390001);
    assert(tick(510000) == OMK_RECOVERY_NONE);
    assert(tick(510001) == OMK_RECOVERY_NONE);
    in.is_root = false;
    assert(tick(510002) == OMK_RECOVERY_RESTART);
}
static void grace(void) {
    healthy(); in.is_root=true; in.mqtt_connected=false;
    tick(0);
    mesh_root_recovery_mark_executed(&root, 180000);
    assert(tick(299999) == OMK_RECOVERY_NONE);
    assert(tick(300000) == OMK_RECOVERY_NONE);
    in.is_root = false;
    assert(tick(300001) == OMK_RECOVERY_RESTART);
    init(false); tick(0);
    mesh_root_recovery_mark_executed(&root, 180000);
    assert(tick(299999) == OMK_RECOVERY_NONE);
    assert(tick(300000) == OMK_RECOVERY_CHILD_RESELECT);
}
static void old_grace(uint32_t days) {
    uint32_t start=1000U, later=start+days*86400000U;
    healthy(); in.is_root=true;
    mesh_root_recovery_mark_executed(&root,start);
    tick(start);
    tick(start+120000U);
    tick(start+900000U);
    in.mqtt_connected=false;
    assert(tick(later)==OMK_RECOVERY_NONE);
    assert(tick(later+180000U)==OMK_RECOVERY_NONE);
    in.is_root=false;
    assert(tick(later+180001U)==OMK_RECOVERY_RESTART);

    init(false); healthy();
    mesh_root_recovery_mark_executed(&root,start);
    tick(start);
    tick(start+120000U);
    tick(start+900000U);
    in.parent_connected=false;
    assert(tick(later)==OMK_RECOVERY_NONE);
    assert(tick(later+120000U)==OMK_RECOVERY_CHILD_RESELECT);
}
static void events(void) {
    tick(0);
    for (int i=0; i<120000; ++i) {
        mesh_recovery_note_no_parent(&s, i);
        mesh_recovery_note_stopped(&s);
        assert(tick((uint32_t)i) == OMK_RECOVERY_NONE);
    }
    assert(s.no_parent_found_count == 120000 && s.last_scan_times == 119999);
    assert(s.stop_reconnection_count == 120000 && s.reason == OMK_RECOVERY_STOPPED);
    accepted(120000);
    assert(tick(599999) != OMK_RECOVERY_RESTART);
    assert(tick(600000) == OMK_RECOVERY_RESTART);
    mesh_recovery_complete(&s, 600000, OMK_RECOVERY_RESTART, false);
    assert(s.restart_spent && tick(630000) != OMK_RECOVERY_RESTART);
    healthy(); tick(640000);
    mesh_recovery_note_no_parent(&s, 999); /* Stale event cannot break healthy state. */
    assert(tick(640001) == OMK_RECOVERY_NONE && s.stage == OMK_RECOVERY_HEALTHY);
}
static void api_failure(void) {
    tick(0);
    for (uint32_t t=120000;t<=900000;t+=30000) {
        assert(tick(t) == OMK_RECOVERY_CHILD_RESELECT);
        mesh_recovery_complete(&s,t,OMK_RECOVERY_CHILD_RESELECT,false);
        assert(!s.explicit_accepted && !s.restart_spent);
    }
    accepted(930000);
    assert(tick(1109999) == OMK_RECOVERY_NONE);
    assert(tick(1110000) == OMK_RECOVERY_RESTART);
    init(true); tick(0);
    assert(tick(120000) == OMK_RECOVERY_CHILD_RESELECT);
    mesh_recovery_complete(&s,120000,OMK_RECOVERY_CHILD_RESELECT,false);
    assert(tick(150000) == OMK_RECOVERY_NONE); /* Low frequency after budget spent. */
    assert(tick(420000) == OMK_RECOVERY_CHILD_RESELECT);
}
static void wrap(void) {
    uint32_t base = UINT32_MAX-60000;
    tick(base);
    assert(tick(base+119999U) == OMK_RECOVERY_NONE);
    accepted(base+120000U);
    assert(mesh_recovery_loss_duration_s(&s,base+180000U)==180);
    assert(tick(base+599999U) != OMK_RECOVERY_RESTART);
    assert(tick(base+600000U) == OMK_RECOVERY_RESTART);
    init(true); healthy(); tick(base);
    assert(tick(base+899999U)==OMK_RECOVERY_NONE);
    assert(tick(base+900000U)==OMK_RECOVERY_REARM);
    init(false); healthy(); in.mqtt_connected=false; tick(base);
    assert(tick(base+179999U)==OMK_RECOVERY_NONE);
    assert(tick(base+180000U)==OMK_RECOVERY_RESTART);
}
static void jitter(void) {
    unsigned slots=0;
    const uint64_t ids[]={0x09dda0d5a8f2ULL,0x55f94c790e12ULL,0x9490b46aee0dULL};
    for(unsigned i=0;i<3;i++) {
        mesh_recovery_init(&s,false,ids[i]);
        assert(s.jitter_ms<=120000);
        uint32_t delay=s.jitter_ms;
        mesh_recovery_init(&s,false,ids[i]); assert(delay==s.jitter_ms);
        slots |= 1U << (delay/30000U);
        tick(0);
        assert(tick(120000+delay/2-1)==OMK_RECOVERY_NONE);
        accepted(120000+delay/2);
        assert(tick(600000+delay-1)!=OMK_RECOVERY_RESTART);
        assert(tick(600000+delay)==OMK_RECOVERY_RESTART);
    }
    assert((slots & (slots-1)) != 0); /* Field node IDs are not all synchronized. */
}
static void mqtt(void) {
    healthy(); in.mqtt_connected=false; tick(0);
    assert(tick(179999)==OMK_RECOVERY_NONE);
    assert(tick(180000)==OMK_RECOVERY_RESTART);
    in.parent_connected=false; tick(180001);
    healthy(); in.mqtt_connected=false; tick(200000);
    assert(tick(379999)==OMK_RECOVERY_NONE);
    assert(tick(380000)==OMK_RECOVERY_RESTART);
    in.has_ip=false;
    assert(tick(400000)==OMK_RECOVERY_NONE && s.stage==OMK_RECOVERY_WAIT_IP);
    in.started=false;
    assert(tick(900000)==OMK_RECOVERY_NONE && s.stage==OMK_RECOVERY_IDLE);
}

static void stalled_connected(void) {
    healthy(); in.mqtt_progress=false;
    assert(tick(0)==OMK_RECOVERY_NONE && s.stage==OMK_RECOVERY_MQTT_WAIT);
    assert(tick(179999)==OMK_RECOVERY_NONE);
    assert(tick(180000)==OMK_RECOVERY_RESTART);
    in.is_root=true;
    assert(tick(200000)==OMK_RECOVERY_NONE); /* Root MQTT failure cannot reboot tree. */
    in.rootless=true;
    tick(210000); s.explicit_accepted=true; s.explicit_since_ms=210000;
    assert(tick(900000)==OMK_RECOVERY_NONE); /* Associated Root stays protected. */
}
static void retry_after_boot(void) {
    init(true); healthy(); in.mqtt_progress=false;
    tick(0);
    assert(s.stage==OMK_RECOVERY_BUDGET_HOLD && !s.healthy_active);
    assert(tick(OMK_MESH_RESTART_RETRY_MS-1)==OMK_RECOVERY_NONE);
    assert(tick(OMK_MESH_RESTART_RETRY_MS)==OMK_RECOVERY_RESTART);
    mesh_recovery_complete(&s,OMK_MESH_RESTART_RETRY_MS,OMK_RECOVERY_RESTART,true);
    assert(s.restart_spent); /* Time alone never rearms the normal budget. */
    assert(tick(2*OMK_MESH_RESTART_RETRY_MS-1)==OMK_RECOVERY_NONE);
    assert(tick(2*OMK_MESH_RESTART_RETRY_MS)==OMK_RECOVERY_RESTART);
    init(true); healthy(); in.mqtt_progress=false; tick(0);
    assert(tick(180000)==OMK_RECOVERY_NONE); /* Fresh reboot restarts cooldown. */
    s.persistence_blocked=true;
    assert(tick(4*OMK_MESH_RESTART_RETRY_MS)==OMK_RECOVERY_NONE);
    init(true); healthy(); tick(0); in.mqtt_progress=false; tick(899999);
    in.mqtt_progress=true; tick(900000);
    assert(tick(1799999)!=OMK_RECOVERY_REARM);
    assert(tick(1800000)==OMK_RECOVERY_REARM);
}
static void retry_wrap(void) {
    uint32_t base=UINT32_MAX-1000U;
    init(true); healthy(); in.mqtt_progress=false; tick(base);
    assert(tick(base+OMK_MESH_RESTART_RETRY_MS-1)==OMK_RECOVERY_NONE);
    assert(tick(base+OMK_MESH_RESTART_RETRY_MS)==OMK_RECOVERY_RESTART);
    mesh_recovery_complete(&s,base+OMK_MESH_RESTART_RETRY_MS,OMK_RECOVERY_RESTART,false);
    assert(tick(base+2*OMK_MESH_RESTART_RETRY_MS)==OMK_RECOVERY_NONE);
}
static void repeated_boots(void) {
    for(unsigned boot=0;boot<8;boot++) {
        init(true);healthy();in.mqtt_progress=false;
        s.jitter_ms=boot%2 ? 120000 : 0;
        uint32_t deadline=OMK_MESH_RESTART_RETRY_MS+s.jitter_ms;
        assert(tick(0)==OMK_RECOVERY_NONE);
        assert(tick(deadline-1)==OMK_RECOVERY_NONE);
        assert(tick(deadline)==OMK_RECOVERY_RESTART);
        // A Root transition invalidates a child's selected restart even after cooldown.
        omk_mesh_recovery_t selected=s;
        in.is_root=true;mesh_recovery_observe(&s,deadline,&in);
        assert(!mesh_recovery_restart_valid(&selected,&s,deadline,&in,&root,false));
        assert(tick(deadline+1000000)==OMK_RECOVERY_NONE);
        in.is_root=false;
        assert(tick(deadline+1000001)==OMK_RECOVERY_RESTART);
        mesh_recovery_complete(&s,deadline+1000001,OMK_RECOVERY_RESTART,true);
        assert(tick(deadline+1000002)==OMK_RECOVERY_NONE);
    }
}
int main(int argc,char **argv) {
    assert(argc==2); init(false);
    if (!strcmp(argv[1],"transient")) transient();
    else if (!strcmp(argv[1],"reselection")) reselection();
    else if (!strcmp(argv[1],"repeated_reselection")) repeated_reselection();
    else if (!strcmp(argv[1],"restart")) restart();
    else if (!strcmp(argv[1],"rearm")) rearm();
    else if (strstr(argv[1],"flap")) healthy_interrupt(argv[1]);
    else if (!strcmp(argv[1],"roles")) roles();
    else if (!strcmp(argv[1],"priority")) priority();
    else if (!strcmp(argv[1],"grace")) grace();
    else if (!strcmp(argv[1],"old_grace_25")) old_grace(25);
    else if (!strcmp(argv[1],"old_grace_30")) old_grace(30);
    else if (!strcmp(argv[1],"events")) events();
    else if (!strcmp(argv[1],"api_failure")) api_failure();
    else if (!strcmp(argv[1],"wrap")) wrap();
    else if (!strcmp(argv[1],"jitter")) jitter();
    else if (!strcmp(argv[1],"mqtt")) mqtt();
    else if (!strcmp(argv[1],"stalled_connected")) stalled_connected();
    else if (!strcmp(argv[1],"retry_after_boot")) retry_after_boot();
    else if (!strcmp(argv[1],"retry_wrap")) retry_wrap();
    else if (!strcmp(argv[1],"repeated_boots")) repeated_boots();
    else assert(false);
}
''')
    binary = directory / "policy"
    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-I", str(SOURCE),
                    str(program), *(str(SOURCE / f) for f in (
                        "mesh_recovery.c", "mesh_root_recovery.c", "mesh_liveness.c")),
                    "-o", str(binary)], check=True)
    return binary


@pytest.mark.parametrize("scenario", [
    "transient", "reselection", "restart", "rearm", "mqtt_flap", "parent_flap",
    "ip_flap", "rootless_flap", "roles", "priority", "grace", "events",
    "api_failure", "wrap", "jitter", "mqtt", "repeated_reselection", "old_grace_25", "old_grace_30", "stalled_connected", "retry_after_boot", "retry_wrap", "repeated_boots",
])
def test_policy(policy_binary, scenario):
    subprocess.run([str(policy_binary), scenario], check=True)
