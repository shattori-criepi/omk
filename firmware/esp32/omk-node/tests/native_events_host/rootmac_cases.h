/* Real transport/owner/receiver, with IDF 6.0.1's missing child ROOT_ADDRESS. */
static void rootmac_assert(const uint8_t mac[6]) {
    mesh_transport_status_t status; mesh_transport_get_status(&status);
    assert(!memcmp(status.root_mac,mac,6));
}
static void rootmac_unknown(void) {
    const uint8_t zero[6]={0};rootmac_assert(zero);
}
static void rootmac_reconnect_hook(void) { connected(false); }
static void rootmac_early_ack(void) {
    assert(delivery_ack(1,1));
    rootmac_assert(delivery_root);
}
static void test_rootmac(const char *mode) {
    tx_init(0); /* deliberately no delivery_root_event() */
    if(!strcmp(mode,"rootmac_no_event_healthy")) {
        /* Two hours, normal SDK success and a Root receipt every 30 seconds. */
        delivery_run(1000,7201000,true,true);
        rootmac_assert(delivery_root);
        assert(!reselect_calls && !state.status.recovery_requests && !state.status.recovery_ok);
        assert(!state.status.mesh_tx_errors && !state.status.rx_dropped && !state.status.parent_disconnect_count);
        assert(state.delivery.progress==241 && !state.status.root_change_count);
    } else if(!strcmp(mode,"rootmac_latest_only")) {
        delivery_offer(1,1,true);at_ms(31000);delivery_offer(3,31,true);
        assert(!delivery_ack(3,30));rootmac_unknown();
        assert(delivery_ack(1,1));
        assert(delivery_ack(3,31));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_invalid_unknown")) {
        delivery_offer(1,1,false);
        assert(!delivery_ack(1,1)); /* queued, not submitted */
        tx_drain();mesh_wire_message_t m=delivery_reply(1,1);
        uint8_t zero[6]={0}, multicast[6]={3,0,0,0,0,99};
        assert(!receive_status_receipt(&m,zero) && !receive_status_receipt(&m,multicast));
        assert(!receive_status_receipt(&m,own_mac));
        m.node_id[0]='1';assert(!receive_status_receipt(&m,delivery_root));
        m=delivery_reply(1,1);m.payload[12]='f';assert(!receive_status_receipt(&m,delivery_root));
        assert(!delivery_ack(2,1) && !delivery_ack(1,2));rootmac_unknown();
        at_ms(122000);delivery_offer(3,122,true);
        assert(!delivery_ack(1,1));rootmac_unknown();
        assert(delivery_ack(3,122));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_reconnect_same") || !strcmp(mode,"rootmac_reconnect_new") ||
              !strcmp(mode,"rootmac_rootless") || !strcmp(mode,"rootmac_role") || !strcmp(mode,"rootmac_restart")) {
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        delivery_offer(2,1,true); /* in-flight old episode */
        at_ms(31000);
        if(!strcmp(mode,"rootmac_rootless")) {
            rootless(true);assert(!delivery_ack(2,1));rootless(false);
        } else if(!strcmp(mode,"rootmac_role")) {
            connected(true);assert(!delivery_ack(2,1));connected(false);
        } else if(!strcmp(mode,"rootmac_restart")) {
            event(MESH_EVENT_STOPPED,NULL);
            mesh_wire_message_t m=delivery_reply(2,1);
            assert(!receive_status_receipt(&m,delivery_root));
            event(MESH_EVENT_STARTED,NULL);connected(false);
        } else {
            event(MESH_EVENT_PARENT_DISCONNECTED,NULL);assert(!delivery_ack(2,1));
            connected(false);
            if(!strcmp(mode,"rootmac_reconnect_new")) ++delivery_root[5];
        }
        rootmac_unknown();assert(!delivery_ack(2,1));
        delivery_offer(3,31,true);assert(!delivery_ack(2,1));assert(delivery_ack(3,31));
        rootmac_assert(delivery_root);
        assert(state.status.root_change_count==(!strcmp(mode,"rootmac_reconnect_new") ? 1 : 0));
        delivery_seq=5;delivery_run(32000,601000,true,true);assert(!reselect_calls);
    } else if(!strcmp(mode,"rootmac_silent_change")) {
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        uint8_t old[6];memcpy(old,delivery_root,6);
        at_ms(31000);delivery_offer(3,31,true);
        uint32_t epoch=state.session.epoch;++delivery_root[5];
        assert(!delivery_ack(3,31));delivery_root_event();
        at_ms(32000);delivery_offer(4,32,true);
        assert(delivery_ack(4,32));rootmac_assert(delivery_root);
        assert(state.session.epoch==epoch+1 && state.status.root_change_count==1);
        mesh_wire_message_t m=delivery_reply(3,31);
        assert(!receive_status_receipt(&m,old)); /* duplicate cannot switch back */
        at_ms(61000);delivery_offer(5,61,true);m=delivery_reply(1,1);
        assert(!receive_status_receipt(&m,old));rootmac_assert(delivery_root);
        assert(delivery_ack(5,61));delivery_seq=7;
        delivery_run(62000,601000,true,true);assert(!reselect_calls);
    } else if(!strcmp(mode,"rootmac_event_before_parent")) {
        event(MESH_EVENT_PARENT_DISCONNECTED,NULL);delivery_root_event();
        connected(false);rootmac_unknown(); /* no reuse of disconnected history */
        delivery_offer(1,1,true);assert(delivery_ack(1,1));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_event_after_receipt")) {
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        uint32_t epoch=state.session.epoch;delivery_root_event();delivery_root_event();
        assert(state.session.epoch==epoch && state.delivery.confirmed && !state.status.root_change_count);
        at_ms(31000);delivery_offer(3,31,true);
        uint8_t other[6]={2,0,0,0,0,98};mesh_wire_message_t m=delivery_reply(1,1);
        assert(!receive_status_receipt(&m,other)); /* old proof cannot replace an SDK address */
        assert(delivery_ack(3,31));
        ++delivery_root[5];delivery_root_event();assert(state.session.epoch==epoch+1);
        assert(!delivery_ack(3,31));at_ms(61000);delivery_offer(5,61,true);
        assert(delivery_ack(5,61));rootmac_assert(delivery_root);
        /* A subsequent silent change must still converge after an SDK event. */
        at_ms(91000);delivery_offer(7,91,true);++delivery_root[5];
        assert(!delivery_ack(7,91));delivery_root_event();
        at_ms(92000);delivery_offer(8,92,true);assert(delivery_ack(8,92));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_vote")) {
        event(MESH_EVENT_VOTE_STARTED,NULL);delivery_offer(1,1,true);
        assert(!delivery_ack(1,1));rootmac_unknown();
        event(MESH_EVENT_VOTE_STOPPED,NULL);at_ms(31000);delivery_offer(3,31,true);
        assert(!delivery_ack(1,1) && delivery_ack(3,31));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_config_generation")) {
        delivery_offer(1,1,true);mesh_transport_drop_pending();
        assert(!delivery_ack(1,1));at_ms(31000);delivery_offer(3,31,true);
        assert(!delivery_ack(1,1));assert(delivery_ack(3,31));
    } else if(!strcmp(mode,"rootmac_race")) {
        delivery_offer(1,1,true);role_query_hook=rootmac_reconnect_hook;
        assert(!delivery_ack(1,1));rootmac_unknown();
        at_ms(31000);delivery_offer(3,31,true);assert(delivery_ack(3,31));
    } else if(!strcmp(mode,"rootmac_before_send_return")) {
        send_hook=rootmac_early_ack;delivery_offer(1,1,true);
        rootmac_assert(delivery_root);assert(state.tx_progress==1 && state.delivery.progress==1);
    } else if(!strcmp(mode,"rootmac_wrap")) {
        delivery_offer(UINT32_MAX,1,true);assert(delivery_ack(UINT32_MAX,1));
        at_ms(31000);delivery_offer(0,31,true);
        assert(delivery_ack(0,31));rootmac_assert(delivery_root);
    } else if(!strcmp(mode,"rootmac_config_source")) {
        mesh_wire_message_t m={.kind=MESH_MESSAGE_CONFIG,.length=2,.payload="{}"};strcpy(m.node_id,own_id);
        delivery_receive(&m,delivery_root);assert(!apply_configs->count);rootmac_unknown();
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        uint8_t other[6]={2,0,0,0,0,98};delivery_receive(&m,other);assert(!apply_configs->count);
        delivery_receive(&m,delivery_root);assert(apply_configs->count==1);
    } else if(!strcmp(mode,"rootmac_real_loss")) {
        delivery_run(1000,181000,true,false);
        assert(reselect_calls==1 && tx_controller.child_causes==REC_CHILD_DELIVERY_STALL);
    } else assert(0);
}
