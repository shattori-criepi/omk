#include "diag_store.h"

#include <stdbool.h>
#include <string.h>
#include "esp_partition.h"
#include "esp_timer.h"

#define STORE_ADDRESS 0x210000U
#define STORE_SUBTYPE 0x41U
#define HEADER_SIZE 32U
#define FOOTER_OFFSET (DIAG_STORE_SLOT_SIZE - 8U)
#define IO_CHUNK 256U

_Static_assert(HEADER_SIZE + DIAG_STORE_PAYLOAD_MAX == FOOTER_OFFSET,
               "Diagnostic record exceeds its erase sector");

static const esp_partition_t *partition;
static diag_store_status_t status;
static uint32_t phase;
static uint32_t revision;
static bool boot_protected;
/* Static DRAM avoids flash-driver temporary heap and USB-task stack growth. */
static uint32_t staging_words[IO_CHUNK / sizeof(uint32_t)];
#define staging ((uint8_t *)staging_words)
static const uint8_t record_magic[8] = {'O', 'M', 'K', 'D', 'I', 'A', 'G', '1'};

static uint32_t get_u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static void put_u32(uint8_t *p, uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) p[i] = (uint8_t)(value >> (8U * i));
}

static uint32_t crc_update(uint32_t crc, const uint8_t *data, size_t length) {
    while (length--) {
        crc ^= *data++;
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0U - (crc & 1U)));
    }
    return crc;
}

uint32_t diag_store_crc32(const void *data, size_t length) {
    return crc_update(UINT32_MAX, data, length) ^ UINT32_MAX;
}

static esp_err_t finish_io(int64_t started, esp_err_t error) {
    uint64_t elapsed = (uint64_t)(esp_timer_get_time() - started);
    status.last_io_us = elapsed > UINT32_MAX ? UINT32_MAX : (uint32_t)elapsed;
    if (status.last_io_us > status.max_io_us) status.max_io_us = status.last_io_us;
    status.error = error;
    if (error == ESP_OK) phase = 0;
    return error;
}

/* Scan every byte, including padding/footer. A partially erased header must
 * never make a used sector appear reusable. Read errors fail closed. */
static esp_err_t sector_blank(unsigned slot, bool *blank) {
    *blank = true;
    for (size_t offset = 0; offset < DIAG_STORE_SLOT_SIZE; offset += IO_CHUNK) {
        esp_err_t err = esp_partition_read(partition,
            slot * DIAG_STORE_SLOT_SIZE + offset, staging, IO_CHUNK);
        if (err != ESP_OK) { *blank = false; return err; }
        for (size_t i = 0; i < IO_CHUNK; ++i)
            if (staging[i] != 0xff) *blank = false;
    }
    return ESP_OK;
}

static esp_err_t validate_slot(unsigned slot, bool require_footer) {
    uint8_t header[HEADER_SIZE];
    size_t base = slot * DIAG_STORE_SLOT_SIZE;
    esp_err_t err = esp_partition_read(partition, base, header, sizeof(header));
    if (err != ESP_OK) return err;
    if (memcmp(header, record_magic, sizeof(record_magic)) != 0 ||
        get_u32(header + 8) != 1U || get_u32(header + 12) != slot ||
        get_u32(header + 16) > DIAG_STORE_PAYLOAD_MAX || get_u32(header + 24) != 0U ||
        get_u32(header + 28) != diag_store_crc32(header, 28))
        return ESP_ERR_INVALID_CRC;
    size_t length = get_u32(header + 16);
    uint32_t crc = UINT32_MAX;
    for (size_t offset = 0; offset < length; offset += IO_CHUNK) {
        size_t count = length - offset;
        if (count > IO_CHUNK) count = IO_CHUNK;
        err = esp_partition_read(partition, base + HEADER_SIZE + offset, staging, count);
        if (err != ESP_OK) return err;
        crc = crc_update(crc, staging, count);
    }
    if ((crc ^ UINT32_MAX) != get_u32(header + 20)) return ESP_ERR_INVALID_CRC;
    if (require_footer) {
        uint8_t footer[8];
        err = esp_partition_read(partition, base + FOOTER_OFFSET, footer, sizeof(footer));
        if (err != ESP_OK) return err;
        if (get_u32(footer) != DIAG_STORE_COMMIT_MAGIC ||
            get_u32(footer + 4) != ~DIAG_STORE_COMMIT_MAGIC) return ESP_ERR_INVALID_CRC;
    }
    return ESP_OK;
}

