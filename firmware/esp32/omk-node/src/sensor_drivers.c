#include "sensor_driver.h"
#include "sen66_sensor.h"
#include "mqtt_registration.h"
#include "sen66_diagnostics.h"
#include "sen66_delivery.h"
#include "esp_timer.h"

/* Per-endpoint context, allocated by the manager. No singleton sensor state. */
typedef struct {
    sen66_sensor_t sensor;
    sen66_measurement_t sample;
} sen66_context_t;

static bool accepts(const sensor_endpoint_t *endpoint) {
    return endpoint->location.i2c_address == 0x6B;
}
static sensor_identity_t identify(const sensor_endpoint_t *endpoint) {
    return sen66_sensor_identify(endpoint->handle);
}
static esp_err_t start(const sensor_endpoint_t *endpoint, void *context) {
    sen66_context_t *ctx = context;
    esp_err_t err = sen66_sensor_probe(endpoint->handle, &ctx->sensor);
    return err == ESP_OK ? sen66_sensor_start(endpoint->handle, &ctx->sensor) : err;
}
static esp_err_t read_sample(void *context, bool *ready) {
    sen66_context_t *ctx = context;
    uint64_t started_ms = (uint64_t)(esp_timer_get_time() / 1000);
    sen66_diagnostics_read_begin(started_ms);
    esp_err_t err = sen66_sensor_read(&ctx->sensor, &ctx->sample, ready);
    uint32_t seq = sen66_diagnostics_read_end(err, *ready,
        (uint64_t)(esp_timer_get_time() / 1000) - started_ms);
    if (err == ESP_OK && *ready) ctx->sample.sequence = seq;
    return err;
}
static esp_err_t publish(void *context, const char *logical_id) {
    sen66_context_t *ctx = context;
    return sen66_delivery_submit(logical_id, &ctx->sample);
}
static void close_sensor(void *context) {
    sen66_context_t *ctx = context;
    sen66_sensor_close(&ctx->sensor);
}
static const sensor_driver_t sen66_driver = {
    .driver_id = "SEN66", .transport = SENSOR_TRANSPORT_I2C, .automatic_identity = true,
    .accepts = accepts, .identify = identify, .context_size = sizeof(sen66_context_t),
    .start = start, .read = read_sample, .publish = publish, .close = close_sensor,
    .connected = mqtt_registration_set_sen66_connected,
    .diagnostics = mqtt_registration_set_sen66_diagnostics,
};

const sensor_driver_t *const sensor_drivers[] = { &sen66_driver };
const size_t sensor_driver_count = sizeof(sensor_drivers) / sizeof(sensor_drivers[0]);
