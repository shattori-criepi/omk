import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from omk_ble import main
from omk_ble.registry import SensorRegistry
from omk_ble.service import BleManager


@pytest.mark.parametrize("failure", [None, "bluetooth", "startup", "connect", "loop_start", "stop", "body"])
def test_lifespan_preserves_startup_and_always_cleans_up(tmp_path, monkeypatch, failure):
    events = []
    client = Mock()
    manager = BleManager(SensorRegistry(tmp_path / "sensors.json"))
    scanner = Mock()

    def connect(*args):
        assert args == ("mqtt-test", 1884)
        assert client.on_connect is main._on_mqtt_connect
        assert client.on_message is main._on_mqtt_message
        events.append("connect")
        if failure == "connect":
            raise ValueError("connect")

    def loop_start():
        events.append("loop_start")
        if failure == "loop_start":
            raise ValueError("loop_start")

    async def start():
        events.append("start")
        manager._scanner = scanner
        if failure == "bluetooth":
            raise RuntimeError("Bluetooth adapter or BlueZ is unavailable")
        if failure == "startup":
            raise ValueError("startup")

    async def stop():
        events.append("stop")
        if failure == "stop":
            raise ValueError("stop")

    scanner.stop = AsyncMock(side_effect=stop)
    manager.start_collection = AsyncMock(side_effect=start)
    client.connect_async.side_effect = connect
    client.loop_start.side_effect = loop_start
    client.loop_stop.side_effect = lambda: events.append("loop_stop")
    monkeypatch.setattr(main, "client", client)
    monkeypatch.setattr(main, "manager", manager)
    monkeypatch.setenv("MQTT_HOST", "mqtt-test")
    monkeypatch.setenv("MQTT_PORT", "1884")

    async def run():
        async with main.app.router.lifespan_context(main.app):
            events.append("serving")
            assert main.health() == {"status": "ok"}
            if failure == "body":
                raise ValueError("body")

    if failure in (None, "bluetooth"):
        asyncio.run(run())
    else:
        with pytest.raises(ValueError, match=failure):
            asyncio.run(run())

    expected = ["connect"]
    if failure != "connect":
        expected.append("loop_start")
    if failure not in ("connect", "loop_start"):
        expected.append("start")
        if failure != "startup":
            expected.append("serving")
        expected.append("stop")
        scanner.stop.assert_awaited_once()
    expected.append("loop_stop")
    assert events == expected
    assert manager._scanner is None
    client.loop_stop.assert_called_once()
