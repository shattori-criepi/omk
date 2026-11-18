static void lr2_long_sdk_call(void) {
    assert(state.tx_inflight && tx_pacing.active);
    for(uint64_t t=12000;t<=200000;t+=1000) {
        at_ms(t);recovery_tick(&tx_controller,&tx_context);
    }
    assert(reselect_calls==1 && state.tx_inflight && tx_pacing.active);
    assert(state.status.recovery_requests==1 && state.status.recovery_ok==1);
}

static void lr2_pacing(const char *mode) {
    tx_init(0); send_error=ESP_ERR_MESH_TIMEOUT;
    for(unsigned i=0;i<3;i++) tx_tick(1000+i,true,true);
    assert(send_calls==3 && tx_pacing.active);
    at_ms(6001);tx_offer();tx_drain();assert(send_calls==3);
    at_ms(6002);tx_drain();assert(send_calls==4);
    if(!strcmp(mode,"lr2_nomem")) {
        send_error=ESP_ERR_MESH_NO_MEMORY;
        for(unsigned i=0;i<200;i++) { at_ms(6003+i);tx_offer();tx_drain(); }
        assert(send_calls==4 && local_data->count<=LOCAL_DATA_COUNT);
        at_ms(11002);tx_drain();assert(send_calls==5 && tx_pacing.active);
    } else if(!strcmp(mode,"lr2_success")) {
        send_error=0; at_ms(11002);tx_offer();tx_drain();assert(!tx_pacing.active);
        unsigned n=send_calls; at_ms(11003);tx_offer();tx_drain();assert(send_calls==n+1);
    } else if(!strcmp(mode,"lr2_parent")) {
        event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
        tx_drain();assert(tx_pacing.active);
        connected(false);send_error=0;tx_offer();tx_drain();assert(send_calls==4);
        at_ms(11002);tx_drain();assert(send_calls==5 && !tx_pacing.active);
    } else if(!strcmp(mode,"lr2_root")) {
        reconnect_root();tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_root_address")) {
        mesh_event_root_address_t root={.addr={2,0,0,0,0,99}};
        event(MESH_EVENT_ROOT_ADDRESS,&root);tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_root_learned")) {
        ++state.status.root_change_count;tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_rootless")) {
        rootless(true);tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_config")) {
        mesh_transport_drop_pending();tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_recovery")) {
        ++state.status.recovery_requests;tx_drain();assert(tx_pacing.active);
    } else if(!strcmp(mode,"lr2_tx_stall")) {
        tx_run(7000,400000,true,true);assert(reselect_calls>=1);
    } else if(!strcmp(mode,"lr2_observed_nomem") || !strcmp(mode,"lr2_observed_nomem_api_fail")) {
        send_error=ESP_ERR_MESH_NO_MEMORY;
        if(strstr(mode,"api_fail")) disconnect_error=ESP_FAIL;
        for(uint64_t t=7002;t<=181002;t+=1000) tx_tick(t,true,true);
        assert(disconnect_calls==1 && !reselect_calls && state.status.recovery_requests==1 && tx_pacing.active);
        assert(!state.status.mesh_tx_ok && !state.tx_progress);
        at_ms(181003);event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
        recovery_tick(&tx_controller,&tx_context);
        recovery_tick(&tx_controller,&tx_context);
        assert(self_organized && !recovery_gate);
        assert(state.status.recovery_ok==(!disconnect_error ? 1 : 0));
        tx_drain();
        connected(false);tx_offer();tx_drain();
        unsigned previous=send_calls;
        for(uint64_t t=181004;t<186002;t+=10) { at_ms(t);tx_offer();tx_drain(); }
        assert(send_calls==previous && tx_pacing.active);
        for(uint64_t t=186002;t<=246002;t+=10) {
            at_ms(t);tx_offer();tx_drain();
            assert(send_calls==previous+1+(t-186002)/5000);
        }
        assert(!state.status.mesh_tx_ok && !state.tx_progress && tx_pacing.active);
        assert(local_data->count<=LOCAL_DATA_COUNT);
        printf("resource pacing after reconnect: %u calls / 60 s boundary-inclusive, SDK success 0\n",send_calls-previous);
    } else if(!strcmp(mode,"lr2_blocked_return")) {
        send_hook=lr2_long_sdk_call;
        at_ms(11002);tx_offer();tx_drain();
        assert(!state.tx_inflight && tx_pacing.active && tx_pacing.returned_ms==200000);
        unsigned calls=send_calls;
        at_ms(204999);tx_offer();tx_drain();assert(send_calls==calls);
        at_ms(205000);tx_drain();assert(send_calls==calls+1 && tx_pacing.active);
    } else if(!strcmp(mode,"lr2_root_work")) {
        reconnect_root();lease();bridge_once();mqtt_connected();
        unsigned calls=send_calls;tx_offer();tx_drain();
        assert(send_calls==calls && !local_data->count && tx_pacing.active);
        connected(false);at_ms(6003);tx_offer();tx_drain();assert(send_calls==calls);
        at_ms(11002);send_error=0;tx_drain();assert(send_calls==calls+1 && !tx_pacing.active);
    } else assert(0);
}
