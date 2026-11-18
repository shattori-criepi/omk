#pragma once
#include <stdbool.h>
#include <stddef.h>
#define OMK_NODE_LOGICAL_ID_MAX_LENGTH 48
/* Shared wire, command and persisted-value contract. Bounded, ASCII only. */
static inline bool node_logical_id_valid(const char *id) {
    if (!id || !id[0]) return false;
    for (size_t i = 0; i <= OMK_NODE_LOGICAL_ID_MAX_LENGTH; ++i) {
        unsigned char c = (unsigned char)id[i];
        if (!c) return true;
        if (i == OMK_NODE_LOGICAL_ID_MAX_LENGTH ||
            !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '-' || c == '_')) return false;
    }
    return false;
}
