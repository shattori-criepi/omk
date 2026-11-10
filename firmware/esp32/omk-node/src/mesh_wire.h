#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MESH_TRANSPORT_PAYLOAD_MAX 1024
#define MESH_WIRE_NODE_ID_LENGTH 12
#define MESH_WIRE_HEADER_SIZE 20
#define MESH_WIRE_MAX_SIZE (MESH_WIRE_HEADER_SIZE + MESH_TRANSPORT_PAYLOAD_MAX)
#define MESH_TRANSPORT_TTL_MS 30000U

typedef enum {
    MESH_MESSAGE_SEN66 = 1,
    MESH_MESSAGE_BLE_RAW,
    MESH_MESSAGE_NODE_STATUS,
    MESH_MESSAGE_REGISTRATION_STATUS,
    MESH_MESSAGE_REGISTRATION_ACK,
    /* Transport-private downlink, never accepted by publish(). */
    MESH_MESSAGE_CONFIG,
} mesh_message_kind_t;

typedef struct {
    mesh_message_kind_t kind;
    char node_id[MESH_WIRE_NODE_ID_LENGTH + 1];
    uint16_t length;
    char payload[MESH_TRANSPORT_PAYLOAD_MAX + 1];
} mesh_wire_message_t;

bool mesh_wire_valid_node_id(const char *node_id);
bool mesh_wire_valid_logical_id(const char *logical_id);
bool mesh_wire_is_registration(mesh_message_kind_t kind);
bool mesh_wire_is_fresh(uint64_t queued_ms, uint64_t now_ms);
/* Byte-serialized header; no compiler padding or native-endian fields. */
bool mesh_wire_encode(const mesh_wire_message_t *message, uint8_t *buffer,
                      size_t capacity, size_t *written);
bool mesh_wire_decode(const uint8_t *buffer, size_t length,
                      mesh_wire_message_t *message);

/* Topic target is a single validated 12-hex Node ID, not a wildcard. */
bool mesh_wire_config_topic(const char *topic, size_t length, char node_id[13]);

typedef struct {
    bool valid;
    size_t received;
    mesh_wire_message_t message;
} mesh_config_assembler_t;

/* 1: complete, 0: waiting for remainder, -1: malformed/oversized. */
int mesh_wire_config_fragment(mesh_config_assembler_t *assembler,
                              const char *topic, size_t topic_length,
                              const char *payload, size_t length,
                              size_t total, size_t offset);