void diag_store_init(void) {
    int64_t started = esp_timer_get_time();
    memset(&status, 0, sizeof(status));
    phase = 1; revision = 0; boot_protected = false;
    partition = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, STORE_SUBTYPE, "omk_diag");
    if (partition == NULL) { finish_io(started, ESP_ERR_NOT_FOUND); return; }
    if (partition->address != STORE_ADDRESS || partition->size != DIAG_STORE_SIZE ||
        partition->encrypted || partition->readonly) {
        partition = NULL;
        finish_io(started, ESP_ERR_INVALID_STATE);
        return;
    }
    esp_err_t error = ESP_OK;
    for (unsigned slot = 0; slot < 2; ++slot) {
        bool blank;
        esp_err_t err = sector_blank(slot, &blank);
        if (err != ESP_OK) error = err;
        if (!blank) {
            status.occupied_mask |= 1U << slot;
            status.attempt_mask |= 1U << slot;
            err = validate_slot(slot, true);
            if (err == ESP_OK) status.valid_mask |= 1U << slot;
            else if (error == ESP_OK) error = err;
        }
    }
    status.ready = status.occupied_mask == 0 && error == ESP_OK;
    boot_protected = status.occupied_mask != 0;
    finish_io(started, error);
}

static esp_err_t write_chunks(size_t offset, const uint8_t *data, size_t length) {
    while (length) {
        size_t count = length > IO_CHUNK ? IO_CHUNK : length;
        memcpy(staging, data, count);
        esp_err_t err = esp_partition_write(partition, offset, staging, count);
        if (err != ESP_OK) return err;
        offset += count;
        data += count;
        length -= count;
    }
    return ESP_OK;
}

esp_err_t diag_store_save(unsigned slot, const void *payload, size_t length) {
    int64_t started = esp_timer_get_time();
    if (slot >= 2 || length > DIAG_STORE_PAYLOAD_MAX || (length && !payload)) {
        phase = 10; return finish_io(started, ESP_ERR_INVALID_ARG);
    }
    if (!partition || !status.ready || (status.attempt_mask & (1U << slot))) {
        phase = 11; return finish_io(started, ESP_ERR_INVALID_STATE);
    }
    /* Consume before any flash I/O; even an all-FF failure cannot loop/retry. */
    ++revision;
    status.attempt_mask |= 1U << slot;
    status.ready = status.attempt_mask != 3U;
    bool blank;
    phase = 4;
    esp_err_t err = sector_blank(slot, &blank);
    status.occupied_mask |= 1U << slot; /* Conservatively used even on I/O failure. */
    if (err != ESP_OK) return finish_io(started, err);
    if (!blank) return finish_io(started, ESP_ERR_INVALID_STATE);

    uint8_t header[HEADER_SIZE] = {0};
    memcpy(header, record_magic, sizeof(record_magic));
    put_u32(header + 8, 1);
    put_u32(header + 12, slot);
    put_u32(header + 16, (uint32_t)length);
    put_u32(header + 20, diag_store_crc32(payload, length));
    put_u32(header + 28, diag_store_crc32(header, 28));
    size_t base = slot * DIAG_STORE_SLOT_SIZE;
    phase = 5; err = write_chunks(base, header, sizeof(header));
    if (err == ESP_OK) { phase = 6; err = write_chunks(base + HEADER_SIZE, payload, length); }
    if (err == ESP_OK) { phase = 7; err = validate_slot(slot, false); }
    if (err == ESP_OK) {
        uint8_t footer[8];
        put_u32(footer, DIAG_STORE_COMMIT_MAGIC);
        put_u32(footer + 4, ~DIAG_STORE_COMMIT_MAGIC);
        /* The commit marker is a separate, final flash write. */
        phase = 8; err = write_chunks(base + FOOTER_OFFSET, footer, sizeof(footer));
    }
    if (err == ESP_OK) { phase = 9; err = validate_slot(slot, true); }
    if (err == ESP_OK) status.valid_mask |= 1U << slot;
    return finish_io(started, err);
}

