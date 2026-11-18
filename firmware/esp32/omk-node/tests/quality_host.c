#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "mesh_quality.h"
#include "native_recovery.h"
/* Replay batches are an explicit placement model, never fabricated RF truth. */
int main(int argc,char **argv) {
    mesh_quality_t q; mq_reset(&q,0);
    if(argc>1 && !strcmp(argv[1],"replay")) {
        unsigned long long ms; unsigned ok,err,timeout,nomem,eligible;
        while(scanf("%llu %u %u %u %u %u",&ms,&ok,&err,&timeout,&nomem,&eligible)==6) {
            for(unsigned i=0;i<ok;i++) mq_sdk(&q,ms,0);
            for(unsigned i=0;i<err;i++) mq_sdk(&q,ms,i<timeout?0x400a:i<timeout+nomem?0x4007:1);
            mesh_quality_view_t v=mq_evaluate(&q,ms,eligible);
            printf("%llu %u %u %u %u %u\n",ms,v.bad,v.reasons,v.attempts,v.ok,v.healthy);
        } return 0;
    }
    assert(argc==2); const char *mode=argv[1];
    if(!strcmp(mode,"intermittent") || !strcmp(mode,"resource") || !strcmp(mode,"healthy") || !strcmp(mode,"burst") || !strcmp(mode,"recovered_burst")) {
        bool detected=false;
        native_recovery_t old_policy,new_policy;
        native_recovery_init(&old_policy,0);native_recovery_init(&new_policy,0);
        native_recovery_input_t input={.started=true,.parent=true,.tx_demand=true};
        unsigned repairs=0;
        for(uint64_t t=1000;t<=900000;t+=1000) {
            int e=0;
            if(!strcmp(mode,"intermittent") && t%2000) e=1;
            if(!strcmp(mode,"resource") && t%4000==0) e=0x400a;
            if(!strcmp(mode,"burst") && t<16000) e=0x4007;
            if(!strcmp(mode,"recovered_burst") && t>=600000 && t<660000) e=0x400a;
            mq_request(&q,t,t%30000==0,(uint32_t)t,t);
            mq_sdk(&q,t,e); if(t%30000==0) mq_receipt(&q,t,t);
            mesh_quality_view_t v=mq_evaluate(&q,t,true); detected|=v.bad;
            if(!e) ++input.tx_progress;
            input.quality_bad=false;
            assert(native_recovery_select(&old_policy,t,&input)==REC_NONE);
            input.quality_bad=v.bad;
            if(native_recovery_select(&new_policy,t,&input)==REC_CHILD_REPAIR) ++repairs;
            if(t==900000 && !strcmp(mode,"healthy")) assert(v.healthy);
        }
        assert(detected==(!strcmp(mode,"intermittent") || !strcmp(mode,"resource")));
        assert((repairs!=0)==detected && repairs<=3);
    } else if(!strcmp(mode,"receipt_deadline")) {
        for(uint64_t t=0;t<=210000;t+=30000) { mq_request(&q,t,true,(uint32_t)t,t); mq_receipt(&q,t,t+120000); }
        mq_sdk(&q,210000,0);mesh_quality_view_t v=mq_evaluate(&q,210000,true);
        assert(v.statuses==2 && v.receipts==2);
        mq_receipt(&q,0,210000); assert(mq_evaluate(&q,210000,true).receipts==2);
    } else if(!strcmp(mode,"queue_receipt")) {
        for(uint64_t t=1000;t<=360000;t+=1000) {
            mq_request(&q,t,t%30000==0,(uint32_t)t,t);mq_sdk(&q,t,0);
            if(t%2000) mq_drop(&q,t);
            (void)mq_evaluate(&q,t,true);
        }
        mesh_quality_view_t v=mq_evaluate(&q,360000,true);
        assert(v.bad && !(v.reasons&MQ_SDK_BAD) && (v.reasons&MQ_RECEIPT_BAD));
    } else if(!strcmp(mode,"reset_and_stale")) {
        for(uint64_t t=1000;t<=180000;t+=1000) { mq_sdk(&q,t,1); (void)mq_evaluate(&q,t,true); }
        assert(mq_evaluate(&q,180000,true).bad);
        assert(!mq_evaluate(&q,240001,true).bad);
        assert(!mq_evaluate(&q,240001,false).healthy);
        mq_reset(&q,240001);assert(!mq_evaluate(&q,500000,true).bad);
    } else if(!strcmp(mode,"saturation")) {
        mq_bucket_t *b=mq_bucket(&q,180000);b->attempts=UINT32_MAX;mq_sdk(&q,180000,1);
        assert(mq_evaluate(&q,180000,true).saturated && !mq_evaluate(&q,180000,true).bad);
    } else if(!strcmp(mode,"bounded_budget")) {
        native_recovery_t c; native_recovery_init(&c,0);
        native_recovery_input_t in={.started=true,.parent=true,.quality_bad=true};
        unsigned actions=0;
        for(uint64_t t=0;t<3600000;t+=1000) {
            in.tx_progress++; /* a success every tick must not cancel quality */
            native_recovery_action_t a=native_recovery_select(&c,t,&in);
            if(a==REC_CHILD_REPAIR) actions++;
            assert(actions<=3);
        } assert(actions==3);
    } else assert(0);
    puts("ok");
}
