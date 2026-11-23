#include "diag_core.h"
#include <limits.h>
#include <string.h>

static uint32_t increment(uint32_t n) { return n == UINT32_MAX ? n : n + 1; }
static void copy_string(char *to, size_t capacity, const char *from) {
    if (!capacity) return;
    size_t n = 0;
    if (from) while (n + 1 < capacity && from[n]) { to[n] = from[n]; ++n; }
    to[n] = 0;
}
static bool hex(char c) {
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}
static bool whitespace(char c) { return c == ' ' || c == '\t' || c == '\r' || c == '\n'; }
static void space(const char *p, size_t n, size_t *at) { while (*at < n && whitespace(p[*at])) ++*at; }
/* Returns the raw string span, rejecting invalid/control escapes. */
static bool string_span(const char *p, size_t n, size_t *at, size_t *start, size_t *length) {
    if (*at >= n || p[(*at)++] != '"') return false;
    *start = *at;
    while (*at < n) {
        unsigned char c = (unsigned char)p[*at];
        if (c == '"') { *length = *at - *start; ++*at; return true; }
        if (c < 0x20) return false;
        ++*at;
        if (c == '\\') {
            if (*at == n) return false;
            char esc = p[(*at)++];
            if (esc == 'u') {
                for (unsigned i = 0; i < 4; ++i) if (*at == n || !hex(p[(*at)++])) return false;
            } else if (!esc || !strchr("\"\\/bfnrt", esc)) return false;
        }
    }
    return false;
}
static bool number(const char *p, size_t start, size_t end) {
    size_t at = start;
    if (at < end && p[at] == '-') ++at;
    if (at == end) return false;
    if (p[at] == '0') ++at;
    else {
        if (p[at] < '1' || p[at] > '9') return false;
        do { ++at; } while (at < end && p[at] >= '0' && p[at] <= '9');
    }
    if (at < end && p[at] == '.') {
        ++at; size_t before = at;
        while (at < end && p[at] >= '0' && p[at] <= '9') ++at;
        if (at == before) return false;
    }
    if (at < end && (p[at] == 'e' || p[at] == 'E')) {
        ++at;
        if (at < end && (p[at] == '-' || p[at] == '+')) ++at;
        size_t before = at;
        while (at < end && p[at] >= '0' && p[at] <= '9') ++at;
        if (at == before) return false;
    }
    return at == end;
}
/* Only other values are skipped; nested identities never become top-level. */
static bool skip_value(const char *p, size_t n, size_t *at) {
    if (*at == n) return false;
    if (p[*at] == '"') { size_t start, length; return string_span(p, n, at, &start, &length); }
    if (p[*at] == '{' || p[*at] == '[') {
        char closers[16]; unsigned depth = 0;
        do {
            char c = p[*at];
            if (c == '"') { size_t start, length; if (!string_span(p, n, at, &start, &length)) return false; continue; }
            if (c == '{' || c == '[') {
                if (depth == sizeof(closers)) return false;
                closers[depth++] = c == '{' ? '}' : ']';
            } else if (c == '}' || c == ']') {
                if (!depth || closers[--depth] != c) return false;
            } else if ((unsigned char)c < 0x20 && !whitespace(c)) return false;
            ++*at;
        } while (depth && *at < n);
        return !depth;
    }
    size_t start = *at;
    while (*at < n && p[*at] != ',' && p[*at] != '}' && !whitespace(p[*at])) ++*at;
    size_t length = *at - start;
    return (length == 4 && (!memcmp(p + start, "null", 4) || !memcmp(p + start, "true", 4))) ||
        (length == 5 && !memcmp(p + start, "false", 5)) || number(p, start, *at);
}
void diag_identity_read(diag_identity_t *id, uint8_t kind, const char *node_id,
                        const char *p, size_t n) {
    memset(id, 0, sizeof(*id));
    id->kind = kind;
    bool node_valid = node_id != NULL;
    if (node_valid) {
        for (unsigned i = 0; i < 12; ++i) if (!hex(node_id[i])) { node_valid = false; break; }
        if (node_valid && node_id[12]) node_valid = false;
    }
    if (node_valid) memcpy(id->node_id, node_id, 12);
    if (!node_valid || !p || n > 1024) return;
    size_t at = 0; unsigned found = 0;
    space(p, n, &at);
    if (at == n || p[at++] != '{') return;
    space(p, n, &at);
    if (at < n && p[at] == '}') return;
    for (;;) {
        size_t key, length;
        if (!string_span(p, n, &at, &key, &length)) return;
        if (memchr(p + key, '\\', length)) return; /* escaped aliases are ambiguous */
        space(p, n, &at);
        if (at == n || p[at++] != ':') return;
        space(p, n, &at);
        if (length == 7 && !memcmp(p + key, "boot_id", 7)) {
            size_t value, size;
            if ((found & 1U) || !string_span(p, n, &at, &value, &size) || size != 16) return;
            for (unsigned i = 0; i < 16; ++i) if (!hex(p[value + i])) return;
            memcpy(id->boot_id, p + value, 16); found |= 1U;
        } else if (length == 3 && !memcmp(p + key, "seq", 3)) {
            if ((found & 2U) || at == n || p[at] < '0' || p[at] > '9') return;
            uint32_t value = 0; size_t start = at;
            while (at < n && p[at] >= '0' && p[at] <= '9') {
                unsigned digit = (unsigned)(p[at++] - '0');
                if (value > (UINT32_MAX - digit) / 10) return;
                value = value * 10 + digit;
            }
            if (at - start > 1 && p[start] == '0') return;
            id->seq = value; found |= 2U;
        } else if (!skip_value(p, n, &at)) return;
        space(p, n, &at);
        if (at == n) return;
        char separator = p[at++];
        if (separator == '}') break;
        if (separator != ',') return;
        space(p, n, &at);
    }
    space(p, n, &at);
    id->valid = at == n && found == 3U;
}

