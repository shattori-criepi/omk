/* M1 assertions exercise actual send_message, queues and Root MQTT bridge.
 * Logical state must be byte-identical; elapsed time and physical load aren't. */
static mesh_wire_message_t measurement_message(void) {
    mesh_wire_message_t m={.kind=MESH_MESSAGE_MEASUREMENT};
    strcpy(m.node_id,own_id);uint8_t bytes[256]={0};
    m.length=ab_report_json(bytes,own_id,"0123456789abcdef",1,0,m.payload,sizeof(m.payload));
    assert(m.length && m.length<=480);return m;
}
static void scan_invalidate_nonce(void) { ++root_nonce; }
static void scan_measurement_success(void) {
    mesh_wire_message_t m=measurement_message();
    publication_t p={.epoch=state.session.epoch,.local_generation=state.local_generation};
    assert(send_message(&m,NULL,&p)==ESP_OK);
}
static void scan_settle(void) {
    uint16_t window;
    for(unsigned i=0;i<4;i++) {enum ab_op op=ab_runtime_tick(&window);if(!op)return;assert(window==80||window==64||window==40||window==24);ab_runtime_complete(op,true);}
}
static uint32_t scan_state(void) {uint8_t r[256];ab_runtime_pack(r);return (uint32_t)r[40]|(uint32_t)r[41]<<8|(uint32_t)r[42]<<16|(uint32_t)r[43]<<24;}
static void test_ble_scan_transport(const char *mode) {
    ab_runtime_boot(own_id,"0123456789abcdef");
    if(!strcmp(mode,"scan_pacing_active") || !strcmp(mode,"scan_pacing_baseline")) {
        bool with_report=!strcmp(mode,"scan_pacing_active");
        connected(false);ab_runtime_ready();scan_settle();
        for(unsigned i=0;i<3;i++)mesh_tx_pacing_result(&tx_pacing,1000,ESP_ERR_MESH_TIMEOUT);
        mesh_wire_message_t m=measurement_message();
        if(with_report)assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_OK);
        tx_offer();at_ms(5999);assert(upstream_paused() && !send_calls);
        at_ms(6000);assert(!upstream_paused());tx_drain();
        assert(send_calls==1 && sent_message.kind==MESH_MESSAGE_SEN66 && now_ms()==6000);
        uint8_t report[256];ab_runtime_pack(report);assert(report[238]==with_report && !report[236] && !report[234]);
        assert(!tx_pacing.active); /* Ordinary success alone released pacing. */
        ab_runtime_sdk_begin(1,true);ab_runtime_sdk_end(ESP_ERR_MESH_TIMEOUT);
        ab_runtime_pack(report);assert(report[52+5*3]==1); /* Recoverable boot bank. */
    } else if(!strcmp(mode,"scan_schedule_reporting")) {
        connected(false);delivery_root_event();ab_runtime_ready();scan_settle();
        char payload[512];int length=snprintf(payload,sizeof(payload),"{\"protocol_version\":2,\"command\":\"ble_scan_plan\",\"node_id\":\"%s\",\"target_boot_id\":\"0123456789abcdef\",\"root_id\":\"020000000009\",\"root_nonce\":\"0000000000000001\",\"control_epoch\":1,\"accept_before_ms\":2000,\"segments\":[[20000,40000,0]],\"plan_end_ms\":40000}",own_id);
        assert(ab_runtime_command(payload,length,delivery_root)==AB_ACCEPTED);ab_runtime_reported();
        at_ms(19999);scan_settle();assert(!ab_runtime_report_due());
        at_ms(20000);scan_settle();assert(ab_runtime_report_due());ab_runtime_reported();
        at_ms(40000);scan_settle();assert(ab_runtime_report_due());
    } else if(!strcmp(mode,"scan_topology_quiet")) {
        connected(false);ab_runtime_ready();scan_settle();ab_runtime_reported();
        for(unsigned i=1;i<100;i++) {
            at_ms(1000+i*1000);
            event(MESH_EVENT_PARENT_DISCONNECTED,NULL);scan_settle();assert(!ab_runtime_report_due());
            connected(false);rootless(i%2);delivery_root_event();scan_settle();
            assert(!ab_runtime_report_due());
        }
        const char *bad="{}";ab_runtime_command(bad,2,delivery_root);
        assert(ab_runtime_report_due());ab_runtime_reported();
        for(unsigned i=1;i<100;i++) {test_time+=100000;ab_runtime_command(bad,2,delivery_root);assert(!ab_runtime_report_due());}
        test_time+=100000;assert(ab_runtime_report_due());
    } else if(!strcmp(mode,"scan_stale_topology")) {
        ab_topology_t newer={.started=true,.root=true,.observation=100,.root_nonce=123};
        ab_runtime_topology(&newer);assert(((scan_state()>>10)&3)==1);
        ab_topology_t stale={.started=true,.parent=true,.observation=99};
        ab_runtime_topology(&stale);assert(((scan_state()>>10)&3)==1);
        newer.observation=101;newer.root=false;ab_runtime_topology(&newer);assert(((scan_state()>>10)&3)==0);
    } else if(!strcmp(mode,"scan_m1_recovery")) {
        tx_make_due();role_query_hook=scan_measurement_success;
        recovery_tick(&tx_controller,&tx_context);
        assert(reselect_calls==1 && state.status.recovery_requests==1 && state.status.recovery_ok==1);
        assert(!state.tx_progress && !state.status.mesh_tx_ok);
    } else if(!strcmp(mode,"scan_runtime_resume")) {
        connected(false);delivery_root_event();ab_runtime_ready();scan_settle();
        char payload[512];int length=snprintf(payload,sizeof(payload),"{\"protocol_version\":2,\"command\":\"ble_scan_plan\",\"node_id\":\"%s\",\"target_boot_id\":\"0123456789abcdef\",\"root_id\":\"020000000009\",\"root_nonce\":\"0000000000000001\",\"control_epoch\":1,\"accept_before_ms\":2000,\"segments\":[[3000,50000,2]],\"plan_end_ms\":50000}",own_id);
        assert(ab_runtime_command(payload,length,delivery_root)==AB_ACCEPTED);
        at_ms(3000);scan_settle();assert(((scan_state()>>7)&7)==2);
        ab_runtime_sdk_begin(1,true); /* Sole sender blocked; control task still runs. */
        at_ms(4000);rootless(true);scan_settle();assert(((scan_state()>>7)&7)==0);
        rootless(false);scan_settle();assert(((scan_state()>>15)&7)==AB_PLAN_SUSPENDED);
        at_ms(5000);delivery_root_event();scan_settle();at_ms(9999);scan_settle();assert(((scan_state()>>7)&7)==0);
        at_ms(10000);scan_settle();assert(((scan_state()>>7)&7)==2);
        ab_runtime_sdk_end(ESP_ERR_MESH_TIMEOUT);uint8_t r[256];ab_runtime_pack(r);assert(r[48]==1 && (r[44]&(1<<6)));
        ab_runtime_sdk_begin(1,true);ab_runtime_sdk_end(ESP_ERR_MESH_TIMEOUT);
        ab_runtime_boot(own_id,"0123456789abcdef");ab_runtime_pack(r);assert(r[136+5*3]==1);
        at_ms(50000);scan_settle();assert(((scan_state()>>7)&7)==0);
        assert(ab_runtime_command(payload,length,delivery_root)==AB_DUPLICATE);scan_settle();assert(((scan_state()>>7)&7)==0);
    } else if(!strcmp(mode,"scan_m1_ok") || !strcmp(mode,"scan_m1_error") || !strcmp(mode,"scan_m1_reject")) {
        connected(false);
        state.tx_offered=true;state.tx_offer_ms=now_ms();state.tx_offer_epoch=state.session.epoch;
        state.tx_offer_generation=state.local_generation;state.tx_progress=17;
        mesh_tx_pacing_result(&tx_pacing,now_ms(),ESP_ERR_MESH_TIMEOUT);
        publication_t p={.message=measurement_message(),.epoch=state.session.epoch,.local_generation=state.local_generation,.local=true};
        if(!strcmp(mode,"scan_m1_reject"))p.epoch--;
        if(!strcmp(mode,"scan_m1_error"))send_error=ESP_ERR_MESH_NO_MEMORY;
        transport_state_t before=state;mesh_tx_pacing_t pacing_before=tx_pacing;
        send_message(&p.message,NULL,&p);
#if OMK_DIAGNOSTICS
        extern unsigned diag_kind8_begin, diag_kind8_end;
        unsigned expected = strcmp(mode,"scan_m1_reject") != 0;
        assert(diag_kind8_begin == expected && diag_kind8_end == expected);
#endif
        /* Every real SDK call now has liveness metadata, including kind 8.
         * It must still leave ordinary traffic/quality counters unchanged. */
        assert(!state.call_active && state.call_id==before.call_id+(strcmp(mode,"scan_m1_reject")!=0));
        before.call_id=state.call_id; before.call_started_ms=state.call_started_ms;
        assert(!memcmp(&state,&before,sizeof(state)));
        assert(!memcmp(&tx_pacing,&pacing_before,sizeof(tx_pacing)));
        uint8_t data[256];ab_runtime_pack(data);
        assert((data[234]==1)==!strcmp(mode,"scan_m1_ok"));
        assert((data[236]==1)==!strcmp(mode,"scan_m1_error"));
        assert((data[238]==1)==!strcmp(mode,"scan_m1_reject"));
    } else if(!strcmp(mode,"scan_m1_publish")) {
        connected(false);mesh_wire_message_t m=measurement_message();transport_state_t before=state;
        for(unsigned i=0;i<LOCAL_DATA_COUNT;i++)assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_OK);
        assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_ERR_NO_MEM);
        assert(!memcmp(&state,&before,sizeof(state)) && !state.tx_offered);
    } else if(!strcmp(mode,"scan_m1_bridge")) {
        connected(true);ip_event_got_ip_t e=lease();got_ip(&e);bridge_once();mqtt_connected();
        publication_t p={.message=measurement_message(),.epoch=state.session.epoch,.queued_ms=now_ms(),.local=false};
        memcpy(p.source,own_mac,6);origin_route_t routes[ROUTE_COUNT]={0};
        mqtt_context_t context={.client=state.active_client,.epoch=state.client_epoch};
        transport_state_t before=state;bridge_publication(&context,routes,&p);
        assert(!memcmp(&state,&before,sizeof(state)) && !routes[0].used);
        assert(last_enqueue_qos==0 && strstr(last_topic,"/measurement"));
        state.session.mqtt_connected=false;before=state;bridge_publication(&context,routes,&p);
        assert(!memcmp(&state,&before,sizeof(state)));
    } else if(!strcmp(mode,"scan_topic")) {
        mesh_config_assembler_t a={0};const char *topic="omk/node/020000000001/ble_scan/config";
        assert(mesh_wire_config_fragment(&a,topic,strlen(topic),"{",1,2,0)==0 && a.scan);
        const char *wrong="omk/node/020000000001/registration/config";
        assert(mesh_wire_config_fragment(&a,wrong,strlen(wrong),"}",1,2,1)==-1);
    } else if(!strcmp(mode,"scan_nonce") || !strcmp(mode,"scan_nonce_race")) {
        connected(true);ip_event_got_ip_t e=lease();got_ip(&e);bridge_once();mqtt_connected();
        mesh_wire_message_t m={.kind=MESH_MESSAGE_CONFIG};strcpy(m.node_id,own_id);
        m.length=snprintf(m.payload,sizeof(m.payload),"{\"protocol_version\":2,\"command\":\"ble_scan_cancel\",\"node_id\":\"%s\",\"target_boot_id\":\"0123456789abcdef\",\"root_id\":\"%s\",\"root_nonce\":\"%016" PRIx64 "\",\"control_epoch\":1,\"accept_before_ms\":3000}",own_id,own_id,root_nonce);
        assert(root_scan_config(&m));uint64_t previous=root_nonce;
        if(!strcmp(mode,"scan_nonce_race")) {
            time_hook=scan_invalidate_nonce;
            assert(send_message(&m,own_mac,NULL)==ESP_ERR_INVALID_STATE && !send_calls);
            return;
        }
        mqtt_context_t *context=mqtt_arg;esp_mqtt_event_t event={.client=context->client};
        mqtt_event(context,0,MQTT_EVENT_DISCONNECTED,&event);assert(!root_scan_config(&m));
        mqtt_event(context,0,MQTT_EVENT_CONNECTED,&event);assert(root_nonce && root_nonce!=previous && !root_scan_config(&m));
        assert(send_message(&m,own_mac,NULL)==ESP_ERR_INVALID_STATE && !send_calls);
        event.topic="omk/node/020000000041/ble_scan/config";event.topic_len=strlen(event.topic);
        event.data=m.payload;event.data_len=event.total_data_len=m.length;event.retain=1;
        mqtt_event(context,0,MQTT_EVENT_DATA,&event);assert(!broker_configs->count);
    } else assert(!"unknown scan test");
}
