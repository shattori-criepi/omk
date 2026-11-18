include(${CMAKE_CURRENT_LIST_DIR}/probe_abi.cmake)
idf_component_register(
    SRCS "main.c" "discovery_ble.c" "node_state.c" "node_identity.c"
         "node_registration.c" "node_messages.c" "sensor_manager.c"
         "sensor_driver.c" "sensor_drivers.c" "sen66_sensor.c"
         "usb_provisioning.c" "wifi_station.c" "gateway_credentials.c"
         "switchbot_relay.c" "mesh_transport.c" "mesh_wire.c" "mesh_session.c" "mesh_credentials.c"
         "ble_scan_trial.c" "ble_scan_command.c" "ble_scan_metrics.c" "ble_scan_runtime.c"
         "native_recovery.c" "mesh_probe.c" "diag_core.c" "diag_store.c" "mesh_diag.c" "diag_usb.c"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_event esp_netif esp_wifi lwip cjson bt mqtt esp_timer
             esp_driver_i2c esp_driver_usb_serial_jtag mbedtls)
target_compile_definitions(${COMPONENT_LIB} PRIVATE OMK_BLE_ENABLED=1)
target_compile_definitions(${COMPONENT_LIB} PRIVATE OMK_MESH_PROBE=1)

# Compile-time stack reports only; no runtime instrumentation.
set_source_files_properties(diag_core.c diag_store.c mesh_diag.c diag_usb.c ble_scan_trial.c ble_scan_command.c ble_scan_metrics.c ble_scan_runtime.c mesh_transport.c node_messages.c discovery_ble.c PROPERTIES COMPILE_OPTIONS "-fstack-usage")
set_source_files_properties(mesh_probe.c native_recovery.c PROPERTIES COMPILE_OPTIONS "-fstack-usage")
