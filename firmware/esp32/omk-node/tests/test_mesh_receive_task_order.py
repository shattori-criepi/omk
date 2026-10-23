from pathlib import Path


SOURCE = Path(__file__).parents[1] / "src"


def test_receive_task_starts_only_after_mesh_start():
    netif = (SOURCE / "mesh_netif.c").read_text()
    network = (SOURCE / "mesh_network.c").read_text()

    init = netif[netif.index("esp_err_t mesh_netifs_init") : netif.index("esp_err_t mesh_netif_start_receive_task")]
    assert "xTaskCreate(receive_task" not in init

    receive_start = netif[netif.index("esp_err_t mesh_netif_start_receive_task") :]
    assert "if (receive_task_running) return ESP_ERR_INVALID_STATE;" in receive_start
    assert "xTaskCreate(receive_task" in receive_start

    assert network.index("err = esp_mesh_start();") < network.index(
        "err = mesh_netif_start_receive_task();"
    )


def test_mesh_diagnostic_handlers_only_record_state():
    network = (SOURCE / "mesh_network.c").read_text()

    assert "esp_mesh_set_ap_assoc_expire(30)" in network
    assert "MESH_EVENT_NETWORK_STATE" in network
    assert "is_rootless = event->is_rootless;" in network
    assert "last_parent_disconnect_reason = event->reason;" in network
    assert "last_wifi_disconnect_reason = event->reason;" in network
    assert "esp_event_handler_register(WIFI_EVENT" not in network
    assert "root_switch_count++" in network


def test_mesh_parent_rssi_thresholds_are_configured_and_read_back_before_start():
    network = (SOURCE / "mesh_network.c").read_text()
    kconfig = (SOURCE / "Kconfig.projbuild").read_text()
    defaults = (SOURCE.parents[0] / "sdkconfig.defaults").read_text()
    atom_defaults = (SOURCE.parents[0] / "sdkconfig.atom-s3-lite").read_text()

    assert '#include "esp_mesh_internal.h"' in network
    assert "esp_mesh_set_rssi_threshold(&configured)" in network
    assert "esp_mesh_get_rssi_threshold(&applied)" in network
    assert "Mesh RSSI thresholds: high=%d medium=%d low=%d dBm" in network
    assert network.index("configure_parent_rssi_thresholds();") < network.index("esp_mesh_start();")
    for name, value in (("HIGH", "-78"), ("MEDIUM", "-82"), ("LOW", "-85")):
        assert f"config MESH_PARENT_RSSI_{name}" in kconfig
        assert f"CONFIG_MESH_PARENT_RSSI_{name}={value}" in defaults

    # AtomS3 Lite loads this file after sdkconfig.defaults and therefore owns
    # the same IDF standard boundaries as the common profile.
    for name, value in (("HIGH", "-78"), ("MEDIUM", "-82"), ("LOW", "-85")):
        assert f"CONFIG_MESH_PARENT_RSSI_{name}={value}" in atom_defaults


def test_mesh_disables_root_conflicts_before_start():
    network = (SOURCE / "mesh_network.c").read_text()

    assert "esp_mesh_allow_root_conflicts(false)" in network
    assert "esp_mesh_is_root_conflicts_allowed()" in network
    assert "Mesh root conflicts: disabled" in network
    assert network.index("disable_root_conflicts();") < network.index("esp_mesh_start();")


def lifecycle_sections():
    netif = (SOURCE / "mesh_netif.c").read_text()
    start = netif[netif.index("esp_err_t mesh_netifs_start("):netif.index("esp_err_t mesh_netifs_stop(")]
    root, child = start.split("    } else {", 1)
    dhcp = netif[netif.index("static esp_err_t start_root_external_dhcp"):netif.index("static esp_err_t start_mesh_child_station")]
    return netif, root, child, dhcp


def test_root_parent_connection_signals_external_sta_link_and_starts_dhcp():
    _, root, _, dhcp = lifecycle_sections()
    assert "err = start_root_external_dhcp();" in root
    assert "esp_netif_action_connected(station_netif, NULL, 0, NULL);" in dhcp
    assert "esp_netif_dhcpc_get_status(station_netif, &dhcp_status)" in dhcp
    assert "dhcp_status != ESP_NETIF_DHCP_STARTED" in dhcp
    assert "esp_netif_dhcpc_start" not in dhcp


