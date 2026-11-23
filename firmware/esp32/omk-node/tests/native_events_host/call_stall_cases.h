static const char *call_mode;
static void call_return_race(void) { state.call_active=false; }

static void call_blocked(void) {
    assert(state.call_active && sdk_busy && state.call_started_ms==1000);
    tx_tick(60999,false,false); assert(!reselect_calls && !restart_calls);
    tx_tick(61000,false,false);
    assert(reselect_calls==1 && tx_controller.call_attempted && !disconnect_calls);
    tx_tick(180999,false,false); assert(!restart_calls && reselect_calls==1);
    if(!strcmp(call_mode,"call_cancel")) return;
    if(!strcmp(call_mode,"call_restart_race")) log_hook=call_return_race;
    if(!strcmp(call_mode,"call_restart_root")) role_query_hook=reconnect_root;
    tx_tick(181000,false,false);
    assert(restart_calls==(!strcmp(call_mode,"call_restart") || !strcmp(call_mode,"call_sen66_restart")));
    assert(reselect_calls==1 && self_organized);
}

static void call_sensor_retry_blocked(void) {
    bool unexecuted=!strcmp(call_mode,"call_sensor_unexecuted");
    bool suppressed=!strcmp(call_mode,"call_sensor_suppressed");
    uint64_t first=suppressed ? 121000 : 61000;
    uint64_t cooldown=300000+tx_controller.jitter_ms;
    assert(!native_restart_allowed(now_ms()) && state.call_active && sdk_busy);
    if(suppressed) state.inhibit_until=first;
    tx_run(1000,first-1000,false,false);
    assert(!reselect_calls && !tx_controller.tx_attempt_count);
    if(unexecuted) recovery_gate=true; /* selected, but final API gate rejects */
    tx_tick(first,false,false);
    assert(tx_controller.call_requested && tx_controller.tx_attempt_count==1);
    assert(reselect_calls==(unexecuted ? 0U : 1U));
    assert(tx_controller.call_attempted==!unexecuted);
    recovery_gate=false;
    for(uint64_t t=first+1000;t<=first+NATIVE_RECOVERY_TX_WINDOW_MS;t+=1000) {
        tx_tick(t,false,false);
        unsigned attempts=t<first+cooldown ? 1 : t<first+2*cooldown ? 2 :
                          t<first+NATIVE_RECOVERY_TX_WINDOW_MS ? 3 : 4;
        assert(tx_controller.tx_attempt_count==(attempts<3 ? attempts : 3));
        assert(state.status.recovery_requests==attempts);
        assert(reselect_calls==attempts-(unexecuted ? 1U : 0U));
        assert(!restart_calls && state.call_active);
        if(t==first+cooldown) {
            /* Second selection came from the existing general TX_STALL path. */
            assert(tx_controller.child_causes==REC_CHILD_TX_STALL);
        }
    }
}