esp_err_t diag_store_read(uint32_t offset, void *data, size_t length) {
    if (offset > DIAG_STORE_SIZE || length > DIAG_STORE_SIZE - offset || (length && !data))
        return ESP_ERR_INVALID_ARG;
    if (!partition) return ESP_ERR_INVALID_STATE;
    uint8_t *out = data;
    while (length) {
        size_t count = length > IO_CHUNK ? IO_CHUNK : length;
        esp_err_t err = esp_partition_read(partition, offset, staging, count);
        if (err != ESP_OK) return err;
        memcpy(out, staging, count);
        out += count;
        offset += count;
        length -= count;
    }
    return ESP_OK;
}

esp_err_t diag_store_replace(unsigned slot, const void *payload, size_t length) {
    int64_t started = esp_timer_get_time();
    if (slot >= 2 || length > DIAG_STORE_PAYLOAD_MAX || (length && !payload)) {
        phase = 10; return finish_io(started, ESP_ERR_INVALID_ARG);
    }
    if (!partition || boot_protected || status.valid_mask != 3 || status.error != ESP_OK) {
        phase = 11; return finish_io(started, ESP_ERR_INVALID_STATE);
    }
    /* Revalidate the survivor before touching the victim. On any failure keep
     * the remaining committed sector; do not erase/retry it automatically. */
    phase = 9;
    esp_err_t err = validate_slot(slot ^ 1U, true);
    if (err != ESP_OK) return finish_io(started, err);
    ++revision;
    status.ready = 0;
    status.valid_mask &= ~(1U << slot);
    phase = 2;
    err = esp_partition_erase_range(partition, slot * DIAG_STORE_SLOT_SIZE, DIAG_STORE_SLOT_SIZE);
    if (err != ESP_OK) return finish_io(started, err);
    bool blank;
    phase = 3;
    err = sector_blank(slot, &blank);
    if (err != ESP_OK || !blank)
        return finish_io(started, err != ESP_OK ? err : ESP_ERR_INVALID_STATE);
    status.attempt_mask &= ~(1U << slot);
    status.occupied_mask &= ~(1U << slot);
    status.ready = 1;
    err = diag_store_save(slot, payload, length);
    return finish_io(started, err); /* Include sector erase in measured I/O pause. */
}

esp_err_t diag_store_arm(void) {
    int64_t started = esp_timer_get_time();
    if (!partition) { phase = 11; return finish_io(started, ESP_ERR_INVALID_STATE); }
    ++revision;
    status.ready = 0;
    status.attempt_mask = status.occupied_mask = 3;
    status.valid_mask = 0;
    /* Only this explicitly authorized operation erases. Stop on first error. */
    esp_err_t err = ESP_OK;
    phase = 2;
    for (unsigned slot = 0; slot < 2 && err == ESP_OK; ++slot)
        err = esp_partition_erase_range(partition, slot * DIAG_STORE_SLOT_SIZE, DIAG_STORE_SLOT_SIZE);
    for (unsigned slot = 0; slot < 2 && err == ESP_OK; ++slot) {
        bool blank;
        phase = 3;
        err = sector_blank(slot, &blank);
        if (err == ESP_OK && !blank) err = ESP_ERR_INVALID_STATE;
    }
    if (err == ESP_OK) {
        status.attempt_mask = status.occupied_mask = 0;
        status.ready = 1;
        boot_protected = false;
    }
    return finish_io(started, err);
}

diag_store_status_t diag_store_status(void) { return status; }
uint32_t diag_store_phase(void) { return phase; }
uint32_t diag_store_revision(void) { return revision; }
uint32_t diag_store_block_reason(void) {
    if (!partition) return 3;
    if (boot_protected) return 1;
    if (status.valid_mask == 3 && status.error == ESP_OK) return 4;
    if (status.ready) return 0;
    return status.attempt_mask == 3 && status.error == ESP_OK ? 2 : 3;
}
esp_err_t diag_store_flash_crc32(uint32_t *out) {
    if (!partition || !out) return ESP_ERR_INVALID_STATE;
    uint32_t crc = UINT32_MAX;
    for (size_t offset = 0; offset < DIAG_STORE_SIZE; offset += IO_CHUNK) {
        esp_err_t err = esp_partition_read(partition, offset, staging, IO_CHUNK);
        if (err != ESP_OK) return err;
        crc = crc_update(crc, staging, IO_CHUNK);
    }
    *out = crc ^ UINT32_MAX;
    return ESP_OK;
}
