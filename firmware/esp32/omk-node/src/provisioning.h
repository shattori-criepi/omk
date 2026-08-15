#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

esp_err_t provisioning_start(uint64_t node_id, bool wifi_stack_ready);
