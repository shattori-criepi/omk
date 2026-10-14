#pragma once
/* MQTT packet bytes (not total allocator overhead). 8 KiB admits a BLE burst
 * plus measurements; another 4 KiB keeps registration/probes available.
 * Override at build time after measuring heap and the actual device count. */
#ifndef OMK_MQTT_OUTBOX_BYTES
#define OMK_MQTT_OUTBOX_BYTES 12288U
#endif
#ifndef OMK_MQTT_DATA_BYTES
#define OMK_MQTT_DATA_BYTES 8192U
#endif
#ifndef OMK_MESH_BACKOFF_MIN_MS
#define OMK_MESH_BACKOFF_MIN_MS 1000U
#endif
#ifndef OMK_MESH_BACKOFF_MAX_MS
#define OMK_MESH_BACKOFF_MAX_MS 30000U
#endif
#ifndef OMK_MESH_BACKOFF_RECOVER_MS
#define OMK_MESH_BACKOFF_RECOVER_MS 30000U
#endif

_Static_assert(OMK_MQTT_DATA_BYTES < OMK_MQTT_OUTBOX_BYTES, "Reserve MQTT control headroom");
_Static_assert(OMK_MESH_BACKOFF_MIN_MS > 0 && OMK_MESH_BACKOFF_MIN_MS <= OMK_MESH_BACKOFF_MAX_MS,
               "Invalid Mesh backoff range");
_Static_assert(OMK_MESH_BACKOFF_MAX_MS < 0x80000000U && OMK_MESH_BACKOFF_RECOVER_MS < 0x80000000U,
               "Mesh intervals must fit unsigned timer differences");
