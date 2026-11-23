/* Repeated failures use the production callbacks and the same recovery_tick
 * as continuous outages. No alternative policy implementation in these tests. */
static void other_parent(void) {
    mesh_event_connected_t e={.self_layer=sdk_root ? 1 : 3,
                             .connected={.bssid={2,0,0,0,0,21}}};
    event(MESH_EVENT_PARENT_CONNECTED,&e);
}

static void test_repeated_link(const char *mode) {
    bool root=strstr(mode,"root")!=NULL;
    uint32_t jitter=strstr(mode,"jitter") ? 30000 : 0;
    native_recovery_t r; mqtt_context_t c={0}; native_recovery_init(&r,jitter);
    at_ms(0); connected(root);
    for(unsigned i=0;i<3;++i) {
        at_ms(i*60000); router_loss(-85,true);
        if(strstr(mode,"rootless")) rootless(true);
        if(c.client) stop_client(&c); /* the real bridge discards the old epoch */
        recovery_tick(&r,&c); assert(!vote_calls && !reselect_calls);
        if(i<2) {
            at_ms(i*60000+30000); connected(root);
            if(root) {
                client_ready(&c); offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
                puback(&c,1);
            }
            recovery_tick(&r,&c); assert(!vote_calls && !reselect_calls);
            assert(state.parent_losses==i+1); /* PUBACK and epoch preserve RF evidence */
        }
    }
    assert(weak_parent_losses(&state,now_ms()));
    at_ms(150000); connected(root);
    if(strstr(mode,"other")) {
        other_parent(); assert(!state.parent_losses);
        recovery_tick(&r,&c); assert(!vote_calls && !reselect_calls);
        at_ms(1000000); recovery_tick(&r,&c); assert(!vote_calls && !reselect_calls);
        return;
    }
    if(strstr(mode,"role")) {
        connected(!root); assert(!state.parent_losses);
        recovery_tick(&r,&c); assert(!vote_calls && !reselect_calls); return;
    }
    if(strstr(mode,"expired")) {
        at_ms(300001); recovery_tick(&r,&c);
        assert(!vote_calls && !reselect_calls); return;
    }
    if(strstr(mode,"epoch_race")) role_query_hook=other_parent;
    if(strstr(mode,"promote")) role_query_hook=reconnect_root;
    if(strstr(mode,"failure")) { vote_error=ESP_FAIL; reselect_error=ESP_FAIL; }
    if(strstr(mode,"callback")) self_organized_hook=reconnect_child;
    /* Root has no lease, client or outstanding QoS1 demand at this point. */
    unsigned destroys_before=mqtt_destroys;
    recovery_tick(&r,&c);
    if(strstr(mode,"epoch_race") || strstr(mode,"promote")) {
        assert(!vote_calls && !reselect_calls); return;
    }
    assert(vote_calls==(unsigned)root && reselect_calls==(unsigned)!root);
    assert(!state.parent_losses && !mqtt_disconnects && mqtt_destroys==destroys_before);
    assert((root ? r.vote_after : r.mesh_after)==150000+(root ? 900000 : 300000)+jitter);
    recovery_tick(&r,&c); assert(vote_calls+reselect_calls==1);
    if(strstr(mode,"failure")) {
        assert(!state.status.recovery_ok && !state.recovery_wait_link);
    } else if(!root) {
        if(strstr(mode,"callback")) assert(state.status.recovery_link==1);
        else {
            /* API acceptance while still on the old connection is not success. */
            assert(!state.status.recovery_link && !parent_recovery_log[0]);
            if(strstr(mode,"topology")) {
                rootless(true); rootless(false); recovery_tick(&r,&c);
                assert(!state.status.recovery_link && !parent_recovery_log[0]);
            }
            at_ms(151000); router_loss(-85,true);
            at_ms(152000);
            if(strstr(mode,"changed")) other_parent(); else connected(false);
            recovery_tick(&r,&c);
            assert(state.status.recovery_link==1);
        }
        assert(strstr(parent_recovery_log,strstr(mode,"changed") ? "same=0" : "same=1"));
        at_ms(1000000); recovery_tick(&r,&c);
        assert(reselect_calls==1); /* stable time never creates new evidence */
        return;
    }
    /* Fresh losses cannot bypass cooldown. Expired old evidence cannot cause
     * another request when cooldown ends. All three NEW losses are required. */
    uint64_t due=root ? r.vote_after : r.mesh_after;
    for(unsigned i=0;i<3;++i) {
        at_ms(180000+i*60000); router_loss(-85,true); connected(root);
        recovery_tick(&r,&c); assert(vote_calls+reselect_calls==1);
    }
    at_ms(due-1); recovery_tick(&r,&c); assert(vote_calls+reselect_calls==1);
    at_ms(due+1); recovery_tick(&r,&c);
    /* Child's short cooldown can end with still-valid evidence. */
    unsigned expected=root ? 1 : 2;
    assert(vote_calls+reselect_calls==expected);
    if(root) {
        for(unsigned i=0;i<3;++i) {
            at_ms(due+10000+i*10000); router_loss(-85,true); connected(true);
            recovery_tick(&r,&c); assert(vote_calls==(i==2 ? 2U : 1U));
        }
    }
}

