/* Real transport/codec/owner paths; only SDK, RTOS and time are doubled. */
static const char delivery_boot[]="0123456789abcdef";
static uint8_t delivery_root[6]={2,0,0,0,0,99};
static mesh_wire_message_t delivery_last;
static uint32_t delivery_seq=1;

static void delivery_root_event(void) {
    mesh_event_root_address_t e; memcpy(e.addr,delivery_root,6);
    event(MESH_EVENT_ROOT_ADDRESS,&e);
}
static mesh_wire_message_t delivery_status(uint32_t seq,uint64_t uptime) {
    mesh_wire_message_t m={.kind=MESH_MESSAGE_NODE_STATUS};
    strcpy(m.node_id,own_id);
    m.length=snprintf(m.payload,sizeof(m.payload),
        "{\"protocol_version\":2,\"node_id\":\"%s\",\"revision\":0,\"boot_id\":\"%s\",\"seq\":%" PRIu32 ",\"uptime_s\":%" PRIu64 "}",
        own_id,delivery_boot,seq,uptime);
    return m;
}
static mesh_wire_message_t delivery_reply(uint32_t seq,uint64_t uptime) {
    mesh_wire_message_t m={.kind=MESH_MESSAGE_STATUS_RECEIPT};
    strcpy(m.node_id,own_id);
    m.length=snprintf(m.payload,sizeof(m.payload),
        "{\"boot_id\":\"%s\",\"seq\":%" PRIu32 ",\"uptime_s\":%" PRIu64 "}",delivery_boot,seq,uptime);
    return m;
}
static void delivery_receive(const mesh_wire_message_t *m,const uint8_t source[6]) {
    injected_message=*m; memcpy(injected_source,source,6);
    injected_receive=injected_pending=true;
    if(!setjmp(receipt_receive_exit)) mesh_receiver(NULL);
    injected_receive=false;
}
static bool delivery_ack(uint32_t seq,uint64_t uptime) {
    mesh_wire_message_t m=delivery_reply(seq,uptime);
    uint32_t progress=state.delivery.progress;
    delivery_receive(&m,delivery_root);
    return state.delivery.progress!=progress;
}
static void delivery_init(uint32_t jitter) {
    tx_init(jitter); delivery_root_event();
    recovery_tick(&tx_controller,&tx_context);
}
static void delivery_offer(uint32_t seq,uint64_t uptime,bool drain) {
    delivery_last=delivery_status(seq,uptime);
    esp_err_t result=mesh_transport_publish(delivery_last.kind,delivery_last.payload,delivery_last.length);
    assert(result==ESP_OK || result==ESP_ERR_NO_MEM);
    if(drain) tx_drain();
}
static void delivery_run(uint64_t start,uint64_t end,bool drain,bool ack) {
    for(uint64_t t=start;t<=end;t+=1000) {
        at_ms(t);
        if((t-start)%30000==0) {
            delivery_offer(delivery_seq,t/1000,drain);
            if(ack) assert(delivery_ack(delivery_seq,t/1000));
            delivery_seq+=2; /* registration consumes the intervening seq */
        }
        recovery_tick(&tx_controller,&tx_context);
    }
}
static void delivery_sdk_progress(void) { tx_send_success(); }
static void delivery_early_ack(void) {
    assert(state.tx_inflight && state.delivery.submitted);
    assert(delivery_ack(state.delivery.last_sent_seq,now_ms()/1000));
}
static recovery_request_t delivery_request(bool a,bool b) {
    return (recovery_request_t){.action=REC_CHILD_RESELECT,.epoch=state.session.epoch,
        .tx_stall=a,.delivery_stall=b,.tx_progress=state.tx_progress,
        .delivery_progress=state.delivery.progress,.local_generation=state.local_generation};
}

