#include "ble_scan_trial.h"
#include "cJSON.h"
#include <math.h>
#include <string.h>

static bool integer(const cJSON *v,uint64_t max,uint64_t *out) {
    if(!cJSON_IsNumber(v) || !isfinite(v->valuedouble) || v->valuedouble<0 || v->valuedouble>(double)max) return false;
    uint64_t n=(uint64_t)v->valuedouble;
    if((double)n!=v->valuedouble) return false;
    *out=n; return true;
}
static bool hex(const cJSON *v,char *out,unsigned n) {
    if(!cJSON_IsString(v) || !v->valuestring || strlen(v->valuestring)!=n) return false;
    for(unsigned i=0;i<n;i++) if(!((v->valuestring[i]>='0'&&v->valuestring[i]<='9') ||
        (v->valuestring[i]>='a'&&v->valuestring[i]<='f'))) return false;
    memcpy(out,v->valuestring,n+1); return true;
}
bool ab_command_parse(const char *payload,size_t length,ab_command_t *out) {
    if(!payload || !out || !length || length>512 || memchr(payload,0,length)) return false;
    const char *end=NULL;
    cJSON *j=cJSON_ParseWithLengthOpts(payload,length,&end,false);
    if(!j) return false;
    while(end<payload+length && (*end==' '||*end=='\n'||*end=='\r'||*end=='\t')) ++end;
    static const char *keys[]={"protocol_version","command","node_id","target_boot_id","root_id","root_nonce",
        "control_epoch","accept_before_ms","segments","plan_end_ms"};
    unsigned mask=0; bool valid=cJSON_IsObject(j) && end==payload+length;
    for(cJSON *v=j->child;v && valid;v=v->next) {
        unsigned i=0; for(;i<10;i++) if(v->string && !strcmp(v->string,keys[i])) break;
        if(i==10 || (mask&(1U<<i))) valid=false; else mask|=1U<<i;
    }
    ab_command_t c={0}; uint64_t n; char nonce[17];
    const cJSON *cmd=cJSON_GetObjectItemCaseSensitive(j,"command");
    valid=valid && cJSON_IsString(cmd) && cmd->valuestring;
    if(valid) {
        c.plan.cancel=!strcmp(cmd->valuestring,"ble_scan_cancel");
        valid=c.plan.cancel || !strcmp(cmd->valuestring,"ble_scan_plan");
    }
    valid=valid && mask==(c.plan.cancel?255U:1023U) &&
        integer(cJSON_GetObjectItemCaseSensitive(j,"protocol_version"),2,&n) && n==2 &&
        hex(cJSON_GetObjectItemCaseSensitive(j,"node_id"),c.node_id,12) &&
        hex(cJSON_GetObjectItemCaseSensitive(j,"target_boot_id"),c.boot_id,16) &&
        hex(cJSON_GetObjectItemCaseSensitive(j,"root_id"),c.root_id,12) &&
        hex(cJSON_GetObjectItemCaseSensitive(j,"root_nonce"),nonce,16) &&
        integer(cJSON_GetObjectItemCaseSensitive(j,"control_epoch"),UINT32_MAX,&n) && n>0;
    if(valid) {
        c.plan.epoch=(uint32_t)n;
        for(unsigned i=0;i<16;i++) c.plan.nonce=(c.plan.nonce<<4)|(uint64_t)(nonce[i]<='9'?nonce[i]-'0':nonce[i]-'a'+10);
        valid=c.plan.nonce!=0 && integer(cJSON_GetObjectItemCaseSensitive(j,"accept_before_ms"),UINT64_C(9007199254740991),&c.plan.accept_before_ms);
    }
    if(valid && !c.plan.cancel) {
        const cJSON *segments=cJSON_GetObjectItemCaseSensitive(j,"segments");
        int count=cJSON_GetArraySize(segments);
        valid=cJSON_IsArray(segments) && count>=1 && count<=4 &&
            integer(cJSON_GetObjectItemCaseSensitive(j,"plan_end_ms"),UINT64_C(9007199254740991),&c.plan.end_ms);
        if(valid) c.plan.count=(uint8_t)count;
        for(unsigned i=0;valid && i<c.plan.count;i++) {
            const cJSON *g=cJSON_GetArrayItem(segments,(int)i);
            valid=cJSON_IsArray(g) && cJSON_GetArraySize(g)==3 &&
                integer(cJSON_GetArrayItem(g,0),UINT64_C(9007199254740991),&c.plan.segments[i].start_ms) &&
                integer(cJSON_GetArrayItem(g,1),UINT64_C(9007199254740991),&c.plan.segments[i].end_ms) &&
                integer(cJSON_GetArrayItem(g,2),3,&n);
            if(valid) c.plan.segments[i].preset=(uint8_t)n;
        }
    }
    cJSON_Delete(j);
    if(valid) *out=c;
    return valid;
}
