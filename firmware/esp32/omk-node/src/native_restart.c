#include "native_restart.h"
#include "node_messages.h"
#include "esp_attr.h"
#include "esp_system.h"
#include "esp_log.h"
#include "sdkconfig.h"
#include <string.h>
#include <inttypes.h>

#ifndef CONFIG_OMK_RELAY_RESTART_NODE_ID
#define CONFIG_OMK_RELAY_RESTART_NODE_ID ""
#endif

#define RESTART_MAGIC UINT32_C(0x4f4d5231)
/* RTC noinit survives esp_restart (unlike RTC_DATA_ATTR). No Flash writes.
 * pending identifies only the immediately preceding reboot, while used stays
 * set across intervening software/watchdog resets to prevent a reboot loop. */
static RTC_NOINIT_ATTR struct {
    uint32_t magic, used, pending, call_id, age_ms, reselected, check;
} retained_restart;
static bool authorized, used, previous_stall;

static uint32_t checksum(void) {
    return ~(RESTART_MAGIC ^ retained_restart.used ^ retained_restart.pending ^
             retained_restart.call_id ^ retained_restart.age_ms ^ retained_restart.reselected);
}

void native_restart_init(const char *node_id) {
    const char *allowed = CONFIG_OMK_RELAY_RESTART_NODE_ID;
    authorized = node_id && strlen(allowed) == 12 && !strcmp(node_id, allowed);
    esp_reset_reason_t reason = esp_reset_reason();
    bool valid = retained_restart.magic == RESTART_MAGIC && retained_restart.check == checksum() &&
                 retained_restart.used <= 1 && retained_restart.pending <= 1 && retained_restart.reselected <= 1;
    previous_stall = valid && retained_restart.pending && reason == ESP_RST_SW;
    if (previous_stall)
        ESP_LOGW("omk-recovery", "Previous restart: tx_call_stall call=%" PRIu32 " age_ms=%" PRIu32 " reselected=%" PRIu32,
                 retained_restart.call_id, retained_restart.age_ms, retained_restart.reselected);
    /* An invalid warm-reset record is fail-closed for the first hour. */
    used = reason != ESP_RST_POWERON && (!valid || retained_restart.used);
    memset(&retained_restart, 0, sizeof(retained_restart));
    retained_restart.used = used;
    retained_restart.check = checksum();
    retained_restart.magic = RESTART_MAGIC;
}

bool native_restart_allowed(uint64_t uptime_ms) {
    return (authorized || node_messages_sen66_connected()) &&
           (!used || uptime_ms >= NATIVE_RESTART_HOLDOFF_MS);
}

bool native_restart_previous_stall(void) { return previous_stall; }

void native_restart_record(uint32_t call_id, uint32_t age_ms, bool reselected) {
    retained_restart.magic = 0;
    retained_restart.used = retained_restart.pending = 1;
    retained_restart.call_id = call_id;
    retained_restart.age_ms = age_ms;
    retained_restart.reselected = reselected;
    retained_restart.check = checksum();
    retained_restart.magic = RESTART_MAGIC;
    used = true;
}
