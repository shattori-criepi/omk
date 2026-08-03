import pytest

from ichijo_energy_node.config import Config


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("ICHJO_DEVICE_ID", raising=False)
    assert Config.from_env().device_id == "ichijo-001"


def test_config_rejects_invalid_timeout(monkeypatch):
    monkeypatch.setenv("ICHJO_ECHONET_TIMEOUT_SECONDS", "zero")
    with pytest.raises(ValueError, match="number"):
        Config.from_env()
