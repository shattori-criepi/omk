/* Production callbacks/executor with faults injected at SDK call boundaries. */
static void at_ms(uint64_t ms) { test_time=(int64_t)ms*1000; }
static void puback(mqtt_context_t *c,int id) {
    esp_mqtt_event_t e={.client=c->client,.msg_id=id}; mqtt_event(c,0,MQTT_EVENT_PUBLISHED,&e);
}
static void offer_publication(mqtt_context_t *c,mesh_message_kind_t kind) {
    origin_route_t routes[ROUTE_COUNT]={0};
    publication_t record={.epoch=state.session.epoch,.queued_ms=now_ms()};
    record.message.kind=kind;
    snprintf(record.message.node_id,sizeof(record.message.node_id),"%s",own_id);
    snprintf(record.message.payload,sizeof(record.message.payload),
        "{\"protocol_version\":2,\"node_id\":\"%s\",\"revision\":0,\"seq\":1,\"boot_id\":\"0123456789abcdef\",\"uptime_s\":1}",own_id);
    record.message.length=strlen(record.message.payload); bridge_publication(c,routes,&record);
}
static void client_ready(mqtt_context_t *c) {
    sdk_acquire(lease().ip_info); assert(start_client(c,state.session.epoch)); mqtt_connected();
}
static void router_loss(int rssi,bool matching) {
    mesh_event_disconnected_t d={.reason=200,.rssi=rssi,.bssid={2,0,0,0,0,20}};
    if(!matching) ++d.bssid[5];
    event(MESH_EVENT_PARENT_DISCONNECTED,&d);
}
static void demote_sdk(void) { sdk_root=false; }
static void demote_event(void) { connected(false); }
static void reconnect_child(void) { connected(false); rootless(false); }
static void reconnect_root(void) { connected(true); }
static void become_rootless(void) { rootless(true); }
static void disconnect_parent(void) { router_loss(-90,true); }
static void puback_during_selection(void) { puback(mqtt_arg,1); }
static void reconnect_mqtt(void) { mqtt_connected(); }
static void disconnect_mqtt(void) {
    esp_mqtt_event_t e={.client=&fake_client}; mqtt_event(mqtt_arg,0,MQTT_EVENT_DISCONNECTED,&e);
}
static void test_window(const char *mode) {
    connected(true); at_ms(0); router_loss(-90,true); connected(true);
    at_ms(299000); router_loss(-90,true); connected(true);
    at_ms(301000); router_loss(-90,true); connected(true);
    assert(state.parent_losses==3 && !weak_parent_losses(&state,now_ms()));
    at_ms(302000); router_loss(-90,true); connected(true);
    assert(weak_parent_losses(&state,now_ms()));
    assert(state.parent_loss_ms[0]==299000 && state.parent_loss_ms[1]==301000 && state.parent_loss_ms[2]==302000);
    if(!strcmp(mode,"rec_window_reset")) {
        at_ms(302001); rootless(true); assert(weak_parent_losses(&state,now_ms()));
        rootless(false); router_loss(-90,true); connected(true);
        connected(false); assert(!state.parent_losses);
    } else {
        at_ms(599000); assert(weak_parent_losses(&state,now_ms()));
        at_ms(599001); assert(!weak_parent_losses(&state,now_ms()));
    }
}
static void test_vote(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; native_recovery_init(&r,0);
    connected(true);
    int rssi=!strcmp(mode,"rec_vote_good") ? -79 : !strcmp(mode,"rec_vote_zero") ? 0 :
             !strcmp(mode,"rec_vote_invalid") ? -128 : !strcmp(mode,"rec_vote_boundary") ? -80 : -90;
    unsigned count=!strcmp(mode,"rec_vote_threshold") ? 2 : 3;
    for(unsigned i=0;i<count;++i) {
        at_ms(10000+i*10000); router_loss(rssi,strcmp(mode,"rec_vote_bad_bssid")!=0); connected(true);
    }
    if(!strcmp(mode,"rec_vote_other_router")) {
        mesh_event_connected_t e={.self_layer=1,.connected={.bssid={2,0,0,0,0,21}}};
        event(MESH_EVENT_PARENT_CONNECTED,&e);
    }
    client_ready(&c); offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
    recovery_tick(&r,&c);
    at_ms(120999); recovery_tick(&r,&c); assert(!vote_calls);
    if(!strcmp(mode,"rec_vote_fail")) vote_error=ESP_FAIL;
    if(!strcmp(mode,"rec_vote_ack")) puback(&c,1);
    if(!strcmp(mode,"rec_vote_epoch")) role_query_hook=demote_event;
    if(!strcmp(mode,"rec_vote_role")) role_query_hook=demote_sdk;
    if(!strcmp(mode,"rec_vote_rootless")) role_query_hook=become_rootless;
    if(!strcmp(mode,"rec_vote_disconnect")) role_query_hook=disconnect_parent;
    if(!strcmp(mode,"rec_vote_ack_race")) role_query_hook=puback_during_selection;
    if(!strcmp(mode,"rec_vote_voting")) event(MESH_EVENT_VOTE_STARTED,NULL);
    if(!strcmp(mode,"rec_vote_no_demand")) state.qos1_pending=false;
    at_ms(121000); recovery_tick(&r,&c);
    bool voted=!strcmp(mode,"rec_vote") || !strcmp(mode,"rec_vote_boundary") || !strcmp(mode,"rec_vote_fail") ||
               !strcmp(mode,"rec_vote_ack") || !strcmp(mode,"rec_vote_ack_race") || !strcmp(mode,"rec_vote_no_demand");
    assert(vote_calls==(unsigned)voted && self_organized);
    assert(state.status.recovery_votes==(unsigned)(voted && !vote_error));
    assert(!state.status.recovery_demotions);
    if(voted) {
        uint64_t due=r.vote_after; assert(due==1021000);
        recovery_tick(&r,&c); assert(vote_calls==1);
        /* API success without demotion (no candidate), and API failure, both
         * consume the same budget. New losses cannot bypass it. */
        for(unsigned i=0;i<3;++i) { at_ms(160000+i*10000); router_loss(-90,true); connected(true); }
        stop_client(&c); client_ready(&c); offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
        recovery_tick(&r,&c); at_ms(300000); recovery_tick(&r,&c); assert(vote_calls==1);
        if(!vote_error) { connected(false); assert(state.status.recovery_demotions==1); }
    }
}
static void test_parent(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; native_recovery_init(&r,30000);
    connected(false);
    if(!strcmp(mode,"rec_rootless") || !strcmp(mode,"rec_rootless_cleared")) rootless(true);
    else event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
    recovery_tick(&r,&c); at_ms(150999); recovery_tick(&r,&c); assert(!reselect_calls);
    if(!strcmp(mode,"rec_parent_natural")) reconnect_child();
    if(!strcmp(mode,"rec_parent_stale")) role_query_hook=demote_event; /* new parent, same child role */
    if(!strcmp(mode,"rec_parent_promote")) role_query_hook=reconnect_root;
    if(!strcmp(mode,"rec_rootless_cleared")) role_query_hook=reconnect_child;
    if(!strcmp(mode,"rec_parent_failure")) reselect_error=ESP_FAIL;
    at_ms(151000); recovery_tick(&r,&c);
    bool acted=!strcmp(mode,"rec_parent") || !strcmp(mode,"rec_parent_failure") || !strcmp(mode,"rec_rootless");
    assert(reselect_calls==(unsigned)acted && self_organized);
    if(!acted) return;
    at_ms(480999); recovery_tick(&r,&c); assert(reselect_calls==1);
    at_ms(481000); recovery_tick(&r,&c); assert(reselect_calls==2);
    reconnect_child(); recovery_tick(&r,&c); assert(state.status.recovery_link==(unsigned)!reselect_error);
}
static void test_router(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; native_recovery_init(&r,0);
    connected(true); router_loss(-90,true); recovery_tick(&r,&c);
    if(!strcmp(mode,"rec_router_reconnected")) self_organized_hook=reconnect_root;
    if(!strcmp(mode,"rec_router_demoted")) self_organized_hook=demote_event;
    if(!strcmp(mode,"rec_router_failure")) connect_error=ESP_FAIL;
    at_ms(121000); recovery_tick(&r,&c);
    bool acted=!strcmp(mode,"rec_router") || !strcmp(mode,"rec_router_failure");
    assert(connect_calls==(unsigned)acted && !vote_calls && self_organized);
    at_ms(420999); recovery_tick(&r,&c); assert(connect_calls==(unsigned)acted);
}
static void dhcp_lease(void) { start_error=0; dhcp=ESP_NETIF_DHCP_STARTED; state.lease_observation_ready=true; ip_event_got_ip_t e=lease(); got_ip(&e); }
static void test_dhcp(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; native_recovery_init(&r,0);
    if(strcmp(mode,"rec_dhcp_running")) start_error=ESP_FAIL;
    connected(true); recovery_tick(&r,&c);
    unsigned starts=dhcp_starts,stops=dhcp_stops;
    if(strcmp(mode,"rec_dhcp_failure")) start_error=0;
    if(!strcmp(mode,"rec_dhcp_stale")) role_query_hook=demote_event;
    if(!strcmp(mode,"rec_dhcp_lease_race")) role_query_hook=dhcp_lease;
    at_ms(121000); recovery_tick(&r,&c);
    bool attempted=!strcmp(mode,"rec_dhcp") || !strcmp(mode,"rec_dhcp_failure");
    assert(dhcp_starts==starts+(unsigned)attempted);
    if(strcmp(mode,"rec_dhcp_stale")) assert(dhcp_stops==stops);
    recovery_tick(&r,&c); assert(dhcp_starts==starts+(unsigned)attempted);
    if(!strcmp(mode,"rec_dhcp")) {
        assert(state.lease_observation_ready && !state.session.has_ip);
        esp_netif_ip_info_t ip=lease().ip_info; sdk_acquire(ip); sdk_lost(); sdk_acquire(ip);
        assert(!state.session.has_ip); reconcile_root_lease(NULL); assert(state.session.has_ip);
    }
}
static void prepare_mqtt(native_recovery_t *r,mqtt_context_t *c,bool connected_mqtt) {
    native_recovery_init(r,0); connected(true); sdk_acquire(lease().ip_info);
    assert(start_client(c,state.session.epoch));
    if(connected_mqtt) {
        mqtt_connected(); offer_publication(c,MESH_MESSAGE_NODE_STATUS);
        assert(last_enqueue_qos==0 && !state.qos1_pending);
        offer_publication(c,MESH_MESSAGE_REGISTRATION_STATUS); assert(state.qos1_pending);
    }
    recovery_tick(r,c);
}
static void test_mqtt(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,strcmp(mode,"rec_broker_down")!=0);
    mqtt_context_t old=c;
    if(!strcmp(mode,"rec_mqtt_no_demand")) state.qos1_pending=false;
    if(!strcmp(mode,"rec_mqtt_ack_race")) role_query_hook=puback_during_selection;
    if(!strcmp(mode,"rec_mqtt_role_race")) role_query_hook=demote_sdk;
    if(!strcmp(mode,"rec_mqtt_rootless")) role_query_hook=become_rootless;
    at_ms(120999); recovery_tick(&r,&c); assert(!mqtt_disconnects && !mqtt_destroys);
    at_ms(121000); recovery_tick(&r,&c);
    if(!strcmp(mode,"rec_mqtt_no_demand") || !strcmp(mode,"rec_mqtt_ack_race") ||
       !strcmp(mode,"rec_mqtt_role_race") || !strcmp(mode,"rec_mqtt_rootless")) {
        assert(!mqtt_disconnects && !mqtt_destroys); return;
    }
    if(!strcmp(mode,"rec_broker_down")) {
        assert(!mqtt_disconnects); at_ms(300999); recovery_tick(&r,&c); assert(!mqtt_destroys);
        at_ms(301000); recovery_tick(&r,&c);
    } else {
        assert(mqtt_disconnects==1 && !mqtt_destroys);
        at_ms(200000);
        if(!strcmp(mode,"rec_mqtt_natural")) { mqtt_connected(); puback(&c,1); }
        else if(!strcmp(mode,"rec_mqtt_epoch")) connected(false);
        else if(!strcmp(mode,"rec_mqtt_recreate_race")) role_query_hook=puback_during_selection;
        else { mqtt_connected(); puback(&c,0); }
        at_ms(240999); recovery_tick(&r,&c); assert(!mqtt_destroys);
        at_ms(241000); recovery_tick(&r,&c);
        if(!strcmp(mode,"rec_mqtt_natural") || !strcmp(mode,"rec_mqtt_epoch") || !strcmp(mode,"rec_mqtt_recreate_race")) {
            assert(!mqtt_destroys); return;
        }
    }
    assert(mqtt_destroys==1 && mqtt_stops==1 && !c.client && state.session.epoch!=old.epoch);
    assert(start_client(&c,state.session.epoch)); mqtt_connected(); puback(&old,1);
    assert(!state.status.mqtt_pubacks && !vote_calls);
    offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS); recovery_tick(&r,&c);
    uint64_t now=now_ms(); at_ms(now+120000); recovery_tick(&r,&c);
    at_ms(now+240000); recovery_tick(&r,&c); assert(mqtt_destroys==1);
}
static void test_mqtt_timing(const char *mode) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,true); puback(&c,1);
    at_ms(3600000); recovery_tick(&r,&c); assert(!mqtt_disconnects && !mqtt_destroys);
    if(!strcmp(mode,"rec_mqtt_idle_demand")) {
        offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS); recovery_tick(&r,&c);
        at_ms(3650000); offer_publication(&c,MESH_MESSAGE_REGISTRATION_STATUS);
        assert(state.qos1_demand_ms==3600000); /* later demand must not postpone recovery */
        at_ms(3719999); recovery_tick(&r,&c); assert(!mqtt_disconnects && !mqtt_destroys);
        at_ms(3720000); recovery_tick(&r,&c); assert(mqtt_disconnects==1 && !mqtt_destroys);
    } else {
        disconnect_mqtt(); recovery_tick(&r,&c);
        at_ms(3650000); disconnect_mqtt(); /* duplicate must not restart timer */
        assert(state.mqtt_disconnected_ms==3600000);
        if(!strcmp(mode,"rec_mqtt_normal_reconnect")) { reconnect_mqtt(); puback(&c,1); }
        at_ms(3899999); recovery_tick(&r,&c); assert(!mqtt_destroys);
        at_ms(3900000); recovery_tick(&r,&c);
        assert(mqtt_destroys==(unsigned)!strcmp(mode,"rec_mqtt_disconnect_grace"));
    }
}
static void test_mqtt_reconnect_stall(void) {
    native_recovery_t r; mqtt_context_t c={0}; prepare_mqtt(&r,&c,true);
    at_ms(121000); recovery_tick(&r,&c); assert(mqtt_disconnects==1);
    at_ms(122000); disconnect_mqtt(); recovery_tick(&r,&c);
    at_ms(127000); mqtt_connected(); recovery_tick(&r,&c);
    at_ms(246999); recovery_tick(&r,&c); assert(!mqtt_destroys && mqtt_disconnects==1);
    at_ms(247000); recovery_tick(&r,&c); assert(mqtt_destroys==1 && mqtt_disconnects==1);
}
#include "repeated_cases.h"

