#pragma once
#include <stdbool.h>
#include "cJSON.h"

/* Existing USB task only. Return true for a recognized diagnostic command,
 * including a rejected request. Responses fit one 256-byte driver write. */
bool diag_usb_handle(const cJSON *request, const char *command, const char *node_id);
