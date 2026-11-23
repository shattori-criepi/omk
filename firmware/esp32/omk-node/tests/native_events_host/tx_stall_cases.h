/* Isolate the A1 production enqueue/sender/owner/selection paths. These minimal
 * payloads have no status identity, so the B delivery monitor is not armed. */
static native_recovery_t tx_controller;
static mqtt_context_t tx_context;

static void tx_init(uint32_t jitter) {
    connected(false);
    native_recovery_init(&tx_controller, jitter);
    recovery_tick(&tx_controller, &tx_context);
}
static void tx_offer(void) {
    esp_err_t result=mesh_transport_publish(MESH_MESSAGE_SEN66,"{}",2);
    assert(result==ESP_OK || result==ESP_ERR_NO_MEM);
}
static void tx_drain(void) {
    stop_sender_idle=true;
    if (!setjmp(worker_exit)) mesh_sender(NULL);
    stop_sender_idle=false;
}
static void tx_send_success(void) {
    mesh_wire_message_t m={.kind=MESH_MESSAGE_NODE_STATUS,.node_id="020000000041",.length=2,.payload="{}"};
    send_error=ESP_OK;
    publication_t origin={.epoch=state.session.epoch,.local_generation=state.local_generation};
    assert(send_message(&m,NULL,&origin)==ESP_OK);
}
static void tx_tick(uint64_t time, bool offer, bool drain) {
    at_ms(time);
    if (offer) tx_offer();
    if (drain) tx_drain();
    recovery_tick(&tx_controller,&tx_context);
}
static void tx_run(uint64_t start, uint64_t end, bool offer, bool drain) {
    for (uint64_t t=start;t<=end;t+=1000) tx_tick(t,offer && (t-start)%30000==0,drain);
}
static void tx_make_due(void) {
    tx_init(0);
    tx_run(1000,180000,true,false);
    assert(!reselect_calls && state.status.tx_dropped && !send_calls);
    at_ms(181000); tx_offer();
}
static void tx_drop_config(void) { mesh_transport_drop_pending(); }
static void tx_expire_demand(void) { test_time+=61000000; }

static void tx_while_send_blocked(void) {
    assert(state.tx_inflight && !state.status.mesh_tx_ok && !state.status.mesh_tx_errors);
    /* Simulate the independent recovery owner being scheduled while the
     * sender is inside the SDK, with no further producer/status activity. */
    tx_run(1000,60000,false,false);
    assert(!reselect_calls);
    tx_tick(61000,false,false);
    assert(reselect_calls==1 && state.tx_inflight && !state.status.mesh_tx_ok);
    assert(state.status.recovery_requests==1 && state.status.recovery_ok==1);
    tx_run(62000,180000,false,false);
    assert(reselect_calls==1 && !restart_calls); /* one request for this call */
}

static void tx_quality_priority(const char *mode) {
    /* Policy inputs isolate coexisting causes, including QUALITY becoming
     * due before TX. No simulated SDK completions during a blocked call. */
    native_recovery_t r;
    bool busy=strstr(mode,"busy")!=NULL;
    uint32_t jitter=strstr(mode,"jitter") ? 30000 : 0;
    native_recovery_init(&r,jitter);
    native_recovery_input_t in={.started=true,.parent=true,.tx_demand=true,.sdk_busy=busy};
    uint64_t due=1000+NATIVE_RECOVERY_TX_STALL_MS+jitter;
    for(uint64_t t=1000;t<due;t+=1000) {
        /* With an idle sender, make QUALITY coincide with TX, so its usual
         * earlier repair remains covered separately by the v2 cases. */
        in.quality_bad=busy && t>=121000+jitter;
        assert(native_recovery_select(&r,t,&in)==REC_NONE);
        assert(!r.tx_attempt_count && !r.mesh_after);
    }
    in.quality_bad=true;
    assert(native_recovery_select(&r,due,&in)==REC_CHILD_RESELECT);
    assert(r.child_causes==(REC_CHILD_TX_STALL|REC_CHILD_QUALITY));
    assert(r.tx_attempt_count==1 && r.mesh_after==due+300000+jitter);
    assert(native_recovery_select(&r,due+1,&in)==REC_NONE);
    assert(r.tx_attempt_count==1);
}

