#pragma once
#include <stdbool.h>
#include <stddef.h>
/* USB pages 27..30: current RAM only, independent observations. */
bool diagnostic_runtime_page(unsigned page, char *out, size_t size);
