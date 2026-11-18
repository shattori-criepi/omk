/* Drive the production owner FSM, sender guard, and quality monitor. */
static bool v2_age_error;
static void v2_due(void) {
    tx_init(0); mq_reset(&quality,0);
    for(uint64_t t=60000;t<=120000;t+=1000) {
        mq_request(&quality,t,false,0,0);
        mq_sdk(&quality,t,t%5000?ESP_ERR_MESH_NO_MEMORY:ESP_OK);
    }
    assert(!mq_evaluate(&quality,120000,true).bad);
    for(uint64_t t=121000;t<=180000;t+=1000) mq_sdk(&quality,t,t%5000?ESP_ERR_MESH_NO_MEMORY:ESP_OK);
    if(v2_age_error) quality.last_error_ms=150001;
    at_ms(180000);
    recovery_tick(&tx_controller,&tx_context);
    assert(state.status.recovery_requests==1 && state.status.recovery_action==REC_CHILD_REPAIR);
}
static void v2_tick(uint64_t t) { at_ms(t); recovery_tick(&tx_controller,&tx_context); }
static void v2_promote(void) { reconnect_root(); }
static void v2_test(const char *mode) {
    if(!strncmp(mode,"v2_quiet_",9)) { sdk_busy=true; v2_age_error=true; }
    if(!strcmp(mode,"v2_gate_exclusion") || !strcmp(mode,"v2_busy_non_tx")) {
        tx_make_due();
        recovery_request_t r={.action=REC_CHILD_RESELECT,.epoch=state.session.epoch,
            .local_generation=state.local_generation,.tx_progress=state.tx_progress,.tx_stall=true};
        if(!strcmp(mode,"v2_gate_exclusion")) recovery_gate=true;
        else { r.tx_stall=false;event(MESH_EVENT_PARENT_DISCONNECTED,NULL);r.epoch=state.session.epoch;sdk_busy=true; }
        recover_mesh(&r);assert(!reselect_calls);return;
    }
    if(!strcmp(mode,"v2_root_race")) self_organized_hook=v2_promote;
    if(!strcmp(mode,"v2_api_fail")) disconnect_error=ESP_FAIL;
    if(!strcmp(mode,"v2_busy")) sdk_busy=true;
    v2_due();
    if(!strncmp(mode,"v2_quiet_",9)) {
        assert(repair.phase==REPAIR_QUIET && recovery_gate && !disconnect_calls);
        at_ms(185000);assert(!mq_evaluate(&quality,185000,true).bad);
        if(!strcmp(mode,"v2_quiet_parent")) { event(MESH_EVENT_PARENT_DISCONNECTED,NULL);connected(false); }
        else if(!strcmp(mode,"v2_quiet_root")) reconnect_root();
        else if(!strcmp(mode,"v2_quiet_config")) mesh_transport_drop_pending();
        else if(!strcmp(mode,"v2_quiet_vote")) event(MESH_EVENT_VOTE_STARTED,NULL);
        else assert(!strcmp(mode,"v2_quiet_aged"));
        sdk_busy=false; v2_tick(185001);
        if(!strcmp(mode,"v2_quiet_aged")) assert(disconnect_calls==1 && repair.phase==REPAIR_DISCONNECT);
        else assert(!disconnect_calls && repair.phase==REPAIR_IDLE && !recovery_gate);
        return;
    }
    if(!strcmp(mode,"v2_busy")) {
        assert(!disconnect_calls && recovery_gate);
        /* Continued SDK failure freshness does not permit overlap. */
        for(uint64_t t=181000;t<=210000;t+=1000) { mq_sdk(&quality,t,ESP_FAIL); v2_tick(t); }
        assert(!disconnect_calls && !recovery_gate && repair.phase==REPAIR_IDLE);
        assert(tx_controller.tx_attempt_count==1); sdk_busy=false; return;
    }
    if(!strcmp(mode,"v2_root_race")) {
        assert(!disconnect_calls); v2_tick(180001);
        assert(self_organized && repair.phase==REPAIR_IDLE && !recovery_gate && ble_root); return;
    }
    assert(disconnect_calls==1 && !self_organized && recovery_gate && !reselect_calls);
    if(!strcmp(mode,"v2_api_fail")) {
        v2_tick(180001); assert(self_organized && !recovery_gate && !state.status.recovery_ok); return;
    }
    if(!strcmp(mode,"v2_root_during_cut")) {
        reconnect_root(); v2_tick(180001); v2_tick(180002);
        assert(self_organized && !recovery_gate && !connect_calls && ble_root); return;
    }
    if(!strcmp(mode,"v2_disconnect_missing")) {
        v2_tick(190000); v2_tick(190001);
        assert(self_organized && !recovery_gate && repair.phase==REPAIR_IDLE && !state.status.recovery_ok); return;
    }
    if(!strcmp(mode,"v2_restore_failure")) restore_error=ESP_FAIL;
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL); v2_tick(180001); v2_tick(180002);
    if(!strcmp(mode,"v2_restore_failure")) {
        v2_tick(181002); v2_tick(182002);
        assert(repair.phase==REPAIR_LATCHED && !recovery_gate && !state.status.recovery_ok);
        unsigned restores=restore_calls;
        for(uint64_t t=183002;t<4000000;t+=1000) v2_tick(t);
        assert(restore_calls==restores && disconnect_calls==1 && !connect_calls); return;
    }
    assert(self_organized && !recovery_gate && connect_calls==1 && state.status.recovery_ok==1);
    assert(repair.phase==REPAIR_LINK);
    if(!strcmp(mode,"v2_link_missing")) {
        v2_tick(300002); assert(repair.phase==REPAIR_IDLE && disconnect_calls==1); return;
    }
    connected(false); v2_tick(180003); assert(repair.phase==REPAIR_VERIFY && !ble_root);
    if(!strcmp(mode,"v2_verify_failure")) {
        v2_tick(540003); assert(repair.phase==REPAIR_IDLE && disconnect_calls==1); return;
    }
    if(!strcmp(mode,"v2_m1")) {
        mesh_quality_t before=quality;
        mesh_wire_message_t m={.kind=MESH_MESSAGE_MEASUREMENT,.length=2,.payload="{}"};
        strcpy(m.node_id,own_id); publication_t origin={.epoch=state.session.epoch,.local_generation=state.local_generation};
        send_error=ESP_ERR_MESH_NO_MEMORY; (void)send_message(&m,NULL,&origin);
        assert(!memcmp(&before,&quality,sizeof(quality))); return;
    }
    assert(!strcmp(mode,"v2_recovered"));
    for(uint64_t t=181000;t<=540000 && repair.phase==REPAIR_VERIFY;t+=1000) {
        mq_request(&quality,t,t%30000==0,(uint32_t)(t/30000),t);
        mq_sdk(&quality,t,ESP_OK);
        if(t%30000==0) mq_receipt(&quality,t,t);
        v2_tick(t);
    }
    assert(repair.phase==REPAIR_IDLE && disconnect_calls==1 && state.status.recovery_ok==1);
}