static void test_call_stall(const char *mode) {
    if(!strcmp(mode,"call_sensor_retry") || !strcmp(mode,"call_sensor_retry_jitter") ||
       !strcmp(mode,"call_sensor_unexecuted") || !strcmp(mode,"call_sensor_suppressed")) {
        tx_init(strstr(mode,"jitter") ? 30000 : 0);
        call_mode=mode; send_hook=call_sensor_retry_blocked;
        send_error=ESP_ERR_MESH_TIMEOUT;
        tx_offer(); tx_drain();
        assert(!state.call_active && !state.tx_inflight && !state.tx_progress);
        unsigned requests=state.status.recovery_requests;
        uint64_t returned=now_ms();
        tx_run(returned+1000,returned+600000,false,false);
        assert(state.status.recovery_requests==requests && !restart_calls);
        assert(!tx_controller.call_seen && !tx_controller.tx_watching);
        return;
    }
    if(!strcmp(mode,"call_restart_limit")) {
        native_restart_init(CONFIG_OMK_RELAY_RESTART_NODE_ID);
        assert(native_restart_allowed(0));
        native_restart_record(7,180000,true);
        reset_reason=ESP_RST_SW; native_restart_init(CONFIG_OMK_RELAY_RESTART_NODE_ID);
        assert(native_restart_previous_stall());
        assert(!native_restart_allowed(3599999) && native_restart_allowed(3600000));
        /* Another software reset must not clear the restriction or repeat
         * attribution to the previous automatic restart. */
        native_restart_init(CONFIG_OMK_RELAY_RESTART_NODE_ID);
        assert(!native_restart_previous_stall() && !native_restart_allowed(180000));
        native_restart_init("sensor-node"); assert(!native_restart_allowed(7200000));
        sen66_running=true;
        assert(!native_restart_allowed(3599999) && native_restart_allowed(3600000));
        sen66_running=false;
        reset_reason=ESP_RST_POWERON; native_restart_init(CONFIG_OMK_RELAY_RESTART_NODE_ID);
        assert(native_restart_allowed(0) && !native_restart_previous_stall());
        return;
    }
    if(!strcmp(mode,"call_restart") || !strcmp(mode,"call_cancel") ||
       !strcmp(mode,"call_restart_race") || !strcmp(mode,"call_restart_root") ||
       !strcmp(mode,"call_sen66_restart")) {
        tx_init(0);
        sen66_running=!strcmp(mode,"call_sen66_restart") || !strcmp(mode,"call_restart_root");
        native_restart_init(sen66_running ? "sensor-node" : CONFIG_OMK_RELAY_RESTART_NODE_ID);
        call_mode=mode; send_hook=call_blocked; tx_offer(); tx_drain();
        if(!strcmp(mode,"call_cancel")) {
            tx_tick(181000,false,false);
            assert(!restart_calls && !tx_controller.call_seen && !state.call_active);
            uint32_t previous=state.call_id;
            tx_send_success(); assert(state.call_id==previous+1 && !state.call_active);
        }
        return;
    }
    native_recovery_t r; native_recovery_init(&r,30000);
    native_recovery_input_t in={.started=true,.parent=true,.sdk_busy=true,
        .call_active=true,.call_id=1,.call_started_ms=1000,.restart_allowed=true,.quality_bad=true};
    if(!strcmp(mode,"call_budget")) {
        r.mesh_after=900000; r.tx_attempt_count=NATIVE_RECOVERY_TX_ATTEMPTS;
    } else if(!strcmp(mode,"call_inhibit")) {
        in.voting=true; in.inhibit_until=900000; in.recovery_busy=true;
    } else if(!strcmp(mode,"call_root")) in.root=true;
    else if(!strcmp(mode,"call_sensor")) in.restart_allowed=false;
    else assert(!strcmp(mode,"call_timeline"));
    assert(native_recovery_select(&r,60999,&in)==REC_NONE);
    native_recovery_action_t a=native_recovery_select(&r,61000,&in);
    bool selected=!strcmp(mode,"call_timeline") || !strcmp(mode,"call_sensor");
    assert(a==(selected ? REC_CHILD_RESELECT : REC_NONE));
    if(selected) {
        assert(r.child_causes==(REC_CHILD_TX_STALL|REC_CHILD_CALL_STALL|REC_CHILD_QUALITY));
        native_recovery_call_attempt(&r,1,61000);
    }
    assert(native_recovery_select(&r,180999,&in)==REC_NONE);
    if(!in.root) { ++in.epoch; in.parent=false; in.rootless=true; }
    assert(native_recovery_select(&r,181000,&in)==
           ((in.root || !in.restart_allowed) ? REC_NONE : REC_NODE_RESTART));
    if(strcmp(mode,"call_timeline")) return;
    /* Return cancels A even on failure, and a new ID gets a new deadline.
     * An attempt delayed by cooldown gets its full 120 seconds afterwards. */
    in.call_active=false; in.quality_bad=false;
    (void)native_recovery_select(&r,181001,&in); assert(!r.call_seen);
    in.call_active=true; in.call_id=2; in.call_started_ms=300000;
    in.parent=true; in.rootless=false;
    assert(native_recovery_select(&r,360000,&in)==REC_NONE); /* cooldown + jitter */
    assert(native_recovery_select(&r,391000,&in)==REC_CHILD_RESELECT);
    native_recovery_call_attempt(&r,2,391000);
    assert(native_recovery_select(&r,510999,&in)==REC_NONE);
    assert(native_recovery_select(&r,511000,&in)==REC_NODE_RESTART);
}
