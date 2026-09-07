#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    uint32_t rx_success_count;
    uint32_t tx_success_count;
    uint32_t tx_failure_count;
    uint32_t last_rx_success_uptime_s;
    uint32_t last_tx_success_uptime_s;
} mesh_netif_diagnostics_t;

esp_err_t mesh_netifs_init(void);
/* Must be called only after esp_mesh_start() has succeeded. */
esp_err_t mesh_netif_start_receive_task(void);
esp_err_t mesh_netifs_start(bool is_root);
esp_err_t mesh_netifs_stop(void);
esp_err_t mesh_netif_start_root_ap(bool is_root, uint32_t dns_addr);
void mesh_netif_get_diagnostics(mesh_netif_diagnostics_t *diagnostics);
