/* AB5: reproduce both AB4 final-review findings on the production paths. */
static void test_ab5(const char *mode) {
    tx_init(0);
    if (!strncmp(mode,"ab5_vote_",9)) {
        delivery_offer(1,1,true);assert(delivery_ack(1,1));
        event(MESH_EVENT_VOTE_STARTED,NULL);rootmac_unknown();
        at_ms(2000);delivery_root_event(); /* delayed old Root address during election */
        rootmac_assert(delivery_root);
        at_ms(300000);delivery_offer(2,300,true);
        assert(!delivery_ack(2,300));
        uint32_t epoch=state.session.epoch, generation=state.delivery_generation;
        uint32_t connection=state.parent_connection;
        at_ms(301000);
        if (!strcmp(mode,"ab5_vote_timeout")) recovery_tick(&tx_controller,&tx_context);
        else { assert(!strcmp(mode,"ab5_vote_stopped"));event(MESH_EVENT_VOTE_STOPPED,NULL); }
        rootmac_unknown();
        assert(!state.voting && !state.delivery.offered && !state.session.gateway_reachable);
        assert(state.session.epoch==epoch && state.parent_connection==connection);
        assert(state.inhibit_until==421000 && !reselect_calls);
        assert(!delivery_ack(2,300));
        ++delivery_root[5]; /* Root changed upstream; direct parent and layer stayed the same. */
        delivery_offer(3,301,true);
        assert(state.delivery_generation==generation+1 && !delivery_ack(2,300));
        sdk_parent_override[0]=2;sdk_parent_override[5]=88;
        assert(!delivery_ack(3,301));rootmac_unknown(); /* SDK guard still applies */
        memset(sdk_parent_override,0,6);
        assert(delivery_ack(3,301));rootmac_assert(delivery_root);
        event(MESH_EVENT_VOTE_STOPPED,NULL); /* duplicate must not revoke new evidence */
        rootmac_assert(delivery_root);
        delivery_seq=5;delivery_run(302000,901000,true,true);assert(!reselect_calls);
    } else if (!strncmp(mode,"ab5_delay_",10)) {
        unsigned lag=(unsigned)atoi(mode+10);
        assert(lag==119500 || lag==120000);
        for (uint64_t t=1800;t<=7201800;t+=100) {
            at_ms(t);
            if ((t-1800)%30000==0) delivery_offer((uint32_t)((t-1800)/30000)*2+1,t/1000,true);
            if (t>=1800+lag && (t-1800-lag)%30000==0) {
                uint64_t sent=t-lag;
                assert(delivery_ack((uint32_t)((sent-1800)/30000)*2+1,sent/1000));
                assert(state.delivery.proven_ms==(sent/1000)*1000);
            }
            recovery_tick(&tx_controller,&tx_context);assert(!reselect_calls);
        }
        rootmac_assert(delivery_root);assert(state.delivery.progress>230);
    } else if (!strncmp(mode,"ab5_boundary_",13)) {
        bool known=strstr(mode,"known") && !strstr(mode,"unknown");
        bool expired=strstr(mode,"expired")!=NULL;
        if (known) delivery_root_event();
        at_ms(1800);delivery_offer(1,1,true);
        at_ms(expired ? 122000 : 121999);delivery_offer(3,now_ms()/1000,true);
        assert(delivery_ack(1,1)==!expired);
        if (!expired) {
            assert(state.delivery.proven_ms==1000);
            assert(!delivery_ack(1,1)); /* quantization tolerance never permits duplicates */
        } else if (!known) rootmac_unknown();
        assert(delivery_ack(3,now_ms()/1000));rootmac_assert(delivery_root);
    } else if (!strcmp(mode,"ab5_fractional_progress")) {
        for (uint64_t t=1800;t<=181800;t+=1000) {
            at_ms(t);
            if ((t-1800)%30000==0) delivery_offer((uint32_t)((t-1800)/30000)*2+1,t/1000,true);
            if (t==121800) { /* exactly 120 s after generation, fractional uptime */
                assert(delivery_ack(1,1) && state.delivery.proven_ms==1000);
            }
            recovery_tick(&tx_controller,&tx_context);
            assert(reselect_calls==(unsigned)(t==181800));
        }
        assert(tx_controller.child_causes==REC_CHILD_DELIVERY_STALL);
    } else assert(0);
}
