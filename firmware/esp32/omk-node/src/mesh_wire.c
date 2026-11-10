#include "mesh_wire.h"

#include <string.h>

bool mesh_wire_valid_node_id(const char *id) {
    if (!id) return false;
    for (size_t i = 0; i < MESH_WIRE_NODE_ID_LENGTH; ++i) {
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f')))
            return false;
    }
    return id[MESH_WIRE_NODE_ID_LENGTH] == '\0';
}

bool mesh_wire_valid_logical_id(const char *id) {
    if (!id || !*id) return false;
    size_t i = 0;
    for (; id[i] && i <= 48; ++i) {
        char c = id[i];
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_')) return false;
    }
    return i <= 48;
}

bool mesh_wire_is_registration(mesh_message_kind_t kind) {
    return kind == MESH_MESSAGE_REGISTRATION_STATUS || kind == MESH_MESSAGE_REGISTRATION_ACK;
}

bool mesh_wire_is_fresh(uint64_t queued_ms, uint64_t now_ms) {
    return now_ms >= queued_ms && now_ms - queued_ms < MESH_TRANSPORT_TTL_MS;
}

static bool valid_kind(unsigned kind) {
    return kind >= MESH_MESSAGE_SEN66 && kind <= MESH_MESSAGE_CONFIG;
}

bool mesh_wire_encode(const mesh_wire_message_t *m, uint8_t *buffer,
                      size_t capacity, size_t *written) {
    if (written) *written = 0;
    if (!m || !buffer || !written || !valid_kind(m->kind) ||
        !mesh_wire_valid_node_id(m->node_id) || !m->length ||
        m->length > MESH_TRANSPORT_PAYLOAD_MAX ||
        capacity < MESH_WIRE_HEADER_SIZE + (size_t)m->length ||
        memchr(m->payload, '\0', m->length)) return false;
    memcpy(buffer, "OMK2", 4);
    buffer[4] = 1;
    buffer[5] = (uint8_t)m->kind;
    buffer[6] = (uint8_t)(m->length >> 8);
    buffer[7] = (uint8_t)m->length;
    memcpy(buffer + 8, m->node_id, MESH_WIRE_NODE_ID_LENGTH);
    memcpy(buffer + MESH_WIRE_HEADER_SIZE, m->payload, m->length);
    *written = MESH_WIRE_HEADER_SIZE + m->length;
    return true;
}

bool mesh_wire_decode(const uint8_t *buffer, size_t length, mesh_wire_message_t *m) {
    if (!buffer || !m || length < MESH_WIRE_HEADER_SIZE ||
        length > MESH_WIRE_MAX_SIZE || memcmp(buffer, "OMK2", 4) ||
        buffer[4] != 1 || !valid_kind(buffer[5])) return false;
    size_t payload_length = ((size_t)buffer[6] << 8) | buffer[7];
    if (!payload_length || payload_length > MESH_TRANSPORT_PAYLOAD_MAX ||
        length != MESH_WIRE_HEADER_SIZE + payload_length ||
        memchr(buffer + MESH_WIRE_HEADER_SIZE, '\0', payload_length)) return false;
    char id[13];
    memcpy(id, buffer + 8, 12);
    id[12] = '\0';
    if (!mesh_wire_valid_node_id(id)) return false;
    m->kind = (mesh_message_kind_t)buffer[5];
    memcpy(m->node_id, id, sizeof(id));
    m->length = (uint16_t)payload_length;
    memcpy(m->payload, buffer + MESH_WIRE_HEADER_SIZE, payload_length);
    m->payload[payload_length] = '\0';
    return true;
}

bool mesh_wire_config_topic(const char *topic, size_t length, char node_id[13]) {
    static const char prefix[] = "omk/node/";
    static const char suffix[] = "/registration/config";
    if (!topic || !node_id || length != sizeof(prefix) - 1 + 12 + sizeof(suffix) - 1 ||
        memcmp(topic, prefix, sizeof(prefix) - 1) ||
        memcmp(topic + sizeof(prefix) - 1 + 12, suffix, sizeof(suffix) - 1)) return false;
    memcpy(node_id, topic + sizeof(prefix) - 1, 12);
    node_id[12] = '\0';
    return mesh_wire_valid_node_id(node_id);
}

int mesh_wire_config_fragment(mesh_config_assembler_t *a,
                              const char *topic, size_t topic_length,
                              const char *payload, size_t length,
                              size_t total, size_t offset) {
    if (!a) return -1;
    if (offset == 0) {
        a->valid = false;
        a->received = 0;
        if (!total || total > MESH_TRANSPORT_PAYLOAD_MAX ||
            !mesh_wire_config_topic(topic, topic_length, a->message.node_id)) return -1;
        a->message.kind = MESH_MESSAGE_CONFIG;
        a->message.length = (uint16_t)total;
        a->valid = true;
    }
    if (!a->valid || total != a->message.length || offset != a->received ||
        length > total - a->received || (length && !payload) ||
        (length && memchr(payload, '\0', length))) {
        a->valid = false;
        return -1;
    }
    if (offset && topic_length) {
        char id[13];
        if (!mesh_wire_config_topic(topic, topic_length, id) || strcmp(id, a->message.node_id)) {
            a->valid = false;
            return -1;
        }
    }
    if (length) memcpy(a->message.payload + a->received, payload, length);
    a->received += length;
    if (a->received < total) return 0;
    a->message.payload[total] = '\0';
    a->valid = false;
    return 1;
}
