idf_component_register(SRCS "main.c" "boot_diagnostics.c" "network_diagnostics.c" "network_diagnostics_store.c" "discovery_ble.c" "node_state.c" "node_identity.c" "node_registration.c" "sensor_manager.c" "sensor_driver.c" "sensor_drivers.c" "sen66_sensor.c" "sen66_diagnostics.c" "sen66_delivery.c" "usb_provisioning.c" "wifi_station.c" "gateway_credentials.c" "mqtt_registration.c" "switchbot_relay.c" "mesh_network.c" "mesh_netif.c" "mesh_tx_policy.c" "mesh_liveness.c" "mesh_recovery.c" "mesh_recovery_store.c" "mesh_root_recovery.c" "mesh_credentials.c" INCLUDE_DIRS "." REQUIRES nvs_flash esp_event esp_netif esp_wifi cjson bt mqtt esp_timer esp_driver_i2c esp_driver_usb_serial_jtag mbedtls)

if(OMK_NETWORK_DIAGNOSTICS)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE OMK_NETWORK_DIAGNOSTICS=1)
endif()
