#include "sensor_driver.h"

sensor_identity_t sensor_discover(const sensor_endpoint_t *endpoint,
                                 const sensor_driver_t *const *drivers, size_t count,
                                 const sensor_driver_t **selected) {
    if (!selected) return SENSOR_ID_ERROR;
    *selected = NULL;
    if (!endpoint || !drivers) return SENSOR_ID_ERROR;
    /* Analog identity comes from a future validated profile, never ADC values.
     * UART also requires a reviewed port/profile before any probe is sent. */
    if (endpoint->transport != SENSOR_TRANSPORT_I2C) return SENSOR_ID_CONFIGURATION_REQUIRED;
    const sensor_driver_t *exact = NULL;
    unsigned exact_count = 0;
    bool uncertain = false, error = false, configuration = false;
    for (size_t i = 0; i < count; ++i) {
        const sensor_driver_t *driver = drivers[i];
        if (driver->transport != endpoint->transport || !driver->accepts(endpoint)) continue;
        if (!driver->automatic_identity) { configuration = true; continue; }
        sensor_identity_t result = driver->identify(endpoint);
        if (result == SENSOR_ID_EXACT) { exact = driver; ++exact_count; }
        else if (result == SENSOR_ID_AMBIGUOUS) uncertain = true;
        else if (result == SENSOR_ID_ERROR) error = true;
        else if (result == SENSOR_ID_CONFIGURATION_REQUIRED) configuration = true;
    }
    if (exact_count > 1 || uncertain) return SENSOR_ID_AMBIGUOUS;
    if (error) return SENSOR_ID_ERROR;
    if (configuration) return SENSOR_ID_CONFIGURATION_REQUIRED;
    if (exact_count == 1) { *selected = exact; return SENSOR_ID_EXACT; }
    return SENSOR_ID_NO_MATCH;
}