static void test_delivery(const char *mode) {
    delivery_init(!strcmp(mode,"delivery_jitter") ? 30000 : 0);
    if(!strcmp(mode,"delivery_sdk_success") || !strcmp(mode,"delivery_jitter") || !strcmp(mode,"delivery_both")) {
        bool drain=strcmp(mode,"delivery_both")!=0;
        uint64_t due=181000+tx_controller.jitter_ms;
        delivery_run(1000,due-1000,drain,false);
        assert(!reselect_calls && !state.status.parent_disconnect_count);
        delivery_run(due,due,drain,false);
        assert(reselect_calls==1 && tx_controller.tx_attempt_count==1);
        assert(tx_controller.child_causes==(REC_CHILD_DELIVERY_STALL|(drain ? 0 : REC_CHILD_TX_STALL)));
        assert(drain ? state.status.mesh_tx_ok>0 : local_status->count==LOCAL_STATUS_COUNT);
        assert(!state.status.mqtt_pubacks);
    } else if(!strcmp(mode,"delivery_healthy") || !strcmp(mode,"delivery_short") || !strcmp(mode,"delivery_congestion")) {
        if(!strcmp(mode,"delivery_short")) delivery_run(1000,120000,true,false);
        if(!strcmp(mode,"delivery_congestion")) {
            for(int i=0;i<LOCAL_DATA_COUNT+1;++i) tx_offer();
            delivery_run(1000,120000,false,false);
            delivery_offer(delivery_seq,120,false); delivery_seq+=2;
            assert(state.status.tx_dropped);
            tx_drain(); /* expired entries leave room for the next status */
        }
        delivery_run(121000,7201000,true,true);
        assert(!reselect_calls && state.delivery.progress>200);
    } else if(!strcmp(mode,"delivery_idle") || !strcmp(mode,"delivery_burst")) {
        if(!strcmp(mode,"delivery_burst")) delivery_offer(1,1,true);
        tx_run(1000,7201000,false,false);
        assert(!reselect_calls && !tx_controller.delivery_watching);
    } else if(!strcmp(mode,"delivery_sample_gap")) {
        delivery_run(1000,120000,true,false);
        delivery_run(3601000,3780000,true,false);
        assert(!reselect_calls);
        delivery_run(3781000,3781000,true,false); assert(reselect_calls==1);
    } else if(!strcmp(mode,"delivery_budget") || !strncmp(mode,"delivery_legacy_",16)) {
        delivery_run(1000,2001000,true,false);
        assert(reselect_calls==3 && tx_controller.tx_attempt_count==3);
        if(!strcmp(mode,"delivery_budget")) {
            /* SDK progress, reconnection and a fresh proof do not refund attempts. */
            connected(false); delivery_root_event();
            delivery_run(2002000,3780000,true,false); assert(reselect_calls==3);
            delivery_run(3781000,3781000,true,false); assert(reselect_calls==4);
        } else {
            if(!strcmp(mode,"delivery_legacy_disconnect")) event(MESH_EVENT_PARENT_DISCONNECTED,NULL);
            else if(!strcmp(mode,"delivery_legacy_rootless")) rootless(true);
            else if(!strcmp(mode,"delivery_legacy_weak")) {
                for(int i=0;i<3;++i) { router_loss(-90,true);connected(false); }
            }
            else assert(0);
            recovery_tick(&tx_controller,&tx_context);
            at_ms(2121000); recovery_tick(&tx_controller,&tx_context);
            assert(reselect_calls==4 && tx_controller.tx_attempt_count==3 && !tx_controller.child_causes);
        }
    } else if(!strcmp(mode,"delivery_shared_budget")) {
        delivery_run(1000,181000,false,false);
        assert(reselect_calls==1 && tx_controller.child_causes==3);
        tx_drain();
        delivery_run(182000,481000,true,false);
        assert(reselect_calls==2 && tx_controller.child_causes==REC_CHILD_DELIVERY_STALL);
        delivery_run(482000,781000,false,false);
        assert(reselect_calls==3 && tx_controller.child_causes==3);
        delivery_run(782000,2001000,true,false);
        assert(reselect_calls==3 && tx_controller.tx_attempt_count==3);
    } else if(!strcmp(mode,"delivery_sdk_race")) {
        delivery_run(1000,180000,true,false);
        role_query_hook=delivery_sdk_progress;
        delivery_run(181000,181000,true,false); assert(reselect_calls==1);
    } else if(!strcmp(mode,"delivery_early_ack")) {
        send_hook=delivery_early_ack; send_error=ESP_FAIL;
        delivery_offer(1,1,true);
        assert(state.delivery.progress==1 && state.delivery.proven_ms==1000 && !state.tx_progress);
    } else if(!strncmp(mode,"delivery_cancel_",16)) {
        delivery_offer(1,1,true);
        recovery_request_t r=delivery_request(true,true);
        if(strcmp(mode,"delivery_cancel_a")) assert(delivery_ack(1,1));
        if(strcmp(mode,"delivery_cancel_b")) ++state.tx_progress;
        recover_mesh(&r);
        assert(reselect_calls==(unsigned)(strcmp(mode,"delivery_cancel_both")!=0));
    } else if(!strcmp(mode,"delivery_delayed")) {
        delivery_run(1000,59000,true,false);
        at_ms(60000); assert(delivery_ack(1,1));
        assert(state.delivery.proven_ms==1000);
        delivery_run(61000,180000,true,false); assert(!reselect_calls);
        delivery_run(181000,181000,true,false); assert(reselect_calls==1);
    } else if(!strcmp(mode,"delivery_transit_delay")) {
        /* Three bounded queue hops can exceed 60 s. A 90 s steady delay is
         * delivery progress, and must not cause repeated parent selection. */
        for(uint64_t t=1000;t<=7201000;t+=1000) {
            at_ms(t);
            if((t-1000)%30000==0) {
                delivery_offer(delivery_seq,t/1000,true);
                if(t>=91000) assert(delivery_ack(delivery_seq-6,(t-90000)/1000));
                delivery_seq+=2;
            }
            recovery_tick(&tx_controller,&tx_context);
        }
        assert(!reselect_calls);
    } else if(!strcmp(mode,"delivery_duplicate")) {
        delivery_offer(1,1,true); assert(delivery_ack(1,1)); assert(!delivery_ack(1,1));
        at_ms(31000); delivery_offer(3,31,true); assert(delivery_ack(3,31));
        assert(!delivery_ack(1,1) && state.delivery.progress==2);
    } else if(!strcmp(mode,"delivery_wrap")) {
        delivery_offer(UINT32_MAX-1,1,true); assert(delivery_ack(UINT32_MAX-1,1));
        at_ms(31000); delivery_offer(0,31,true); assert(delivery_ack(0,31));
        assert(!delivery_ack(UINT32_MAX-1,1));
    } else if(!strcmp(mode,"delivery_invalid")) {
        delivery_offer(1,1,true);
        assert(!delivery_ack(2,1) && !delivery_ack(0,1) && !delivery_ack(1,2));
        mesh_wire_message_t m=delivery_reply(1,1);
        m.node_id[0]='1'; assert(!receive_status_receipt(&m,delivery_root));
        m=delivery_reply(1,1); m.payload[12]='f'; assert(!receive_status_receipt(&m,delivery_root));
        /* A different source may identify a changed Root only by echoing the
         * latest submitted status. An unsolicited/unsent identity cannot. */
        uint8_t other[6]={2,0,0,0,0,98}; m=delivery_reply(0,1);
        assert(!receive_status_receipt(&m,other));
        const char *bad[]={"{}","{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"uptime_s\":1.5}",
            "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"uptime_s\":-1}",
            "{\"boot_id\":\"0123456789abcdef\",\"seq\":1,\"uptime_s\":1e30}"};
        for(unsigned i=0;i<sizeof(bad)/sizeof(bad[0]);++i) {
            strcpy(m.payload,bad[i]);m.length=strlen(bad[i]);assert(!receive_status_receipt(&m,delivery_root));
        }
        at_ms(122000); delivery_offer(3,122,true); assert(!delivery_ack(1,1));
        assert(!state.delivery.progress && delivery_ack(3,122));
    } else if(!strcmp(mode,"delivery_reconnect") || !strcmp(mode,"delivery_root_change") || !strcmp(mode,"delivery_config")) {
        delivery_offer(1,1,true);
        at_ms(31000);
        if(!strcmp(mode,"delivery_config")) mesh_transport_drop_pending();
        else {
            if(!strcmp(mode,"delivery_root_change")) ++delivery_root[5];
            else connected(false);
            delivery_root_event();
        }
        assert(!delivery_ack(1,1));
        delivery_offer(3,31,true); assert(!delivery_ack(1,1)); assert(delivery_ack(3,31));
        delivery_seq=5;
        delivery_run(32000,600000,true,true); assert(!reselect_calls);
    } else if(!strcmp(mode,"delivery_unknown_root")) {
        connected(false); /* ROOT_ADDRESS absent after same-Root reconnect */
        delivery_run(1000,7201000,true,false);
        assert(reselect_calls==6 && tx_controller.delivery_watching);
    } else if(!strcmp(mode,"delivery_time64")) {
        uint64_t start=(UINT64_C(1)<<32)+1000;
        delivery_run(start,start+179000,true,false); assert(!reselect_calls);
        delivery_run(start+180000,start+180000,true,false); assert(reselect_calls==1);
    } else if(!strcmp(mode,"delivery_inhibit")) {
        event(MESH_EVENT_VOTE_STARTED,NULL);
        delivery_run(1000,300000,true,false); assert(!reselect_calls);
        event(MESH_EVENT_VOTE_STOPPED,NULL);
        delivery_run(301000,419000,true,false); assert(!reselect_calls);
        delivery_run(420000,481000,true,false); assert(reselect_calls==1);
    } else assert(0);
}

