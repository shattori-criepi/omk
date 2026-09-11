import pytest

from ichijo_energy_node.config import Config


def test_config_rejects_missing_target_ip(monkeypatch):
    monkeypatch.delenv("ICHIJO_ECHONET_TARGET_IP", raising=False)
    with pytest.raises(ValueError, match="ICHIJO_ECHONET_TARGET_IP must be set"):
        Config.from_env()


def test_config_rejects_empty_target_ip(monkeypatch):
    monkeypatch.setenv("ICHIJO_ECHONET_TARGET_IP", "")
    with pytest.raises(ValueError, match="ICHIJO_ECHONET_TARGET_IP must be set"):
        Config.from_env()


def test_config_does_not_accept_legacy_target_ip_name(monkeypatch):
    monkeypatch.delenv("ICHIJO_ECHONET_TARGET_IP", raising=False)
    monkeypatch.setenv("ICHJO_ECHONET_TARGET_IP", "192.0.2.10")
    with pytest.raises(ValueError, match="ICHIJO_ECHONET_TARGET_IP must be set"):
        Config.from_env()


def test_config_accepts_explicit_target_ip(monkeypatch):
    monkeypatch.delenv("ICHIJO_DEVICE_ID", raising=False)
    monkeypatch.setenv("ICHIJO_ECHONET_TARGET_IP", "192.0.2.10")
    assert Config.from_env().device_id == "ichijo-001"


def test_config_rejects_invalid_timeout(monkeypatch):
    monkeypatch.setenv("ICHIJO_ECHONET_TARGET_IP", "192.0.2.10")
    monkeypatch.setenv("ICHIJO_ECHONET_TIMEOUT_SECONDS", "zero")
    with pytest.raises(ValueError, match="number"):
        Config.from_env()
