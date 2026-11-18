#include "ble_scan_metrics.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static ab_topology_t topology(void) {
    ab_topology_t t={.started=true,.parent=true,.link_epoch=1};t.root_mac[0]=2;t.root_mac[5]=9;return t;
}
static void settle(ab_scan_t *s,uint64_t now) {
    for(unsigned i=0;i<4;i++) {enum ab_op op=ab_scan_tick(s,now);if(!op)return;ab_scan_complete(s,op,true,now);}
    assert(!ab_scan_tick(s,now));
}
static ab_plan_t plan(void) {
    ab_plan_t p={.accept_before_ms=2000,.end_ms=50000,.nonce=123,.epoch=1,.count=3};
    p.segments[0]=(ab_segment_t){3000,20000,1};p.segments[1]=(ab_segment_t){20000,40000,2};p.segments[2]=(ab_segment_t){40000,50000,3};return p;
}
static uint64_t get(const uint8_t *p,unsigned n) {uint64_t v=0;for(unsigned i=0;i<n;i++)v|=(uint64_t)p[i]<<(8*i);return v;}
int main(int argc,char **argv) {
    assert(argc==2);unsigned c=(unsigned)atoi(argv[1]);ab_scan_t s;ab_scan_init(&s);
    ab_topology_t t=topology();ab_scan_topology(&s,&t,1000);ab_scan_ready(&s);settle(&s,1000);
    ab_plan_t p=plan();ab_metrics_t m;ab_metrics_init(&m);ab_metrics_context(&m,true,false,1000);
    if(c<20) {
        assert(ab_scan_accept(&s,&p,1000)==AB_ACCEPTED);settle(&s,3000);assert(ab_scan_effective(&s)==1);
        if(c==0) {settle(&s,20000);assert(ab_scan_effective(&s)==2);settle(&s,40000);assert(ab_scan_effective(&s)==3);settle(&s,50000);assert(ab_scan_effective(&s)==0);assert(s.result==AB_APPLIED || s.result==AB_EXPIRED);}
        if(c==1) {assert(ab_scan_accept(&s,&p,45000)==AB_DUPLICATE);assert(s.plan.end_ms==50000);settle(&s,50000);assert(ab_scan_accept(&s,&p,50001)==AB_DUPLICATE);settle(&s,50002);assert(s.desired==0 && s.plan_state==AB_PLAN_FINISHED);}
        if(c==2) {p.end_ms++;assert(ab_scan_accept(&s,&p,4000)==AB_CONFLICT);assert(s.plan.end_ms==50000);}
        if(c==3) {t.parent=false;memset(t.root_mac,0,6);t.link_epoch++;ab_scan_topology(&s,&t,5000);settle(&s,5000);assert(s.plan_state==AB_PLAN_SUSPENDED && ab_scan_effective(&s)==0);t.parent=true;ab_scan_topology(&s,&t,6000);settle(&s,15000);assert(s.plan_state==AB_PLAN_SUSPENDED);memcpy(t.root_mac,topology().root_mac,6);ab_scan_topology(&s,&t,16000);settle(&s,20999);assert(ab_scan_effective(&s)==0);settle(&s,21000);assert(ab_scan_effective(&s)==2);assert(s.plan.end_ms==50000);}
        if(c==4) {t.root_mac[5]=8;t.root_changes++;ab_scan_topology(&s,&t,4000);t.root_mac[5]=9;t.root_changes++;ab_scan_topology(&s,&t,4001);settle(&s,4001);assert(s.plan_state==AB_PLAN_FINISHED && ab_scan_effective(&s)==0);}
        if(c==5) {ab_scan_role(&s,true);settle(&s,4000);assert(!s.running && s.plan_state==AB_PLAN_FINISHED);ab_scan_role(&s,false);settle(&s,4001);assert(ab_scan_effective(&s)==0);}
        if(c==6) {t.rootless=true;ab_scan_topology(&s,&t,4000);settle(&s,4000);t.rootless=false;ab_scan_topology(&s,&t,46000);settle(&s,51000);assert(s.plan_state==AB_PLAN_FINISHED && ab_scan_effective(&s)==0);}
        if(c==7) {for(unsigned i=0;i<20;i++){t.parent=false;ab_scan_topology(&s,&t,4000+i*100);settle(&s,4000+i*100);t.parent=true;ab_scan_topology(&s,&t,4050+i*100);settle(&s,4050+i*100);}assert(ab_scan_effective(&s)==0 && s.plan.end_ms==50000);}
        if(c==8) {p.epoch=2;p.cancel=true;p.count=0;p.accept_before_ms=5000;p.end_ms=0;assert(ab_scan_accept(&s,&p,4000)==AB_CANCELLED);settle(&s,4000);assert(ab_scan_effective(&s)==0);assert(ab_scan_accept(&s,&p,6000)==AB_DUPLICATE);}
        if(c==9) {t.started=false;ab_scan_topology(&s,&t,4000);t.started=true;ab_scan_topology(&s,&t,5000);settle(&s,10000);assert(s.plan_state==AB_PLAN_FINISHED);}
        if(c==10) {t.voting=true;ab_scan_topology(&s,&t,4000);settle(&s,4000);assert(s.plan_state==AB_PLAN_SUSPENDED);}
        if(c==11) {enum ab_op op=ab_scan_tick(&s,20000);assert(op==AB_OP_STOP);for(unsigned i=0;i<100;i++)assert(ab_scan_tick(&s,22000+i*1000)==AB_OP_NONE);assert(s.timed_out && s.operation==AB_OP_STOP && s.desired==0);ab_scan_complete(&s,op,true,130000);settle(&s,130000);assert(ab_scan_effective(&s)==0);}
        if(c==12) {enum ab_op op=ab_scan_tick(&s,20000);ab_scan_complete(&s,op,false,20001);for(unsigned i=0;i<100;i++)assert(ab_scan_tick(&s,22000+i)==AB_OP_NONE);assert(s.failed);}
        if(c==13) {ab_scan_complete(&s,AB_OP_STOP,true,4000);assert(s.running && ab_scan_effective(&s)==1);}
        if(c==14) {assert(ab_scan_tick(&s,50000)==AB_OP_STOP);assert(s.desired==0 && s.plan_state==AB_PLAN_FINISHED);}
        if(c==15) {t.link_epoch++;ab_scan_topology(&s,&t,4000);assert(s.plan_state==AB_PLAN_ACTIVE);}
        if(c==16) {t.root_changes+=2;ab_scan_topology(&s,&t,4000);settle(&s,4000);assert(s.plan_state==AB_PLAN_FINISHED);}
        if(c==17) {p.epoch=2;p.accept_before_ms=6000;assert(ab_scan_accept(&s,&p,5000)==AB_BUSY);assert(s.max_epoch==1);}
        if(c==18) {settle(&s,50001);p.epoch=0;assert(ab_scan_accept(&s,&p,50002)==AB_OLD_EPOCH);}
        if(c==19) {ab_scan_init(&s);assert(s.max_epoch==0 && s.plan_state==AB_PLAN_NONE);}
    } else if(c<30) {
        if(c==20)p.count=5;
        if(c==21)p.accept_before_ms=121001;
        if(c==22)p.segments[0].start_ms=p.accept_before_ms;
        if(c==23)p.segments[1].start_ms=p.segments[0].end_ms-1;
        if(c==24)p.segments[1].preset=4;
        if(c==25)p.end_ms++;
        if(c==26)p.end_ms=36001001;
        if(c==27)p.segments[0].end_ms=p.segments[0].start_ms;
        if(c==28)p.accept_before_ms=999;
        if(c==29)p.count=0;
        assert(ab_scan_accept(&s,&p,1000)==AB_BAD_TIME);
    } else if(c<50) {
        ab_metrics_demand(&m,1000);
        if(c==30) {for(unsigned i=0;i<4;i++){s.configured=i;ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1001000);}assert(m.bank[0][0]==1 && m.bank[3][12]==1);}
        if(c==31) {uint64_t d[]={19999,20000,199999,200000,1999999,2000000,4999999,5000000};for(unsigned i=0;i<8;i++){ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1000000+d[i]);}assert(m.bank[0][0]==1 && m.bank[0][1]==2 && m.bank[0][2]==2 && m.bank[0][3]==2 && m.bank[0][4]==1);}
        if(c==32) {int e[]={0x400a,0x4007,0x400d,0x4017,0x400b,0x400f,-1};for(unsigned i=0;i<7;i++){ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,e[i],1000001);}for(unsigned i=5;i<12;i++)assert(m.bank[0][i]==1);}
        if(c==33) {ab_metrics_begin(&m,&s,1,true,1000000);s.configured=1;s.change++;s.configured=0;s.change++;ab_metrics_end(&m,&s,0,1000001);assert(m.mixed[0]==1 && !m.bank[0][0]);}
        if(c==34) {ab_metrics_begin(&m,&s,1,true,1000000);ab_scan_role(&s,true);ab_metrics_end(&m,&s,0,1000001);assert(m.mixed[0]==1);}
        if(c==35) {ab_metrics_begin(&m,&s,8,true,1000000);ab_metrics_end(&m,&s,0,1000001);ab_metrics_begin(&m,&s,8,true,1000002);ab_metrics_end(&m,&s,-1,1000003);assert(m.self_ok==1 && m.self_error==1 && !m.bank[0][0] && !m.last_ok);}
        if(c==36) {for(unsigned i=1;i<8;i++){ab_metrics_begin(&m,&s,i,false,1000000);ab_metrics_end(&m,&s,0,1000001);}assert(!m.bank[0][0] && !m.invalid);}
        if(c==37) {ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1001000);s.configured=1;s.change++;ab_metrics_begin(&m,&s,1,true,2000000);ab_metrics_end(&m,&s,0x400a,2001000);s.configured=0;s.change++;ab_metrics_context(&m,false,false,3000);ab_metrics_context(&m,true,false,4000);ab_metrics_begin(&m,&s,1,true,4000000);ab_metrics_end(&m,&s,0,4001000);assert(m.bank[0][0]==2 && m.bank[1][5]==1);}
        if(c==38 || c==39) {for(unsigned i=1;i<=200;i++){ab_metrics_demand(&m,i*1000);ab_metrics_begin(&m,&s,1,true,i*1000000ULL);ab_metrics_end(&m,&s,0x400a,i*1000000ULL+1000);}assert(m.episodes==1 && m.mode==1 && m.bank[0][5]==200 && m.bank[0][13]==180);if(c==39){for(unsigned i=201;i<=503;i++){ab_metrics_demand(&m,i*1000);ab_metrics_begin(&m,&s,1,true,i*1000000ULL);ab_metrics_end(&m,&s,0,i*1000000ULL+1000);}assert(!m.mode && m.bank[0][5]==200 && m.episodes==1);}}
        if(c==40) {m.bank[2][6]=0xffffffff;m.self_ok=65536;m.episodes=65536;}
        if(c==41) {s.running=false;ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1000001);assert(m.invalid==1);}
        if(c==42) {ab_metrics_begin(&m,&s,1,true,1000000);t.parent=false;ab_scan_topology(&s,&t,1001);ab_metrics_end(&m,&s,0,1001000);assert(m.mixed[0]==1 && !m.last_ok);}
        if(c==43) {ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1001000);ab_metrics_init(&m);assert(!m.bank[0][0]);}
        if(c==44) {ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_demand(&m,181000);ab_metrics_end(&m,&s,0x400a,181000000);assert(m.episodes==1 && !m.bank[0][12]);}
        if(c==45) {ab_metrics_demand(&m,181000);ab_metrics_tick(&m,181000);ab_metrics_context(&m,false,true,182000);assert(m.censored && !m.mode);}
        if(c==46) {ab_metrics_demand(&m,181000);ab_metrics_tick(&m,181000);ab_metrics_begin(&m,&s,8,true,182000000);ab_metrics_end(&m,&s,0,182000001);assert(m.mode==1);}
        if(c==47) {m.bank[1][0]=0xffffff;ab_metrics_begin(&m,&s,1,true,1000000);ab_metrics_end(&m,&s,0,1000001);}
        if(c==48) {m.mixed[3]=0xffffffff;m.invalid=1;}
        if(c==49) {m.self_drop=0xffffffff;ab_metrics_drop(&m);assert(m.self_drop==0xffffffff);}
        ab_report_aux_t aux={.now_ms=600000,.nonce=0x0123456789abcdefULL,.heap_free=12345,.heap_min=10000,.nonce_valid=true};
        uint8_t data[256];ab_report_pack(&s,&m,&aux,data);assert(get(data+240,4)==12345 && get(data+28,8)==aux.nonce);
        if(c==40){assert(get(data+136+6*3,3)==0xffffff && get(data+234,2)==65535 && get(data+220,2)==65535);assert((get(data+44,4)&((1<<2)|AB_FLAG_SELF_SAT|AB_FLAG_EPISODE_SAT))==((1<<2)|AB_FLAG_SELF_SAT|AB_FLAG_EPISODE_SAT));}
        char json[481];size_t n=ab_report_json(data,"020000000001","0123456789abcdef",0xffffffff,0xffffffff,json,sizeof(json));assert(n && n<=480 && strlen(json)==n);puts(json);
    } else if(c==50) {printf("%zu %zu %zu\n",sizeof(ab_scan_t),sizeof(ab_metrics_t),sizeof(ab_report_aux_t));}
    if(c==60) {char input[1026];size_t length=fread(input,1,sizeof(input),stdin);ab_command_t command;puts(ab_command_parse(input,length,&command)?"valid":"invalid");}
    return 0;
}