static origin_route_t receipt_routes[ROUTE_COUNT];
static mqtt_context_t receipt_context;
static void receipt_bridge(const mesh_wire_message_t *m) {
    const uint8_t peer[6]={2,0,0,0,0,42};
    delivery_receive(m,peer);
    publication_t record;
    QueueHandle_t q=mesh_wire_is_registration(m->kind) ? root_registration :
                    m->kind==MESH_MESSAGE_NODE_STATUS ? root_status : root_data;
    assert(xQueueReceive(q,&record,0)==pdTRUE);
    bridge_publication(&receipt_context,receipt_routes,&record);
}
static void test_receipt_root(const char *mode) {
    connected(true);
    mesh_wire_message_t m=delivery_status(1,1);
    if(!strcmp(mode,"receipt_no_mqtt") || !strcmp(mode,"receipt_mqtt_full")) {
        if(!strcmp(mode,"receipt_mqtt_full")) { client_ready(&receipt_context);mqtt_outbox_size=MQTT_OUTBOX_BYTES; }
        receipt_bridge(&m);
        assert(status_receipts->count==1 && !state.status.mqtt_published);
        tx_drain();
        assert(send_calls==1 && sent_message.kind==MESH_MESSAGE_STATUS_RECEIPT);
        assert(!strcmp(sent_message.node_id,own_id) && sent_destination[5]==42);
        status_identity_t identity; cJSON *json=parse_json(&sent_message);
        assert(read_status_identity(json,&identity)); cJSON_Delete(json);
        assert(identity.seq==1 && identity.uptime_s==1 && !strcmp(identity.boot_id,delivery_boot));
        assert(!apply_configs->count && !state.tx_progress);
    } else if(!strcmp(mode,"receipt_queue_full")) {
        for(int i=0;i<RECEIPT_COUNT+20;++i) receipt_bridge(&m);
        assert(status_receipts->count==RECEIPT_COUNT);
        send_error=ESP_FAIL;
        for(int i=0;i<RECEIPT_COUNT;++i) tx_drain();
        assert(send_calls==RECEIPT_COUNT && !status_receipts->count);
        tx_drain();assert(send_calls==RECEIPT_COUNT); /* no retries */
    } else if(!strcmp(mode,"receipt_rx_full")) {
        uint8_t peer[6]={2,0,0,0,0,42};
        for(int i=0;i<ROOT_STATUS_COUNT+1;++i) delivery_receive(&m,peer);
        assert(root_status->count==ROOT_STATUS_COUNT && state.status.rx_dropped==1 && !status_receipts->count);
        publication_t record; assert(xQueueReceive(root_status,&record,0));
        bridge_publication(&receipt_context,receipt_routes,&record);assert(status_receipts->count==1);
    } else if(!strcmp(mode,"receipt_invalid")) {
        strcpy(m.payload,"{}");m.length=2;receipt_bridge(&m);assert(!status_receipts->count);
        m=delivery_status(1,1);m.kind=MESH_MESSAGE_REGISTRATION_STATUS;
        receipt_bridge(&m);assert(!status_receipts->count);
        m=delivery_status(1,1);m.kind=MESH_MESSAGE_STATUS_RECEIPT;
        uint8_t peer[6]={2,0,0,0,0,42};delivery_receive(&m,peer);
        assert(!root_data->count && !status_receipts->count); /* no receipt loops */
        assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_ERR_INVALID_ARG);
    } else if(!strcmp(mode,"receipt_stale") || !strcmp(mode,"receipt_epoch") || !strcmp(mode,"receipt_demote")) {
        receipt_bridge(&m);
        if(!strcmp(mode,"receipt_stale")) at_ms(31000);
        else connected(strcmp(mode,"receipt_demote")!=0);
        tx_drain();assert(!send_calls && !status_receipts->count);
    } else if(!strcmp(mode,"receipt_root_local")) {
        client_ready(&receipt_context);
        receipt_bridge(&m); assert(status_receipts->count==1 && state.status.mqtt_published==1);
        assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_OK);tx_drain();
        publication_t record;assert(xQueueReceive(root_status,&record,0));
        bridge_publication(&receipt_context,receipt_routes,&record);
        assert(state.status.mqtt_published==2 && !status_receipts->count && send_calls==1);
        assert(!strcmp(last_topic,"omk/node/020000000041/status") && last_enqueue_qos==0);
    } else assert(0);
}