static void test_tx_budget(const char *mode) {
    tx_init(0);
    if (!strcmp(mode,"tx_api_failure_budget")) reselect_error=ESP_FAIL;
    uint64_t first=0;
    unsigned previous=0;
    for (uint64_t t=1000;t<4000000;t+=1000) {
        tx_tick(t,(t-1000)%30000==0,false);
        if (reselect_calls!=previous) {
            assert(reselect_calls==previous+1);
            if (!first) first=t;
            if (!strcmp(mode,"tx_reconnect_budget")) connected(false);
            if (!strcmp(mode,"tx_success_budget")) tx_send_success();
            previous=reselect_calls;
        }
        if (t==first+NATIVE_RECOVERY_TX_WINDOW_MS-1000) assert(reselect_calls==3);
        if (t==first+NATIVE_RECOVERY_TX_WINDOW_MS) {
            assert(reselect_calls==4); /* oldest attempt expires at the boundary */
            assert(tx_controller.tx_attempt_count==3);
            if (reselect_error) assert(!state.status.recovery_ok);
            else assert(state.status.recovery_ok==4);
            return;
        }
        assert(!first || t>=first+NATIVE_RECOVERY_TX_WINDOW_MS || reselect_calls<=3);
    }
    assert(!"budget boundary not reached");
}

