#pragma once
#ifndef OMK_DIAGNOSTICS
#define OMK_DIAGNOSTICS 0
#endif
#if OMK_DIAGNOSTICS
#include "diag_core.h"
#include "diag_store.h"
#include "mesh_wire.h"
void mesh_diag_scan(uint64_t ms,uint32_t epoch,uint32_t detail);
void mesh_diag_boot(const char *node_id, const char *boot_id, uint32_t reset_reason);
void mesh_diag_tx_begin(const mesh_wire_message_t *message, bool upstream);
void mesh_diag_tx_end(int32_t result);
void mesh_diag_request(unsigned kind);
void mesh_diag_ble(unsigned type, bool event, bool rejected, bool suppressed);
void mesh_diag_mesh_event(int32_t id);
void mesh_diag_disconnected(const uint8_t bssid[6], uint8_t reason, int8_t rssi);
void mesh_diag_quality(bool bad);
void mesh_diag_demand(bool tx, bool delivery, uint64_t proven_ms, uint64_t inhibit_until, bool voting);
void mesh_diag_recovery(uint32_t action, uint32_t reasons, bool finished, int32_t result);
void mesh_diag_receipt(const char *node_id, const char *boot_id, uint32_t seq);
void mesh_diag_root(const mesh_wire_message_t *message, unsigned stage);
/* Only the existing USB task calls the following owner APIs. */
void mesh_diag_poll(void);
uint32_t mesh_diag_live_begin(size_t *length);
uint32_t mesh_diag_v2_begin(size_t *length);
esp_err_t mesh_diag_v2_read(uint32_t token,uint32_t offset,void *out,size_t length);
uint32_t mesh_diag_live_crc32(void);
esp_err_t mesh_diag_live_read(uint32_t token, uint32_t offset, void *out, size_t length);
esp_err_t mesh_diag_rearm(void);
void mesh_diag_state(char boot_id[17], uint64_t *start, uint32_t *generation, uint32_t *reason);
#else
#define mesh_diag_scan(...) ((void)0)
#define mesh_diag_boot(...) ((void)0)
#define mesh_diag_tx_begin(...) ((void)0)
#define mesh_diag_tx_end(...) ((void)0)
#define mesh_diag_request(...) ((void)0)
#define mesh_diag_ble(...) ((void)0)
#define mesh_diag_mesh_event(...) ((void)0)
#define mesh_diag_disconnected(...) ((void)0)
#define mesh_diag_quality(...) ((void)0)
#define mesh_diag_demand(...) ((void)0)
#define mesh_diag_recovery(...) ((void)0)
#define mesh_diag_receipt(...) ((void)0)
#define mesh_diag_root(...) ((void)0)
#define mesh_diag_poll(...) ((void)0)
#endif
