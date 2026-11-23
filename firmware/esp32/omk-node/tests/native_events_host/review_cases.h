/* AB1 independent review: exercise each reported failure on production code. */
static void review_ack_during_send(void) {
    if(sent_message.kind==MESH_MESSAGE_NODE_STATUS) {
        status_identity_t identity;assert(parse_status_identity(&sent_message,&identity));
        assert(delivery_ack(identity.seq,identity.uptime_s));
    }
    send_hook=review_ack_during_send;
}
static void review_vote_restart(void) {
    event(MESH_EVENT_VOTE_STOPPED,NULL);event(MESH_EVENT_VOTE_STARTED,NULL);
}
static void review_log_ack(void) { assert(delivery_ack(1,1)); }
static void review_log_vote(void) { event(MESH_EVENT_VOTE_STARTED,NULL); }

static void test_review(const char *mode) {
    if(!strncmp(mode,"review_unknown_",15)) {
        delivery_init(0);
        if(!strcmp(mode,"review_unknown_rootless")) {rootless(true);rootless(false);}
        else connected(false);
        delivery_run(1000,180000,true,false);assert(!reselect_calls);
        if(!strcmp(mode,"review_unknown_restored")) {
            delivery_root_event();delivery_run(181000,601000,true,true);assert(!reselect_calls);
        } else {
            /* Zero source and an identity never submitted
             * cannot establish a Root while address discovery is running. */
            mesh_wire_message_t m=delivery_reply(delivery_seq-4,120);
            uint8_t unknown[6]={0};assert(!receive_status_receipt(&m,unknown));
            assert(!receive_status_receipt(&m,delivery_root));
            delivery_run(181000,181000,true,false);assert(reselect_calls==1);
            delivery_run(182000,2001000,true,false);assert(reselect_calls==3);
        }
    } else if(!strcmp(mode,"review_vote_lost")) {
        delivery_init(0);event(MESH_EVENT_VOTE_STARTED,NULL);
        delivery_run(1000,300000,true,false);assert(!reselect_calls && state.voting);
        delivery_run(301000,420000,true,false);assert(!state.voting && !reselect_calls);
        /* Expiry at 301 s invalidates election sends; the next periodic offer
         * at 331 s starts a new bounded 180 s observation. */
        delivery_run(421000,510000,true,false);assert(!reselect_calls);
        delivery_run(511000,511000,true,false);assert(reselect_calls==1);
    } else if(!strcmp(mode,"review_vote_rootless")) {
        delivery_init(0);rootless(true);event(MESH_EVENT_VOTE_STARTED,NULL);
        recovery_tick(&tx_controller,&tx_context);
        at_ms(301000);recovery_tick(&tx_controller,&tx_context);
        assert(!state.voting && !reselect_calls);
        at_ms(421000);recovery_tick(&tx_controller,&tx_context);assert(reselect_calls==1);
    } else if(!strcmp(mode,"review_vote_mqtt")) {
        prepare_mqtt(&tx_controller,&tx_context,true);event(MESH_EVENT_VOTE_STARTED,NULL);
        at_ms(301000);recovery_tick(&tx_controller,&tx_context);
        assert(!state.voting && !mqtt_disconnects);
        at_ms(421000);recovery_tick(&tx_controller,&tx_context);assert(mqtt_disconnects==1);
    } else if(!strcmp(mode,"review_vote_query_failure")) {
        connected(true);client_ready(&tx_context);native_recovery_init(&tx_controller,0);
        unsigned starts=dhcp_starts,stops=dhcp_stops;uint32_t epoch=state.session.epoch;
        event(MESH_EVENT_VOTE_STARTED,NULL);parent_query_error=ESP_FAIL;
        at_ms(301000);recovery_tick(&tx_controller,&tx_context);
        assert(!state.voting && state.session.epoch==epoch && state.session.has_ip && state.session.mqtt_connected);
        assert(dhcp_starts==starts && dhcp_stops==stops && !mqtt_disconnects && !reselect_calls);
    } else if(!strncmp(mode,"review_vote_",12)) {
        delivery_init(0);event(MESH_EVENT_VOTE_STARTED,NULL);
        at_ms(301000);
        if(!strcmp(mode,"review_vote_role")) sdk_root=true;
        else if(!strcmp(mode,"review_vote_parent")) {sdk_parent_override[0]=2;sdk_parent_override[5]=88;}
        else if(!strcmp(mode,"review_vote_disconnect")) sdk_layer_override=-1;
        else if(!strcmp(mode,"review_vote_race")) parent_query_hook=review_vote_restart;
        else if(!strcmp(mode,"review_vote_duplicate")) event(MESH_EVENT_VOTE_STARTED,NULL);
        else assert(0);
        recovery_tick(&tx_controller,&tx_context);
        assert(!reselect_calls);
        if(!strcmp(mode,"review_vote_role")) {
            assert(!state.session.is_root && !state.root_mac[0] && !state.voting);
            event(MESH_EVENT_ROOT_SWITCH_ACK,NULL);assert(state.session.is_root && ble_root);
        }
        else if(!strcmp(mode,"review_vote_parent")) assert(state.status.parent_mac[5]==20 && !state.root_mac[0]);
        else if(!strcmp(mode,"review_vote_disconnect")) assert(!state.root_mac[0] && !state.status.parent_disconnect_count);
        else if(!strcmp(mode,"review_vote_race")) assert(state.voting && state.vote_hold_until==601000);
        else assert(!state.voting && state.inhibit_until==421000);
    } else if(!strcmp(mode,"review_config_worker")) {
        connected(true);client_ready(&receipt_context);
        mesh_wire_message_t m=delivery_status(1,1);receipt_bridge(&m);
        control_t config={.epoch=state.session.epoch,.queued_ms=now_ms(),.peer={2,0,0,0,0,42}};
        config.message.kind=MESH_MESSAGE_CONFIG;strcpy(config.message.node_id,own_id);
        strcpy(config.message.payload,"{\"protocol_version\":2,\"revision\":1,\"logical_id\":\"sen66-001\"}");
        config.message.length=strlen(config.message.payload);
        assert(xQueueSend(mesh_configs,&config,0));
        assert(mesh_transport_publish(m.kind,m.payload,m.length)==ESP_OK);
        /* Downlink failure must not retry/hold the worker: it still handles the
         * receipt and forwards Root's own data on this pass. SDK double checks
         * both CONFIG and RECEIPT use DEF|NONBLOCK, not P2P's blocking service. */
        send_error=ESP_ERR_NO_MEM;tx_drain();
        assert(send_calls==2 && !mesh_configs->count && !status_receipts->count);
        publication_t record;assert(xQueueReceive(root_status,&record,0));
        bridge_publication(&receipt_context,receipt_routes,&record);
        assert(state.status.mqtt_published==2 && !status_receipts->count);
        tx_drain();assert(send_calls==2);
    } else if(!strcmp(mode,"review_status_busy")) {
        delivery_init(0);send_hook=review_ack_during_send;
        for(uint64_t t=1000;t<=601000;t+=1000) {
            at_ms(t);
            for(int i=0;i<LOCAL_DATA_COUNT+2;++i) tx_offer();
            for(int i=0;i<CONTROL_COUNT+1;++i) {
                esp_err_t e=mesh_transport_publish(MESH_MESSAGE_REGISTRATION_STATUS,"{}",2);
                assert(e==ESP_OK || e==ESP_ERR_NO_MEM);
            }
            if((t-1000)%30000==0) {delivery_offer(delivery_seq,t/1000,false);delivery_seq+=2;}
            tx_drain();recovery_tick(&tx_controller,&tx_context);
        }
        assert(state.status.tx_dropped>600 && state.delivery.progress==21 && !reselect_calls);
        assert(local_status->capacity+local_data->capacity==4);
        assert(root_status->capacity+root_data->capacity==8);
    } else if(!strcmp(mode,"review_fair_local") || !strcmp(mode,"review_fair_root")) {
        bool root=!strcmp(mode,"review_fair_root");
        QueueHandle_t queues[]={root ? root_registration : local_registration,
                               root ? root_status : local_status,root ? root_data : local_data};
        mesh_message_kind_t kinds[]={MESH_MESSAGE_REGISTRATION_STATUS,MESH_MESSAGE_NODE_STATUS,MESH_MESSAGE_BLE_RAW};
        for(unsigned i=0;i<3;++i) {publication_t r={0};r.message.kind=kinds[i];assert(xQueueSend(queues[i],&r,0));}
        unsigned turn=0,counts[3]={0};
        for(unsigned i=0;i<300;++i) {
            publication_t r;assert(next_publication(queues[0],queues[1],queues[2],&turn,&r));
            assert(r.message.kind==kinds[i%3]);++counts[i%3];assert(xQueueSend(queues[i%3],&r,0));
        }
        assert(counts[0]==100 && counts[1]==100 && counts[2]==100);
    } else if(!strcmp(mode,"review_status_latest")) {
        delivery_init(0);for(uint32_t n=1;n<=100;++n) delivery_offer(n,1,false);
        assert(local_status->count==1 && !local_data->count && state.delivery.offered);
        tx_drain();assert(send_calls==1 && state.delivery.last_sent_seq==100 && delivery_ack(100,1));
        delivery_offer(101,1,false);mesh_transport_drop_pending();assert(!local_status->count);
    } else if(!strcmp(mode,"review_json_depth")) {
        assert(CJSON_NESTING_LIMIT==8);
        mesh_wire_message_t m={.kind=MESH_MESSAGE_NODE_STATUS};strcpy(m.node_id,own_id);
        for(unsigned depth=7;depth<=100;depth+=(depth==7 ? 1 : 92)) {
            strcpy(m.payload,"{\"x\":");size_t n=strlen(m.payload);
            for(unsigned i=0;i<depth;++i)m.payload[n++]='[';
            m.payload[n++]='0';for(unsigned i=0;i<depth;++i)m.payload[n++]=']';m.payload[n++]='}';
            m.payload[n]=0;m.length=n;
            cJSON *json=parse_json(&m);assert((json!=NULL)==(depth==7));cJSON_Delete(json);
            if(depth>7) {status_identity_t id;assert(!parse_status_identity(&m,&id));assert(!valid_config(&m));}
        }
        m=delivery_status(1,1);status_identity_t id;assert(parse_status_identity(&m,&id));
    } else if(!strncmp(mode,"review_log_",11)) {
        delivery_init(0);delivery_offer(1,1,true);
        recovery_request_t request=delivery_request(true,false);
        if(!strcmp(mode,"review_log_progress")) log_hook=delivery_sdk_progress;
        else if(!strcmp(mode,"review_log_epoch")) log_hook=reconnect_child;
        else if(!strcmp(mode,"review_log_config")) log_hook=tx_drop_config;
        else if(!strcmp(mode,"review_log_vote")) log_hook=review_log_vote;
        else if(!strcmp(mode,"review_log_receipt")) {request=delivery_request(false,true);log_hook=review_log_ack;}
        else assert(0);
        recover_mesh(&request,NULL);assert(!reselect_calls && !log_hook);
    } else if(!strncmp(mode,"review_origin_",14)) {
        delivery_init(0);delivery_offer(1,1,false);
        publication_t record;assert(xQueueReceive(local_status,&record,0));
        /* Worker has checked this snapshot; config/link changes before entry
         * to send_message must not relabel the old record as new progress. */
        transport_state_t checked=snapshot();assert(record.local_generation==checked.local_generation);
        if(!strcmp(mode,"review_origin_config")) mesh_transport_drop_pending();
        else if(!strcmp(mode,"review_origin_link")) {connected(false);delivery_root_event();}
        else if(!strcmp(mode,"review_origin_role")) connected(true);
        else assert(0);
        assert(send_message(&record.message,NULL,&record)==ESP_ERR_INVALID_STATE);
        assert(!send_calls && !state.tx_progress && !state.delivery.submitted && !state.tx_inflight);
    } else assert(0);
}