static void test_delivery_roundtrip(void) {
    delivery_init(0);
    delivery_offer(1,1,true);
    mesh_wire_message_t uplink=sent_message;
    transport_state_t child=state;
    /* Switch the harness to a Root instance, preserving the child's evidence.
     * Feed the actual sent wire frame through recv, validation, queue and send. */
    connected(true);
    receipt_bridge(&uplink); assert(status_receipts->count==1);
    tx_drain();
    mesh_wire_message_t receipt=sent_message;
    assert(receipt.kind==MESH_MESSAGE_STATUS_RECEIPT);
    state=child; sdk_root=false;
    delivery_receive(&receipt,delivery_root);
    assert(state.delivery.progress==1 && state.delivery.proven_ms==1000);
    assert(!state.status.mqtt_published && !root_data->count && !apply_configs->count);
    /* Receiving a receipt must not consume or alter the SEN66/BLE data queue. */
    for(int kind=MESH_MESSAGE_SEN66;kind<=MESH_MESSAGE_BLE_RAW;++kind) {
        uplink.kind=kind;
        assert(mesh_transport_publish(kind,uplink.payload,uplink.length)==ESP_OK);
        unsigned before=send_calls;
        delivery_receive(&receipt,delivery_root); /* duplicate */
        assert(local_data->count==1);
        tx_drain();assert(send_calls==before+1 && sent_message.kind==(mesh_message_kind_t)kind);
    }
}
