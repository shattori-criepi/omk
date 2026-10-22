#include <stdio.h>
#include <stdlib.h>
#include "network_diagnostics.c"
#include "network_diagnostics_store.c"

static void *parallel(void *arg) {
    unsigned op=(uintptr_t)arg;
    for(unsigned i=0;i<10000;++i) {
        uint32_t token=network_diag_begin(op); assert(token);
        network_diag_wait(token); network_diag_locked(token);
        assert(network_diag_end(token,-7,true)==-7);
    }
    return NULL;
}
static void reboot(int reason) {
    memset(&retained,0,sizeof(retained));memset(&saved,0,sizeof(saved));
    saved_reasons=saved_critical=saves=sequence=usb_dropped=0;save_error=0;fault_slot=0;
    storage_disabled=initialized=false;next_report_view=live_report_sequence=0;
    last_save_us=next_report_us=0;flash_started_us=flash_finished_us=0;fake_us=0;reset_kind=reason;
    network_diag_start();
}
static void stall(unsigned op) {
    (void)network_diag_begin(op);fake_us+=11000000;observe_once();
}
int main(int argc,char **argv) {
    assert(argc==2);network_diag_start();
    if(!strcmp(argv[1],"timing")) {
        fake_us=1000000;uint32_t t=network_diag_begin(ND_ENQUEUE);
        fake_us=2000000;network_diag_wait(t);
        fake_us=77000000;network_diag_locked(t);
        fake_us=78000000;network_diag_end(t,-1,true);
        nd_snapshot_t s;network_diag_snapshot(&s);
        nd_stats_t *a=&s.stats[ND_ENQUEUE];
        assert(a->calls==1 && a->completed==1 && a->errors==1);
        assert(a->last_ms==77000 && a->last_wait_ms==75000 && a->last_locked_ms==1000);
        assert(a->last_end_us==78000000 && a->last_rc==-1);
        assert(network_diag_reasons(&s)&(1U<<ND_ENQUEUE));
    } else if(!strcmp(argv[1],"parallel")) {
        pthread_t workers[4];
        for(unsigned i=0;i<4;++i) pthread_create(&workers[i],NULL,parallel,(void *)(uintptr_t)i);
        for(unsigned i=0;i<1000;++i) { nd_snapshot_t s;network_diag_snapshot(&s); }
        for(unsigned i=0;i<4;++i) pthread_join(workers[i],NULL);
        nd_snapshot_t s;network_diag_snapshot(&s);
        for(unsigned i=0;i<4;++i) assert(s.stats[i].calls==10000 && s.stats[i].completed==10000 && s.stats[i].errors==10000);
        assert(!s.dropped);
    } else if(!strcmp(argv[1],"capacity")) {
        uint32_t tokens[ND_ACTIVE_SLOTS];
        for(unsigned i=0;i<ND_ACTIVE_SLOTS;++i) tokens[i]=network_diag_begin(ND_ENQUEUE);
        assert(!network_diag_begin(ND_OUTBOX));
        nd_snapshot_t s;network_diag_snapshot(&s);assert(s.dropped==1);
        for(unsigned i=0;i<ND_ACTIVE_SLOTS;++i) network_diag_end(tokens[i],0,false);
        assert(network_diag_begin(ND_OUTBOX));
    } else if(!strcmp(argv[1],"token_wrap")) {
        uint32_t a=network_diag_begin(ND_ENQUEUE);next_token=UINT32_MAX;
        uint32_t b=network_diag_begin(ND_OUTBOX);assert(a!=b && b);
        network_diag_end(b,12,false);network_diag_end(a,-1,true);
        assert(state.stats[ND_ENQUEUE].errors==1 && state.stats[ND_OUTBOX].completed==1);
    } else if(!strcmp(argv[1],"error_codes")) {
        for(int i=1;i<=10;++i) {
            uint32_t t=network_diag_begin(ND_MESH_UP);network_diag_end(t,i,true);
        }
        uint32_t t=network_diag_begin(ND_MESH_DOWN);network_diag_end(t,1,true);
        assert(state.mesh_errors[0].rc==1 && state.mesh_errors[0].count==2);
        assert(state.mesh_error_other==2);
    } else if(!strcmp(argv[1],"event_stall")) {
        for(unsigned i=1;i<=4;++i) { fake_us=i*30000000ULL;network_diag_tick(fake_us);network_diag_post(fake_us,0); }
        observe_once();assert(retained.reasons&(1U<<9));assert(retained.reasons&(1U<<10));
        assert(!(retained.reasons&(1U<<8)) && flash_writes==1);
        for(unsigned i=0;i<100;++i) { fake_us+=1000000;observe_once(); }
        assert(flash_writes==1);
    } else if(!strcmp(argv[1],"persistence")) {
        stall(ND_MESH_UP);assert(saves==1 && flash_writes==1 && valid((checkpoint_t *)disk_bytes));
        unsigned mask=saved_reasons;reboot(ESP_RST_SW);
        assert(saves==1 && saved_reasons==mask && retained.schema==ND_SCHEMA);
        stall(ND_MESH_UP);assert(flash_writes==1);
        stall(ND_OUTBOX);assert(flash_writes==1);
        fake_us+=ND_SAVE_GAP_US;observe_once();assert(flash_writes==2);
        memset(rtc_checkpoints,0xee,sizeof(rtc_checkpoints));reboot(ESP_RST_POWERON);
        assert(saves==2 && (saved_reasons&(1U<<ND_OUTBOX)) && saved.schema==ND_SCHEMA);
    } else if(!strcmp(argv[1],"no_periodic_write")) {
        unsigned reports=0;char out[ND_REPORT_SIZE];
        for(unsigned i=0;i<7200;++i) {
            fake_us+=1000000;observe_once();
            if(network_diag_report(out,sizeof(out))) {
                assert(strstr(out,"\"view\":0,"));
                network_diag_report_result(true);++reports;
            }
        }
        assert(reports==24 && flash_writes==0);
        assert(!retained.schema && !saved.schema);
    } else if(!strcmp(argv[1],"failure")) {
        flash_error=ESP_ERR_NO_MEM;stall(ND_MESH_UP);assert(storage_disabled && saves==0);
        for(unsigned i=0;i<10;++i) { fake_us+=ND_SAVE_GAP_US;observe_once(); }
        assert(flash_writes==1 && save_error==ESP_ERR_NO_MEM);
        assert(valid(&rtc_checkpoints[sequence%2]));
        reboot(ESP_RST_SW);assert(storage_disabled);stall(ND_OUTBOX);assert(flash_writes==1);
    } else if(!strcmp(argv[1],"space")) {
        free_entries=128;stall(ND_MESH_UP);assert(!flash_writes && storage_disabled);
    } else if(!strcmp(argv[1],"corrupt")) {
        stall(ND_WRITE);disk_bytes[20]^=0x55;reboot(ESP_RST_POWERON);
        assert(storage_disabled && save_error==ESP_ERR_INVALID_STATE);
        stall(ND_ENQUEUE);assert(flash_writes==1);
    } else if(!strcmp(argv[1],"rtc_interrupted")) {
        stall(ND_WRITE);fake_us+=1000000;observe_once();
        uint32_t valid_seq=rtc_faults[fault_slot].snapshot.sequence;
        rtc_faults[fault_slot^1].magic=0;
        reboot(ESP_RST_SW);assert(retained.sequence==valid_seq);
    } else if(!strcmp(argv[1],"multiple_restarts")) {
        stall(ND_MESH_UP); /* first NVS record, starts ten-minute gap */
        uint32_t t=network_diag_begin(ND_OUTBOX);fake_us+=31000000;observe_once();
        network_diag_end(t,0,false);
        uint64_t fault_boot=retained.boot_id;
        assert(retained.critical_reasons&(1U<<ND_OUTBOX));assert(saves==1);
        for(unsigned i=0;i<5;++i) {
            reboot(ESP_RST_SW);
            assert(retained.boot_id==fault_boot);
            if(i%2) { fake_us=2000000;observe_once();fake_us+=1000000;observe_once(); }
        }
        fake_us=ND_SAVE_GAP_US+1000000;observe_once();
        assert(saves==2 && saved.boot_id==fault_boot && (saved_critical&(1U<<ND_OUTBOX)));
        reboot(ESP_RST_POWERON);assert(saved.boot_id==fault_boot && saves==2);
    } else if(!strcmp(argv[1],"resolved_pending")) {
        stall(ND_MESH_UP);
        fake_us=120000000;network_diag_tick(fake_us);observe_once();
        assert(retained.critical_reasons&(1U<<9));uint64_t at=retained.sampled_us;
        network_diag_control(false);network_diag_recovery(0,0,0);
        for(unsigned i=0;i<20;++i) {
            fake_us+=30000000;network_diag_tick(fake_us);
            network_diag_control(false);network_diag_recovery(0,0,0);observe_once();
        }
        assert(saved.critical_reasons&(1U<<9));assert(saved.sampled_us==at && saves==2);
    } else if(!strcmp(argv[1],"severity")) {
        uint32_t t=network_diag_begin(ND_MESH_UP);uint64_t begin=state.active[0].started_us;
        fake_us=begin+10000000;observe_once();assert(!saves);
        fake_us=begin+11000000;observe_once();assert(saves==1 && !saved_critical);
        fake_us=begin+30000000;observe_once();assert(retained.critical_reasons==1 && saves==1);
        fake_us=begin+31000000;network_diag_end(t,0,false);observe_once();
        fake_us=ND_SAVE_GAP_US+12000000;observe_once();
        assert(saves==2 && saved_critical==1 && saved.stats[0].last_ms==31000);
        for(unsigned i=0;i<20;++i) { fake_us+=ND_SAVE_GAP_US;observe_once(); }
        assert(saves==2);
    } else if(!strcmp(argv[1],"priority")) {
        uint32_t t=network_diag_begin(ND_WRITE);fake_us+=31000000;observe_once();
        network_diag_end(t,0,false);uint64_t fault_boot=retained.boot_id;
        reboot(ESP_RST_SW);t=network_diag_begin(ND_MESH_UP);fake_us+=11000000;network_diag_end(t,0,false);observe_once();
        assert(retained.boot_id==fault_boot && retained.critical_reasons==(1U<<ND_WRITE));
        fake_us+=ND_SAVE_GAP_US;observe_once();assert(saves==1);
    } else if(!strcmp(argv[1],"early_budget")) {
        for(unsigned i=0;i<5;++i) {
            uint32_t t=network_diag_begin(i);fake_us+=11000000;network_diag_end(t,0,false);
            fake_us+=ND_SAVE_GAP_US;observe_once();
        }
        assert(saves==4);
        uint32_t t=network_diag_begin(ND_WRITE);fake_us+=31000000;network_diag_end(t,0,false);
        fake_us+=ND_SAVE_GAP_US;observe_once();assert(saves==5 && saved_critical);
    } else if(!strcmp(argv[1],"minor_after_critical")) {
        uint32_t t=network_diag_begin(ND_WRITE);fake_us+=31000000;network_diag_end(t,0,false);observe_once();
        assert(saves==1);uint32_t original=saved.sequence;
        t=network_diag_begin(ND_MESH_UP);fake_us+=11000000;network_diag_end(t,0,false);
        fake_us+=ND_SAVE_GAP_US;observe_once();assert(saves==1 && saved.sequence==original);
        reboot(ESP_RST_SW);
        t=network_diag_begin(ND_OUTBOX);fake_us+=31000000;network_diag_end(t,0,false);
        fake_us+=ND_SAVE_GAP_US;observe_once();assert(saves==2 && (saved_critical&(1U<<ND_OUTBOX)));
    } else if(!strcmp(argv[1],"report_busy")) {
        stall(ND_ENQUEUE);char out[ND_REPORT_SIZE];uint32_t seq=retained.sequence;
        assert(network_diag_report(out,sizeof(out)));puts(out);network_diag_report_result(false);
        assert(!network_diag_report(out,sizeof(out)));
        fake_us=next_report_us-1;assert(!network_diag_report(out,sizeof(out)));
        fake_us=next_report_us;assert(network_diag_report(out,sizeof(out)));puts(out);
        assert(retained.sequence==seq);network_diag_report_result(true);
        fake_us+=30000000;assert(!network_diag_report(out,sizeof(out)));
        fake_us=next_report_us;assert(network_diag_report(out,sizeof(out)));puts(out);
        network_diag_report_result(false);
        fake_us=next_report_us;assert(network_diag_report(out,sizeof(out)));puts(out);
        assert(retained.sequence==seq && flash_writes==1);
    } else if(!strcmp(argv[1],"budget")) {
        saves=ND_SAVE_LIMIT;stall(ND_ENQUEUE);assert(!flash_writes);
    } else if(!strcmp(argv[1],"budget_restore")) {
        stall(ND_ENQUEUE);checkpoint_t c;
        c.snapshot=saved;c.snapshot.saves=16;seal(&c,&c.snapshot);
        memcpy(disk_bytes,&c,sizeof(c));disk_size=sizeof(c);
        reboot(ESP_RST_POWERON);assert(saves==16);
        stall(ND_OUTBOX);fake_us+=ND_SAVE_GAP_US;observe_once();assert(flash_writes==1);
    } else if(!strcmp(argv[1],"fault_slot_parity")) {
        stall(ND_ENQUEUE);uint32_t seq=retained.sequence;
        /* Normal-only observations can change parity between fault writes. */
        ++sequence;fake_us+=1000000;observe_once();
        assert(valid(&rtc_faults[0]) && valid(&rtc_faults[1]));
        rtc_faults[fault_slot^1].magic=0;
        reboot(ESP_RST_SW);assert(retained.sequence==seq);
    } else if(!strcmp(argv[1],"usb")) {
        stall(ND_ENQUEUE);
        for(unsigned view=0;view<3;++view) for(unsigned page=0;page<=26;++page) {
            char line[40];snprintf(line,sizeof(line),"OMKDIAG %u %u",view,page);
            usb_output[0]=0;assert(network_diag_usb(line,0x020000000001));
            assert(usb_output[0]=='{');fputs(usb_output,stdout);
        }
        usb_full=true;network_diag_usb("OMKDIAG 0 0",1);assert(usb_dropped==1);
        assert(network_diag_usb("OMKDIAG 0 9999999999",1));
        assert(!network_diag_usb("{\"command\":\"identify\"}",1));
    } else if(!strcmp(argv[1],"report")) {
        char out[ND_REPORT_SIZE];stall(ND_ENQUEUE);
        nd_snapshot_t held=retained, disk=saved;
        for(unsigned i=0;i<6;++i) {
            assert(network_diag_report(out,sizeof(out)));puts(out);
            network_diag_report_result(true);
            assert(!network_diag_report(out,sizeof(out)));
            fake_us=next_report_us-1;assert(!network_diag_report(out,sizeof(out)));
            fake_us=next_report_us;
        }
        assert(!memcmp(&held,&retained,sizeof(held)) && !memcmp(&disk,&saved,sizeof(disk)));
        assert(flash_writes==1);
    } else if(!strcmp(argv[1],"report_live")) {
        stall(ND_WRITE);reboot(ESP_RST_SW);
        nd_snapshot_t held=retained, disk=saved;
        uint32_t observer_seq=sequence;
        for(unsigned i=0;i<1409;++i) {
            uint32_t t=network_diag_begin(ND_MESH_UP);network_diag_end(t,0x4007,true);
        }
        uint32_t t=network_diag_begin(ND_MESH_UP);fake_us+=1234000;
        network_diag_end(t,0x400a,true);
        network_diag_tick(fake_us);network_diag_post(fake_us,0);
        network_diag_control(true);network_diag_control(false);network_diag_recovery(0x175,1,0);
        observe_once();assert(sequence==observer_seq+1);
        t=network_diag_begin(ND_WRITE);
        char out[ND_REPORT_SIZE];
        for(unsigned i=0;i<4;++i) {
            assert(network_diag_report(out,sizeof(out)));puts(out);
            network_diag_report_result(true);
            fake_us=next_report_us;
        }
        network_diag_end(t,0,false);
        assert(!memcmp(&held,&retained,sizeof(held)) && !memcmp(&disk,&saved,sizeof(disk)));
        assert(sequence==observer_seq+1 && flash_writes==1);
        /* Both soft and cold restart reset the live sender, not saved history. */
        for(unsigned i=0;i<2;++i) {
            reboot(i ? ESP_RST_POWERON : ESP_RST_SW);
            for(unsigned j=0;j<3;++j) {
                assert(network_diag_report(out,sizeof(out)));puts(out);
                network_diag_report_result(true);fake_us=next_report_us;
            }
            assert(!memcmp(&disk,&saved,sizeof(disk)) && flash_writes==1);
        }
    } else if(!strcmp(argv[1],"report_missing_saved")) {
        flash_error=ESP_ERR_NO_MEM;stall(ND_MESH_UP);
        assert(retained.schema && !saved.schema);
        nd_snapshot_t held=retained;char out[ND_REPORT_SIZE];
        for(unsigned i=0;i<6;++i) {
            assert(network_diag_report(out,sizeof(out)));puts(out);
            network_diag_report_result(true);fake_us=next_report_us;
        }
        assert(!memcmp(&held,&retained,sizeof(held)) && flash_writes==1);
    } else if(!strcmp(argv[1],"report_size")) {
        char out[ND_REPORT_SIZE];
        nd_snapshot_t s;memset(&s,0xff,sizeof(s));s.schema=ND_SCHEMA;
        s.save_error=INT32_MIN;
        for(unsigned i=0;i<ND_OPERATIONS;++i) s.stats[i].last_rc=INT32_MIN;
        for(unsigned i=0;i<8;++i) s.mesh_errors[i].rc=INT32_MIN;
        for(unsigned i=0;i<3;++i) { assert(format_report(&s,i,out,sizeof(out)));puts(out); }
        /* A formatting failure must not produce a sendable partial JSON or
         * a tight retry loop, and must leave saved records alone. */
        assert(!network_diag_report(out,8));assert(!network_diag_report(out,sizeof(out)));
        assert(next_report_view==0 && !flash_writes && !retained.schema && !saved.schema);
        fake_us=next_report_us;assert(network_diag_report(out,sizeof(out)));
    } else assert(0);
    return 0;
}