static void test_tx_stall(const char *mode) {
    if(!strncmp(mode,"tx_quality_",11)) { tx_quality_priority(mode); return; }
    if (!strcmp(mode,"tx_api_failure_budget") || !strcmp(mode,"tx_reconnect_budget") ||
        !strcmp(mode,"tx_success_budget")) { test_tx_budget(mode); return; }
    if (strstr(mode,"_race")) {
        tx_make_due();
        if (!strcmp(mode,"tx_progress_race")) role_query_hook=tx_send_success;
        else if (!strcmp(mode,"tx_epoch_race")) role_query_hook=reconnect_child;
        else if (!strcmp(mode,"tx_role_race")) role_query_hook=reconnect_root;
        else if (!strcmp(mode,"tx_config_race")) role_query_hook=tx_drop_config;
        else if (!strcmp(mode,"tx_idle_race")) role_query_hook=tx_expire_demand;
        else assert(0);
        recovery_tick(&tx_controller,&tx_context);
        assert(!reselect_calls && state.status.recovery_requests==1 && !state.status.recovery_ok);
        assert(tx_controller.tx_attempt_count==1); /* cancellation also charges budget */
        return;
    }
    tx_init(!strcmp(mode,"tx_queue_jitter") ? 30000 : 0);
    if (!strcmp(mode,"tx_queue") || !strcmp(mode,"tx_queue_jitter")) {
        uint64_t due=181000+tx_controller.jitter_ms;
        tx_run(1000,due-1000,true,false);
        assert(!reselect_calls && !state.status.parent_disconnect_count);
        assert(local_data->count==LOCAL_DATA_COUNT && state.status.tx_dropped>0);
        tx_tick(due,true,false);
        assert(reselect_calls==1 && state.status.recovery_action==REC_CHILD_RESELECT);
        tx_run(due+1000,due+299000+tx_controller.jitter_ms,true,false);
        assert(reselect_calls==1);
        tx_tick(due+300000+tx_controller.jitter_ms,true,false);
        assert(reselect_calls==2);
    } else if (!strcmp(mode,"tx_idle") || !strcmp(mode,"tx_burst") || !strcmp(mode,"tx_invalid")) {
        if (!strcmp(mode,"tx_burst")) for (int i=0;i<10;++i) tx_offer();
        if (!strcmp(mode,"tx_invalid")) {
            assert(mesh_transport_publish(MESH_MESSAGE_NODE_STATUS,NULL,2)==ESP_ERR_INVALID_ARG);
            assert(mesh_transport_publish(MESH_MESSAGE_NODE_STATUS,"{}",0)==ESP_ERR_INVALID_SIZE);
            assert(!state.tx_offered);
        }
        tx_run(1000,7201000,false,false);
        assert(!reselect_calls && !tx_controller.tx_attempt_count);
    } else if (!strcmp(mode,"tx_errors")) {
        send_error=ESP_ERR_MESH_TIMEOUT;
        tx_run(1000,180000,true,true);
        assert(!reselect_calls && state.status.mesh_tx_errors && !state.tx_progress);
        tx_tick(181000,true,true);
        assert(reselect_calls==1);
    } else if (!strcmp(mode,"tx_short")) {
        send_error=ESP_ERR_MESH_TIMEOUT;
        tx_run(1000,120000,true,true);
        send_error=ESP_OK;
        tx_run(121000,721000,true,true);
        assert(state.tx_progress && !reselect_calls);
    } else if (!strcmp(mode,"tx_sdk_success_without_delivery")) {
        tx_run(1000,7201000,true,true);
        /* No mesh_receiver or MQTT callback has run: success cannot establish
         * end-to-end delivery. A1 alone must never claim otherwise. */
        assert(state.status.mesh_tx_ok && !state.status.mesh_rx_ok && !state.status.mqtt_pubacks);
        assert(!reselect_calls && !tx_controller.tx_attempt_count);
    } else if (!strcmp(mode,"tx_blocked_call")) {
        tx_offer();
        send_hook=tx_while_send_blocked;
        tx_drain();
        assert(reselect_calls==1 && !state.tx_inflight);
    } else if (!strncmp(mode,"tx_late_",8)) {
        if (!strcmp(mode,"tx_late_epoch")) send_hook=reconnect_child;
        else if (!strcmp(mode,"tx_late_config")) send_hook=tx_drop_config;
        else if (!strcmp(mode,"tx_late_role")) send_hook=reconnect_root;
        else if (!strcmp(mode,"tx_late_rootless")) send_hook=become_rootless;
        else assert(0);
        tx_offer(); tx_drain();
        assert(state.status.mesh_tx_ok==1 && !state.tx_progress && !state.tx_inflight);
    } else if (!strcmp(mode,"tx_root")) {
        connected(true); client_ready(&tx_context);
        tx_run(1000,7201000,true,false);
        assert(!reselect_calls && !tx_controller.tx_attempt_count && !vote_calls);
    } else if (!strcmp(mode,"tx_voting") || !strcmp(mode,"tx_inhibit")) {
        if (!strcmp(mode,"tx_voting")) event(MESH_EVENT_VOTE_STARTED,NULL);
        else state.inhibit_until=501000;
        tx_run(1000,!strcmp(mode,"tx_voting") ? 200000 : 500000,true,false);
        assert(!reselect_calls);
        if (!strcmp(mode,"tx_voting")) {
            event(MESH_EVENT_VOTE_STOPPED,NULL);
            tx_run(201000,319000,true,false);
            assert(!reselect_calls);
            tx_tick(320000,true,false);
        } else tx_tick(501000,true,false);
        assert(reselect_calls==1);
    } else if (!strcmp(mode,"tx_disconnected") || !strcmp(mode,"tx_rootless")) {
        if (!strcmp(mode,"tx_disconnected")) event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
        else rootless(true);
        tx_run(1000,120000,true,false);
        assert(!reselect_calls);
        tx_tick(121000,true,false);
        assert(reselect_calls==1 && !tx_controller.tx_attempt_count); /* original P1 path */
    } else if (!strcmp(mode,"tx_time64")) {
        uint64_t start=(UINT64_C(1)<<32)+1000;
        tx_run(start,start+179000,true,false);
        assert(!reselect_calls);
        tx_tick(start+180000,true,false);
        assert(reselect_calls==1);
    } else if (!strcmp(mode,"tx_sample_gap")) {
        tx_run(1000,120000,true,false);
        /* The recovery owner was not scheduled while producers went idle.
         * Its old watch must not trigger immediately on newly resumed demand. */
        tx_tick(3601000,true,false);
        assert(!reselect_calls);
        tx_run(3602000,3780000,true,false);
        assert(!reselect_calls);
        tx_tick(3781000,true,false);
        assert(reselect_calls==1);
    } else assert(0);
}