static void test_recovery(const char *mode) {
    if(!strncmp(mode,"rec_repeat_",11)) test_repeated_link(mode);
    else if(!strncmp(mode,"rec_loss_",9)) test_loss_validation(mode);
    else if(!strncmp(mode,"rec_mqtt_flap",13)) test_mqtt_flapping(mode);
    else if(!strcmp(mode,"rec_mqtt_budget_disconnected")) test_mqtt_budget_disconnected();
    else if(!strcmp(mode,"rec_mqtt_demand_epoch")) test_mqtt_demand_epoch();
    else if(!strcmp(mode,"rec_mqtt_reconnect_stall")) test_mqtt_reconnect_stall();
    else if(!strncmp(mode,"rec_window",10)) test_window(mode);
    else if(!strncmp(mode,"rec_vote",8)) test_vote(mode);
    else if(!strncmp(mode,"rec_parent",10) || !strncmp(mode,"rec_rootless",12)) test_parent(mode);
    else if(!strncmp(mode,"rec_router",10)) test_router(mode);
    else if(!strncmp(mode,"rec_dhcp",8)) test_dhcp(mode);
    else if(!strcmp(mode,"rec_mqtt_idle_demand") || !strcmp(mode,"rec_mqtt_disconnect_grace") ||
            !strcmp(mode,"rec_mqtt_normal_reconnect")) test_mqtt_timing(mode);
    else test_mqtt(mode);
}
