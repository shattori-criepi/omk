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


def test_root_parent_connection_restarts_default_sta_dhcp_only():
    netif = (SOURCE / "mesh_netif.c").read_text()
    root_branch = netif[netif.index("if (is_root) {") : netif.index("    if (station_netif != NULL && strcmp(esp_netif_get_desc(station_netif), \"omk_mesh_sta\") == 0) return ESP_OK;")]

    assert "esp_netif_dhcpc_stop(station_netif)" in root_branch
    assert "esp_netif_dhcpc_start(station_netif)" in root_branch


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