static void test_loss_validation(const char *mode) {
    bool root=strstr(mode,"child")==NULL;
    uint64_t start=strstr(mode,"clock") ? UINT64_C(4294900000) : 0;
    at_ms(start); connected(root);
    if(strstr(mode,"clock")) {
        router_loss(-85,true); connected(root);
        at_ms(start+100000); router_loss(-85,true); connected(root);
        at_ms(start+200000); router_loss(-85,true); connected(root);
        at_ms(start+300000); assert(weak_parent_losses(&state,now_ms()));
        at_ms(start+300001); assert(!weak_parent_losses(&state,now_ms()));
        return;
    }
    const int invalid[]={0,1,-127,-128,-121,-79};
    for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);++i) {
        router_loss(invalid[i],true); connected(root); assert(!state.parent_losses);
    }
    router_loss(-85,false); connected(root); assert(!state.parent_losses);
    event(MESH_EVENT_PARENT_DISCONNECTED,NULL); connected(root); assert(!state.parent_losses);
    router_loss(-120,true); assert(state.parent_losses==1);
    router_loss(-85,true); assert(state.parent_losses==1); /* duplicate */
    connected(root); at_ms(60000); router_loss(-79,true); connected(root);
    assert(state.parent_losses==1); /* strong event is not weak evidence */
    at_ms(120000); router_loss(-80,true); connected(root);
    at_ms(180000); router_loss(-85,true); connected(root);
    assert(weak_parent_losses(&state,now_ms())); /* three weak losses, four events */
    connected(!root); assert(!state.parent_losses);
    connected(root); assert(!weak_parent_losses(&state,now_ms()));
}

static void test_mqtt_flapping(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,true);
    bool ack=strstr(mode,"progress")!=NULL;
    uint32_t jitter=strstr(mode,"jitter") ? 30000 : 0;
    r.jitter_ms=jitter;
    if(strstr(mode,"failure")) mqtt_disconnect_error=ESP_FAIL;
    uint64_t initial=now_ms(), first_light=0;
    for(unsigned second=1;second<=450;++second) {
        at_ms(initial+(uint64_t)second*1000);
        if(second%65==60) disconnect_mqtt();
        if(second%65==0) mqtt_connected();
        if(first_light && now_ms()==first_light+1000) disconnect_mqtt();
        if(first_light && now_ms()==first_light+6000) mqtt_connected();
        if(ack && second==310) { mqtt_connected(); puback(&c,1); }
        if(second%30==0) offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
        if(!ack || second<310) assert(state.qos1_demand_ms==initial && state.qos1_pending);
        if(strstr(mode,"ack_race") && second==300) role_query_hook=puback_during_selection;
        recovery_tick(&r,&c);
        assert(!vote_calls && !reselect_calls);
        if(mqtt_disconnects && !first_light) {
            first_light=now_ms();
            assert(first_light==initial+NATIVE_RECOVERY_MQTT_BUDGET_MS+jitter);
        }
        if(strstr(mode,"ack_race") && second==300) {
            assert(!mqtt_disconnects && !mqtt_destroys && !state.qos1_pending); return;
        }
        if(mqtt_destroys) {
            assert(!ack && mqtt_disconnects==1 && first_light && now_ms()==first_light+120000);
            mqtt_context_t old={.client=&fake_client,.epoch=state.session.epoch-1};
            assert(start_client(&c,state.session.epoch)); mqtt_connected(); puback(&old,1);
            assert(!state.last_puback_ms && state.qos1_pending && state.qos1_demand_ms==initial);
            offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS); recovery_tick(&r,&c);
            uint64_t next=now_ms(); at_ms(next+120000); recovery_tick(&r,&c);
            at_ms(next+300000); recovery_tick(&r,&c); assert(mqtt_destroys==1);
            return;
        }
    }
    assert(ack && mqtt_disconnects==1 && !mqtt_destroys);
}

static void test_mqtt_budget_disconnected(void) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,true);
    uint64_t initial=now_ms();
    for(unsigned i=1;i<=4;++i) {
        at_ms(initial+i*60000); disconnect_mqtt(); recovery_tick(&r,&c);
        at_ms(initial+i*60000+5000); mqtt_connected(); recovery_tick(&r,&c);
    }
    at_ms(initial+299000); disconnect_mqtt(); recovery_tick(&r,&c);
    assert(!mqtt_destroys && !mqtt_disconnects);
    at_ms(initial+300000); recovery_tick(&r,&c);
    assert(mqtt_destroys==1 && !mqtt_disconnects && !vote_calls);
}

static void test_mqtt_demand_epoch(void) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,true);
    uint64_t demand=state.qos1_demand_ms;
    mqtt_context_t old=c;
    at_ms(60000); router_loss(-85,true); stop_client(&c);
    at_ms(65000); connected(true); client_ready(&c);
    assert(state.qos1_pending && state.qos1_demand_ms==demand);
    puback(&old,1); assert(!state.last_puback_ms && state.qos1_pending);
    offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
    assert(state.qos1_demand_ms==demand);
    recovery_tick(&r,&c); /* give the replacement link its ordinary grace */
    connected(false); assert(!state.qos1_pending && !state.last_puback_ms);
    puback(&c,1); assert(!state.last_puback_ms);
    stop_client(&c); connected(true); client_ready(&c);
    assert(!state.qos1_pending && !state.qos1_demand_ms);
}
