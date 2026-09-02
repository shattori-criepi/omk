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
    # the field-validated board profile without changing other targets.
    for name, value in (("HIGH", "-78"), ("MEDIUM", "-80"), ("LOW", "-82")):
        assert f"CONFIG_MESH_PARENT_RSSI_{name}={value}" in atom_defaults


def test_mesh_disables_root_conflicts_before_start():
    network = (SOURCE / "mesh_network.c").read_text()

    assert "esp_mesh_allow_root_conflicts(false)" in network
    assert "esp_mesh_is_root_conflicts_allowed()" in network
    assert "Mesh root conflicts: disabled" in network
    assert network.index("disable_root_conflicts();") < network.index("esp_mesh_start();")


def test_root_parent_connection_signals_external_sta_link_and_starts_dhcp():
    netif = (SOURCE / "mesh_netif.c").read_text()
    root_branch = netif[netif.index("if (is_root) {") : netif.index("    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), \"omk_mesh_sta\") == 0) return ESP_OK;")]

    assert "return start_root_external_dhcp();" in root_branch
    helper = netif[netif.index("static esp_err_t start_root_external_dhcp") : netif.index("static void destroy_mesh_ap")]
    assert "esp_netif_action_connected(station_netif, NULL, 0, NULL);" in helper
    assert "esp_netif_dhcpc_get_status(station_netif, &dhcp_status)" in helper
    assert "dhcp_status != ESP_NETIF_DHCP_STARTED" in helper
    assert "esp_netif_dhcpc_start" not in helper


def test_root_recreated_default_sta_starts_wifi_link_once():
    netif = (SOURCE / "mesh_netif.c").read_text()
    root_branch = netif[
        netif.index("if (is_root) {") : netif.index(
            "    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), \"omk_mesh_sta\") == 0) return ESP_OK;"
        )
    ]

    assert "bool default_station_created = false;" in root_branch
    assert root_branch.count("default_station_created = true;") == 2
    assert "if (default_station_created) {" in root_branch
    assert root_branch.count("start_default_station_link()") == 1


def test_root_parent_disconnect_recreates_external_sta_even_before_internal_ap_exists():
    netif = (SOURCE / "mesh_netif.c").read_text()
    stop = netif[netif.index("esp_err_t mesh_netifs_stop") : netif.index("esp_err_t mesh_netif_start_root_ap")]

    assert "mesh_ap_netif == NULL" not in stop
    assert "esp_wifi_clear_default_wifi_driver_and_handlers(station_netif)" in stop
    assert "esp_netif_destroy(station_netif)" in stop
    assert "create_default_station()" in stop
    assert "start_default_station_link()" in stop


def test_repeated_root_parent_reconnects_reuse_safe_dhcp_action():
    netif = (SOURCE / "mesh_netif.c").read_text()
    root_branch = netif[netif.index("if (is_root) {") : netif.index("    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), \"omk_mesh_sta\") == 0) return ESP_OK;")]
    helper = netif[netif.index("static esp_err_t start_root_external_dhcp") : netif.index("static void destroy_mesh_ap")]

    assert root_branch.count("start_root_external_dhcp()") == 1
    assert "ESP_NETIF_DHCP_STARTED" in helper
    assert "esp_netif_dhcpc_stop" not in helper


def test_root_child_role_transitions_keep_their_existing_netif_replacements():
    netif = (SOURCE / "mesh_netif.c").read_text()
    root_branch = netif[netif.index("if (is_root) {") : netif.index("    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), \"omk_mesh_sta\") == 0) return ESP_OK;")]
    child_branch = netif[netif.index("    ESP_LOGI(TAG, \"Starting child internal STA") : netif.index("esp_err_t mesh_netifs_stop")]

    assert "Replacing child internal STA netif with root external STA" in root_branch
    assert "destroy_mesh_driver(station_netif)" in root_branch
    assert "create_default_station()" in root_branch
    assert "Replacing root external STA netif with child internal STA" in child_branch
    assert "esp_wifi_clear_default_wifi_driver_and_handlers(station_netif)" in child_branch
    assert "destroy_mesh_ap();" in child_branch
    assert "start_mesh_child_station();" in child_branch


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
    assert "esp_wifi_register_if_rxcb(driver, esp_netif_receive, station_netif)" in netif


def test_internal_ip_path_uses_default_sta_dhcp_and_mesh_frame_forwarding():
    netif = (SOURCE / "mesh_netif.c").read_text()

    assert "ESP_NETIF_INHERENT_DEFAULT_WIFI_STA()" in netif
    assert "esp_netif_dhcpc_get_status(station_netif, &dhcp_status)" in netif
    assert "esp_netif_dhcps_get_status(mesh_ap_netif, &dhcp_status)" in netif
    assert "MESH_PROTO_AP" in netif
    assert "MESH_DATA_TODS" in netif
    assert "MESH_PROTO_STA" in netif
    assert "esp_netif_receive(mesh_ap_netif" in netif
    assert "esp_netif_receive(station_netif" in netif
