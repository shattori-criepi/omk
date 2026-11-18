/* AB4 review regressions: real transport and recovery, SDK boundaries mocked. */
static void ab4_parent_race(void) { connected(false); }
static void test_ab4(const char *mode) {
    tx_init(0);
    if (!strncmp(mode,"ab4_delay_",10) || !strcmp(mode,"ab4_burst")) {
        unsigned lag=!strcmp(mode,"ab4_burst") ? 120 : (unsigned)atoi(mode+10);
        bool burst=!strcmp(mode,"ab4_burst");
        for(uint64_t t=1000;t<=7201000;t+=1000) {
            at_ms(t);
            if((t-1000)%30000==0) {
                delivery_offer((uint32_t)(t/1000)*100,t/1000,true);
                if(burst) for(unsigned i=1;i<80;++i)
                    delivery_offer((uint32_t)(t/1000)*100+i,t/1000,true);
            }
            if(t>=1000+lag*1000 && (t-1000-lag*1000)%30000==0) {
                uint64_t sent=t-lag*1000;
                assert(delivery_ack((uint32_t)(sent/1000)*100,sent/1000));
            }
            recovery_tick(&tx_controller,&tx_context);
            assert(!reselect_calls);
        }
        rootmac_assert(delivery_root);assert(state.delivery.progress>230);
    } else if(!strcmp(mode,"ab4_vote")) {
        delivery_offer(1,1,true);event(MESH_EVENT_VOTE_STARTED,NULL);
        at_ms(2000);delivery_offer(2,2,true);
        event(MESH_EVENT_VOTE_STOPPED,NULL);
        assert(!delivery_ack(1,1) && !delivery_ack(2,2));rootmac_unknown();
        at_ms(3000);delivery_offer(3,3,true);
        event(MESH_EVENT_VOTE_STOPPED,NULL); /* duplicate must not revoke new proof */
        assert(delivery_ack(3,3));
    } else if(!strncmp(mode,"ab4_sdk_",8)) {
        delivery_offer(1,1,true);
        if(!strcmp(mode,"ab4_sdk_layer")) sdk_layer_override=-1;
        else if(!strcmp(mode,"ab4_sdk_layer_mismatch")) sdk_layer_override=2;
        else if(!strcmp(mode,"ab4_sdk_parent")) {sdk_parent_override[0]=2;sdk_parent_override[5]=21;}
        else if(!strcmp(mode,"ab4_sdk_error")) parent_query_error=ESP_FAIL;
        else if(!strcmp(mode,"ab4_sdk_root")) sdk_root=true;
        else if(!strcmp(mode,"ab4_sdk_race")) parent_query_hook=ab4_parent_race;
        else assert(0);
        assert(!delivery_ack(1,1));rootmac_unknown();
        sdk_layer_override=-2;memset(sdk_parent_override,0,6);parent_query_error=0;sdk_root=false;
        at_ms(31000);delivery_offer(3,31,true);assert(delivery_ack(3,31));
    } else if(!strcmp(mode,"ab4_source")) {
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        at_ms(31000);delivery_offer(3,31,true);
        uint8_t old[6];memcpy(old,delivery_root,6);++delivery_root[5];
        assert(!delivery_ack(3,31));rootmac_assert(old);
        delivery_root_event();assert(!delivery_ack(3,31));
        at_ms(61000);delivery_offer(5,61,true);assert(delivery_ack(5,61));
    } else if(!strcmp(mode,"ab4_order")) {
        delivery_offer(1,1,true);at_ms(31000);delivery_offer(3,31,true);
        assert(delivery_ack(3,31));assert(!delivery_ack(1,1) && !delivery_ack(3,31));
        at_ms(61000);delivery_offer(5,61,false); /* queued is not submitted */
        connected(false);at_ms(62000);delivery_offer(7,62,false);
        assert(!delivery_ack(7,62));tx_drain();assert(delivery_ack(7,62));
    } else if(!strcmp(mode,"ab4_expiry")) {
        /* AB5 allows up to 999 ms of uptime_s quantization, then expires. */
        delivery_offer(1,1,true);at_ms(122000);delivery_offer(3,122,true);
        assert(!delivery_ack(1,1));assert(delivery_ack(3,122));
        delivery_seq=5;delivery_run(123000,302000,true,false);
        delivery_run(303000,303000,true,false);assert(reselect_calls==1);
    } else if(!strcmp(mode,"ab4_unsent")) {
        delivery_offer(1,1,true);at_ms(31000);delivery_offer(5,31,true);
        assert(!delivery_ack(3,16));rootmac_unknown();assert(delivery_ack(1,1));
    } else assert(0);
}
