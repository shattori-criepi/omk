#pragma once
#include <stdbool.h>
#include <stdint.h>

#define NATIVE_RECOVERY_LOSS_WINDOW_MS 300000U
#define NATIVE_RECOVERY_MQTT_BUDGET_MS 300000U
/* Local uplink liveness only; an SDK send success is not a delivery ACK. */
#define NATIVE_RECOVERY_TX_STALL_MS 180000U
#define NATIVE_RECOVERY_TX_DEMAND_MS 60000U
#define NATIVE_RECOVERY_TX_WINDOW_MS 3600000U
#define NATIVE_RECOVERY_TX_ATTEMPTS 3U
#define NATIVE_RECOVERY_CALL_STALL_MS 60000U
#define NATIVE_RECOVERY_CALL_RESTART_MS 120000U
#define NATIVE_RECOVERY_DELIVERY_STALL_MS 180000U
/* Allow bounded queue/transit delay without moving proof to ACK arrival time.
 * Still leave two nominal status periods before the delivery-stall deadline. */
#define NATIVE_RECOVERY_RECEIPT_FRESH_MS 120000U

enum { REC_CHILD_TX_STALL = 1U, REC_CHILD_DELIVERY_STALL = 2U, REC_CHILD_QUALITY = 4U,
       REC_CHILD_CALL_STALL = 8U };

/* Policy only; one worker selects and executes, revalidating before each API.
 * Monotonic 64-bit milliseconds. Cooldowns survive role/lease generations. */
typedef enum {
    REC_NONE, REC_ROOT_VOTE, REC_CHILD_RESELECT, REC_ROUTER_RECONNECT,
    REC_DHCP_START, REC_MQTT_RECONNECT, REC_MQTT_RECREATE, REC_CHILD_REPAIR, REC_NODE_RESTART
} native_recovery_action_t;

typedef struct {
    uint32_t epoch, mqtt_generation;
    bool started, root, parent, rootless, ip, mqtt, client, qos1_pending;
    bool voting;
    uint64_t inhibit_until, last_ack_ms, demand_since_ms, disconnected_since_ms;
    bool weak_parent_losses;
    bool tx_demand;
    uint32_t tx_progress;
    bool delivery_demand;
    uint32_t delivery_generation;
    uint64_t delivery_proven_ms;
    bool quality_bad; /* debounced sliding window; single successes do not cancel */
    bool sdk_busy; /* sender is inside the SDK; QUALITY repair needs it idle */
    bool call_active, recovery_busy, restart_allowed;
    uint32_t call_id;
    uint64_t call_started_ms;
} native_recovery_input_t;

typedef struct {
    bool initialized;
    uint32_t epoch, jitter_ms, condition, mqtt_generation;
    uint64_t episode_ms, light_ms, mqtt_since_ms;
    bool light_attempted;
    uint64_t mesh_after, vote_after, recreate_after;
    bool tx_watching;
    uint32_t tx_epoch, tx_progress;
    uint64_t tx_since_ms, tx_observed_ms;
    bool delivery_watching;
    uint32_t delivery_epoch, delivery_generation;
    uint64_t delivery_since_ms, delivery_observed_ms;
    unsigned child_causes; /* Selected A/B causes; zero for legacy P1 recovery. */
    /* Shared A/B sliding-hour budget, retained across link/role changes and
     * successes. Legacy parent-loss/rootless/weak-link recovery is preserved. */
    uint64_t tx_attempt_ms[NATIVE_RECOVERY_TX_ATTEMPTS];
    unsigned tx_attempt_count;
    /* One early request per actual SDK call; survives Mesh link epochs. */
    bool call_seen, call_requested, call_attempted;
    uint32_t call_id;
    uint64_t call_attempt_ms;
} native_recovery_t;

void native_recovery_init(native_recovery_t *s, uint32_t jitter_ms);
/* Selection charges cooldown BEFORE calling APIs, including failures. */
native_recovery_action_t native_recovery_select(native_recovery_t *s,
    uint64_t now, const native_recovery_input_t *in);
/* Called immediately before the existing parent-reselection API. */
void native_recovery_call_attempt(native_recovery_t *s, uint32_t call_id, uint64_t now);
