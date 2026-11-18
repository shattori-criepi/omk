#pragma once
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#define DIAG_STORE_SLOT_SIZE 4096U
#define DIAG_STORE_PAYLOAD_MAX 4056U
#define DIAG_STORE_SIZE (2U * DIAG_STORE_SLOT_SIZE)
#define DIAG_STORE_COMMIT_MAGIC UINT32_C(0x31474c44)

typedef struct {
    uint32_t ready, attempt_mask, valid_mask, occupied_mask;
    int32_t error;
    uint32_t last_io_us, max_io_us;
} diag_store_status_t;

/* Single owner: the existing USB task calls every function below. No task,
 * queue, allocation, or retry is introduced by this module.
 * init is called once per boot. Existing/partial data disarms both slots.
 * arm destroys the previous evidence and requires explicit user confirmation
 * in the USB command handler. status/read are available while disarmed.
 * The caller keeps payload immutable for the duration of save. */
void diag_store_init(void);
esp_err_t diag_store_save(unsigned slot, const void *payload, size_t length);
/* Same-boot rotation only, and only while the other sector verifies committed.
 * Caller limits rate/severity. A reset never authorizes replacing old evidence. */
esp_err_t diag_store_replace(unsigned slot, const void *payload, size_t length);
esp_err_t diag_store_read(uint32_t offset, void *data, size_t length);
esp_err_t diag_store_arm(void);
diag_store_status_t diag_store_status(void);
uint32_t diag_store_crc32(const void *data, size_t length);
esp_err_t diag_store_flash_crc32(uint32_t *crc);
/* Phase: 0 success, 1 init, 2 erase, 3 erased verification, 4 blank check,
 * 5 header, 6 payload, 7 body verification, 8 commit, 9 commit verification,
 * 10 invalid argument, 11 disarmed/used. Block: 0 available, 1 old evidence,
 * 2 attempts exhausted, 3 partition/I/O unavailable, 4 same-boot rotation
 * available subject to the scheduler's severity/rate policy. ready denotes a
 * blank writable slot; ready=0/block=4 does not mean recording has stopped. */
uint32_t diag_store_phase(void);
uint32_t diag_store_block_reason(void);
/* Changes before each automatic/manual Flash mutation, including replacement.
 * USB readers compare it across chunks; slot masks alone can remain identical. */
uint32_t diag_store_revision(void);