void diag_core_init(diag_core_t *s, const char *node_id, const char *boot_id,
                    const char *firmware, uint32_t reset_reason) {
    memset(s, 0, sizeof(*s));
    s->schema = DIAG_SCHEMA; s->bytes = sizeof(*s); s->reset_reason = reset_reason;
    copy_string(s->node_id, sizeof(s->node_id), node_id);
    copy_string(s->boot_id, sizeof(s->boot_id), boot_id);
    copy_string(s->firmware, sizeof(s->firmware), firmware);
}
void diag_core_context(diag_core_t *s, const diag_context_t *context, uint64_t now_ms) {
    bool was_root = (s->context.topology.flags & DIAG_F_ROOT) != 0;
    bool is_root = (context->topology.flags & DIAG_F_ROOT) != 0;
    if (was_root != is_root) {
        s->role_changed_ms = now_ms;
        /* Demand/progress clocks belong to one role observation. A long Root
         * tenure must not look like a newly demoted child's TX/delivery stall.
         * Keep cumulative evidence and an actual SDK call crossing the change. */
        s->tx_demand = s->delivery_demand = 0;
        s->demand_observed_ms = now_ms;
        s->tx_demand_since_ms = s->delivery_demand_since_ms = now_ms;
        s->delivery_proven_ms = 0;
        s->kind_error_mask = 0;
        s->quality_bad = 0;
        s->upstream_attempts = 0;
        s->first_upstream_ms = s->last_upstream_ms = s->last_upstream_ok_ms = 0;
        if (is_root) s->root_since_ms = now_ms;
    }
    uint32_t flags = context->topology.flags;
    bool bad_link = (flags & DIAG_F_STARTED) && !(flags & DIAG_F_ROOT) &&
        (!(flags & DIAG_F_PARENT) || (flags & DIAG_F_ROOTLESS));
    if (!bad_link) s->link_bad_since_ms = 0;
    else if (!s->link_bad_since_ms) s->link_bad_since_ms = now_ms ? now_ms : 1;
    s->context = *context;
}
void diag_core_demand(diag_core_t *s, uint64_t now_ms, bool tx_demand,
                      bool delivery_demand, uint64_t proven_ms,
                      uint64_t inhibit_ms, bool voting) {
    bool gap = now_ms >= s->demand_observed_ms &&
        now_ms - s->demand_observed_ms > DIAG_RECENT_MS;
    /* A missing observer interval is not evidence of continuous demand. */
    if (tx_demand && (!s->tx_demand || gap)) s->tx_demand_since_ms = now_ms;
    if (delivery_demand && (!s->delivery_demand || gap)) s->delivery_demand_since_ms = now_ms;
    s->tx_demand = tx_demand; s->delivery_demand = delivery_demand;
    s->demand_observed_ms = now_ms; s->delivery_proven_ms = proven_ms;
    s->inhibit_until_ms = inhibit_ms; s->voting = voting;
}
unsigned diag_error_bucket(int32_t error) {
    switch (error) {
    case 0: return 0;
    case 0x400a: return 1; /* ESP_ERR_MESH_TIMEOUT */
    case 0x4007: return 2; /* ESP_ERR_MESH_NO_MEMORY */
    case 0x400d: return 3; /* ESP_ERR_MESH_QUEUE_FULL */
    case 0x4017: return 4; /* ESP_ERR_MESH_XMIT */
    case 0x400b: return 5; /* ESP_ERR_MESH_DISCONNECTED */
    case 0x400f: return 6; /* ESP_ERR_MESH_NO_ROUTE_FOUND */
    default: return 7;
    }
}
static uint32_t errors(const diag_core_t *s) {
    uint64_t total = 0;
    for (unsigned i = 1; i < DIAG_ERROR_COUNT; ++i) total += s->counts[i];
    return total > UINT32_MAX ? UINT32_MAX : (uint32_t)total;
}
static void append(diag_core_t *s, const diag_event_t *event) {
    s->events[s->event_head] = *event;
    s->event_head = (s->event_head + 1) % DIAG_EVENT_COUNT;
    if (s->event_count < DIAG_EVENT_COUNT) ++s->event_count;
    else s->event_overwrites = increment(s->event_overwrites);
}
static diag_event_t event_value(const diag_core_t *s, uint64_t ms, uint32_t type,
                                int32_t code, uint32_t detail) {
    diag_event_t event = {0};
    event.ms = ms; event.type = type; event.code = code; event.detail = detail;
    event.tx_ok = s->counts[0]; event.tx_errors = errors(s);
    event.topology = s->context.topology;
    return event;
}
void diag_core_tx_begin(diag_core_t *s, uint64_t now_us,
                        const diag_identity_t *id, bool upstream) {
    uint64_t ms = now_us / 1000;
    /* Retire a quiet kind's old failure watch before marking the new call
     * in-flight; otherwise its old first error could instantly age this call. */
    if (id->kind >= 1 && id->kind <= 7) {
        unsigned k = id->kind - 1;
        uint64_t last = s->kind_last_ok_ms[k] > s->kind_last_error_ms[k] ?
            s->kind_last_ok_ms[k] : s->kind_last_error_ms[k];
        if (ms >= last && ms - last > DIAG_RECENT_MS) s->kind_error_mask &= ~(1U << k);
    }
    s->tx_start_us = now_us; s->inflight = 1; s->upstream = upstream;
    s->active_identity = *id;
    if (id->kind >= 1 && id->kind <= 7)
        s->sdk_count[id->kind - 1] = increment(s->sdk_count[id->kind - 1]);
    if (upstream && id->kind != 8) {
        /* A quiet producer interval is not a sustained SDK failure. The last
         * activity timestamp is also updated on return, so time spent inside
         * a long SDK call cannot be mistaken for idle time here. */
        if (s->upstream_attempts && ms >= s->last_upstream_ms &&
            ms - s->last_upstream_ms > DIAG_RECENT_MS) {
            s->upstream_attempts = 0;
            s->last_upstream_ok_ms = 0;
        }
        if (!s->upstream_attempts) s->first_upstream_ms = ms;
        s->upstream_attempts = increment(s->upstream_attempts);
        s->last_upstream_ms = now_us / 1000;
    }
}
void diag_core_tx_end(diag_core_t *s, uint64_t now_us, int32_t error) {
    if (!s->inflight) return;
    uint64_t ms = now_us / 1000;
    uint64_t elapsed = now_us >= s->tx_start_us ? now_us - s->tx_start_us : 0;
    s->last_duration_us = elapsed > UINT32_MAX ? UINT32_MAX : (uint32_t)elapsed;
    if (s->last_duration_us > s->max_duration_us) s->max_duration_us = s->last_duration_us;
    if (s->active_identity.kind == 8) {
        diag_event_t event = event_value(s, ms, DIAG_E_TX, error, s->upstream);
        event.identity = s->active_identity; event.duration_us = s->last_duration_us;
        append(s, &event);
        s->last_result = error; s->inflight = 0;
        return; /* Timing evidence only: no normal-traffic/quality accounting. */
    }
    unsigned bucket = diag_error_bucket(error);
    if (!s->counts[bucket]) s->first_seen_ms[bucket] = ms;
    s->counts[bucket] = increment(s->counts[bucket]);
    if (s->active_identity.kind >= 1 && s->active_identity.kind <= 7) {
        unsigned k = s->active_identity.kind - 1;
        if (error) s->sdk_errors[k] = increment(s->sdk_errors[k]);
        uint32_t bit = 1U << k;
        if (!error) {
            s->kind_last_ok_ms[k] = ms; s->kind_error_mask &= ~bit;
        } else {
            if (!(s->kind_error_mask & bit) || (ms >= s->kind_last_error_ms[k] &&
                ms - s->kind_last_error_ms[k] > DIAG_RECENT_MS)) s->kind_first_error_ms[k] = ms;
            s->kind_error_mask |= bit; s->kind_last_error_ms[k] = ms;
        }
    }
    if (s->upstream) s->last_upstream_ms = ms;
    if (!error) {
        s->last_ok_ms = ms; s->last_ok_identity = s->active_identity;
        if (s->upstream) s->last_upstream_ok_ms = ms;
    }
    diag_event_t event = event_value(s, ms, DIAG_E_TX, error, s->upstream);
    event.identity = s->active_identity; event.duration_us = s->last_duration_us;
    if (error) {
        if (!s->first_error_valid) {
            s->first_error_valid = 1; s->first_error = event; s->baseline = s->current;
            s->onset_sample_needed |= 1U;
        }
        if (!s->episode_number || (ms >= s->last_error_ms && ms - s->last_error_ms >= DIAG_RECENT_MS)) {
            s->episode_number = increment(s->episode_number);
            s->episode_first_error = event; s->episode_baseline = s->current;
            memset(&s->episode_onset, 0, sizeof(s->episode_onset));
            s->onset_sample_needed |= 2U;
        }
        s->last_error_ms = ms;
    }
    if (error != s->last_result || (error && s->counts[bucket] == 1)) append(s, &event);
    s->last_result = error; s->inflight = 0;
}
void diag_core_event(diag_core_t *s, uint64_t now_ms, uint32_t type,
                     int32_t code, uint32_t detail) {
    diag_event_t event = event_value(s, now_ms, type, code, detail);
    append(s, &event);
}
void diag_core_sample(diag_core_t *s, const diag_sample_t *sample) {
    s->current = *sample; s->current.valid = 1;
    for (unsigned i = 0; i < DIAG_ERROR_COUNT; ++i) s->current.counts[i] = s->counts[i];
    if (!s->first_error_valid) s->baseline = s->current;
    if (s->onset_sample_needed & 1U) s->onset = s->current;
    if (s->onset_sample_needed & 2U) s->episode_onset = s->current;
    s->onset_sample_needed = 0;
}
void diag_core_receipt(diag_core_t *s, uint64_t now_ms, const diag_identity_t *id) {
    if (!s->receipt_count || (now_ms >= s->last_receipt_ms && now_ms - s->last_receipt_ms >= DIAG_RECENT_MS)) {
        diag_event_t event = event_value(s, now_ms, DIAG_E_RECEIPT, 0, 0);
        event.identity = *id; append(s, &event);
    }
    s->last_receipt = *id; s->last_receipt_ms = now_ms;
    s->receipt_count = increment(s->receipt_count);
}
void diag_core_recovery(diag_core_t *s, uint64_t now_ms, uint32_t action,
                        uint32_t reason_flags, bool finished, int32_t result) {
    diag_core_event(s, now_ms, finished ? DIAG_E_REC_RESULT : DIAG_E_REC_REQUEST,
                    finished ? result : (int32_t)action, (action << 16) | (reason_flags & 0xffffU));
    /* Parent/rootless and quality REPAIR can precede a total stall.
     * Preserve their requests even with flags=0; schema/slot policy is unchanged. */
    /* A transport recovery may have been selected using pre-arm demand.
     * Log it, but do not latch it into the new interval's automatic save. */
    bool fresh_window = !s->arm_generation ||
        (now_ms >= s->observation_start_ms && now_ms - s->observation_start_ms >= DIAG_STALL_MS);
    if (fresh_window && !finished && ((action == 2 || action == 5 || action == 6 || action == 7) || (reason_flags & (DIAG_REC_TX | DIAG_REC_DELIVERY)))) s->recovery_trigger = 1;
}
void diag_core_root(diag_core_t *s, uint64_t now_ms, const diag_identity_t *id, unsigned stage) {
    if (stage >= DIAG_STAGE_COUNT) return;
    s->root_counts[stage] = increment(s->root_counts[stage]);
    if (stage != DIAG_ROOT_RX && stage != DIAG_ROOT_MQTT_ACCEPT) {
        if (id) s->last_root_failure = *id; else memset(&s->last_root_failure, 0, sizeof(s->last_root_failure));
        s->last_root_failure_ms = now_ms; s->last_root_failure_stage = stage;
    }
    if (!id || (id->kind != 1 && id->kind != 3) || !id->node_id[0]) return;
    unsigned slot = DIAG_PEER_COUNT;
    for (unsigned i = 0; i < DIAG_PEER_COUNT; ++i) {
        if (s->peers[i].rx_count && !memcmp(s->peers[i].rx_identity.node_id, id->node_id, 13)) { slot = i; break; }
    }
    if (slot == DIAG_PEER_COUNT && stage == DIAG_ROOT_RX) {
        for (unsigned i = 0; i < DIAG_PEER_COUNT; ++i) if (!s->peers[i].rx_count) { slot = i; break; }
        if (slot == DIAG_PEER_COUNT) s->peer_overflows = increment(s->peer_overflows);
    }
    if (slot == DIAG_PEER_COUNT) return;
    if (stage == DIAG_ROOT_RX) {
        s->peers[slot].rx_identity = *id; s->peers[slot].rx_ms = now_ms;
        s->peers[slot].rx_count = increment(s->peers[slot].rx_count);
    }
    if (stage >= DIAG_ROOT_INGRESS_REJECT && stage <= DIAG_ROOT_MQTT_ERROR) {
        if (!s->peers[slot].last_failure_ms ||
            (now_ms >= s->peers[slot].last_failure_ms && now_ms - s->peers[slot].last_failure_ms >= DIAG_RECENT_MS))
            s->peers[slot].failure_since_ms = now_ms;
        s->peers[slot].last_failure_ms = now_ms;
    }
    s->peers[slot].stage_identity = *id; s->peers[slot].stage_ms = now_ms; s->peers[slot].stage = stage;
    if (stage == DIAG_ROOT_MQTT_ACCEPT) s->peers[slot].mqtt_accepted = increment(s->peers[slot].mqtt_accepted);
}
static bool elapsed(uint64_t now, uint64_t since, uint64_t duration) { return now >= since && now - since >= duration; }
static bool recent(uint64_t now, uint64_t since) { return now >= since && now - since <= DIAG_RECENT_MS; }
uint32_t diag_core_trigger(const diag_core_t *s, uint64_t now_ms) {
    if (s->arm_generation && !elapsed(now_ms, s->observation_start_ms, DIAG_EARLY_STALL_MS))
        return DIAG_CAPTURE_NONE;
    /* An actual outstanding SDK call remains evidence across role changes;
     * its own start clock, not an old role's last success, defines its age. */
    if (s->inflight && elapsed(now_ms, s->tx_start_us / 1000, DIAG_EARLY_STALL_MS))
        return DIAG_CAPTURE_TX_STALL;
    if (!s->inflight && s->last_duration_us >= DIAG_EARLY_STALL_MS * 1000 &&
        recent(now_ms, s->tx_start_us / 1000 + s->last_duration_us / 1000))
        return DIAG_CAPTURE_TX_STALL; /* Also retain a slow call that returned OK. */
    if (s->link_bad_since_ms && elapsed(now_ms, s->link_bad_since_ms, DIAG_EARLY_STALL_MS))
        return DIAG_CAPTURE_PARENT_STALL;
    if ((s->context.topology.flags & DIAG_F_STARTED) && !(s->context.topology.flags & DIAG_F_ROOT)) {
        uint64_t since = s->observation_start_ms;
        if (s->role_changed_ms > since) since = s->role_changed_ms;
        if (s->last_receipt_ms > since) since = s->last_receipt_ms;
        if (elapsed(now_ms, since, UINT64_C(300000))) return DIAG_CAPTURE_RECEIPT_SILENT;
    }
    if (s->quality_bad && recent(now_ms, s->demand_observed_ms) &&
        !(s->context.topology.flags & DIAG_F_ROOT)) return DIAG_CAPTURE_QUALITY;
    if (s->context.topology.flags & DIAG_F_ROOT) {
        /* Root receipt/config SDK failure is distinct from healthy ingress and
         * MQTT enqueue. Previous child-kind failures must not trigger here. */
        for (unsigned i = 5; i < 7; ++i) {
            bool in_kind = s->inflight && s->active_identity.kind == i + 1;
            if ((s->kind_error_mask & (1U << i)) && elapsed(now_ms, s->kind_first_error_ms[i], DIAG_STALL_MS) &&
                (in_kind || recent(now_ms, s->kind_last_error_ms[i]))) return DIAG_CAPTURE_KIND_STALL;
        }
        for (unsigned i = 0; i < DIAG_PEER_COUNT; ++i) {
            uint64_t failure_since = s->peers[i].failure_since_ms;
            if (failure_since < s->root_since_ms) failure_since = s->root_since_ms;
            if (s->peers[i].rx_count && s->peers[i].last_failure_ms >= s->root_since_ms &&
                s->peers[i].last_failure_ms && elapsed(now_ms, failure_since, DIAG_STALL_MS) &&
                recent(now_ms, s->peers[i].last_failure_ms)) return DIAG_CAPTURE_ROOT_FAILURE;
            if (s->peers[i].rx_count && s->peers[i].rx_ms >= s->root_since_ms &&
                elapsed(now_ms, s->peers[i].rx_ms, DIAG_STALL_MS)) return DIAG_CAPTURE_PEER_SILENT;
        }
        return s->recovery_trigger ? DIAG_CAPTURE_RECOVERY : DIAG_CAPTURE_NONE;
    }
    if (recent(now_ms, s->demand_observed_ms)) {
        uint64_t progress = s->tx_demand_since_ms;
        if (s->last_upstream_ok_ms > progress) progress = s->last_upstream_ok_ms;
        if (s->tx_demand && elapsed(now_ms, progress, DIAG_STALL_MS)) return DIAG_CAPTURE_TX_STALL;
        progress = s->delivery_demand_since_ms;
        if (s->delivery_proven_ms > progress) progress = s->delivery_proven_ms;
        if (s->delivery_demand && elapsed(now_ms, progress, DIAG_STALL_MS)) return DIAG_CAPTURE_DELIVERY_STALL;
    }
    for (unsigned i = 0; i < 7; ++i) {
        bool in_kind = s->inflight && s->upstream && s->active_identity.kind == i + 1;
        if ((s->kind_error_mask & (1U << i)) && elapsed(now_ms, s->kind_first_error_ms[i], DIAG_STALL_MS) &&
            (in_kind || recent(now_ms, s->kind_last_error_ms[i]))) return DIAG_CAPTURE_KIND_STALL;
    }
    uint64_t progress = s->last_upstream_ok_ms ? s->last_upstream_ok_ms : s->first_upstream_ms;
    if (!s->inflight && s->upstream_attempts && recent(now_ms, s->last_upstream_ms) &&
        elapsed(now_ms, progress, DIAG_STALL_MS)) return DIAG_CAPTURE_TX_STALL;
    return s->recovery_trigger ? DIAG_CAPTURE_RECOVERY : DIAG_CAPTURE_NONE;
}
void diag_core_capture(diag_core_t *s, uint64_t now_ms, uint32_t reason) {
    s->capture_ms = now_ms; s->capture_reason = reason;
}