def test_root_recreated_default_sta_starts_wifi_link_once():
    netif, root, _, _ = lifecycle_sections()
    assert "if (station_netif == NULL) err = create_default_station(true);" in root
    create = netif[netif.index("static esp_err_t create_default_station"):netif.index("static esp_err_t start_root_external_dhcp")]
    assert "if (err == ESP_OK && start_link) err = start_default_station_link(netif);" in create
    assert create.count("start_default_station_link(netif)") == 1
    # Executable duplicate-event/link-start assertions live in the lifecycle harness.


def test_root_parent_disconnect_recreates_external_sta_even_before_internal_ap_exists():
    netif, _, _, _ = lifecycle_sections()
    stop = netif[netif.index("esp_err_t mesh_netifs_stop"):netif.index("esp_err_t mesh_netif_start_root_ap")]
    assert "mesh_ap_netif == NULL" not in stop
    assert "destroy_station();" in stop
    assert "err = create_default_station(true);" in stop
    dispose = netif[netif.index("static void dispose_netif"):netif.index("static void destroy_mesh_ap")]
    assert "esp_wifi_clear_default_wifi_driver_and_handlers(netif)" in dispose
    assert "esp_netif_destroy(netif)" in dispose
    assert dispose.index("esp_netif_destroy(netif)") < dispose.index("free(driver)")


def test_repeated_root_parent_reconnects_reuse_safe_dhcp_action():
    _, root, _, dhcp = lifecycle_sections()
    assert root.count("start_root_external_dhcp()") == 1
    assert "ESP_NETIF_DHCP_STARTED" in dhcp
    assert "esp_netif_dhcpc_stop" not in dhcp


def test_root_child_role_transitions_keep_their_existing_netif_replacements():
    netif, root, child, _ = lifecycle_sections()
    assert "if (station_is_child) destroy_station();" in root
    assert "create_default_station(true)" in root
    assert "if (!station_is_child) destroy_station();" in child
    assert "destroy_mesh_ap();" in child
    assert "start_mesh_child_station();" in child
    destroy = netif[netif.index("static void destroy_station"):netif.index("static void destroy_mesh_ap")]
    assert "station_is_child ? esp_netif_get_io_driver(netif) : NULL" in destroy
    assert "dispose_netif(netif, driver, !station_is_child)" in destroy


def test_parent_disconnect_clears_stale_mesh_status_ip():
    network = (SOURCE / "mesh_network.c").read_text()
    disconnect = network[network.index("case MESH_EVENT_PARENT_DISCONNECTED") : network.index("    case MESH_EVENT_NETWORK_STATE")]

    assert "current_ip.addr = 0;" in disconnect
    assert disconnect.index("current_ip.addr = 0;") < disconnect.index("mesh_netifs_stop()")


def test_default_sta_is_created_before_wifi_initialization():
    main = (SOURCE / "main.c").read_text()
    network = (SOURCE / "mesh_network.c").read_text()
    netif = (SOURCE / "mesh_netif.c").read_text()

    assert main.index("wifi_station_init_network_core()") < main.index("mesh_netifs_init()")
    assert main.index("mesh_netifs_init()") < main.index("wifi_station_prepare(&has_wifi_credentials)")
    assert "err = mesh_netifs_init();" not in network
    assert "esp_wifi_register_if_rxcb(driver, esp_netif_receive, netif)" in netif


def test_internal_ip_path_uses_default_sta_dhcp_and_mesh_frame_forwarding():
    netif = (SOURCE / "mesh_netif.c").read_text()

    assert "ESP_NETIF_INHERENT_DEFAULT_WIFI_STA()" in netif
    assert "esp_netif_dhcpc_get_status(station_netif, &dhcp_status)" in netif
    assert "esp_netif_dhcps_get_status(netif, &dhcp_status)" in netif
    assert "MESH_PROTO_AP" in netif
    assert "MESH_DATA_TODS" in netif
    assert "MESH_PROTO_STA" in netif
    assert "esp_netif_receive(mesh_ap_netif" in netif
    assert "esp_netif_receive(station_netif" in netif
