#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

typedef enum { SENSOR_TRANSPORT_I2C, SENSOR_TRANSPORT_UART, SENSOR_TRANSPORT_ANALOG } sensor_transport_t;
typedef enum {
    SENSOR_ID_NO_MATCH, SENSOR_ID_EXACT, SENSOR_ID_AMBIGUOUS,
    SENSOR_ID_ERROR, SENSOR_ID_CONFIGURATION_REQUIRED,
} sensor_identity_t;

/* A configured endpoint, not a model identity. No arbitrary UART scanning. */
typedef struct {
    sensor_transport_t transport;
    void *handle;
    union { uint16_t i2c_address; unsigned uart_port; unsigned adc_channel; } location;
} sensor_endpoint_t;

typedef struct sensor_driver {
    const char *driver_id;
    sensor_transport_t transport;
    bool automatic_identity;
    bool (*accepts)(const sensor_endpoint_t *endpoint);
    /* Must release temporary resources and never reset/configure/start. */
    sensor_identity_t (*identify)(const sensor_endpoint_t *endpoint);
    size_t context_size;
    esp_err_t (*start)(const sensor_endpoint_t *endpoint, void *context);
    esp_err_t (*read)(void *context, bool *ready);
    esp_err_t (*publish)(void *context, const char *logical_id);
    /* Recovery closes locally; never send commands to a possibly replaced device. */
    void (*close)(void *context);
    void (*connected)(bool connected);
    void (*diagnostics)(uint32_t recoveries, uint32_t timeouts);
} sensor_driver_t;

/* Evaluate every eligible driver before choosing. No first-success rule. */
sensor_identity_t sensor_discover(const sensor_endpoint_t *endpoint,
                                 const sensor_driver_t *const *drivers, size_t count,
                                 const sensor_driver_t **selected);
extern const sensor_driver_t *const sensor_drivers[];
extern const size_t sensor_driver_count;
